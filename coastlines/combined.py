import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Tuple, Union

import click
import geopandas as gpd
import xarray as xr
from datacube.utils.dask import start_local_dask
from dea_tools.coastal import pixel_tides
from dea_tools.spatial import hillshade
from odc.algo import mask_cleanup, to_f32
from odc.stac import configure_s3_access, load
from pystac import ItemCollection
from pystac_client import Client
from s3path import S3Path

from coastlines.config import CoastlinesConfig
from coastlines.raster import tide_cutoffs

# from dea_tools.datahandling import parallel_apply  # Needs a PR merged
from coastlines.utils import (
    CoastlinesException,
    click_config_path,
    click_output_location,
    click_output_version,
    click_overwrite,
    click_study_area,
    configure_logging,
    get_study_site_geometry,
    is_s3,
    load_config,
    parallel_apply,
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


BAD_IDS = [
    "LC09_L2SP_124054_20220621_20220624_02_T1_SR",  # Reported to USGS on 11/01/23
    "LC09_L2SP_124053_20220621_20220624_02_T1_SR",  # Reported to USGS on 12/01/23
    "LC09_L2SP_124052_20220621_20220624_02_T1_SR",  # Reported to USGS on 12/01/23
]


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
    config: CoastlinesConfig,
    query: dict,
    include_nir: bool = False,
    include_awei: bool = False,
    include_wi: bool = False,
    debug: bool = False,
) -> xr.Dataset:
    lower_limit = config.stac.lower_scene_limit
    upper_limit = config.stac.upper_scene_limit
    index = config.options.water_index

    if index not in ["mndwi", "ndwi", "combined", "mndwi_nir"]:
        raise CoastlinesException(
            f"Unknown water index: {index}. Must be one of 'mndwi', 'ndwi', 'combined' or 'mndwi_nir'"
        )

    client = Client.open(config.stac.stac_api_url)

    # Search for STAC Items. First for only T1 then for both T1 and T2
    query["collections"] = config.stac.stac_collections
    query_filter = {"landsat:collection_category": {"in": ["T1"]}}
    search = client.search(
        query=query_filter,
        **query,
    )
    n_items = search.matched()

    # If we don't have enough T1 items, search for T2 as well
    if n_items < lower_limit:
        query_filter = {}
        print("Warning, not enough T1 items found, searching for T2 items as well")
        search = client.search(
            query=query_filter,
            **query,
        )
        n_items = search.matched()

    # If we have too many items, filter out some high-cloud scenes
    percentage = 100
    while n_items > upper_limit:
        percentage -= 5
        query_filter["eo:cloud_cover"] = {"lt": percentage}
        print(
            f"Warning, too many items found ({n_items} > {upper_limit}). Pre-filtering to {percentage}% clouds"
        )

        search = client.search(
            query=query_filter,
            **query,
        )
        n_items = search.matched()
        if percentage == 50:
            break

    # If we still have too few items, raise an error
    if n_items < lower_limit:
        raise CoastlinesException(
            f"Found {n_items} items using both T1 and T2 scenes. This is not enough to do a reliable process."
        )

    items = list(search.get_items())

    # Hack to remove some bad items
    items = [i for i in items if i.id not in BAD_IDS]

    epsg_codes = Counter(item.properties["proj:epsg"] for item in items)
    epsg_code = epsg_codes.most_common(1)[0][0]

    bands = ["green", "swir16", "qa_pixel"]

    if include_nir:
        bands.append("nir08")

    if include_awei:
        bands = bands + ["swir22", "blue"]

    if include_wi:
        bands.append("red")

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

    if include_nir or index in ["ndwi", "combined", "mndwi_nir"]:
        ds["nir08"] = to_f32(ds["nir08"], scale=0.0000275, offset=-0.2)

    if include_awei:
        ds["swir22"] = to_f32(ds["swir22"], scale=0.0000275, offset=-0.2)
        ds["blue"] = to_f32(ds["blue"], scale=0.0000275, offset=-0.2)

    if include_wi:
        ds["red"] = to_f32(ds["red"], scale=0.0000275, offset=-0.2)

    # Remove values outside the valid range (0-1), but not for nir or awei bands
    invalid_ard_values = (
        (ds["green"] < 0) | (ds["green"] > 1) | (ds["swir16"] < 0) | (ds["swir16"] > 1)
    )

    # Create MNDWI
    if index == "mndwi" or index == "combined":
        ds["mndwi"] = (ds["green"] - ds["swir16"]) / (ds["green"] + ds["swir16"])

    # Create NDWI
    if index == "ndwi" or index == "combined":
        ds["ndwi"] = (ds["green"] - ds["nir08"]) / (ds["green"] + ds["nir08"])

    if index == "combined":
        ds["combined"] = (ds["mndwi"] + ds["ndwi"]) / 2

    # Create new modified MNDWI index
    if index == "mndwi_nir":
        scaled_green = (ds["green"] + (1 - ds["nir08"])) / 2
        scaled_swir1 = (ds["swir16"] + ds["nir08"]) / 2
        ds["mndwi_nir"] = (scaled_green - scaled_swir1) / (scaled_green + scaled_swir1)

    # Create AWEI
    if include_awei:
        ds["awei_ns"] = 4 * (ds["green"] - ds["swir16"]) - (
            0.25 * ds["nir08"] + 2.75 * ds["swir22"]
        )
        ds["awei_sh"] = (
            ds["blue"]
            + 2.5 * ds["green"]
            - 1.5 * (ds["nir08"] + ds["swir16"])
            - 0.25 * ds["swir22"]
        )

    # Create WI
    if include_wi:
        ds["wi"] = (
            1.7204
            + 171 * ds["green"]
            + 3 * ds["red"]
            - 70 * ds["nir08"]
            - 45 * ds["swir16"]
            - 71 * ds["swir22"]
        )

    # Mask the data, setting the nodata value to `nan`
    final_mask = nodata_mask | dilated_cloud_mask | invalid_ard_values
    ds = ds.where(~final_mask)

    # If we're not debugging, just return the necessary bands
    if not debug:
        return_bands = [index]
        if include_nir:
            return_bands.append("nir08")

        ds = ds[return_bands]

    return ds, items


