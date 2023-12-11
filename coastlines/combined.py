import sys
from collections import Counter
from pathlib import Path
from typing import Tuple, Union

import click
import geopandas as gpd
import xarray as xr
from datacube.utils.dask import start_local_dask
from dea_tools.coastal import pixel_tides
from dea_tools.datahandling import parallel_apply
from dea_tools.spatial import hillshade
from odc.algo import mask_cleanup, to_f32
from odc.stac import configure_s3_access, load
from pystac import ItemCollection
from pystac_client import Client
from s3path import S3Path

from coastlines.raster import tide_cutoffs
from coastlines.utils import (
    CoastlinesException,
    click_aws_request_payer,
    click_aws_unsigned,
    click_baseline_year,
    click_buffer,
    click_config_path,
    click_end_year,
    click_index_threshold,
    click_output_location,
    click_output_version,
    click_overwrite,
    click_start_year,
    click_study_area,
    click_tide_centre,
    configure_logging,
    get_study_site_geometry,
    is_s3,
    load_config,
)
from coastlines.vector import (
    all_time_stats,
    annual_movements,
    calculate_regressions,
    contour_certainty,
    contours_preprocess,
    points_certainty,
    points_on_line,
    subpixel_contours,
)

# TODO: work out how to pass this in...
STAC_CFG = {
    "landsat-c2l2-sr": {
        "assets": {
            "*": {"data_type": "uint16", "nodata": 0},
        }
    },
    "*": {"warnings": "ignore"},
}


# TODO: Make this changeable...
def http_to_s3_url(http_url):
    """Convert a USGS HTTP URL to an S3 URL"""
    s3_url = http_url.replace(
        "https://landsatlook.usgs.gov/data", "s3://usgs-landsat"
    ).rstrip(":1")
    return s3_url


def sanitise_tile_id(tile_id: str, zero_pad: bool = True) -> str:
    tile_parts = tile_id.split(",")
    if len(tile_parts) == 2:
        out_tile_id = "_".join([p.zfill(3) for p in tile_parts])
    else:
        out_tile_id = out_tile_id.zfill(3)
    return out_tile_id


def get_output_path(
    output_location: str,
    output_version: str,
    tile_id: str,
    dataset_name: str,
    extension: str,
) -> Union[Path, S3Path]:
    path = None
    if output_location.startswith("s3://"):
        path = S3Path(output_location.replace("s3:/", ""))
    else:
        path = Path(output_location)

    output_path = (
        path
        / output_version
        / f"{dataset_name}_{sanitise_tile_id(tile_id)}.{extension}"
    )

    return output_path


