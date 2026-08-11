FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    dbt-core==1.12.0 \
    dbt-bigquery==1.12.0

COPY . .

RUN dbt deps

ENV DBT_PROFILES_DIR=/app

CMD ["dbt", "build", "--target", "prod"]