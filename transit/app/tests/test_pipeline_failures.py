import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


APP_DIR = Path(__file__).resolve().parents[1]
REPO_TRANSIT_DIR = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import parse_tripupdates_day_to_parquet as tripupdates_job
import parse_vehiclepositions_day_to_parquet as vehiclepositions_job


class CorruptBlob:
    name = "raw/feed/muni/2026-06-23/2026-06-23T00-00-00.000000Z.pb"

    def download_as_bytes(self) -> bytes:
        return b"\xff"  # Invalid protobuf wire data.


def tripupdates_args(output_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        bucket="bucket",
        agency="muni",
        source_root_prefix="raw/TripUpdates",
        service_date="2026-06-23",
        output_dir=output_dir,
        output_gcs_bucket="bucket",
        output_gcs_prefix="latest/TripUpdates",
        output_file_timestamp="",
        output_use_service_date_folder=True,
        write_single_parquet_to_gcs=True,
        latest_only=True,
        output_shards=1,
        task_index=0,
        task_count=1,
        sample_n_blobs=0,
        sample_every_k=0,
        max_blob_seconds=0.0,
        chunk_size=1,
        parallel_finalize_timeout_seconds=1,
    )


def vehiclepositions_args(output_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        bucket="bucket",
        agency="muni",
        source_root_prefix="raw/VehiclePositions",
        service_date="2026-06-23",
        output_dir=output_dir,
        output_gcs_bucket="bucket",
        output_gcs_prefix="latest/VehiclePositions",
        output_file_timestamp="",
        output_use_service_date_folder=True,
        output_shards=1,
        task_index=0,
        task_count=1,
        sample_n_blobs=0,
        sample_every_k=0,
        parallel_finalize_timeout_seconds=1,
    )


class PipelineFailureTests(unittest.TestCase):
    def test_corrupt_tripupdates_blob_fails_before_upload(self) -> None:
        client = Mock()
        client.list_blobs.return_value = [CorruptBlob()]
        with tempfile.TemporaryDirectory() as output_dir, patch.object(
            tripupdates_job, "parse_args", return_value=tripupdates_args(output_dir)
        ), patch.object(tripupdates_job.storage, "Client", return_value=client), self.assertRaisesRegex(
            RuntimeError, "TripUpdates parsing failed for 1 blob"
        ):
            tripupdates_job.main()
        client.bucket.assert_not_called()

    def test_corrupt_vehiclepositions_blob_fails_before_upload(self) -> None:
        client = Mock()
        client.list_blobs.return_value = [CorruptBlob()]
        with tempfile.TemporaryDirectory() as output_dir, patch.object(
            vehiclepositions_job, "parse_args", return_value=vehiclepositions_args(output_dir)
        ), patch.object(vehiclepositions_job.storage, "Client", return_value=client), self.assertRaisesRegex(
            RuntimeError, "VehiclePositions parsing failed for 1 blob"
        ):
            vehiclepositions_job.main()
        client.bucket.assert_not_called()

    def test_workflow_source_has_dependency_order_without_error_swallowing(self) -> None:
        source = (REPO_TRANSIT_DIR / "orchestration" / "workflow.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("- input_map: ${input}", source)
        self.assertNotIn("${default(input, {})}", source)
        self.assertIn("- parse_today_utc:", source)
        self.assertIn(
            "${source_date_timestamp >= today_utc_timestamp}", source
        )
        self.assertNotIn("${source_date >= today_utc}", source)
        self.assertIn("parallel:", source)
        self.assertIn("job_name: \"tripupdates-parse-day\"", source)
        self.assertIn("job_name: \"vehiclepositions-parse-day\"", source)
        self.assertLess(source.index("- run_join:"), source.index("- run_upsert:"))
        self.assertIn("googleapis.run.v2.projects.locations.jobs.run", source)
        self.assertNotIn("try:", source)
        self.assertNotIn("except:", source)
        self.assertNotIn("retry:", source)


if __name__ == "__main__":
    unittest.main()
