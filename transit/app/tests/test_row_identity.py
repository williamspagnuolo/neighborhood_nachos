import sys
import unittest
from unittest.mock import patch
from pathlib import Path
from unittest.mock import Mock

import pandas as pd


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import join_tripupdates_vehiclepositions_day_to_parquet as join_job
import parse_tripupdates_day_to_parquet as tripupdates_job
import parse_vehiclepositions_day_to_parquet as vehiclepositions_job
import upsert_joined_day_to_bigquery as upsert_job
from transit_row_identity import CANONICAL_ROW_KEY, assert_unique_canonical_keys


def canonical_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "agency_id": "muni",
        "trip_id": "trip-1",
        "trip_start_date": "2026-06-23",
        "trip_start_time": None,
        "direction_id": None,
        "stop_sequence": 7,
    }
    row.update(overrides)
    return row


class CanonicalRowIdentityTests(unittest.TestCase):
    def test_key_contains_the_approved_six_columns(self) -> None:
        self.assertEqual(
            CANONICAL_ROW_KEY,
            [
                "agency_id",
                "trip_id",
                "trip_start_date",
                "trip_start_time",
                "direction_id",
                "stop_sequence",
            ],
        )
        self.assertEqual(join_job.JOIN_KEYS, CANONICAL_ROW_KEY)
        self.assertEqual(upsert_job.JOIN_KEYS, CANONICAL_ROW_KEY)

    def test_duplicate_key_check_treats_nulls_as_equal(self) -> None:
        df = pd.DataFrame([canonical_row(), canonical_row()])
        with self.assertRaisesRegex(ValueError, "duplicate canonical keys"):
            assert_unique_canonical_keys(df, "test frame")

    def test_vehiclepositions_latest_keeps_distinct_start_times(self) -> None:
        rows = [
            canonical_row(
                trip_start_time="08:00:00",
                vp_snapshot_ts="2026-06-23T08:01:00Z",
                blob_order=1,
                blob_name="one.pb",
            ),
            canonical_row(
                trip_start_time="09:00:00",
                vp_snapshot_ts="2026-06-23T09:01:00Z",
                blob_order=2,
                blob_name="two.pb",
            ),
        ]
        df = pd.DataFrame(rows)
        finalized = vehiclepositions_job._finalize_latest_df(df)
        self.assertEqual(len(finalized), 2)

    def test_tripupdates_latest_keeps_distinct_start_times(self) -> None:
        rows = [
            canonical_row(
                trip_start_time="08:00:00",
                snapshot_ts="2026-06-23T08:01:00Z",
                blob_order=1,
                blob_name="one.pb",
                route_id="N",
                stop_id="stop-1",
                arrival_time_predicted=None,
                departure_time_predicted=None,
                arrival_delay_sec=None,
                departure_delay_sec=None,
            ),
            canonical_row(
                trip_start_time="09:00:00",
                snapshot_ts="2026-06-23T09:01:00Z",
                blob_order=2,
                blob_name="two.pb",
                route_id="N",
                stop_id="stop-1",
                arrival_time_predicted=None,
                departure_time_predicted=None,
                arrival_delay_sec=None,
                departure_delay_sec=None,
            ),
        ]
        finalized = tripupdates_job._global_latest_from_stage_df(pd.DataFrame(rows))
        self.assertEqual(len(finalized), 2)

    def test_join_preserves_tripupdates_rows_and_rejects_duplicates(self) -> None:
        tu_rows = [
            canonical_row(trip_start_time="08:00:00", route_id="N"),
            canonical_row(trip_start_time="09:00:00", route_id="N"),
        ]
        vp_rows = [
            canonical_row(
                trip_start_time="08:00:00",
                vp_snapshot_ts="2026-06-23T08:01:00Z",
                vehicle_id="vehicle-1",
            ),
            canonical_row(
                trip_start_time="09:00:00",
                vp_snapshot_ts="2026-06-23T09:01:00Z",
                vehicle_id="vehicle-2",
            ),
        ]
        joined = join_job._join_frames(pd.DataFrame(tu_rows), pd.DataFrame(vp_rows))
        self.assertEqual(len(joined), len(tu_rows))
        self.assertEqual(joined["vehicle_id"].tolist(), ["vehicle-1", "vehicle-2"])

        with self.assertRaisesRegex(ValueError, "TripUpdates input contains"):
            join_job._join_frames(pd.DataFrame(tu_rows + [tu_rows[0]]), pd.DataFrame(vp_rows))

    def test_parsers_fail_after_any_blob_failure(self) -> None:
        tu_stats = tripupdates_job.ParseStats(
            agency="muni", source_date="2026-06-23", blobs_failed=1
        )
        vp_stats = vehiclepositions_job.ParseStats(
            agency="bart", source_date="2026-06-23", blobs_failed=1
        )
        with self.assertRaisesRegex(RuntimeError, "TripUpdates parsing failed"):
            tripupdates_job._raise_for_blob_failures(tu_stats)
        with self.assertRaisesRegex(RuntimeError, "VehiclePositions parsing failed"):
            vehiclepositions_job._raise_for_blob_failures(vp_stats)

    def test_staging_duplicate_query_uses_all_six_key_columns(self) -> None:
        sql = upsert_job._build_duplicate_key_count_sql(
            "project", "dataset", "stage", CANONICAL_ROW_KEY
        )
        self.assertIn("HAVING COUNT(*) > 1", sql)
        for key in CANONICAL_ROW_KEY:
            self.assertIn(f"`{key}`", sql)

        merge_sql = upsert_job._build_merge_sql(
            "project", "dataset", "target", "stage", CANONICAL_ROW_KEY
        )
        self.assertIn("T.`trip_start_time` = S.`trip_start_time`", merge_sql)
        self.assertIn("WHEN MATCHED AND", merge_sql)
        self.assertIn("T.`latest_snapshot_ts` IS NULL", merge_sql)
        self.assertIn("S.`latest_snapshot_ts` IS NOT NULL", merge_sql)
        self.assertIn(
            "S.`latest_snapshot_ts` >= T.`latest_snapshot_ts`", merge_sql
        )

        client = Mock()
        client.query.return_value.result.return_value = [
            {"duplicate_key_group_count": 1}
        ]
        with self.assertRaisesRegex(ValueError, "duplicate canonical key group"):
            upsert_job._assert_stage_has_unique_canonical_keys(
                client, "project", "dataset", "stage", CANONICAL_ROW_KEY
            )

    def test_merge_does_not_replace_a_newer_cross_date_observation(self) -> None:
        """The SQL guard makes a July 1 rerun safe after a July 2 observation."""
        merge_sql = upsert_job._build_merge_sql(
            "project", "dataset", "target", "stage", CANONICAL_ROW_KEY
        )
        expected_guard = """WHEN MATCHED AND (
  T.`latest_snapshot_ts` IS NULL
  OR (
    S.`latest_snapshot_ts` IS NOT NULL
    AND S.`latest_snapshot_ts` >= T.`latest_snapshot_ts`
  )
) THEN"""
        self.assertIn(expected_guard, merge_sql)

    def test_staging_name_is_execution_and_agency_specific(self) -> None:
        muni_name = upsert_job.build_staging_table_name(
            "trip_stops", "muni", "tripupdates-vp-join-day-abc-123"
        )
        bart_name = upsert_job.build_staging_table_name(
            "trip_stops", "bart", "tripupdates-vp-join-day-abc-123"
        )
        next_execution_name = upsert_job.build_staging_table_name(
            "trip_stops", "muni", "tripupdates-vp-join-day-def-456"
        )
        self.assertEqual(
            muni_name, "trip_stops__stage_muni_tripupdates_vp_join_day_abc_123"
        )
        self.assertNotEqual(muni_name, bart_name)
        self.assertNotEqual(muni_name, next_execution_name)

    def test_staging_name_uses_cloud_run_execution_or_safe_local_fallback(self) -> None:
        with patch.dict(
            "os.environ", {"CLOUD_RUN_EXECUTION": "loader-run-123"}, clear=True
        ):
            self.assertEqual(
                upsert_job.build_staging_table_name("trip_stops", "muni"),
                "trip_stops__stage_muni_loader_run_123",
            )
        with patch.object(upsert_job.uuid, "uuid4", return_value=Mock(hex="local123")):
            self.assertEqual(
                upsert_job.build_staging_table_name("trip_stops", "muni", ""),
                "trip_stops__stage_muni_local123",
            )


if __name__ == "__main__":
    unittest.main()
