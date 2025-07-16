FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.0

ENV CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

# These settings prevent a timezone prompt when Python installs
# Use this article to find your time zone (TZ):
# https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
ENV TZ=Australia/Hobart \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa
    
RUN apt-get update \
    && apt-get install -y \
    # python 3.11
    python3.11 \
    python3.11-venv \
    # Build tools
    build-essential \
    git \
    python3-pip \
    # Convenience
    wget nano \
    # For Psycopg2
    libpq-dev python3.11-dev \
    # For SSL
    ca-certificates \
    # for pg_isready
    postgresql-client \
    # Tidy up
    && apt-get autoclean && \
    apt-get autoremove && \
    rm -rf /var/lib/{apt,dpkg,cache,log}

# Python virt environment
ENV VIRTUAL_ENV=/.envs/python311
RUN python3.11 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt /tmp/
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    --no-binary rasterio \
    --no-binary fiona

# Set up a nice workdir and add the live code
ENV APPDIR=/code
RUN mkdir -p $APPDIR
WORKDIR $APPDIR
ADD . $APPDIR

RUN python -m pip install .

CMD ["python", "--version"]

# Smoketest
RUN coastlines-combined --help
