import logging
import yaml
import fsspec
from pathlib import Path
import click
from datacube.utils.geometry import Geometry
import geopandas as gpd
from geopandas import GeoDataFrame

STYLES_FILE = Path(__file__).parent / "styles.csv"


# Create our own exception to raise
class CoastlinesException(Exception):
    pass


def configure_logging(name: str = "Coastlines") -> logging.Logger:
    """
    Configure logging for the application.
    """
    logger = logging.getLogger(name)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    return logger


def load_config(config_path: str) -> dict:
    """
    Loads a YAML config file and returns data as a nested dictionary.

    config_path can be a path or URL to a web accessible YAML file
    """
    with fsspec.open(config_path, mode="r") as f:
        config = yaml.safe_load(f)
    return config


def load_json(grid_path: str) -> GeoDataFrame:
    gridcell_gdf = gpd.read_file(grid_path).to_crs(epsg=4326).set_index("id")
    gridcell_gdf.index = gridcell_gdf.index.astype(str)

    return gridcell_gdf


def get_study_site_geometry(grid_path: str, study_area: str) -> Geometry:
    # Grid cells used to process the analysis
    gridcell_gdf = load_json(grid_path)
    gridcell_gdf = gridcell_gdf.loc[[study_area]]

    return Geometry(gridcell_gdf.iloc[0].values[0], crs=gridcell_gdf.crs)


click_config_path = click.option(
    "--config-path",
    type=str,
    required=True,
    help="Path to the YAML config file defining inputs to "
    "use for this analysis. These are typically located in "
    "the `dea-coastlines/configs/` directory.",
)
click_study_area = click.option(
    "--study-area",
    type=str,
    required=True,
    help="A string providing a unique ID of an analysis "
    "gridcell that will be used to run the analysis. This "
    'should match a row in the "id" column of the provided '
    "analysis gridcell vector file.",
)
click_start_year = click.option(
    "--start-year",
    type=int,
    default=2020,
    help="The first annual shoreline you wish to be included "
    "in the final outputs. To allow low data pixels to be "
    "gapfilled with additional satellite data from neighbouring "
    "years, the full timeseries of satellite data loaded in this "
    "step will include one additional year of preceding satellite data "
    "(i.e. if `--start_year 2000`, satellite data from 1999 onward "
    "will be loaded for gapfilling purposes). Because of this, we "
    "recommend that at least one year of satellite data exists in "
    "your datacube prior to `--start_year`.",
)
click_end_year = click.option(
    "--end-year",
    type=int,
    default=2022,
    help="The final annual shoreline you wish to be included "
    "in the final outputs. To allow low data pixels to be "
    "gapfilled with additional satellite data from neighbouring "
    "years, the full timeseries of satellite data loaded in this "
    "step will include one additional year of ensuing satellite data "
    "(i.e. if `--end_year 2020`, satellite data up to and including "
    "2021 will be loaded for gapfilling purposes). Because of this, we "
    "recommend that at least one year of satellite data exists in your "
    "datacube after `--end_year`.",
)
click_baseline_year = click.option(
    "--baseline-year",
    type=int,
    default=2021,
    help="The annual shoreline used as a baseline from "
    "which to generate the rates of change point statistics. "
    "This is typically the most recent annual shoreline in "
    "the dataset (i.e. the same as `--end_year`).",
)
click_tide_centre = click.option(
    "--tide-centre",
    type=float,
    default=0.0,
    help="The central tide height used to compute the min and max tide "
    "height cutoffs. Tide heights will be masked so all satellite "
    "observations are approximately centred over this value. The "
    "default is 0.0 which represents 0 m Above Mean Sea Level.",
)
click_buffer = click.option(
    "--buffer",
    type=float,
    default=0.05,
    help="The distance (in degrees) to buffer the study area grid cell "
    "extent. This buffer is important for ensuring that generated "
    "rasters overlap along the boundaries of neighbouring study areas "
    "so that we can extract seamless vector shorelines. Defaults to "
    "0.05 degrees, or roughly 5 km at the equator.",
)
click_aws_unsigned = click.option(
    "--aws-unsigned/--no-aws-unsigned",
    type=bool,
    default=True,
    help="Whether to use sign AWS requests for S3 access",
)
click_aws_request_payer = click.option(
    "--aws-request-payer/--no-aws-request-payer",
    type=bool,
    default=False,
    help="Whether to agree to pay for AWS requests for S3 access",
)
click_overwrite = click.option(
    "--overwrite/--no-overwrite",
    type=bool,
    default=True,
    help="Whether to overwrite tiles with existing outputs, "
    "or skip these tiles entirely.",
)
click_index_threshold = click.option(
    "--index_threshold",
    type=float,
    default=0.00,
    help="The water index threshold used to extract "
    "subpixel precision shorelines. Defaults to 0.00.",
)
