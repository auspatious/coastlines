from odc.geo import XY
from odc.geo.gridspec import GridSpec

# numbers
ONE_TWO_FIVE_MILLION = 1_250_000
SIX_TWO_FIVE_MILLION = 6_250_000
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

# Indonesion-related grids

INDONESIA_CRS = "EPSG:3832"

INDONESIA_25 = GridSpec(
    crs=INDONESIA_CRS,
    tile_shape=(2000, 2000),
    resolution=25,
    origin=XY(-SIX_TWO_FIVE_MILLION, -ONE_TWO_FIVE_MILLION),
)

INDONESIA_10 = GridSpec(
    crs=INDONESIA_CRS,
    tile_shape=(5000, 5000),
    resolution=10,
    origin=XY(-SIX_TWO_FIVE_MILLION, -ONE_TWO_FIVE_MILLION),
)