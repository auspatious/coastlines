import sys
import json
import click

from coastlines.utils import load_json
from typing import Optional


def read_tiles_subset_string(tiles_subset: str) -> list:
    return json.loads(tiles_subset)


@click.command("print-tiles")
@click.option("--tiles-file", type=str)
@click.option("--tiles-subset", type=str, default="[]")
@click.option("--limit", type=int, default=None, required=False)
def cli(tiles_file: str, tiles_subset: str, limit: Optional[int]) -> None:
    tiles = load_json(tiles_file)

    tiles_subset = read_tiles_subset_string(tiles_subset)
    if len(tiles_subset) == 0:
        pass
    else:
        tiles = tiles.loc[tiles_subset]

    if limit is not None:
        tiles = tiles[:limit]

    list_to_dump = tiles.index.tolist()

    json.dump(list_to_dump, sys.stdout)
