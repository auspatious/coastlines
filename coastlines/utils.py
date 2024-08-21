import logging
from pathlib import Path
from typing import Union

import click
import fsspec
import geopandas as gpd
import pandas as pd
from geopandas import GeoDataFrame
from odc.stac import load
from planetary_computer import sign_url
from pystac_client import Client
from s3path import S3Path
from xarray import Dataset
from yaml import safe_load

from coastlines.config import CoastlinesConfig

from dea_tools.spatial import subpixel_contours

STYLES_FILE = Path(__file__).parent / "styles.csv"


# Create our own exception to raise
class CoastlinesException(Exception):
    pass


def is_s3(path: Union[Path, S3Path]) -> bool:
    """
    Check if a path is an S3 path.
    """
    return isinstance(path, S3Path)


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


def load_config(config_path: str) -> CoastlinesConfig:
    """
    Load a CoastlinesConfig object from a YAML configuration file.

    Parameters:
        config_path (str): The path to the YAML configuration file.

    Returns:
        CoastlinesConfig: The loaded CoastlinesConfig object.
    """
    with fsspec.open(config_path, mode="r") as f:
        loaded = safe_load(f)
        return CoastlinesConfig(**loaded)


def load_json(grid_path: str) -> GeoDataFrame:
    gridcell_gdf = gpd.read_file(grid_path).to_crs(epsg=4326).set_index("id")
    gridcell_gdf.index = gridcell_gdf.index.astype(str)

    return gridcell_gdf


def get_study_site_geometry(grid_path: str, study_area: str) -> gpd.GeoDataFrame:
    # Grid cells used to process the analysis
    gridcell_gdf = load_json(grid_path)
    try:
        gridcell_gdf = gridcell_gdf.loc[[study_area]]
    except KeyError as e:
        raise CoastlinesException(
            f"Study area {study_area} not found in grid file"
        ) from e

    return gridcell_gdf


def get_esa_water(combined_ds: Dataset):
    pc_url = "https://planetarycomputer.microsoft.com/api/stac/v1/"
    pc_client = Client.open(pc_url)

    bb = combined_ds.odc.geobox.boundingbox.to_crs(4326)
    bbox = [bb.left, bb.bottom, bb.right, bb.top]

    WATER = 80
    NODATA = 0

    items = list(
        pc_client.search(
            collections=["esa-worldcover"], bbox=bbox, datetime="2021"
        ).items()
    )

    water = None

    # Check for no items here
    if len(items) == 0:
        print("Warning, no ESA World Cover data found here")
    else:
        landcover = load(
            items, geobox=combined_ds.odc.geobox, patch_url=sign_url, bands=["map"]
        )["map"]
        water = landcover.isin([WATER, NODATA]).squeeze(dim="time")

    return water


def parallel_apply(ds, dim, func, use_threads=False, *args, **kwargs):
    """
    Temporarily copied until this PR is merged and a new release made:
    https://github.com/GeoscienceAustralia/dea-notebooks/pull/1171
    """

    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
    from functools import partial
    from itertools import repeat

    import xarray as xr
    from tqdm import tqdm

    if use_threads:
        Executor = ThreadPoolExecutor
    else:
        Executor = ProcessPoolExecutor

    with Executor() as executor:
        # Update func to add kwargs
        func = partial(func, **kwargs)

        # Apply func in parallel
        groups = [group for (i, group) in ds.groupby(dim)]
        to_iterate = (groups, *(repeat(i, len(groups)) for i in args))
        out_list = list(tqdm(executor.map(func, *to_iterate), total=len(groups)))

    # Combine to match the original dataset
    return xr.concat(out_list, dim=ds[dim])


