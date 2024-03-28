from odc.geo import XY
from odc.geo.gridspec import GridSpec

# numbers
TEN_MILLION = 10_000_000

# Vietnam-related grids
VIETNAM_CRS = "EPSG:3405"

VIETNAM_25 = GridSpec(
    crs=VIETNAM_CRS, tile_shape=(2000, 2000), resolution=25, origin=XY(0, 0)
)

VIETNAM_10 = GridSpec(
    crs=VIETNAM_CRS, tile_shape=(5000, 5000), resolution=10, origin=XY(0, 0)
)

# Philippines-related grids

PHILIPPINES_CRS = "EPSG:32651"

PHILIPPINES_25 = GridSpec(
    crs=PHILIPPINES_CRS,
    tile_shape=(2000, 2000),
    resolution=25,
    origin=XY(-TEN_MILLION, -TEN_MILLION),
)

PHILIPPINES_10 = GridSpec(
    crs=PHILIPPINES_CRS,
    tile_shape=(5000, 5000),
    resolution=10,
    origin=XY(-TEN_MILLION, -TEN_MILLION),
)