def load_and_mask_data_with_stac(
    config: dict,
    query: dict,
    upper_scene_limit: int = 3000,
    lower_scene_limit: int = 200,
    mask_terrain_shadow: bool = True,
    include_nir: bool = False,
    debug: bool = False,
) -> xr.Dataset:
    stac_config = config["STAC config"]
    stac_api_url = stac_config["stac_api_url"]
    collections = stac_config["stac_collections"]
    lower_limit = stac_config.get("lower_scene_limit", lower_scene_limit)
    upper_limit = stac_config.get("upper_scene_limit", upper_scene_limit)

    client = Client.open(stac_api_url)

    # Search for STAC Items. First for only T1 then for both T1 and T2
    query["collections"] = collections
    search = client.search(
        query={"landsat:collection_category": {"in": ["T1"]}},
        **query,
    )
    n_items = search.matched()

    # If we don't have enough T1 items, search for T2 as well
    if n_items < lower_limit:
        print("Warning, not enough T1 items found, searching for T2 items as well")
        search = client.search(
            **query,
        )
        n_items = search.matched()

    # If we have too many items, filter out some high-cloud scenes
    if n_items > upper_limit:
        print("Warning, too many items found. Filtering out some high-cloud scenes")
        search = client.search(
            query={"eo:cloud_cover": {"lt": 50}},
            **query,
        )
        n_items = search.matched()

    # If we still have too few items, raise an error
    if n_items < lower_limit:
        raise CoastlinesException(
            f"Found {n_items} items using both T1 and T2 scenes. This is not enough to do a reliable process."
        )

    items = list(search.get_items())

    epsg_codes = Counter(item.properties["proj:epsg"] for item in items)
    epsg_code = epsg_codes.most_common(1)[0][0]

    bands = ["green", "swir16", "qa_pixel"]
    if include_nir:
        bands.append("nir08")

    ds = load(
        items,
        bands=bands,
        **query,
        resampling={"qa_pixel": "nearest", "*": "average"},
        group_by="solar_day",
        crs=epsg_code,
        resolution=30,
        stac_cfg=STAC_CFG,
        chunks={"x": 10000, "y": 10000, "time": 1},
        patch_url=http_to_s3_url,
        fail_on_error=False,
    )

    # Get the nodata mask, just for the two main bands
    nodata_mask = (ds.green == 0) | (ds.swir16 == 0)

    # Get cloud and cloud shadow mask
    mask_bitfields = [3, 4]  # cloud, cloud shadow
    bitmask = 0
    for field in mask_bitfields:
        bitmask |= 1 << field

    # Get cloud mask
    cloud_mask = ds["qa_pixel"].astype(int) & bitmask != 0
    # Expand and contract the mask to clean it up
    # DE Africa uses 10 and 5, which Alex doesn't like!
    dilated_cloud_mask = mask_cleanup(cloud_mask, [("opening", 5), ("dilation", 6)])

    # Convert to float and scale data to 0-1
    ds["green"] = to_f32(ds["green"], scale=0.0000275, offset=-0.2)
    ds["swir16"] = to_f32(ds["swir16"], scale=0.0000275, offset=-0.2)

    if include_nir:
        ds["nir08"] = to_f32(ds["nir08"], scale=0.0000275, offset=-0.2)

    # Remove values outside the valid range (0-1), but not for nir
    invalid_ard_values = (
        (ds["green"] < 0) | (ds["green"] > 1) | (ds["swir16"] < 0) | (ds["swir16"] > 1)
    )

    # Create MNDWI
    ds["mndwi"] = (ds["green"] - ds["swir16"]) / (ds["green"] + ds["swir16"])

    # Mask the data, setting the nodata value to `nan`
    final_mask = nodata_mask | dilated_cloud_mask | invalid_ard_values
    ds = ds.where(~final_mask)

    if mask_terrain_shadow:
        # Mask out terrain shadow
        print("Not yet implemented")

    # Delete unneeded data
    if not debug:
        del ds["green"]
        del ds["swir16"]
        del ds["qa_pixel"]

    return ds, items


def terrain_shadow(
    ds: xr.Dataset,
    dem: xr.Dataset,
    items_by_time: dict,
    threshold: float = 0.5,
    radius: int = 1,
):
    item = items_by_time[ds.time.values.astype(str).split(".")[0]]
    elevation = item.properties["view:sun_elevation"]
    azimuth = item.properties["view:sun_azimuth"]

    hs = hillshade(dem, elevation, azimuth)
    hs = hs < threshold
    hs_da = xr.DataArray(hs, dims=["y", "x"])
    hs_da = mask_cleanup(hs_da, [("opening", radius), ("dilation", radius)])

    return hs_da


def mask_pixels_by_hillshadow(
    ds: xr.Dataset,
    items: ItemCollection,
    stac_catalog: str = "https://planetarycomputer.microsoft.com/api/stac/v1/",
    stac_collection="cop-dem-glo-30",
    debug: bool = False,
) -> xr.Dataset:
    client = Client.open(stac_catalog)
    bbox = list(ds.geobox.extent.to_crs("epsg:4326").boundingbox)
    items_by_time = {
        item.datetime.strftime("%Y-%m-%dT%H:%M:%S"): item for item in items
    }

    dem_items = list(client.search(collections=[stac_collection], bbox=bbox).items())
    dem = load(dem_items, like=ds, measurements=["data"])

    hillshadow = parallel_apply(
        ds,
        "time",
        terrain_shadow,
        dem=dem.squeeze().data.values,
        items_by_time=items_by_time,
    )

    ds = ds.where(~hillshadow)
    if debug:
        return ds, hillshadow

    return ds


def mask_pixels_by_tide(
    ds: xr.Dataset, tide_data_location: str, tide_centre: float, debug: bool = False
) -> xr.Dataset:
    tides, tides_lowres = pixel_tides(
        ds, resample=True, directory=tide_data_location, dask_compute=True
    )

    tide_cutoff_min, tide_cutoff_max = tide_cutoffs(
        ds, tides_lowres, tide_centre=tide_centre, reproject=True
    )

    extreme_tides = (tides <= tide_cutoff_min) | (tides >= tide_cutoff_max)

    # Filter out the extreme high- and low-tide pixels
    ds = ds.where(~extreme_tides)

    if debug:
        return ds, tides, tides_lowres, extreme_tides

    return ds


