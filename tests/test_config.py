from coastlines.config import CoastlinesConfig
from pydantic_yaml import parse_yaml_file_as


TEST_CONFIG_FILE = "tests/test_config.yaml"
USGS_STAC_API_URL = "https://landsatlook.usgs.gov/stac-server"


def test_load_config_file():
    config = parse_yaml_file_as(CoastlinesConfig, TEST_CONFIG_FILE)

    assert config.stac.stac_api_url == USGS_STAC_API_URL

    assert config.aws is None

    assert config.options.use_combined_index is True
