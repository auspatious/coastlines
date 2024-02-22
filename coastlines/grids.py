from odc.geo.gridspec import GridSpec
from odc.geo import XY

# Vietnam-related grids
vietnam_epsg = "EPSG:3405"

VIETNAM_25 = GridSpec(
    crs=vietnam_epsg,
    tile_shape=(2000, 2000),
    resolution=25,
    origin=XY(0, 0)
)

VIETNAM_10 = GridSpec(
    crs=vietnam_epsg,
    tile_shape=(5000, 5000),
    resolution=10,
    origin=XY(0, 0)
)
