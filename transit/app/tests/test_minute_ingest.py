import os
import sys
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import upload_transit_to_bucket as ingest


class MinuteIngestTests(unittest.TestCase):
    def setUp(self):
        self.keys = {
            feed: {agency: ["test-key"] for agency in ingest.AGENCIES}
            for feed in ingest.FEEDS
        }
        self.intervals = {agency: 60 for agency in ingest.AGENCIES}

    @mock.patch.object(ingest, "copy_blob_between_buckets")
    @mock.patch.object(ingest, "upload_feed_to_bucket")
    @mock.patch.object(ingest, "fetch_gtfs_rt_feed", return_value=b"protobuf")
    @mock.patch.object(ingest, "should_fetch_agency", return_value=True)
    def test_default_writes_raw_only(
        self, _should_fetch, _fetch, upload, copy_blob
    ):
        result = ingest.call_transit_and_upload(
            self.keys,
            self.intervals,
            "raw-bucket",
            "raw",
            "latest-bucket",
            "latest",
        )

        self.assertEqual(upload.call_count, 4)
        copy_blob.assert_not_called()
        self.assertIn("latest copied: disabled", result)
        for call in upload.call_args_list:
            self.assertRegex(
                call.args[1],
                r"^raw/(TripUpdates|VehiclePositions)/(muni|bart)/"
                r"\d{4}-\d{2}-\d{2}/.+\.pb$",
            )

    @mock.patch.object(ingest, "copy_blob_between_buckets")
    @mock.patch.object(ingest, "upload_feed_to_bucket")
    @mock.patch.object(ingest, "fetch_gtfs_rt_feed", return_value=b"protobuf")
    @mock.patch.object(ingest, "should_fetch_agency", return_value=True)
    def test_legacy_copy_can_be_explicitly_reenabled(
        self, _should_fetch, _fetch, _upload, copy_blob
    ):
        ingest.call_transit_and_upload(
            self.keys,
            self.intervals,
            "raw-bucket",
            "raw",
            "latest-bucket",
            "latest",
            copy_raw_to_latest=True,
        )

        self.assertEqual(copy_blob.call_count, 4)
        for call in copy_blob.call_args_list:
            self.assertEqual(call.args[0], "raw-bucket")
            self.assertEqual(call.args[2], "latest-bucket")
            self.assertRegex(
                call.args[3],
                r"^latest/(TripUpdates|VehiclePositions)/(muni|bart)/"
                r"\d{4}-\d{2}-\d{2}T.+\.pb$",
            )

    def test_copy_flag_defaults_false_and_rejects_invalid_values(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(ingest.env_bool("TRANSIT_COPY_RAW_TO_LATEST"))
        with mock.patch.dict(
            os.environ, {"TRANSIT_COPY_RAW_TO_LATEST": "true"}, clear=True
        ):
            self.assertTrue(ingest.env_bool("TRANSIT_COPY_RAW_TO_LATEST"))
        with mock.patch.dict(
            os.environ, {"TRANSIT_COPY_RAW_TO_LATEST": "sometimes"}, clear=True
        ):
            with self.assertRaisesRegex(ValueError, "TRANSIT_COPY_RAW_TO_LATEST"):
                ingest.env_bool("TRANSIT_COPY_RAW_TO_LATEST")


if __name__ == "__main__":
    unittest.main()
