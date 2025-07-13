import os
import sys
from pathlib import Path
from typing import Union

import boto3
import click
import xarray as xr
import numpy as np
from datacube.utils.dask import start_local_dask
from datacube.utils.geometry import Geometry
from dep_tools.aws import write_stac_s3, object_exists
from dep_tools.namers import S3ItemPath
from dep_tools.stac_utils import StacCreator, set_stac_properties
from dep_tools.writers import AwsDsCogWriter
from intertidal.elevation import elevation
from intertidal.exposure import exposure
from intertidal.tidal_bias_offset import bias_offset
from intertidal.io import prepare_for_export
from odc.stac import configure_s3_access, load
from pystac_client import Client
from s3path import S3Path
from xarray import Dataset

from coastlines.utils import (
    CoastlinesException,
    click_config_path,
    click_output_location,
    click_output_version,
    click_overwrite,
    click_study_area,
    configure_logging,
    get_study_site_geometry,
    load_config,
)

AWS_REGION = os.environ.get("AWS_REGION", "ap-southeast-2")


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


def get_s2_ls(
    aoi: Geometry,
    catalog="https://earth-search.aws.element84.com/v1",
    ls_collection="landsat-c2-l2",
    s2_collection="sentinel-2-l2a",
    start_year="2020",
    end_year="2022",
    cloud_cover=50,
) -> tuple[Dataset, Dataset]:
    datetime = f"{start_year}/{end_year}"

    # # Connect to client
    client = Client.open(catalog)

    # # Search for Landsat items
    ls_items = client.search(
        collections=[ls_collection],
        intersects=aoi,
        datetime=datetime,
        query={"eo:cloud_cover": {"lt": cloud_cover}},
    ).item_collection()

    # Search for Sentinel-2 items
    s2_items = client.search(
        collections=[s2_collection],
        intersects=aoi,
        datetime=datetime,
        query={"eo:cloud_cover": {"lt": cloud_cover}},
    ).item_collection()

    common_options = {
        "chunks": {"x": 2048, "y": 2048},
        "groupby": "solar_day",
        "resampling": {"qa_pixel": "nearest", "SCL": "nearest", "*": "cubic"},
        "fail_on_error": False,
    }

    # Load Landsat with ODC STAC
    landsat_data = load(
        items=ls_items,
        geopolygon=aoi,
        bands=["green", "nir08", "qa_pixel"],
        **common_options,
    )

    # Apply Landsat cloud mask
    # Bit flag mask for the QA_PIXEL band
    # We need Bit 3: high confidence cloud, bit 4: high confidence shadow,
    # which are the 4th and 5th bits from the right (0-indexed)
    bitflags = 0b00011000

    cloud_mask = (landsat_data.qa_pixel & bitflags) != 0
    landsat_data = landsat_data.where(~cloud_mask).drop_vars("qa_pixel")

    sentinel2_data = load(
        items=s2_items,
        like=landsat_data,
        bands=["green", "nir", "scl"],
        **common_options,
    )

    # Apply Sentinel-2 cloud mask
    # 1: defective, 3: shadow, 9: high confidence cloud
    mask_flags = [1, 3, 9]

    cloud_mask = ~sentinel2_data.scl.isin(mask_flags)
    sentinel2_data = sentinel2_data.where(cloud_mask).drop_vars("scl")

    # Apply scaling
    ds_ls = (landsat_data.where(landsat_data.green != 0) * 0.0000275 + -0.2).clip(0, 1)
    ds_s2 = (sentinel2_data.where(sentinel2_data.green != 0) * 0.0001).clip(0, 1)

    return ds_ls, ds_s2


def get_ndwi(ds_ls: Dataset, ds_s2: Dataset) -> Dataset:
    # Convert to NDWI
    ndwi_ls = (ds_ls.green - ds_ls.nir08) / (ds_ls.green + ds_ls.nir08)
    ndwi_s2 = (ds_s2.green - ds_s2.nir) / (ds_s2.green + ds_s2.nir)

    # Combine into a single dataset
    data = (
        xr.concat([ndwi_ls, ndwi_s2], dim="time").sortby("time").to_dataset(name="ndwi")
    )

    return data