def terrain_shadow(
    ds: xr.Dataset,
    dem: xr.Dataset,
    items_by_time: dict,
    threshold: float = 0.25,
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
    stac_collection: str = "cop-dem-glo-30",
    debug: bool = False,
) -> xr.Dataset:
    client = Client.open(stac_catalog)
    bbox = list(ds.geobox.extent.to_crs("epsg:4326").boundingbox)
    items_by_time = {
        item.datetime.strftime("%Y-%m-%dT%H:%M:%S"): item for item in items
    }

    dem_items = list(client.search(collections=[stac_collection], bbox=bbox).items())

    if len(dem_items) == 0:
        raise CoastlinesException("No DEM items found.")
    else:
        dem = load(dem_items, like=ds, measurements=["data"])

        hillshadow = parallel_apply(
            ds,
            "time",
            terrain_shadow,
            use_threads=True,
            dem=dem.squeeze().data.values,
            items_by_time=items_by_time,
        )

        # Filter out the hill shaded pixels
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
    ds: xr.Dataset,
    year: int,
    water_index: str = "mndwi",
    include_nir: bool = False,
    debug: bool = False,
) -> Tuple[int, xr.Dataset]:
    one_year = ds.sel(time=str(year))
    three_years = ds.sel(time=slice(str(year - 1), str(year + 1)))

    # Get the median and other statistics for this year
    year_summary = one_year[water_index].median(dim="time").to_dataset()

    year_summary["count"] = one_year[water_index].count(dim="time")
    year_summary["stdev"] = one_year[water_index].std(dim="time")

    # And a gapfill summary for the three years
    year_summary[f"gapfill_{water_index}"] = three_years[water_index].median(dim="time")
    year_summary["gapfill_count"] = three_years[water_index].count(dim="time")
    year_summary["gapfill_stdev"] = three_years[water_index].std(dim="time")

    # Optional extras
    if include_nir:
        year_summary["nir"] = one_year.nir08.median(dim="time")
        year_summary["gapfill_nir"] = three_years.nir08.median(dim="time")

    if debug:
        year_summary["green"] = one_year.green.median(dim="time")
        year_summary["swir"] = one_year.swir16.median(dim="time")

        year_summary["gapfill_green"] = three_years.green.median(dim="time")
        year_summary["gapfill_swir"] = three_years.swir16.median(dim="time")

        # Get raw mndwi and ndwi values too
        year_summary["mndwi"] = one_year.mndwi.median(dim="time")
        year_summary["gapfill_mndwi"] = three_years.mndwi.median(dim="time")

        if include_nir:
            year_summary["ndwi"] = one_year.ndwi.median(dim="time")
            year_summary["gapfill_ndwi"] = three_years.ndwi.median(dim="time")

    return year, year_summary


