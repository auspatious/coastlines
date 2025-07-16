# Coastlines

## Important note

This repository is a hard fork from the Digital Earth Australia Coastlines codebase.
Most code and all the innovation comes from there. Adaptations have been made to support
processing using global open data and this tool should be able to be used anywhere
in the world with only minor modifications. But this is not as well validated or
tested as the Australian work.

Please refer to [DEA Coastlines](https://github.com/GeoscienceAustralia/dea-coastlines)
and [DE Africa Coastlines](https://github.com/digitalearthafrica/dea-coastlines) for
other implementations.

## Repository details

**License:** The code in this repository is licensed under the [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0). Digital Earth Australia data is licensed under the [Creative Commons by Attribution 4.0 license](https://creativecommons.org/licenses/by/4.0/).

**Coastlines** is a global data process that includes annual shorelines and rates of
coastal change that can be run on Landsat data from 5 through to 8 and 9.

The product combines satellite data with tidal modelling to map the typical location of the coastline at mean sea level for each year. The product enables trends of coastal erosion and accretion to be examined annually at both a local and continental scale, and for patterns of coastal change to be mapped historically and updated regularly as data continues to be acquired. This allows current rates of coastal change to be compared with that observed in previous years or decades.

**Intertidal** is a global data process that produces an annual digital elevation model (DEM) of the 
coastline intertidal area (the area between the low and high tides) using both Landsat and Sentinel-2 data.

## Repository code

Code in this repository works using [odc-stac](https://github.com/opendatacube/odc-stac)
to find and load data from STAC APIs from the USGS and the Microsoft Planetary Computer.

### Getting started

Clone the `coastlines` repository and checkout the `main` branch:

``` bash
git clone https://github.com/auspatious/coastlines.git
```

#### Tidal model

Coastlines uses pyTMD compatiable tide models to account for the influence of tide
on shoreline positions. To install tidal models, follow the
[Setting up tidal models for DEA Coastlines guide on the Wiki](https://github.com/GeoscienceAustralia/dea-coastlines/wiki/Setting-up-tidal-models-for-DEA-Coastlines).

### Running a Coastlines analysis using the command-line interface (CLI)

There are three commands that can be used, as follows:

* `print-tiles` will take a config file, a config type and an optional subset, and will echo all the tile-ids to the output. This is used to create a list of work that needs to be done.
* `coastlines-combined` runs the full Coastlines process, from setting up raster data and cleaning through to contour extraction.
* `coastlines-merge` will merge results from the tile-based processing into a single combined file.

### Running a Intertidal analysis using the command-line interface (CLI)

There are two commands that can be used, as follows:

* `print-tiles` will take a config file, a config type and an optional subset, and will echo all the tile-ids to the output. This is used to create a list of work that needs to be done.
* `intertidal` runs the full Intertidal process, from setting up raster data and cleaning through to the creation of the DEM.


## Credits

Original implementation from Digital Earth Australia.

Tidal modelling is provided by the [FES2022 global tidal model](https://www.aviso.altimetry.fr/en/data/products/auxiliary-products/global-tide-fes.html), implemented using the pyTMD Python package. FES2022 was produced by NOVELTIS, LEGOS, CLS Space Oceanography Division and CNES. It is distributed by AVISO, with support from CNES [http://www.aviso.altimetry.fr/](http://www.aviso.altimetry.fr/).

## References

> Bishop-Taylor, R., Nanson, R., Sagar, S., Lymburner, L. (2021). Mapping Australia's dynamic coastline at mean sea level using three decades of Landsat imagery. _Remote Sensing of Environment_, 267, 112734. Available: [https://doi.org/10.1016/j.rse.2021.112734](https://doi.org/10.1016/j.rse.2021.112734)

> Bishop-Taylor, R., Sagar, S., Lymburner, L., & Beaman, R. J. (2019a). Between the tides: Modelling the elevation of Australia's exposed intertidal zone at continental scale. _Estuarine, Coastal and Shelf Science_, 223, 115-128. Available: [https://doi.org/10.1016/j.ecss.2019.03.006](https://doi.org/10.1016/j.ecss.2019.03.006)

> Bishop-Taylor, R., Sagar, S., Lymburner, L., Alam, I., & Sixsmith, J. (2019b). Sub-pixel waterline extraction: Characterising accuracy and sensitivity to indices and spectra. _Remote Sensing_, 11(24), 2984. Available: [https://doi.org/10.3390/rs11242984](https://doi.org/10.3390/rs11242984)

> Nanson, R., Bishop-Taylor, R., Sagar, S., Lymburner, L., (2022). Geomorphic insights into Australia's coastal change using a national dataset derived from the multi-decadal Landsat archive. _Estuarine, Coastal and Shelf Science_, 265, p.107712. Available: [https://doi.org/10.1016/j.ecss.2021.107712](https://doi.org/10.1016/j.ecss.2021.107712)