def get_elevation(
    ds: Dataset,
    tide_model: str,
    tide_model_dir: str,
    ensemble_models: list,
    ranking_points: str,
):
    dse, tide_m = elevation(
        ds,
        tide_model=tide_model,
        tide_model_dir=tide_model_dir,
        ensemble_models=ensemble_models,
        ranking_points=ranking_points,
    )
    return dse, tide_m


def get_exposure(
    ds: Dataset,
    year,
    tide_model: str,
    tide_model_dir: str,
    ensemble_models: list[str],
    ranking_points: str,
    modelled_freq: str = "3h",
):
    # Exposure variables
    filters = None  # Exposure filters eg None, ['Dry', 'Neap_low']
    filters_combined = None  # Must be a list of tuples containing one temporal and spatial filter each, eg None or [('Einter','Lowtide')]

    exposure_filters, modelledtides_ds = exposure(
        dem=ds.elevation,
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31",
        modelled_freq=modelled_freq,
        tide_model=tide_model,
        tide_model_dir=tide_model_dir,
        filters=filters,
        filters_combined=filters_combined,
        ensemble_models=ensemble_models,
        ranking_points=ranking_points,
    )

    # Write each exposure output as new variables in the main dataset
    for x in exposure_filters.data_vars:
        ds[f"exposure_{x}"] = exposure_filters[x]

    return ds, modelledtides_ds

def split_s3_path(s3_path):
    path_parts = s3_path.replace("s3://", "").split("/")
    bucket = path_parts.pop(0)
    key = "/".join(path_parts)
    return bucket, key


def process_intertidal(
    config: dict,
    study_area: str,
    output_version: str,
    output_location: str | None,
    tide_data_location: str,
    log: callable,
    overwrite: bool = True,
):
    # Set output data types
    custom_dtypes = {
        "qa_ndwi_freq": (np.uint8, 255),
        "qa_count_clear": (np.int16, -999),
    }
        
    # Study site geometry and config parsing
    studyarea_gdf = get_study_site_geometry(config.input.grid_path, study_area)
    geom = Geometry(studyarea_gdf.loc[study_area].geometry, crs=studyarea_gdf.crs)
    run_id = f"[{output_version}] [{config.options.label_year}] [{study_area}]"
    log.info(f"{run_id}: Loaded geometry for study area {study_area}")

    log.info(f"{run_id}: Load satellite imagery")
    landsat_items, sentinel2_items = get_s2_ls(
        aoi=geom,
        catalog=config.stac.stac_api_url,
        ls_collection=config.stac.stac_collections["ls"],
        s2_collection=config.stac.stac_collections["s2"],
        start_year=config.options.start_year,
        end_year=config.options.end_year,
    )

    log.info(f"{run_id}: Calculating NDWI...")
    ndwi = get_ndwi(landsat_items, sentinel2_items)
    ndwi = ndwi.compute()

    # elevation
    log.info(f"{run_id}: Calculating elevation...")
    ds, tide_m = get_elevation(
        ndwi,
        tide_model=config.options.tide_model,
        tide_model_dir=tide_data_location,
        ensemble_models=config.options.ensemble_model_list,
        ranking_points=config.options.ensemble_model_rankings,
    )

    # exposure
    if config.options.use_exposure_offsets:
        log.info(f"{run_id}: Calculating intertidal exposure...")

        # Calculate exposure
        ds, modelledtides_ds = get_exposure(
            ds,
            year=config.options.label_year,
            tide_model=config.options.tide_model,
            tide_model_dir=tide_data_location,
            ensemble_models=config.options.ensemble_model_list,
            ranking_points=config.options.ensemble_model_rankings,
            modelled_freq=config.options.modelled_freq,
        )

        # Rename unfiltered and set datatype
        ds = ds.rename_vars({"exposure_unfiltered": "exposure"})

        # Calculate spread and offsets
        log.info(f"{run_id}: Calculating spread and offset...")
        (
            ds["ta_spread"],
            ds["ta_offset_low"],
            ds["ta_offset_high"],
        ) = bias_offset(
            tide_m=tide_m,
            tide_cq=modelledtides_ds["unfiltered"],
            lot_hot=False,
            lat_hat=False,
        )
 
        # Set spread and offset data types
        custom_dtypes["exposure"] = (np.uint8, 255)
        custom_dtypes["ta_spread"] = (np.uint8, 255)
        custom_dtypes["ta_offset_high"] = (np.uint8, 255)
        custom_dtypes["ta_offset_low"] = (np.uint8, 255)

    else:
        log.info(f"{run_id}: Skipping Exposure and spread/offsets calculation")
    
    # Prepare data for export
    ds["qa_ndwi_freq"] *= 100  # Convert frequency to %
    ds = prepare_for_export(ds, custom_dtypes=custom_dtypes)  # sets correct dtypes and nodata
    
    aws_client = boto3.client("s3")

    bucket, bucket_prefix = split_s3_path(output_location)

    # itempath
    itempath = S3ItemPath(
        bucket=bucket,
        sensor="s2ls",
        dataset_id="intertidal",
        version=output_version,
        time=config.options.label_year,
        prefix=config.input.organisation,
        bucket_prefix=bucket_prefix,
    )
    stac_document = itempath.stac_path(study_area)

    log.info(f"{run_id}: Checking output location")
    if overwrite is False:
        # We don't want to overwrite, so see if we've done this work already
        if object_exists(bucket, stac_document, client=aws_client):
            log.info(f"{run_id}: Item already exists at {stac_document}")
            # This is an exit with success
            sys.exit(0)
        else:
            log.info(f"{run_id}: Item does not exist at {stac_document}, proceeding to write.")

    # Write externally
    output_data = set_stac_properties(ndwi, ds)

    writer = AwsDsCogWriter(
        itempath=itempath,
        overwrite=True,
        convert_to_int16=False,
        extra_attrs=dict(dep_version=output_version),
        write_multithreaded=True,
        client=aws_client,
    )

    log.info(f"{run_id}: Writing cogs...")
    paths = writer.write(output_data, study_area) + [stac_document]

    stac_creator = StacCreator(
        itempath=itempath,
        remote=True,
        collection_url_root=config.input.collection_url,
        make_hrefs_https=config.options.use_https_href_links,
        with_raster=True,
        aws_region=AWS_REGION,
    )

    stac_item = stac_creator.process(output_data, study_area)
    log.info(f"{run_id}: Writing stac...")
    write_stac_s3(stac_item, stac_document, bucket)

    if paths is not None:
        log.info(f"{run_id}: Completed writing to {paths[-1]}")
    else:
        log.warning(f"{run_id}: No paths returned from writer")

    # finish
    log.info(f"{run_id}: {study_area} - {config.options.start_year} Processed.")


