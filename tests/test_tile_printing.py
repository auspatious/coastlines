from coastlines.print_tiles import read_tiles_list_string
from coastlines.utils import load_json
import pytest
import os

from pathlib import Path

THIS_FOLDER = Path(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = THIS_FOLDER.parent
LOCAL_TILES_FILE = PROJECT_ROOT / "data/raw/vietnam_tiles.geojson"
REMOTE_TILES_FILE = "https://s3.ap-southeast-2.amazonaws.com/files.auspatious.com/coastlines/vietnam_tiles.geojson.com"


@pytest.fixture(params=[LOCAL_TILES_FILE, REMOTE_TILES_FILE])
def file(request):
    return request.param


def test_read_file_as_json(file):
    tiles = load_json(file)
    assert len(tiles) == 256


@pytest.mark.parametrize(
    "in_string,expected",
    [
        ("[]", []),
        ('["1,1"]', ["1,1"]),
        ('["1,1", "2,2"]', ["1,1", "2,2"]),
        ('["abc", "def", "ghi"]', ["abc", "def", "ghi"]),
    ],
)
def test_parse_string(in_string, expected):
    tiles_list = read_tiles_list_string(in_string)
    assert len(tiles_list) == len(expected)


@pytest.mark.parametrize(
    "subset,len_expected",
    [('["31,15"]', 1), ('["31,15", "29,16"]', 2), ("[]", 256)],
)
def test_subsetting(subset, len_expected):
    tiles = load_json(LOCAL_TILES_FILE)
    tiles_list = read_tiles_list_string(subset)
    if len(tiles_list) != 0:
        tiles = tiles.loc[tiles_list]
    assert len(tiles) == len_expected
