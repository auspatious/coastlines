import sys
from collections import Counter
from pathlib import Path

import click
import geopandas as gpd
import xarray as xr
from datacube.utils.dask import start_local_dask
from dea_tools.coastal import pixel_tides
from odc.algo import erase_bad, mask_cleanup, to_f32
from odc.stac import configure_s3_access, load
from pystac_client import Client

from coastlines.raster import tide_cutoffs
from coastlines.utils import (
    click_aws_request_payer,
    click_aws_unsigned,
    click_baseline_year,
    click_buffer,
    click_config_path,
    click_end_year,
    click_index_threshold,
    click_overwrite,
    click_start_year,
    click_study_area,
    click_tide_centre,
    configure_logging,
    get_study_site_geometry,
    load_config,
    CoastlinesException,
)
from coastlines.vector import (
    all_time_stats,
    annual_movements,
    calculate_regressions,
    contours_preprocess,
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


def load_and_mask_data_with_stac(config: dict, query: dict, log) -> xr.Dataset:
    stac_api_url = config["STAC config"]["stac_api_url"]
    collections = config["STAC config"]["stac_collections"]

    log.info(f"Loading data from {stac_api_url} for collections {collections}")

    client = Client.open(stac_api_url)
    # Filtering for Tier1 data only
    items = list(
        client.search(
            collections=collections,
            query={"landsat:collection_category": {"in": ["T1"]}},
            **query,
        ).get_all_items()
    )

    epsg_codes = Counter(item.properties["proj:epsg"] for item in items)
    most_common_epsg = epsg_codes.most_common(1)[0][0]

    n_items = len(items)
    if n_items < 50:
        raise CoastlinesException(
            f"Found {n_items} items. This is not enough to do a reliable process."
        )

    log.info(f"Found {len(items)} items. Using epsg:{most_common_epsg} to load data.")

    # REMINDER!
    # TODO: Add a query for Tier 1 data ONLY!
    # /REMINDER!
    ds = load(
        items,
        bands=["green", "swir16", "qa_pixel"],
        **query,
        crs=most_common_epsg,
        resolution=30,
        stac_cfg=STAC_CFG,
        chunks={"x": 2000, "y": 2000, "time": 1},
        group_by="solar_day",
        patch_url=http_to_s3_url,
        fail_on_error=False,
    )

    # Get the nodata mask
    nodata_mask = ds.green == 0

    # Get cloud and cloud shadow mask
    mask_bitfields = [1, 2, 3, 4]  # dilated cloud, cirrus, cloud, cloud shadow
    bitmask = 0
    for field in mask_bitfields:
        bitmask |= 1 << field

    # Get cloud mask
    cloud_mask = ds["qa_pixel"].astype(int) & bitmask != 0
    # Expand and contract the mask to clean it up
    dilated_mask = mask_cleanup(cloud_mask, [("opening", 2), ("dilation", 3)])

    final_mask = nodata_mask | dilated_mask

    ds = erase_bad(ds, final_mask)

    # Convert to float and scale data to 0-1
    ds["green"] = to_f32(ds["green"], scale=0.0000275, offset=-0.2)
    ds["swir16"] = to_f32(ds["swir16"], scale=0.0000275, offset=-0.2)

    # Remove values outside the valid range (0-1)
    mask_invalid = (
        (ds["green"] < 0) | (ds["green"] > 1) | (ds["swir16"] < 0) | (ds["swir16"] > 1)
    )
    ds = erase_bad(ds, mask_invalid)

    # Create MNDWI
    ds["mndwi"] = (ds["green"] - ds["swir16"]) / (ds["green"] + ds["swir16"])

    return ds


def generate_yearly_composites(
    ds: xr.Dataset,
    tide_cutoff_min: float,
    tide_cutoff_max: float,
    start_year: int,
    end_year: int,
) -> xr.Dataset:
    # Load everything into memory once
    ds = ds.compute()

    # Filter out the extreme high- and low-tide pixels
    extreme_tides = (ds.tide_m <= tide_cutoff_min) | (ds.tide_m >= tide_cutoff_max)
    ds = ds.where(~extreme_tides)

    # Filter out empty scenes
    ds = ds.sel(time=extreme_tides.sum(dim=["x", "y"]) == 0)

    # Store a list of output arrays and years, knowing we might lose empty years
    yearly_ds_list = []
    years = range(start_year, end_year + 1)

    # We've found data for start_year - 1 through to end_year + 1.
    # This range includes only the years we want to process...
    for year in years:
        one_year = ds.sel(time=str(year))
        three_years = ds.sel(time=slice(str(year - 1), str(year + 1)))

        year_summary = one_year.mndwi.median(dim="time").to_dataset()
        year_summary["count"] = one_year.mndwi.count(dim="time")
        year_summary["stdev"] = one_year.mndwi.std(dim="time")
        # And a gapfill summary for the years either side of the year we're processing
        year_summary["gapfill"] = three_years.mndwi.median(dim="time")

        yearly_ds_list.append(year_summary)

    time_var = xr.Variable("year", years)

    yearly_ds = xr.concat(yearly_ds_list, dim=time_var)

    return yearly_ds


def export_results(
    points_gdf: gpd.GeoDataFrame,
    contours_gdf: gpd.GeoDataFrame,
    output_version: str,
    output_location: Path,
    study_area: str,
):
    output_location = output_location / output_version
    output_location.mkdir(parents=True, exist_ok=True)

    output_contours = output_location / f"contours_{study_area}.parquet"
    output_points = output_location / f"points_{study_area}.parquet"

    if output_contours.exists():
        output_contours.unlink()

    if output_points.exists():
        output_points.unlink()

    points_gdf.to_parquet(output_points)
    contours_gdf.to_parquet(output_contours)


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
    overwrite: bool,
    log,
    use_datacube: bool = False,
):
    # Output location checking
    output_location = Path(output_location)
    output_contours = (
        output_location / output_version / f"contours_{study_area}.parquet"
    )

    if output_contours.exists() and not overwrite:
        log.info(
            f"Skipping study area {study_area} as output contours file already exists"
        )
        return

    # Study site geometry and config parsing
    geometry = get_study_site_geometry(config["Input files"]["grid_path"], study_area)
    log.info(f"Loaded geometry for study area {study_area}")

    boundingbox = geometry.buffer(buffer).boundingbox
    bbox = [boundingbox.left, boundingbox.bottom, boundingbox.right, boundingbox.top]
    log.info(f"Using bounding box: {bbox}")

    query = {
        "bbox": bbox,
        "datetime": f"{start_year - 1}/{end_year + 1}",
    }

    # Load data
    ds = None
    if not use_datacube:
        ds = load_and_mask_data_with_stac(config, query, log)

    # Calculate tides and combine with the data
    log.info("Modelling tides")
    ds["tide_m"], tides_lowres = pixel_tides(
        ds, resample=True, directory=tide_data_location
    )

    # Create annual composites including gapfilling sparse years
    log.info("Generating annual mosaics using dask lazy loading")
    tide_cutoff_min, tide_cutoff_max = tide_cutoffs(
        ds, tides_lowres, tide_centre=tide_centre
    )
    yearly_ds = generate_yearly_composites(
        ds, tide_cutoff_min, tide_cutoff_max, start_year, end_year
    )

    log.info("Loading combined dataset into memory...")
    combined_ds = yearly_ds.where(
        yearly_ds["count"] > 5, yearly_ds["gapfill"]
    ).compute()
    del combined_ds["gapfill"]
    log.info("Finished loading into memory")

    # TODO: optionally write the combined_ds to a Zarr file somewhere

    # Coastal mask modifications
    log.info("Loading vectors and pre-processing contours")
    modifications_gdf = gpd.read_file(
        config["Input files"]["modifications_path"], bbox=bbox
    ).to_crs(str(combined_ds.odc.crs))

    # Mask dataset to focus on coastal zone only
    masked_ds, _ = contours_preprocess(
        combined_ds=combined_ds,
        water_index="mndwi",
        index_threshold=index_threshold,
        mask_with_esa_wc=True,
        buffer_pixels=33,
        mask_modifications=modifications_gdf,
    )
    # Extract shorelines
    log.info("Extracting shorelines")
    contours_gdf = subpixel_contours(
        da=masked_ds,
        z_values=index_threshold,
        min_vertices=10,
        dim="year",
    ).set_index("year")

    log.info("Calculating annual movements and statistics")
    points_gdf = points_on_line(contours_gdf, baseline_year, distance=30)

    points_gdf = annual_movements(
        points_gdf,
        contours_gdf,
        combined_ds,
        str(baseline_year),
        "mndwi",
        max_valid_dist=1200,
    )

    # Reindex to add any missing annual columns to the dataset
    points_gdf = points_gdf.reindex(
        columns=[
            "geometry",
            *[f"dist_{i}" for i in range(start_year, end_year + 1)],
            "angle_mean",
            "angle_std",
        ]
    )

    points_gdf = calculate_regressions(points_gdf)

    # Add count and span of valid obs, Shoreline Change Envelope (SCE),
    # Net Shoreline Movement (NSM) and Max/Min years
    stats_list = ["valid_obs", "valid_span", "sce", "nsm", "max_year", "min_year"]
    points_gdf[stats_list] = points_gdf.apply(
        lambda x: all_time_stats(x, initial_year=start_year), axis=1
    )

    # TODO: change output location to work for S3 or local file and to be parameterised
    output_location = Path("data/processed")

    log.info(f"Writing to files at {output_location}")
    export_results(
        points_gdf, contours_gdf, output_version, output_location, study_area
    )
    log.info(f"Finished work on study area {study_area}")


