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

    # Text-to-SQL agent configuration.
    # The agent queries a separate "gold" dataset (curated for analytics),
    # not the same warehouse the existing dashboard reads from.
    agent_enabled: bool = True
    agent_dataset: str = "neighborhood_livability_gold"
    llm_project: str = ""
    llm_location: str = "us-central1"
    llm_model: str = "gemini-2.5-flash"
    llm_max_bytes_billed: int = 5 * 1024 * 1024 * 1024  # 5 GB
    llm_row_limit: int = 1000
    llm_timeout_seconds: int = 30

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
            agent_enabled=_env_bool("DASH_AGENT_ENABLED", default=True),
            agent_dataset=os.getenv("DASH_AGENT_DATASET", "neighborhood_livability_gold").strip(),
            llm_project=os.getenv("DASH_LLM_PROJECT", bq_project).strip(),
            llm_location=os.getenv("DASH_LLM_LOCATION", "us-central1").strip(),
            llm_model=os.getenv("DASH_LLM_MODEL", "gemini-2.5-flash").strip(),
            llm_max_bytes_billed=int(
                os.getenv("DASH_LLM_MAX_BYTES_BILLED", str(5 * 1024 * 1024 * 1024))
            ),
            llm_row_limit=int(os.getenv("DASH_LLM_ROW_LIMIT", "1000")),
            llm_timeout_seconds=int(os.getenv("DASH_LLM_TIMEOUT_SECONDS", "30")),
        )

    def table_id(self, table_name: str) -> str:
        return f"`{self.bq_project}.{self.bq_dataset}.{table_name}`"

    def agent_dataset_id(self) -> str:
        return f"`{self.bq_project}.{self.agent_dataset}`"

    def agent_table_id(self, table_name: str) -> str:
        return f"`{self.bq_project}.{self.agent_dataset}.{table_name}`"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
