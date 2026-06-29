from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration sourced from environment variables."""

    bq_project: str
    bq_dataset: str = "neighborhood_livability_data"
    bq_location: str = "US"
    table_311: str = "311_incidents"
    table_police: str = "police_incidents"
    table_trip_stops: str = "trip_stops"
    table_stops: str = "stops"
    table_neighborhoods: str = "neighborhoods"
    table_police_districts: str = "police_districts"
    cache_ttl_seconds: int = 300
    cache_max_entries: int = 512

    @classmethod
    def from_env(cls) -> "AppConfig":
        bq_project = os.getenv("DASH_BQ_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", "")).strip()
        if not bq_project:
            raise ValueError("Missing DASH_BQ_PROJECT (or GOOGLE_CLOUD_PROJECT).")

        return cls(
            bq_project=bq_project,
            bq_dataset=os.getenv("DASH_BQ_DATASET", "neighborhood_livability_data").strip(),
            bq_location=os.getenv("DASH_BQ_LOCATION", "US").strip(),
            table_311=os.getenv("DASH_BQ_TABLE_311", "311_incidents").strip(),
            table_police=os.getenv("DASH_BQ_TABLE_POLICE", "police_incidents").strip(),
            table_trip_stops=os.getenv("DASH_BQ_TABLE_TRIP_STOPS", "trip_stops").strip(),
            table_stops=os.getenv("DASH_BQ_TABLE_STOPS", "stops").strip(),
            table_neighborhoods=os.getenv("DASH_BQ_TABLE_NEIGHBORHOODS", "neighborhoods").strip(),
            table_police_districts=os.getenv(
                "DASH_BQ_TABLE_POLICE_DISTRICTS", "police_districts"
            ).strip(),
            cache_ttl_seconds=int(os.getenv("DASH_CACHE_TTL_SECONDS", "300")),
            cache_max_entries=int(os.getenv("DASH_CACHE_MAX_ENTRIES", "512")),
        )

    def table_id(self, table_name: str) -> str:
        return f"`{self.bq_project}.{self.bq_dataset}.{table_name}`"
