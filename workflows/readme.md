# Notes for running Coastlines in Production

## Argo workflow

Example workflow is included... notes tba.

## Docker Manually

Run with Docker Compose as below.

First, bootstrap your machine with Docker and Docker Compose. Next,
download the tide model data.

```bash
wget https://s3.ap-southeast-2.amazonaws.com/files.auspatious.com/coastlines/vietnam_tide_models.zip \
              -O /tmp/vietnam_tide_models.zip && \
            unzip /tmp/vietnam_tide_models.zip -d /tmp/tide_models_temp && \
            mv /tmp/tide_models_temp/tide_models_clipped /tmp/tide_models
```

After that, run with the below.

Note that your instance needs to be able to read from the `usgs-landsat`
bucket and should be able to write to the destination.

```bash
docker-compose run coastlines \
    coastlines-combined \
        --config-path=configs/vietnam_coastlines_config.yaml \
        --study-area="13,45" \
        --tide-data-location="/tmp/tide_models" \
        --output-version="0.0.0" \
        --output-location="data" \
        --start-year="2020" \
        --end-year="2022" \
        --baseline-year="2021" \
        --aws-request-payer \
        --no-aws-unsigned \
        --overwrite
```
