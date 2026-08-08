import datetime as dt
import os
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import join_tripupdates_vehiclepositions_day_to_parquet as join_job
import parse_tripupdates_day_to_parquet as tripupdates_job
import parse_vehiclepositions_day_to_parquet as vehiclepositions_job
import upsert_joined_day_to_bigquery as upsert_job
from transit_job_config import (
    default_source_date_utc,
    env_bool,
    validate_agency,
    validate_source_date,
)


ENV_DIR = APP_DIR / "jobs"
JOB_CASES = [
    (tripupdates_job, ENV_DIR / "parse_tripupdates.env.yaml"),
    (vehiclepositions_job, ENV_DIR / "parse_vehiclepositions.env.yaml"),
    (join_job, ENV_DIR / "join_tripupdates_vehiclepositions.env.yaml"),
    (upsert_job, ENV_DIR / "upsert_joined_to_bigquery.env.yaml"),
]


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


class JobInputTests(unittest.TestCase):
    def test_job_env_files_satisfy_no_argument_commands(self) -> None:
        for module, env_path in JOB_CASES:
            with self.subTest(module=module.__name__), patch.dict(
                os.environ, load_env(env_path), clear=True
            ):
                args = module.parse_args([])
                self.assertEqual(args.agency, "muni")
                self.assertEqual(args.service_date, default_source_date_utc())

    def test_cli_date_and_agency_override_environment(self) -> None:
        for module, env_path in JOB_CASES:
            with self.subTest(module=module.__name__), patch.dict(
                os.environ, load_env(env_path), clear=True
            ):
                args = module.parse_args(
                    ["--agency", "bart", "--service-date", "2026-06-23"]
                )
                self.assertEqual(args.agency, "bart")
                self.assertEqual(args.service_date, "2026-06-23")

    def test_stable_job_values_are_loaded_from_env_files(self) -> None:
        with patch.dict(
            os.environ,
            load_env(ENV_DIR / "parse_tripupdates.env.yaml"),
            clear=True,
        ):
            args = tripupdates_job.parse_args([])
            self.assertEqual(args.bucket, "511_transit_data")
            self.assertEqual(args.output_shards, 16)
            self.assertTrue(args.latest_only)
            self.assertTrue(args.write_single_parquet_to_gcs)
            self.assertTrue(args.output_use_service_date_folder)

        with patch.dict(
            os.environ,
            load_env(ENV_DIR / "upsert_joined_to_bigquery.env.yaml"),
            clear=True,
        ):
            args = upsert_job.parse_args([])
            self.assertEqual(args.bq_project, "neighboorhood-nachos")
            self.assertEqual(args.bq_dataset, "neighborhood_livability_data")
            self.assertEqual(args.bq_table, "trip_stops")
            self.assertEqual(args.bq_location, "us-central1")

    def test_invalid_agency_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "agency must be one of"):
            validate_agency("other")

    def test_invalid_today_and_future_dates_are_rejected(self) -> None:
        today = dt.date(2026, 8, 7)
        for value in ("not-a-date", "2026-02-30", "2026-08-07", "2026-08-08"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_source_date(value, today=today)

    def test_historical_date_is_normalized(self) -> None:
        self.assertEqual(
            validate_source_date("2026-06-23", today=dt.date(2026, 8, 7)),
            "2026-06-23",
        )

    def test_missing_bucket_fails_during_argument_validation(self) -> None:
        env = load_env(ENV_DIR / "parse_tripupdates.env.yaml")
        env["TRANSIT_BUCKET"] = ""
        with patch.dict(os.environ, env, clear=True), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                tripupdates_job.parse_args([])

    def test_missing_bigquery_table_fails_during_argument_validation(self) -> None:
        env = load_env(ENV_DIR / "upsert_joined_to_bigquery.env.yaml")
        env["TRANSIT_BQ_TABLE"] = ""
        with patch.dict(os.environ, env, clear=True), redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit):
                upsert_job.parse_args([])

    def test_invalid_boolean_environment_value_is_rejected(self) -> None:
        with patch.dict(
            os.environ, {"TRANSIT_WRITE_PARQUET_TO_GCS": "sometimes"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "must be one of true/false"):
                env_bool("TRANSIT_WRITE_PARQUET_TO_GCS")


if __name__ == "__main__":
    unittest.main()