def filter_by_tides(
    ds: xr.Dataset, tide_data_location: str, tide_centre: float
) -> xr.Dataset:
    tides_lowres = pixel_tides(ds, resample=False, directory=tide_data_location)

    tide_cutoff_min, tide_cutoff_max = tide_cutoffs(
        ds, tides_lowres, tide_centre=tide_centre, reproject=False
    )

    extreme_tides = (tides_lowres <= tide_cutoff_min) | (
        tides_lowres >= tide_cutoff_max
    )

    # Filter out empty scenes
    filtered = ds.sel(time=~extreme_tides.all(dim=["x", "y"]))

    # Check we have some scenes remaining after!
    if len(filtered.time) == 0:
        raise CoastlinesException("No data remains after filtering for extreme tides.")

    return filtered


def get_one_year_composite(
    ds: xr.Dataset, year: int, include_nir: bool = False, debug: bool = False
) -> Tuple[int, xr.Dataset]:
    one_year = ds.sel(time=str(year))
    three_years = ds.sel(time=slice(str(year - 1), str(year + 1)))

    # Get the median and other statistics for this year
    year_summary = one_year.mndwi.median(dim="time").to_dataset()
    year_summary["count"] = one_year.mndwi.count(dim="time")
    year_summary["stdev"] = one_year.mndwi.std(dim="time")

    if include_nir:
        year_summary["nir"] = one_year.nir08.median(dim="time")

    # And a gapfill summary for the three years
    year_summary["gapfill_mndwi"] = three_years.mndwi.median(dim="time")
    year_summary["gapfill_count"] = three_years.mndwi.count(dim="time")
    year_summary["gapfill_stdev"] = three_years.mndwi.std(dim="time")

    if include_nir:
        year_summary["gapfill_nir"] = three_years.nir08.median(dim="time")

    if debug:
        year_summary["green"] = one_year.green.median(dim="time")
        year_summary["swir"] = one_year.swir16.median(dim="time")

        year_summary["gapfill_green"] = three_years.green.median(dim="time")
        year_summary["gapfill_swir"] = three_years.swir16.median(dim="time")

    return year, year_summary


def generate_yearly_composites(
    ds: xr.Dataset,
    start_year: int,
    end_year: int,
    replace_with_gapfill: bool = True,
    include_nir: bool = False,
    debug: bool = False,
) -> xr.Dataset:
    # Store a list of output arrays and years, knowing we might lose empty years
    yearly_ds_list = []
    data_year_list = []

    # We've found data for start_year - 1 through to end_year + 1.
    # This range includes only the years we want to process...
    years = range(start_year, end_year + 1)
    for year in years:
        try:
            year, year_summary = get_one_year_composite(
                ds, year, include_nir=include_nir, debug=debug
            )
            data_year_list.append(year)
            yearly_ds_list.append(year_summary)
        except KeyError:
            pass

    if len(yearly_ds_list) == 0:
        raise CoastlinesException("No data found for any years.")

    time_var = xr.Variable("year", data_year_list)
    yearly_ds = xr.concat(yearly_ds_list, dim=time_var)

    if replace_with_gapfill:
        # Remove low obs pixels and replace with 3-year gapfill
        yearly_ds["mndwi"] = yearly_ds["mndwi"].where(
            yearly_ds["count"] > 5, yearly_ds["gapfill_mndwi"]
        )
        yearly_ds["stdev"] = yearly_ds["stdev"].where(
            yearly_ds["count"] > 5, yearly_ds["gapfill_stdev"]
        )

        if include_nir:
            yearly_ds["nir"] = yearly_ds["nir"].where(
                yearly_ds["count"] > 5, yearly_ds["gapfill_nir"]
            )

        # Update the counts with the gapfill counts
        yearly_ds["count"] = yearly_ds["count"].where(
            yearly_ds["count"] > 5, yearly_ds["gapfill_count"]
        )

        # If we're debugging, then return more information
        if debug:
            yearly_ds["green"] = yearly_ds["green"].where(
                yearly_ds["count"] > 5, yearly_ds["gapfill_green"]
            )
            yearly_ds["swir"] = yearly_ds["swir"].where(
                yearly_ds["count"] > 5, yearly_ds["gapfill_swir"]
            )

    if not debug:
        del yearly_ds["gapfill_mndwi"]
        del yearly_ds["gapfill_count"]
        del yearly_ds["gapfill_stdev"]

        if include_nir:
            del yearly_ds["gapfill_nir"]

    return yearly_ds


