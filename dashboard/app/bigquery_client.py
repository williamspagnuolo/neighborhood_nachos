from __future__ import annotations

from google.cloud import bigquery

from .config import AppConfig


def create_client(config: AppConfig) -> bigquery.Client:
    return bigquery.Client(project=config.bq_project, location=config.bq_location)