def tide_cutoffs(
    ds, tides_lowres, tide_centre=0.0, resampling="bilinear", reproject=True
):
    """
    Based on the entire time-series of tide heights, compute the max
    and min satellite-observed tide height for each pixel, then
    calculate tide cutoffs used to restrict our data to satellite
    observations centred over mid-tide (0 m Above Mean Sea Level).

    These tide cutoffs are spatially interpolated into the extent of
    the input satellite imagery so they can be used to mask out low
    and high tide satellite pixels.

    Parameters:
    -----------
    ds : xarray.Dataset
        An `xarray.Dataset` containing a time series of water index
        data (e.g. MNDWI) for the provided datacube query. This is
        used to define the spatial extents into which tide height
        cutoffs will be interpolated.
    tides_lowres : xarray.Dataset
        A low-res `xarray.Dataset` containing tide heights for each
        timestep in `ds`, as produced by the `pixel_tides` function.
    tide_centre : float, optional
        The central tide height used to compute the min and max
        tide height cutoffs. Tide heights will be masked so all
        satellite observations are approximately centred over this
        value. The default is 0.0 which represents 0 m Above Mean
        Sea Level.
    resampling : string, optional
        The resampling method used when reprojecting low resolution
        tides to higher resolution. Defaults to "bilinear".

    Returns:
    --------
    tide_cutoff_min, tide_cutoff_max : xarray.DataArray
        2D arrays containing tide height cutoff values interpolated
        into the extent of `ds`.
    """

    # Calculate min and max tides
    tide_min = tides_lowres.min(dim="time")
    tide_max = tides_lowres.max(dim="time")

    # Identify cutoffs
    tide_cutoff_buffer = (tide_max - tide_min) * 0.25
    tide_cutoff_min = tide_centre - tide_cutoff_buffer
    tide_cutoff_max = tide_centre + tide_cutoff_buffer

    # Reproject into original geobox
    if reproject:
        tide_cutoff_min = tide_cutoff_min.odc.reproject(
            ds.odc.geobox, resampling=resampling
        )
        tide_cutoff_max = tide_cutoff_max.odc.reproject(
            ds.odc.geobox, resampling=resampling
        )

    return tide_cutoff_min, tide_cutoff_max


def wms_fields(gdf):
    """
    Calculates several addition fields required
    for the WMS/TerriaJS Coastlines visualisation.

    Parameters:
    -----------
    gdf : geopandas.GeoDataFrame
        The input rate of change points vector dataset.

    Returns:
    --------
    A `pandas.DataFrame` containing additional fields
    with a "wms_*" prefix.
    """

    wms_fields = pd.DataFrame(
        dict(
            wms_abs=gdf.rate_time.abs(),
            wms_conf=gdf.se_time * 1.96,
            wms_grew=gdf.rate_time < 0,
            wms_retr=gdf.rate_time > 0,
            wms_sig=gdf.sig_time <= 0.01,
            wms_good=gdf.certainty == "good",
        )
    )

    return wms_fields


def extract_contours(
    dataset: Dataset, z_values: float = 0.0, min_vertices: int = 15
) -> GeoDataFrame:
    contour_arrays = {}
    for i, da in dataset.groupby("year"):
        contours = subpixel_contours(
            da=da.combined.squeeze(),
            z_values=z_values,
            crs=dataset.geobox.crs,
            min_vertices=min_vertices,
            verbose=False,
        )
        contour_arrays[i] = contours

    contour_gdf = gpd.GeoDataFrame(
        data={"year": list(contour_arrays.keys())},
        geometry=pd.concat(contour_arrays.values(), ignore_index=True).geometry,
    )

    contour_gdf = contour_gdf.set_index("year")

    return contour_gdf


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
    default=5000.0,
    help="The distance (in CRS) to buffer the study area grid cell "
    "extent. This buffer is important for ensuring that generated "
    "rasters overlap along the boundaries of neighbouring study areas "
    "so that we can extract seamless vector shorelines. Defaults to "
    "5,000, so normaly 5 km.",
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
    default=0.0,
    help="The water index threshold used to extract "
    "subpixel precision shorelines. Defaults to 0.00.",
)
click_output_location = click.option(
    "--output-location",
    type=str,
    default=None,
)
click_output_version = click.option(
    "--output-version",
    type=str,
    required=True,
    help="A unique string proving a name that will be used "
    "for output directories and files. This can be "
    "used to version different analysis outputs.",
)
