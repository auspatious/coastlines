import tempfile
from pathlib import Path
from typing import Iterable, Union

import boto3
import click
import pandas as pd
from geopandas import GeoDataFrame, read_file, read_parquet
from odc.stac import configure_s3_access
from s3path import S3Path

from coastlines.utils import (
    STYLES_FILE,
    CoastlinesException,
    click_config_path,
    click_output_version,
    configure_logging,
    is_s3,
    load_config,
    wms_fields,
)

from coastlines.vector import generate_hotspots

def list_files_s3(input_location: str, suffix: str):
    input_location = input_location.lower()
    if input_location.startswith("s3://"):
        path = S3Path(input_location.replace("s3:/", ""))
    else:
        path = Path(input_location)

    return path.glob(f"**/*{suffix}")


def find_points_contours(files: Iterable) -> list[list[S3Path], list[S3Path]]:
    points_files = []
    contours_files = []
    for file in files:
        file_string = str(file)
        if "points" in file_string:
            points_files.append(file)
        elif "contours" in file_string:
            contours_files.append(file)
    return points_files, contours_files


def load_parquet_files(files: list[S3Path] | list[Path], output_crs: str):
    data_frames = []
    is_s3 = False

    if type(files[0]) is S3Path:
        is_s3 = True

    for file in files:
        file_string = str(file)
        if is_s3:
            file_string = f"s3:/{file}"
        df = read_parquet(file_string).to_crs(output_crs)
        data_frames.append(df)

    return GeoDataFrame(pd.concat(data_frames), geometry="geometry")


def munge_data(
    rates_of_change: GeoDataFrame, shorelines: GeoDataFrame
) -> tuple[GeoDataFrame, GeoDataFrame]:
    # Add a certainty column to shorelinres
    shorelines["certainty"] = "good"
    rates_of_change["certainty"] = "good"

    return rates_of_change, shorelines


def get_output_path(
    output_location: str,
    output_version: str,
    dataset_name: str,
    extension: str,
) -> Union[Path, S3Path]:
    path = None
    if output_location.startswith("s3://"):
        path = S3Path(output_location.replace("s3:/", ""))
    else:
        path = Path(output_location)

    output_path = path / f"{dataset_name}_{output_version}.{extension}"

    return output_path


def write_files(
    rates_of_change: GeoDataFrame,
    shorelines: GeoDataFrame,
    hotspots: GeoDataFrame,
    output_location: str,
    output_version: str,
    write_parquet: bool = False,
) -> list[str]:
    # Destination files
    output_geopackage = get_output_path(
        output_location, output_version, "coastlines", "gpkg"
    )

    write_to_s3 = is_s3(output_geopackage)
    written = []

    # Write the parquet files
    if write_parquet:
        output_shorelines = get_output_path(
            output_location, output_version, "shorelines_annual", "parquet"
        )
        output_rates_of_change = get_output_path(
            output_location, output_version, "rates_of_change", "parquet"
        )

        if write_to_s3:
            output_shorelines = f"s3:/{output_shorelines}"
            output_rates_of_change = f"s3:/{output_rates_of_change}"
        else:
            # Need to clean up existing files maybe
            if output_geopackage.exists():
                output_geopackage.unlink()

            if output_shorelines.exists():
                output_shorelines.unlink()
            if output_rates_of_change.exists():
                output_rates_of_change.unlink()
            # And check the parent directory exists
            output_shorelines.parent.mkdir(parents=True, exist_ok=True)

        # Shorelines and rate of change writing
        shorelines.to_parquet(output_shorelines)
        rates_of_change.to_parquet(output_rates_of_change)

        written.append(output_shorelines)
        written.append(output_rates_of_change)

        # Hotspot writing
        for i, hotspot in enumerate(hotspots):
            output_hotspot = get_output_path(
                output_location,
                output_version,
                f"hotspots_{output_version}_zoom_{i + 1}",
                "parquet",
            )
            if write_to_s3:
                output_hotspot = f"s3:/{output_hotspot}"
            else:
                if output_hotspot.exists():
                    output_hotspot.unlink()
            hotspot.to_parquet(output_hotspot)

            written.append(output_hotspot)

    # Write geopackage
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write to a temporary file first
        temp_geopackage = f"{tmpdir}/coastlines_{output_version}.gpkg"

        # Main data writing
        shorelines.to_file(temp_geopackage, layer="shorelines_annual", driver="GPKG")
        rates_of_change.to_file(temp_geopackage, layer="rates_of_change", driver="GPKG")

        for i, hotspot in enumerate(hotspots):
            hotspot.to_file(
                temp_geopackage, layer=f"hotspots_zoom_{i + 1}", driver="GPKG"
            )

        # Handy built-in styles
        styles = read_file(
            STYLES_FILE, GEOM_POSSIBLE_NAMES="geometry"
        )
        styles.to_file(temp_geopackage, layer="layer_styles", driver="GPKG")

        # Shift the tempfile to its final destination
        if write_to_s3:
            s3 = boto3.client("s3")
            s3.upload_file(
                temp_geopackage, output_geopackage.bucket, output_geopackage.key
            )
            written.append(f"s3:/{output_geopackage}")
        else:
            output_geopackage.parent.mkdir(parents=True, exist_ok=True)
            Path(temp_geopackage).rename(output_geopackage)
            written.append(output_geopackage)

    return written


@click.command("merge-tiles")
@click_config_path
@click_output_version
def cli(config_path, output_version):
    # Set up
    config = load_config(config_path, "coastlines")
    log = configure_logging()

    log.info(f"Merging files from {config.output.location}")

    log.info("Configuring S3")
    configure_s3_access()

    log.info("Listing files")
    input_location = f"{config.output.location}/{output_version}"
    files = list_files_s3(input_location, suffix=".parquet")

    points_files, contours_files = find_points_contours(files)
    n_contours = len(contours_files)
    n_points = len(points_files)
    log.info(f"Found {n_points} points files and {n_contours} contours files")

    if n_contours != n_points:
        raise CoastlinesException("Number of points and contours files must be equal")
    if n_contours == 0:
        raise CoastlinesException("No points or contours files found")

    log.info("Loading files into memory...")
    rates_of_change = load_parquet_files(points_files, config.output.crs)
    shorelines = load_parquet_files(contours_files, config.output.crs)

    # Add the WMS fields to the rates of change data
    rates_of_change = pd.concat(
        [rates_of_change, wms_fields(gdf=rates_of_change)], axis=1
    )

    log.info("Generating hotspots")
    hotspots = generate_hotspots(
        shorelines,
        rates_of_change,
        [10000, 5000, 1000],
        config.options.baseline_year,
        add_wms_fields=True,
    )

    log.info("Writing files")
    written = write_files(
        rates_of_change,
        shorelines,
        hotspots,
        config.output.location,
        output_version,
    )

    for file in written:
        log.info(f"Wrote: {file}")


if __name__ == "__main__":
    cli()
    cli()
    cli()
