#!/usr/bin/env python3

from setuptools import find_packages, setup

tests_require = [
    "pytest",
    "pytest-dependency",
    "pytest-cov",
]

extras = {
    "test": tests_require,
}

# What packages are required for this module to be executed?
REQUIRED = [
    "aiohttp",
    "affine",
    "bokeh",
    "botocore",
    "click",
    "datacube[s3,performance]",
    "dea-tools",
    "eo-tides",
    "Fiona",
    "geopandas",
    "matplotlib",
    "mapclassify",
    "numpy",
    "odc-geo",
    "odc-stac",
    "odc_ui",
    "pandas",
    "pyarrow",
    "pydantic",
    "pyproj",
    "pyTMD>=2.1.4",
    "python_geohash",
    "pytz",
    "PyYAML",
    "rasterio>=1.4.3",
    "rioxarray",
    "s3path",
    "scikit_image",
    "scikit_learn",
    "scipy",
    "setuptools",
    "Shapely",
    "tqdm>=4.66.3",
    "xarray",
    "pyyaml",
    "dea_intertidal @ git+https://github.com/GeoscienceAustralia/dea-intertidal.git",
    "dep-tools @ git+https://github.com/digitalearthpacific/dep-tools.git",
]

# Package metadata
NAME = "coastlines"
DESCRIPTION = "Tools for running Coastlines"
URL = "https://github.com/auspatious/coastlines"
EMAIL = "alex@auspatious.com"
AUTHOR = "Alex Leith"
REQUIRES_PYTHON = ">=3.10.0"

# Setup kwargs
setup_kwargs = {
    "name": NAME,
    "description": DESCRIPTION,
    "long_description": DESCRIPTION,
    "long_description_content_type": "text/markdown",
    "author": AUTHOR,
    "author_email": EMAIL,
    "python_requires": REQUIRES_PYTHON,
    "url": URL,
    "install_requires": REQUIRED,
    "tests_require": tests_require,
    "extras_require": extras,
    "packages": find_packages(),
    "include_package_data": True,
    "license": "Apache License 2.0",
    "entry_points": {
        "console_scripts": [
            "coastlines-print-tiles = coastlines.print_tiles:cli",
            "coastlines-combined = coastlines.combined:cli",
            "coastlines-merge = coastlines.merge_tiles:cli",
            "intertidal = coastlines.intertidal:cli",
        ]
    },
}

setup(**setup_kwargs)