def extract_points_with_movements(
    masked_data: xr.Dataset,
    contours: gpd.GeoDataFrame,
    baseline_year: int,
    start_year: int,
) -> gpd.GeoDataFrame:
    try:
        points = points_on_line(contours, baseline_year, distance=30)
    except KeyError:
        raise CoastlinesException("No data found for baseline year.")

    points = annual_movements(
        points,
        contours,
        masked_data,
        str(baseline_year),
        "mndwi",
        max_valid_dist=1200,
    )

    # Reindex to add any missing annual columns to the dataset
    points = points.reindex(
        columns=[
            "geometry",
            *[f"dist_{i}" for i in range(start_year, end_year + 1)],
            "angle_mean",
            "angle_std",
        ]
    )

    # Calculate coastal change... this is very slow when data is complex
    points = calculate_regressions(points)

    # Add count and span of valid obs, Shoreline Change Envelope (SCE),
    # Net Shoreline Movement (NSM) and Max/Min years
    stats_list = ["valid_obs", "valid_span", "sce", "nsm", "max_year", "min_year"]
    points[stats_list] = points.apply(
        lambda x: all_time_stats(x, initial_year=start_year), axis=1
    )

    return points


def export_results(
    points_gdf: gpd.GeoDataFrame,
    contours_gdf: gpd.GeoDataFrame,
    output_version: str,
    output_location: str,
    study_area: str,
):
    output_contours = get_output_path(
        output_location, output_version, study_area, "contours", "parquet"
    )
    output_points = get_output_path(
        output_location, output_version, study_area, "points", "parquet"
    )

    if is_s3(output_contours):
        output_points = f"s3:/{output_points}"
        output_contours = f"s3:/{output_contours}"
    else:
        output_path = output_contours.parent
        output_path.mkdir(parents=True, exist_ok=True)

        if output_contours.exists():
            output_contours.unlink()

        if output_points.exists():
            output_points.unlink()

    points_gdf.to_parquet(output_points)
    contours_gdf.to_parquet(output_contours)

    return (output_points, output_contours)


def process_coastlines(
    config: dict,
    study_area: str,
    output_version: str,
    output_location: str,
    start_year: int,
    end_year: int,
    baseline_year: int,
    tide_centre: float,
    tide_data_location: str,
    index_threshold: float,
    buffer: float,
    log: callable,
    load_early: bool = True,
    mask_with_hillshade: bool = False,
):
    # Study site geometry and config parsing
    geometry = get_study_site_geometry(config["Input files"]["grid_path"], study_area)
    log.info(f"Loaded geometry for study area {study_area}")

    bbox = list(geometry.buffer(buffer).bounds.values[0])
    log.info(f"Using bounding box: {bbox}")

    query = {
        "bbox": bbox,
        "datetime": f"{start_year - 1}/{end_year + 1}",
    }

    # Loading data
    data = None
    if config.get("STAC config") is not None:
        data, items = load_and_mask_data_with_stac(config, query)
        log.info(f"Found {len(items)} items to load.")
    else:
        raise NotImplementedError("Only STAC loading is currently supported")

    # Calculate tides and combine with the data
    log.info("Filtering by tides")
    n_times = len(data.time)
    data = filter_by_tides(data, tide_data_location, tide_centre)
    log.info(
        f"Dropped {n_times - len(data.time)} out of {n_times} timesteps due to extreme tides"
    )

    if load_early:
        log.info("Loading daily dataset into memory")
        data = data.compute()

    log.info("Running per-pixel tide masking at high resolution")
    data = mask_pixels_by_tide(data, tide_data_location, tide_centre)

    if mask_with_hillshade:
        log.info("Running per-pixel terrain shadow masking")
        data = mask_pixels_by_hillshadow(data, items)

    # Loading combined yearly composites
    log.info("Generating yearly composites")
    combined_data = generate_yearly_composites(data, start_year, end_year)

    if not load_early:
        log.info("Loading annual dataset into memory")
        combined_data = combined_data.compute()

    # TODO: Maybe write the combined_data to a Zarr file somewhere

    # Load the modifications layer to add/remove areas from the analysis
    log.info("Loading vectors")
    modifications_gdf = gpd.read_file(
        config["Input files"]["modifications_path"], bbox=bbox
    ).to_crs(str(combined_data.odc.crs))

    geomorphology_url = config["Input files"].get("geomorphology_path")
    if geomorphology_url is None:
        log.warning("Using empty geomorphology dataset")
        geomorphology_url = "data/raw/empty_modifications.geojson"
    geomorphology_gdf = gpd.read_file(
        geomorphology_url,
        mask=geometry,
    )

    log.info("Preprocessing contours")
    # Mask dataset to focus on coastal zone only
    masked_data, certainty_masks = contours_preprocess(
        combined_ds=combined_data,
        water_index="mndwi",
        index_threshold=index_threshold,
        mask_with_esa_wc=True,
        buffer_pixels=33,
        modifications_gdf=modifications_gdf,
    )

    # Extract shorelines
    log.info("Extracting shorelines")
    contours = subpixel_contours(
        da=masked_data,
        z_values=index_threshold,
        min_vertices=10,
        dim="year",
    ).set_index("year")

    log.info("Adding certainty statistics to contours")
    contours_with_certainty = contour_certainty(contours, certainty_masks)

    log.info("Extracting points and calculating annual movements and statistics")
    points = extract_points_with_movements(masked_data, contours_with_certainty, baseline_year, start_year)

    log.info("Calculating certainty statistics for points")
    points_with_certainty = points_certainty(
        points,
        geomorphology_gdf,
        baseline_year=baseline_year,
        rocky_query="(Preds == 'Bedrock') and (Probs > 0.75)",
        rate_of_change_threshold=200,
    )

    # Clip to the study area
    points_with_certainty = points_with_certainty.clip(
        geometry.to_crs(points_with_certainty.crs)
    )
    contours_with_certainty = contours_with_certainty.clip(
        geometry.to_crs(contours_with_certainty.crs)
    )

    # Write results
    log.info(f"Writing to files to {output_location}")
    outputs = export_results(
        points_with_certainty,
        contours_with_certainty,
        output_version,
        output_location,
        study_area,
    )

    for output in outputs:
        log.info(f"Wrote: {output}")

    log.info(f"Finished work on study area {study_area}")


