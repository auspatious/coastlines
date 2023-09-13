FROM ghcr.io/osgeo/gdal:ubuntu-small-3.7.1

ENV CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update \
    && apt-get install -y \
    # Build tools
    build-essential \
    git \
    python3-pip \
    # Convenience
    wget nano \
    # For Psycopg2
    libpq-dev python3-dev \
    # For SSL
    ca-certificates \
    # for pg_isready
    postgresql-client \
    # Tidy up
    && apt-get autoclean && \
    apt-get autoremove && \
    rm -rf /var/lib/{apt,dpkg,cache,log}


# Environment can be whatever is supported by setup.py
# so, either deployment, test
ARG ENVIRONMENT=deployment

RUN echo "Environment is: $ENVIRONMENT"

COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    --no-binary rasterio \
    # --no-binary shapely \
    --no-binary fiona

# Set up a nice workdir and add the live code
ENV APPDIR=/code
RUN mkdir -p $APPDIR
WORKDIR $APPDIR
ADD . $APPDIR

RUN if [ "$ENVIRONMENT" = "deployment" ] ; then\
        pip install .[$ENVIRONMENT] ; \
    else \
        pip install --editable .[$ENVIRONMENT] ; \
    fi


CMD ["python", "--version"]

RUN  coastlines-combined --help && \
     coastlines-print-tiles --help
