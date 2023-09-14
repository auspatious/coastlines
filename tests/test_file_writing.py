from pathlib import Path

import boto3
import pytest
from moto import mock_s3
import geopandas as gpd

from coastlines.combined import export_results

PROJECT_ROOT = Path(__file__).parent.parent


# mocking S3 isn't working... "s3://example-bucket/example-path"
@pytest.fixture(params=["/tmp/example-path"])
def output_path(request):
    return Path(request.param)


@pytest.fixture()
def gpd_file():
    return gpd.GeoDataFrame.from_file(PROJECT_ROOT / "data/raw/vietnam_tiles.geojson")


@mock_s3
@pytest.mark.xfail(reason="Moto doesn't support s3fs")
def test_export_results(output_path, gpd_file):
    if str(output_path).startswith("s3:/"):
        bucket_name = output_path.parts[1]
        conn = boto3.resource("s3", region_name="us-east-1")
        conn.create_bucket(Bucket=bucket_name)

    export_results(gpd_file, gpd_file, "0.0.0", output_path, "1,2")
