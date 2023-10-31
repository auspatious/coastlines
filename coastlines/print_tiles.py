import sys
import json
import click

from coastlines.utils import load_json, load_config
from typing import Optional


def read_tiles_subset_string(tiles_subset: str) -> list:
    return json.loads(tiles_subset)


@click.command("print-tiles")
@click.option("--config-file", type=str)
@click.option("--tiles-subset", type=str, default="[]")
@click.option("--limit", type=int, default=None, required=False)
def cli(config_file: str, tiles_subset: str, limit: Optional[int]) -> None:
    config = load_config(config_file)
    print(f"Loaded config from {config_file}")
    tiles = load_json(config["Input files"]["grid_path"])

    tiles_subset = read_tiles_subset_string(tiles_subset)
    if len(tiles_subset) == 0:
        pass
    else:
        tiles = tiles.loc[tiles_subset]

    if limit is not None:
        tiles = tiles[:limit]

    list_to_dump = tiles.index.tolist()

    json.dump(list_to_dump, sys.stdout)


if __name__ == "__main__":
    cli()