def generate_yearly_composites(
    ds: xr.Dataset,
    start_year: int,
    end_year: int,
    water_index: str = "mndwi",
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

    # Define a function to be executed in each thread
    def process_year(year):
        try:
            year, year_summary = get_one_year_composite(
                ds,
                year,
                water_index=water_index,
                include_nir=include_nir,
                debug=debug,
            )
            return year, year_summary
        except KeyError:
            return None

    # Use a ThreadPoolExecutor to parallelize the operation
    with ThreadPoolExecutor() as executor:
        results = executor.map(process_year, years)

    for result in results:
        if result is not None:
            year, year_summary = result
            data_year_list.append(year)
            yearly_ds_list.append(year_summary)

    if len(yearly_ds_list) == 0:
        raise CoastlinesException("No data found for any years.")

    time_var = xr.Variable("year", data_year_list)
    yearly_ds = xr.concat(yearly_ds_list, dim=time_var)

    if replace_with_gapfill:
        # Remove low obs pixels and replace with 3-year gapfill
        yearly_ds[water_index] = yearly_ds[water_index].where(
            yearly_ds["count"] > 5, yearly_ds[f"gapfill_{water_index}"]
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
        del yearly_ds[f"gapfill_{water_index}"]
        del yearly_ds["gapfill_count"]
        del yearly_ds["gapfill_stdev"]

        if include_nir:
            del yearly_ds["gapfill_nir"]

    return yearly_ds


def extract_points_with_movements(
    unmasked_data: xr.Dataset,
    contours: gpd.GeoDataFrame,
    baseline_year: int,
    start_year: int,
    end_year: int,
    water_index: str = "mndwi",
) -> gpd.GeoDataFrame:
    try:
        points = points_on_line(contours, baseline_year, distance=30)
    except KeyError:
        raise CoastlinesException("No data found for baseline year.")

    points = annual_movements(
        points,
        contours,
        unmasked_data,
        str(baseline_year),
        water_index,
        max_valid_dist=2500,  # Increased from 1,200
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
    output_location: str | None,
    tide_data_location: str,
    log: callable,
    load_early: bool = True,
):
    # Study site geometry and config parsing
    geometry = get_study_site_geometry(config.input.grid_path, study_area)
    log.info(f"Loaded geometry for study area {study_area}")

    # Config shenanigans
    bbox = list(
        geometry.to_crs(config.output.crs)
        .buffer(config.options.load_buffer_distance)
        .to_crs("epsg:4326")
        .bounds.values[0]
    )
    log.info(f"Using bounding box: {bbox}")

    # Either use the MNDWI index or the combined index
    log.info(f"Using water index: {config.options.water_index}")

    query = {
        "bbox": bbox,
        "datetime": f"{config.options.start_year - 1}/{config.options.end_year + 1}",
    }

    # Loading data
    data = None
    if config.stac is not None:
        data, items = load_and_mask_data_with_stac(
            config,
            query,
            include_nir=config.options.include_nir,
        )

        log.info(f"Found {len(items)} items to load.")
    else:
        raise NotImplementedError("Only STAC loading is currently supported")

    # Calculate tides and combine with the data
    log.info("Filtering by tides")
    n_times = len(data.time)
    data = filter_by_tides(data, tide_data_location, config.options.tide_centre)
    log.info(
        f"Dropped {n_times - len(data.time)} out of {n_times} timesteps due to extreme tides"
    )

    if load_early:
        log.info("Loading daily dataset into memory")
        data = data.compute()

    log.info("Running per-pixel tide masking at high resolution")
    data = mask_pixels_by_tide(data, tide_data_location, config.options.tide_centre)

    if config.options.mask_with_hillshade:
        log.info("Running per-pixel terrain shadow masking")
        try:
            data = mask_pixels_by_hillshadow(data, items)
        except CoastlinesException:
            log.warning("No DEM found for this area. Skipping hillshadow mask")

    # Loading combined yearly composites
    log.info("Generating yearly composites")
    combined_data = generate_yearly_composites(
        data,
        config.options.start_year,
        config.options.end_year,
        water_index=config.options.water_index,
        include_nir=config.options.include_nir,
    )

    if not load_early:
        log.info("Loading annual dataset into memory")
        combined_data = combined_data.compute()

    # TODO: Maybe write the combined_data to a Zarr file somewhere

    # Load the modifications layer to add/remove areas from the analysis
    log.info("Loading vectors")
    modifications_gdf = gpd.read_file(
        config.input.modifications_path, bbox=bbox
    ).to_crs(str(combined_data.odc.crs))

    geomorphology_url = config.input.geomorphology_path
    if config.input.geomorphology_path is None:
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
        water_index=config.options.water_index,
        index_threshold=config.options.index_threshold,
        buffer_pixels=33,
        mask_with_esa_wc=config.options.mask_with_esa_wc,
        modifications_gdf=modifications_gdf,
        include_nir=config.options.include_nir,
    )

    # Extract shorelines
    log.info("Extracting shorelines")
    contours = subpixel_contours(
        da=masked_data,
        z_values=config.options.index_threshold,
        min_vertices=10,
        dim="year",
    ).set_index("year")

    log.info("Adding certainty statistics to contours")
    contours_with_certainty = contour_certainty(contours, certainty_masks)

    log.info("Extracting points and calculating annual movements and statistics")
    points = extract_points_with_movements(
        combined_data,
        contours_with_certainty,
        config.options.baseline_year,
        config.options.start_year,
        config.options.end_year,
        water_index=config.options.water_index,
    )

    log.info("Calculating certainty statistics for points")
    points_with_certainty = points_certainty(
        points,
        geomorphology_gdf,
        baseline_year=config.options.baseline_year,
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
@click.option("--tide-data-location", type=str, required=True)
@click_overwrite
@click.option("--load-early/--no-load-early", default=True)
def cli(
    config_path,
    study_area,
    output_version,
    output_location,
    tide_data_location,
    overwrite,
    load_early,
):
    # Load analysis params from config file
    config = load_config(config_path)

    log = configure_logging("Coastlines")
    log.info(
        f"Starting work on study area {study_area} for years {config.options.start_year}-{config.options.end_year}"
    )

    if output_location is None:
        output_location = config.output.location

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
    if config.virtual_product is not None:
        raise NotImplementedError("Virtual products are not yet implemented")
    if config.stac is None:
        raise ValueError("STAC config must be provided in config file")

    if config.aws.aws_unsigned and config.aws.aws_request_payer:
        raise ValueError("Cannot set both aws_unsigned and aws_request_payer to True")

    log.info("Starting Dask")
    # Set up Dask
    _ = start_local_dask(n_workers=4, threads_per_worker=8, mem_safety_margin="2G")

    log.info("Configuring S3 access")
    # Do an opinionated configuration of S3 for data reading
    configure_s3_access(
        cloud_defaults=True,
        aws_unsigned=config.aws.aws_unsigned,
        requester_pays=config.aws.aws_request_payer,
    )

    try:
        log.info("Starting processing...")
        process_coastlines(
            config,
            study_area,
            output_version,
            output_location,
            tide_data_location,
            log,
            load_early=load_early,
        )
    except CoastlinesException as e:
        log.exception(f"Study area {study_area}: Failed to run process with error {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
