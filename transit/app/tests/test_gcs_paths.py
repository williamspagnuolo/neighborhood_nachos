import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import join_tripupdates_vehiclepositions_day_to_parquet as join_job
import parse_tripupdates_day_to_parquet as tripupdates_job
import parse_vehiclepositions_day_to_parquet as vehiclepositions_job
from transit_gcs_paths import clear_derived_date_prefix, derived_date_prefix


def mock_blob(name: str, generation: int) -> Mock:
    blob = Mock()
    blob.name = name
    blob.generation = generation
    return blob


class DerivedPathTests(unittest.TestCase):
    def test_joined_paths_include_agency_and_date(self) -> None:
        self.assertEqual(
            derived_date_prefix("latest/joined", "muni", "2026-06-23"),
            "latest/joined/muni/2026-06-23/",
        )
        self.assertEqual(
            derived_date_prefix("latest/joined", "bart", "2026-06-23"),
            "latest/joined/bart/2026-06-23/",
        )

    def test_development_prefix_is_allowed(self) -> None:
        self.assertEqual(
            derived_date_prefix(
                "development/latest/TripUpdates", "muni", "2026-06-23"
            ),
            "development/latest/TripUpdates/muni/2026-06-23/",
        )

    def test_unsafe_roots_are_rejected(self) -> None:
        for root in (
            "",
            "raw/TripUpdates",
            "raw/latest/joined",
            "latest",
            "latest/*",
            "gs://bucket/latest/joined",
            "latest/../latest/joined",
        ):
            with self.subTest(root=root), self.assertRaises(ValueError):
                derived_date_prefix(root, "muni", "2026-06-23")

    def test_invalid_agency_and_date_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            derived_date_prefix("latest/joined", "other", "2026-06-23")
        with self.assertRaises(ValueError):
            derived_date_prefix("latest/joined", "muni", "not-a-date")
        with self.assertRaises(ValueError):
            derived_date_prefix("latest/joined", "muni", "2999-01-01")

    def test_cleanup_deletes_only_objects_returned_for_exact_prefix(self) -> None:
        prefix = "latest/VehiclePositions/bart/2026-06-23/"
        blobs = [
            mock_blob(f"{prefix}part-00000.parquet", 1),
            mock_blob(f"{prefix}part-00001.parquet", 2),
        ]
        client = Mock()
        client.list_blobs.return_value = blobs

        actual_prefix, deleted = clear_derived_date_prefix(
            client,
            "bucket",
            "latest/VehiclePositions",
            "bart",
            "2026-06-23",
        )

        self.assertEqual(actual_prefix, prefix)
        self.assertEqual(deleted, 2)
        client.list_blobs.assert_called_once_with("bucket", prefix=prefix)
        blobs[0].delete.assert_called_once_with(if_generation_match=1)
        blobs[1].delete.assert_called_once_with(if_generation_match=2)

    def test_cleanup_refuses_an_out_of_prefix_object(self) -> None:
        client = Mock()
        unexpected_blob = mock_blob("latest/joined/bart/2026-06-23/part.parquet", 1)
        client.list_blobs.return_value = [unexpected_blob]

        with self.assertRaisesRegex(RuntimeError, "outside exact prefix"):
            clear_derived_date_prefix(
                client,
                "bucket",
                "latest/joined",
                "muni",
                "2026-06-23",
            )
        unexpected_blob.delete.assert_not_called()

    def test_join_rewrite_removes_stale_shards_before_upload(self) -> None:
        prefix = "latest/joined/muni/2026-06-23/"
        old_blobs = [
            mock_blob(f"{prefix}part-{index:05d}.parquet", index + 1)
            for index in range(4)
        ]
        client = Mock()
        client.list_blobs.return_value = old_blobs
        bucket = client.bucket.return_value

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            join_job.storage, "Client", return_value=client
        ):
            uploaded = join_job._write_and_upload_shards(
                df=pd.DataFrame({"row": [1, 2]}),
                output_dir=Path(temp_dir),
                output_bucket_name="bucket",
                output_gcs_prefix="latest/joined",
                agency="muni",
                service_date="2026-06-23",
                output_shards=4,
            )

        self.assertEqual(
            uploaded,
            [f"{prefix}part-00000.parquet", f"{prefix}part-00001.parquet"],
        )
        for blob in old_blobs:
            blob.delete.assert_called_once()
        uploaded_names = [call.args[0] for call in bucket.blob.call_args_list]
        self.assertEqual(uploaded_names, uploaded)

    def test_daily_parsers_clear_exact_date_prefix_before_upload(self) -> None:
        cases = [
            (
                tripupdates_job,
                "latest/TripUpdates",
                "output_ts",
                "latest/TripUpdates/muni/2026-06-23/",
            ),
            (
                vehiclepositions_job,
                "latest/VehiclePositions",
                "output_key",
                "latest/VehiclePositions/muni/2026-06-23/",
            ),
        ]
        for module, root, date_argument, expected_prefix in cases:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temp_dir:
                client = Mock()
                clear_result = (expected_prefix, 3)
                kwargs = {
                    "df": pd.DataFrame({"row": [1]}),
                    "output_dir": Path(temp_dir),
                    date_argument: "2026-06-23",
                    "output_bucket_name": "bucket",
                    "output_gcs_prefix": root,
                    "agency": "muni",
                    "output_shards": 2,
                    "clear_source_date_prefix": True,
                }
                with patch.object(
                    module.storage, "Client", return_value=client
                ), patch.object(
                    module, "clear_derived_date_prefix", return_value=clear_result
                ) as clear_mock:
                    uploaded = module._write_and_upload_shards(**kwargs)

                clear_mock.assert_called_once_with(
                    storage_client=client,
                    bucket_name="bucket",
                    root=root,
                    agency="muni",
                    source_date="2026-06-23",
                )
                self.assertEqual(uploaded, [f"{expected_prefix}part-00000.parquet"])


if __name__ == "__main__":
    unittest.main()
