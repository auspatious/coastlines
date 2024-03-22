import json
import sys
from json.decoder import JSONDecodeError
from typing import Optional

import click

from coastlines.utils import load_config, load_json


def read_tiles_subset_string(tiles_subset: str) -> list:
    return json.loads(tiles_subset)


@click.command("print-tiles")
@click.option("--config-file", type=str)
@click.option("--tiles-subset", type=str, default="[]")
@click.option("--limit", type=int, default=None, required=False)
def cli(config_file: str, tiles_subset: str, limit: Optional[int]) -> None:
    config = load_config(config_file)
    tiles = load_json(config.input.grid_path)

    try:
        subset_list = read_tiles_subset_string(tiles_subset)
    except JSONDecodeError:
        print(f"Tiles subset '{tiles_subset}' is not a valid JSON string")
        sys.exit(1)

    if len(subset_list) != 0:
        try:
            tiles = tiles.loc[subset_list]
        except KeyError:
            print("One or more tile keys was not found in the grid file")
            sys.exit(1)

    if limit is not None:
        tiles = tiles[:limit]

    list_to_dump = tiles.index.tolist()

    json.dump(list_to_dump, sys.stdout)


if __name__ == "__main__":
    cli()