@click.command("coastlines-combined")
@click_config_path
@click_study_area
@click_output_version
@click_output_location
@click_start_year
@click_end_year
@click_baseline_year
@click_tide_centre
@click.option("--tide-data-location", type=str, required=True)
@click_index_threshold
@click_buffer
# TODO: remove aws_unsigned and request_payer... or work out how to pass them in
@click_aws_unsigned
@click_aws_request_payer
@click_overwrite
@click.option("--load-early/--no-load-early", default=True)
@click.option("--mask-with-hillshade/--no-mask-with-hillshade", default=False)
def cli(
    config_path,
    study_area,
    output_version,
    output_location,
    start_year,
    end_year,
    baseline_year,
    tide_centre,
    tide_data_location,
    index_threshold,
    buffer,
    aws_unsigned,
    aws_request_payer,
    overwrite,
    load_early,
    mask_with_hillshade,
):
    log = configure_logging("Coastlines")
    log.info(
        f"Starting work on study area {study_area} for years {start_year}-{end_year}"
    )

    # Load analysis params from config file
    config = load_config(config_path=config_path)

    log.info("Checking output location")
    if overwrite is False:
        # We don't want to overwrite, so see if we've done this work already
        output_contours = get_output_path(
            output_location, output_version, study_area, "contours", "parquet"
        )
        output_points = get_output_path(
            output_location, output_version, study_area, "points", "parquet"
        )

        # This function works for both S3 and local paths
        if output_contours.exists() and output_points.exists():
            log.info(
                f"Found existing output files at {output_location}. Skipping processing."
            )
            sys.exit(0)

    log.info("Checking configuration")
    virtual_product = config.get("Virtual product")
    stac_config = config.get("STAC config")

    if virtual_product is not None:
        raise NotImplementedError("Virtual products are not yet implemented")
    if stac_config is None:
        raise ValueError("STAC config must be provided in config file")

    if aws_unsigned and aws_request_payer:
        raise ValueError("Cannot set both aws_unsigned and aws_request_payer to True")

    log.info("Starting Dask")
    # Set up Dask
    _ = start_local_dask(n_workers=4, threads_per_worker=8, mem_safety_margin="2G")

    log.info("Configuring S3 access")
    # Do an opinionated configuration of S3 for data reading
    configure_s3_access(
        cloud_defaults=True, aws_unsigned=aws_unsigned, requester_pays=aws_request_payer
    )

    try:
        log.info("Starting processing...")
        process_coastlines(
            config,
            study_area,
            output_version,
            output_location,
            start_year,
            end_year,
            baseline_year,
            tide_centre,
            tide_data_location,
            index_threshold,
            buffer,
            log,
            load_early=load_early,
            mask_with_hillshade=mask_with_hillshade,
        )
    except CoastlinesException as e:
        log.exception(f"Study area {study_area}: Failed to run process with error {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
