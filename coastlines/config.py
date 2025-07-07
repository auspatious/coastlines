from pydantic import BaseModel


class CoastlinesInput(BaseModel):
    grid_path: str
    modifications_path: str
    geomorphology_path: str = "data/raw/empty_modifications.geojson"


class Output(BaseModel):
    location: str
    crs: str = "EPSG:4326"


class CoastlinesSTAC(BaseModel):
    stac_api_url: str
    stac_collections: list[str]
    lower_scene_limit: int
    upper_scene_limit: int


class AWS(BaseModel):
    aws_request_payer: bool = False
    aws_unsigned: bool = False


class CoastlinesOptions(BaseModel):
    start_year: int = 1985
    end_year: int = 2023
    baseline_year: int = 2021

    tide_model: str = "FES2014"

    water_index: str = "mndwi"
    index_threshold: float = 0.0
    include_nir: bool = True

    mask_with_hillshade: bool = True
    hillshade_stac_catalog: str | None = None
    hillshade_stac_collection: str | None = None

    use_ensemble: bool = True
    ensemble_model_list: list[str] | None = None
    ensemble_model_rankings: str | None = None

    mask_with_esa_wc: bool = True
    use_combined_index: bool = False

    tide_centre: float = 0.0
    load_buffer_distance: int = 5000


class CoastlinesConfig(BaseModel):
    input: CoastlinesInput
    output: Output
    options: CoastlinesOptions

    virtual_product: bool | None = None
    stac: CoastlinesSTAC | None = None
    aws: AWS | None = None


class IntertidalInput(BaseModel):
    grid_path: str
    organisation: str | None = None
    collection_url: str | None = None


class IntertidalSTAC(BaseModel):
    stac_api_url: str
    stac_collections: dict[str,str]


class IntertidalOptions(BaseModel):
    start_year: int = 2020
    end_year: int = 2022
    label_year: int = 2021

    tide_model: str = "FES2014"

    use_ensemble: bool = True
    ensemble_model_list: list[str] | None = None
    ensemble_model_rankings: str | None = None

    use_exposure_offsets: bool = True
    modelled_freq: str = "3h"

    use_https_href_links: bool = True

class IntertidalConfig(BaseModel):
    input: IntertidalInput
    output: Output
    options: IntertidalOptions

    stac: IntertidalSTAC | None = None
    aws: AWS | None = None