@click.command("coastlines-combined")
@click_config_path
@click_study_area
@click.option(
    "--output-version",
    type=str,
    required=True,
    help="A unique string proving a name that will be used "
    "for output directories and files. This can be "
    "used to version different analysis outputs.",
)
@click.option(
    "--output-location",
    type=str,
    default="data/processed",
)
@click_start_year
@click_end_year
@click_baseline_year
@click_tide_centre
@click.option("--tide-data-location", type=str, required=True)
@click_index_threshold
@click_buffer
@click_aws_unsigned
@click_aws_request_payer
@click_overwrite
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
):
    log = configure_logging(f"Coastlines {study_area}")
    log.info(f"Starting work on study area {study_area}")

    # Load analysis params from config file
    config = load_config(config_path=config_path)

    virtual_product = config.get("Virtual product")
    stac_config = config.get("STAC config")

    if virtual_product is not None:
        raise NotImplementedError("Virtual products are not yet implemented")
    if stac_config is None:
        raise ValueError("STAC config must be provided in config file")

    if aws_unsigned and aws_request_payer:
        raise ValueError("Cannot set both aws_unsigned and aws_request_payer to True")

    log.info("Configuring S3 access")
    # Do an opinionated configuration of S3
    configure_s3_access(
        cloud_defaults=True, aws_unsigned=aws_unsigned, requester_pays=aws_request_payer
    )

    log.info("Starting Dask")
    # Set up Dask
    start_local_dask(n_workers=8, threads_per_worker=4, mem_safety_margin="2G")

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
            overwrite,
            log=log,
        )

    except Exception as e:
        log.exception(f"Study area {study_area}: Failed to run process with error {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
