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
    print(f"Loading tiles from {tiles_file}")
    tiles = load_json(tiles_file)

    tiles_subset = read_tiles_subset_string(tiles_subset)
    if len(tiles_subset) == 0:
        print("No tiles_subset provided, using all tiles from the file.")
    else:
        print(f"Found {len(tiles_subset)} tiles in the list. Using these tiles only.")
        tiles = tiles.loc[tiles_subset]

    if limit is not None:
        tiles = tiles[:limit]
        print(f"Limiting to {limit} tiles leaves {len(tiles)} tiles to process.")

    list_to_dump = tiles.index.tolist()

    json.dump(list_to_dump, sys.stdout)
