#!/usr/bin/env python3

import os

from setuptools import find_packages, setup

# Where are we?
IS_SANDBOX = "sandbox" in os.getenv("JUPYTER_IMAGE", default="")

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
    "botocore",
    "click",
    "datacube[s3,performance]",
    "dea_tools>=0.3.0",
    "Fiona",
    "geopandas",
    "matplotlib",
    "numpy",
    "odc-geo",
    "odc_ui",
    "pandas",
    "pyarrow",
    "pyproj",
    "pyTMD",
    "python_geohash",
    "pytz",
    "PyYAML",
    "rasterio",
    "rioxarray",
    "s3path",
    "scikit_image",
    "scikit_learn",
    "scipy",
    "setuptools",
    "Shapely",
    "tqdm",
    "xarray",
]

# Package metadata
NAME = "dea_coastlines"
DESCRIPTION = "Tools for running Digital Earth Australia Coastlines"
URL = "https://github.com/GeoscienceAustralia/dea-coastlines"
EMAIL = "Robbi.BishopTaylor@ga.gov.au"
AUTHOR = "Robbi Bishop-Taylor"
REQUIRES_PYTHON = ">=3.8.0"

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
    "install_requires": REQUIRED if not IS_SANDBOX else [],
    "tests_require": tests_require,
    "extras_require": extras,
    "packages": find_packages(),
    "include_package_data": True,
    "license": "Apache License 2.0",
    "entry_points": {
        "console_scripts": [
            "deacoastlines-raster = coastlines.raster:generate_rasters_cli",
            "deacoastlines-vector = coastlines.vector:generate_vectors_cli",
            "deacoastlines-continental = coastlines.continental:continental_cli",
            "coastlines-print-tiles = coastlines.print_tiles:cli",
            "coastlines-combined = coastlines.combined:cli",
            "coastlines-merge = coastlines.merge_tiles:cli",
        ]
    },
}

setup(**setup_kwargs)