@click.command("intertidal")
@click_config_path
@click_study_area
@click_output_version
@click_output_location
@click.option(
    "--start-year",
    type=str,
    help="The start year of satellite data to load. "
    "For Intertidal, this is set to provide a three year window "
    "centred over `label_date` below.",
)
@click.option(
    "--end-year",
    type=str,
    help="The end year of satellite data to load."
    "For Intertidal, this is set to provide a three year window "
    "centred over `label_date` below.",
)
@click.option(
    "--label-year",
    type=str,
    help="The year used to label output arrays, and to use as the date "
    "assigned to the dataset when indexed into Datacube.",
)
@click.option(
    "--tide-data-location",
    type=str,
    default="/var/share/tide_models",
    help="The directory containing tide model data files. Defaults to "
    "'/var/share/tide_models'; for more information about the required "
    "directory structure, refer to `eo-tides.utils.list_models`.",
)
@click_overwrite
def cli(
    config_path,
    study_area,
    start_year,
    end_year,
    label_year,
    output_version,
    output_location,
    tide_data_location,
    overwrite,
):
    # Load analysis params from config file
    config = load_config(config_path, "intertidal")

    if start_year is not None:
        config.options.start_year = start_year
    if end_year is not None:
        config.options.end_year = end_year
    if label_year is not None:
        config.options.label_year = label_year

    log = configure_logging("Intertidal")
    log.info(
        f"Starting work on study area {study_area} for year {config.options.label_year}"
    )

    if output_location is None:
        output_location = config.output.location

    log.info("Checking configuration")
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
        process_intertidal(
            config,
            study_area,
            output_version,
            output_location,
            tide_data_location,
            log,
            overwrite=overwrite,
        )
    except CoastlinesException as e:
        log.exception(f"Study area {study_area}: Failed to run process with error {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
