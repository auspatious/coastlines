from pydantic import BaseModel


class Input(BaseModel):
    grid_path: str
    modifications_path: str
    geomorphology_path: str = "data/raw/empty_modifications.geojson"


class Output(BaseModel):
    location: str
    crs: str = "EPSG:4326"


class STAC(BaseModel):
    stac_api_url: str
    stac_collections: list[str]
    lower_scene_limit: int
    upper_scene_limit: int


class AWS(BaseModel):
    aws_request_payer: bool = False
    aws_unsigned: bool = False


class Options(BaseModel):
    start_year: int = 1985
    end_year: int = 2023
    baseline_year: int = 2021

    tide_model: str = "FES2014"

    water_index: str = "mndwi"
    index_threshold: float = 0.0
    include_nir: bool = True

    mask_with_hillshade: bool = True
    mask_with_esa_wc: bool = True
    use_combined_index: bool = False

    tide_centre: float = 0.0
    load_buffer_distance: int = 5000

class HillShade(BaseModel):
    stac_catalog: str | None = None
    stac_collection: str | None = None

class CoastlinesConfig(BaseModel):
    input: Input
    output: Output
    options: Options

    hillshade: HillShade | None = None
    virtual_product: bool | None = None
    stac: STAC | None = None
    aws: AWS | None = None
