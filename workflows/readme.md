# Notes for running Coastlines in Production

Interesting tiles:

* Islands: `["28,18", "35,23", "33,24", "33,23", "18,23"]`
* South-west: `["9,18", "9,19", "9,20", "10,18", "10,19"]`
* North-east `["13,44", "13,45", "13,46", "14,45", "14,46"]`
* Central: `["18,31", "18,32", "18,33", "18,34", "19,31"]`

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
            mv /tmp/tide_models_temp/tide_models_clipped ~/tide_models
```

After that, run with the below.

Note that your instance needs to be able to read from the `usgs-landsat`
bucket and should be able to write to the destination.

```bash
docker-compose run coastlines \
    coastlines-combined \
        --config-path=configs/vietnam_coastlines_config.yaml \
        --study-area="9,19" \
        --tide-data-location="/tmp/tide_models" \
        --output-version="0.5.0a1" \
        --output-location="data" \
        --start-year="1981" \
        --end-year="2022" \
        --baseline-year="2021" \
        --aws-request-payer \
        --no-aws-unsigned \
        --overwrite
```

And for writing to S3:

```bash
docker-compose run coastlines \
    coastlines-combined \
        --config-path=configs/vietnam_coastlines_config.yaml \
        --study-area="9,19" \
        --tide-data-location="/tmp/tide_models/" \
        --output-version="0.1.0" \
        --output-location="s3://files.auspatious.com/coastlines/data/" \
        --start-year="1985" \
        --end-year="2022" \
        --baseline-year="2021" \
        --aws-request-payer \
        --no-aws-unsigned \
        --no-overwrite
```

And to run a bunch of tiles...

```bash
for i in 28,18 35,23 33,24 33,23 18,23 9,18 9,19 9,20 10,18 10,19 13,44 13,45 13,46 14,45 14,46 18,31 18,32 18,33 18,34 19,31;
    docker-compose run coastlines \
        coastlines-combined \
            --config-path=configs/vietnam_coastlines_config.yaml \
            --study-area=$i \
            --tide-data-location="/tmp/tide_models/" \
            --output-version="0.1.2" \
            --output-location="s3://files.auspatious.com/coastlines/data/" \
            --start-year="1985" \
            --end-year="2022" \
            --baseline-year="2021" \
            --aws-request-payer \
            --no-aws-unsigned \
            --overwrite
; end
```

28,18 35,23 33,24 33,23 18,23 9,18 9,19 9,20 10,18 10,19 13,44 13,45 13,46 14,45 14,46 18,31 18,32 18,33 18,34 19,31 