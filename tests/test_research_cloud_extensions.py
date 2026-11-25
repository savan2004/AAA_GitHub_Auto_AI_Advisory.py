"""Offline cloud sector isolation and bounded daily schedule contract."""
import hashlib
import json
import os
import unittest
from datetime import datetime, date, timezone
from unittest.mock import patch, Mock
import sector_report_drive as sector
import research_cloud_daily as daily
import research_pdf_drive as drive


class CloudExtensionTests(unittest.TestCase):
    def test_sector_metadata_is_verified_and_cannot_be_stock_fifo_candidate(self):
        session = date(2026, 1, 23)
        desc = json.dumps({"coverage":{"asof":"23-Jan-2026",
                    "index_coverage":[10,15],"n_unique":120,"n_configured":150},
                    "summary":["Verified coverage fixture only"]})
        entry = {"id":"test", "parents":[drive.SECTOR_FOLDER],
                 "mimeType":"application/pdf","trashed":False,"description":desc,
                 "appProperties":{"archive":drive.SECTOR_TAG,"session":str(session),
                    "metadataSha":hashlib.sha256(desc.encode()).hexdigest()}}
        self.assertFalse(drive._managed(entry))
        self.assertTrue(drive._managed(entry,drive.SECTOR_FOLDER,drive.SECTOR_TAG))
        with patch("sector_report_snapshot._expected_session", return_value=session):
            self.assertEqual(sector._metadata(entry,session),["Verified coverage fixture only"])
        entry["description"] += " "
        with self.assertRaisesRegex(ValueError,"integrity"):
            sector._metadata(entry,session)

    def test_sector_client_cannot_delete(self):
        client = object.__new__(drive.Drive)
        client.folder, client.tag = drive.SECTOR_FOLDER, drive.SECTOR_TAG
        client.get = Mock()
        with self.assertRaisesRegex(ValueError,"Only stock"):
            client.delete({"id":"test"})
        client.get.assert_not_called()

    def test_sector_disabled_has_no_io(self):
        with patch.dict(os.environ, {"MSK_SECTOR_DRIVE_ENABLED":"0"}), patch.object(sector,"Drive") as client:
            self.assertIsNone(sector.load_sector_cloud(output_dir="unused"))
            self.assertFalse(sector.store_sector_cloud({}, "unused", []))
            client.assert_not_called()

    def test_daily_allowlist_and_window(self):
        with patch.dict(os.environ,{"MSK_RESEARCH_DAILY_SYMBOLS":"SBIN,DMART,SBIN"}):
            self.assertEqual(daily.configured_symbols(),("SBIN","DMART"))
        with patch.dict(os.environ,{"MSK_RESEARCH_DAILY_SYMBOLS":",".join("X"+str(i) for i in range(21))}):
            self.assertEqual(daily.configured_symbols(),())
        with patch.dict(os.environ,{"MSK_RESEARCH_DAILY_SYMBOLS":"../bad"}):
            self.assertEqual(daily.configured_symbols(),())
        self.assertTrue(daily.eligible(datetime(2026,1,23,12,0,tzinfo=timezone.utc)))
        self.assertFalse(daily.eligible(datetime(2026,1,24,12,0,tzinfo=timezone.utc)))
        self.assertFalse(daily.eligible(datetime(2026,1,23,13,30,tzinfo=timezone.utc)))

if __name__ == "__main__":
    unittest.main()
