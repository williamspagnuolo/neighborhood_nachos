from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Literal

from google.cloud import bigquery

from .config import AppConfig

BoundaryMode = Literal["neighborhoods", "police_districts"]

MODE_TO_BOUNDARY_COLUMN: dict[BoundaryMode, str] = {
    "neighborhoods": "neighborhood_id",
    "police_districts": "police_district_id",
}

LOGGER = logging.getLogger(__name__)


class DashboardQueries:
    def __init__(self, client: bigquery.Client, config: AppConfig) -> None:
        self.client = client
        self.config = config

    def fetch_time_bounds_utc(self) -> tuple[dt.datetime, dt.datetime] | None:
        sql = f"""
                WITH metrics_bounds AS (
                    SELECT
                        MIN(IF(event_type = '311', event_ts_utc, NULL)) AS min_311,
                        MAX(IF(event_type = '311', event_ts_utc, NULL)) AS max_311,

                        MIN(IF(event_type = 'police', event_ts_utc, NULL)) AS min_police,
                        MAX(IF(event_type = 'police', event_ts_utc, NULL)) AS max_police,

                        MIN(IF(event_type = 'transit', event_ts_utc, NULL)) AS min_transit,
                        MAX(IF(event_type = 'transit', event_ts_utc, NULL)) AS max_transit

                    FROM {self.config.table_id(self.config.table_dashboard_events)}
                )

                SELECT
                    LEAST(min_311, min_police, min_transit) AS global_min_ts,
                    GREATEST(max_311, max_police, max_transit) AS global_max_ts,

                    GREATEST(min_311, min_police, min_transit) AS overlap_min_ts,
                    LEAST(max_311, max_police, max_transit) AS overlap_max_ts

                FROM metrics_bounds
                """
        rows = self._run_query(sql=sql, params=[])

        if not rows:
            return None

        row = rows[0]

        global_min_ts = row.get("global_min_ts")
        global_max_ts = row.get("global_max_ts")
        overlap_min_ts = row.get("overlap_min_ts")
        overlap_max_ts = row.get("overlap_max_ts")

        if (
            overlap_min_ts is not None
            and overlap_max_ts is not None
            and overlap_min_ts <= overlap_max_ts
        ):
            return overlap_min_ts, overlap_max_ts

        if global_min_ts is None or global_max_ts is None:
            return None

        return global_min_ts, global_max_ts


    def fetch_boundary_features(self, mode: BoundaryMode) -> list[dict[str, Any]]:
        sql = f"""
                SELECT
                    boundary_id AS id,
                    boundary_name AS name,
                    ST_ASGEOJSON(geometry) AS geojson

                FROM {self.config.table_id(self.config.table_boundaries)}

                WHERE boundary_type = @boundary_type
                AND geometry IS NOT NULL

                ORDER BY boundary_name
                """
        return self._run_query(
            sql=sql,
            params=[
                bigquery.ScalarQueryParameter(
                    "boundary_type",
                    "STRING",
                    mode,
                )
            ],
        )
    def fetch_boundary_metrics(
        self,
        mode: BoundaryMode,
        boundary_id: str,
        start_utc: dt.datetime,
        end_utc: dt.datetime,
        local_start_time: dt.time,
        local_end_time: dt.time,
    ) -> dict[str, Any]:
        boundary_column = MODE_TO_BOUNDARY_COLUMN[mode]

        metrics = {
            "totals": self._fetch_totals(
                boundary_column=boundary_column,
                boundary_id=boundary_id,
                start_utc=start_utc,
                end_utc=end_utc,
                local_start_time=local_start_time,
                local_end_time=local_end_time,
            ),
            "hist_311": self._fetch_311_histogram(
                boundary_column=boundary_column,
                boundary_id=boundary_id,
                start_utc=start_utc,
                end_utc=end_utc,
                local_start_time=local_start_time,
                local_end_time=local_end_time,
            ),
            "hist_police": self._fetch_police_histogram(
                boundary_column=boundary_column,
                boundary_id=boundary_id,
                start_utc=start_utc,
                end_utc=end_utc,
                local_start_time=local_start_time,
                local_end_time=local_end_time,
            ),
        }
        return metrics

    def _fetch_totals(
        self,
        boundary_column: str,
        boundary_id: str,
        start_utc: dt.datetime,
        end_utc: dt.datetime,
        local_start_time: dt.time,
        local_end_time: dt.time,
    ) -> dict[str, Any]:

        sql = f"""
                SELECT
                    COUNTIF(event_type = '311') AS incidents_311_total,

                    COUNTIF(event_type = 'police') AS police_total,

                    COUNTIF(event_type = 'transit') AS transit_arrivals_total,

                    APPROX_QUANTILES(
                        CASE
                            WHEN event_type = 'transit'
                            THEN arrival_delay_sec
                        END,
                        100 IGNORE NULLS
                    )[OFFSET(50)] AS transit_median_delay_sec,

                    SAFE_DIVIDE(
                        COUNTIF(
                            event_type = 'transit'
                            AND arrival_delay_sec > 300
                        ),
                        COUNTIF(
                            event_type = 'transit'
                            AND arrival_delay_sec IS NOT NULL
                        )
                    ) * 100 AS transit_pct_delay_over_300_sec

                FROM {self.config.table_id(self.config.table_dashboard_events)}

                WHERE {boundary_column} = @boundary_id
                AND event_ts_utc >= @start_utc
                AND event_ts_utc < @end_utc
                AND event_time_pacific >= @local_start_time
                AND event_time_pacific <= @local_end_time
    """

        rows = self._run_query(
            sql=sql,
            params=[
                bigquery.ScalarQueryParameter(
                    "boundary_id",
                    "STRING",
                    boundary_id,
                ),
                bigquery.ScalarQueryParameter(
                    "start_utc",
                    "TIMESTAMP",
                    start_utc,
                ),
                bigquery.ScalarQueryParameter(
                    "end_utc",
                    "TIMESTAMP",
                    end_utc,
                ),
                bigquery.ScalarQueryParameter(
                    "local_start_time",
                    "TIME",
                    local_start_time,
                ),
                bigquery.ScalarQueryParameter(
                    "local_end_time",
                    "TIME",
                    local_end_time,
                ),
            ],
        )

        if not rows:
            return {
                "incidents_311_total": 0,
                "police_total": 0,
                "transit_arrivals_total": 0,
                "transit_median_delay_sec": None,
                "transit_pct_delay_over_300_sec": None,
            }

        return rows[0]

    def _fetch_311_histogram(
        self,
        boundary_column: str,
        boundary_id: str,
        start_utc: dt.datetime,
        end_utc: dt.datetime,
        local_start_time: dt.time,
        local_end_time: dt.time,
    ) -> list[dict[str, Any]]:

        sql = f"""
                SELECT
                    category,
                    COUNT(*) AS category_count

                FROM {self.config.table_id(self.config.table_dashboard_events)}

                WHERE event_type = '311'
                AND {boundary_column} = @boundary_id
                AND event_ts_utc >= @start_utc
                AND event_ts_utc < @end_utc
                AND event_time_pacific >= @local_start_time
                AND event_time_pacific <= @local_end_time

                GROUP BY category

                ORDER BY
                    category_count DESC,
                    category ASC
                """

        return self._run_query(
            sql=sql,
            params=[
                bigquery.ScalarQueryParameter(
                    "boundary_id", "STRING", boundary_id
                ),
                bigquery.ScalarQueryParameter(
                    "start_utc", "TIMESTAMP", start_utc
                ),
                bigquery.ScalarQueryParameter(
                    "end_utc", "TIMESTAMP", end_utc
                ),
                bigquery.ScalarQueryParameter(
                    "local_start_time", "TIME", local_start_time
                ),
                bigquery.ScalarQueryParameter(
                    "local_end_time", "TIME", local_end_time
                ),
            ],
        )

    def _fetch_police_histogram(
        self,
        boundary_column: str,
        boundary_id: str,
        start_utc: dt.datetime,
        end_utc: dt.datetime,
        local_start_time: dt.time,
        local_end_time: dt.time,
    ) -> list[dict[str, Any]]:

        sql = f"""
                SELECT
                    category,
                    COUNT(*) AS category_count

                FROM {self.config.table_id(self.config.table_dashboard_events)}

                WHERE event_type = 'police'
                AND {boundary_column} = @boundary_id
                AND event_ts_utc >= @start_utc
                AND event_ts_utc < @end_utc
                AND event_time_pacific >= @local_start_time
                AND event_time_pacific <= @local_end_time

                GROUP BY category

                ORDER BY
                    category_count DESC,
                    category ASC
                """

        return self._run_query(
            sql=sql,
            params=[
                bigquery.ScalarQueryParameter(
                    "boundary_id", "STRING", boundary_id
                ),
                bigquery.ScalarQueryParameter(
                    "start_utc", "TIMESTAMP", start_utc
                ),
                bigquery.ScalarQueryParameter(
                    "end_utc", "TIMESTAMP", end_utc
                ),
                bigquery.ScalarQueryParameter(
                    "local_start_time", "TIME", local_start_time
                ),
                bigquery.ScalarQueryParameter(
                    "local_end_time", "TIME", local_end_time
                ),
            ],
        )

    def _run_query(
        self,
        sql: str,
        params: list[bigquery.ScalarQueryParameter],
        retries: int = 3,
    ) -> list[dict[str, Any]]:
        start = time.perf_counter()
        attempt = 0
        last_error: Exception | None = None
        while attempt < retries:
            attempt += 1
            try:
                job = self.client.query(
                    sql,
                    job_config=bigquery.QueryJobConfig(query_parameters=params),
                )
                result = job.result()
                rows = [dict(row.items()) for row in result]
                duration_ms = int((time.perf_counter() - start) * 1000)
                LOGGER.info("BigQuery query succeeded in %dms (attempt=%d)", duration_ms, attempt)
                return rows
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                LOGGER.warning("BigQuery query failed (attempt=%d/%d): %s", attempt, retries, exc)
                if attempt < retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))

        duration_ms = int((time.perf_counter() - start) * 1000)
        LOGGER.error("BigQuery query failed after %dms and %d attempts", duration_ms, retries)
        if last_error is None:
            raise RuntimeError("Unknown BigQuery failure.")
        raise last_error
