# Load MNDWI from STAC
from collections import Counter
from pathlib import Path

import orjson
from odc.algo import erase_bad, mask_cleanup, to_f32
from odc.stac import load, configure_s3_access
from pystac import Item


_ = configure_s3_access(requester_pays=True, cloud_defaults=True)


def http_to_s3_url(http_url):
    """Convert a USGS HTTP URL to an S3 URL"""
    s3_url = http_url.replace(
        "https://landsatlook.usgs.gov/data", "s3://usgs-landsat"
    ).rstrip(":1")
    return s3_url


def load_data(items):
    cfg = {
        "landsat-c2l2-sr": {
            "assets": {
                "*": {"data_type": "uint16", "nodata": 0},
            }
        },
        "*": {"warnings": "ignore"},
    }

    epsg_codes = Counter(item.properties["proj:epsg"] for item in items)
    most_common_epsg = epsg_codes.most_common(1)[0][0]

    ds = load(
        items,
        bands=["green", "swir16", "qa_pixel"],
        bbox=[
            104.53400728165435,
            8.543114824509242,
            105.05268961831959,
            9.095592649285232,
        ],
        datetime="2023-01",
        crs=most_common_epsg,
        resolution=30,
        stac_cfg=cfg,
        chunks={"x": 2000, "y": 2000, "time": 1},
        group_by="solar_day",
        patch_url=http_to_s3_url,
        fail_on_error=False,
    )

    # Get the nodata mask
    nodata_mask = ds.green == 0

    # Get cloud and cloud shadow mask
    mask_bitfields = [1, 2, 3, 4]  # dilated cloud, cirrus, cloud, cloud shadow
    bitmask = 0
    for field in mask_bitfields:
        bitmask |= 1 << field

    # Get cloud mask
    cloud_mask = ds["qa_pixel"].astype(int) & bitmask != 0
    # Expand and contract the mask to clean it up
    dilated_mask = mask_cleanup(cloud_mask, [("opening", 2), ("dilation", 3)])

    final_mask = nodata_mask | dilated_mask

    ds = erase_bad(ds, final_mask)

    del ds["qa_pixel"]

    return ds


def mndwi_scaled(ds):
    # Convert to float and scale data to 0-1
    ds["green"] = to_f32(ds["green"], scale=0.0000275, offset=-0.2)
    ds["swir16"] = to_f32(ds["swir16"], scale=0.0000275, offset=-0.2)

    # Remove values outside the valid range (0-1)
    ds["green"] = ds["green"].where((ds["green"] >= 0) & (ds["green"] <= 1))
    ds["swir16"] = ds["swir16"].where((ds["swir16"] >= 0) & (ds["swir16"] <= 1))

    ds["mndwi"] = (ds["green"] - ds["swir16"]) / (ds["green"] + ds["swir16"])

    del ds["green"]
    del ds["swir16"]

    return ds


def mndwi_unscaled(ds):
    # Convert to float
    ds["green"] = to_f32(ds["green"])
    ds["swir16"] = to_f32(ds["swir16"])
    ds["mndwi"] = (ds["green"] - ds["swir16"]) / (ds["green"] + ds["swir16"])

    del ds["green"]
    del ds["swir16"]

    return ds


# If main, run the code
if __name__ == "__main__":
    this_dir = Path(__file__).parent.absolute()
    items_dict = orjson.loads(open(this_dir / "mem-items.json", "rb").read())
    items = [Item.from_dict(d) for d in items_dict]

    RUN_MNDWI_SCALED = False

    if RUN_MNDWI_SCALED:
        ds = load_data(items)
        ds = mndwi_scaled(ds)
        ds_median = ds.median(dim="time").compute()
        ds_median.to_zarr("mndwi_scaled.zarr")
    else:
        ds = load_data(items)
        ds = mndwi_unscaled(ds)
        ds_median = ds.median(dim="time").compute()
        ds_median.to_zarr("mndwi_unscaled.zarr")

    print(ds_median)
