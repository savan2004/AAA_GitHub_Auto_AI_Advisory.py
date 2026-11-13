"""Do not turn an invalid or temporarily unreachable index DB into fake Top 5."""
import sys
import types
import unittest
from unittest.mock import patch, MagicMock
import overnight_index_reports as module


class TemporaryConnectionFailure(Exception):
    sqlstate = "08006"


class PermanentConnectionFailure(Exception):
    sqlstate = "28P01"


class IndexEvidenceStoreConnectionTest(unittest.TestCase):
    def test_database_name_is_not_an_index_database_url(self):
        with self.assertRaisesRegex(ValueError, "full postgres"):
            module.EvidenceStore("msk_index_research")

    def test_neon_transient_failure_retries_and_recovers(self):
        db = MagicMock()
        connect = MagicMock(side_effect=[TemporaryConnectionFailure(),
                                         TemporaryConnectionFailure(), db])
        fake = types.SimpleNamespace(connect=connect,
                                     OperationalError=TemporaryConnectionFailure)
        with patch.dict(sys.modules, {"psycopg": fake}), \
             patch.object(module.time, "sleep") as sleep:
            with module.EvidenceStore("postgresql://test.invalid/msk?sslmode=require") as store:
                self.assertTrue(store.pg)
                self.assertEqual(db.execute.call_count, 2)
                self.assertEqual(db.commit.call_count, 1)
            self.assertEqual(connect.call_count, 3)
            sleep.assert_any_call(0.4)
            sleep.assert_any_call(0.8)
            self.assertEqual(sleep.call_count, 2)
            db.close.assert_called_once()

    def test_invalid_credentials_are_not_retried(self):
        db = MagicMock()
        authError = type("AuthError", (PermanentConnectionFailure,), {})
        fake = types.SimpleNamespace(
            connect=MagicMock(side_effect=authError()),
            OperationalError=(TemporaryConnectionFailure, PermanentConnectionFailure))
        with patch.dict(sys.modules, {"psycopg": fake}), \
             patch.object(module.time, "sleep") as sleep:
            with self.assertRaises(authError):
                module.EvidenceStore("postgresql://test.invalid/msk?sslmode=require")
            fake.connect.assert_called_once()
            sleep.assert_not_called()

    def test_connection_outage_does_not_release_unverified_top5(self):
        fake = types.SimpleNamespace(
            connect=MagicMock(side_effect=TemporaryConnectionFailure()),
            OperationalError=TemporaryConnectionFailure)
        with patch.dict(sys.modules, {"psycopg": fake}), \
             patch.dict(module.os.environ, {"MSK_INDEX_DATABASE_URL":
                                             "postgresql://test.invalid/msk?sslmode=require"}), \
             patch.object(module.time, "sleep"):
            result = module.load_prepared_report("conservative")
        self.assertIn("database unavailable", result)
        self.assertIn("No unverified Top 5", result)


if __name__ == "__main__":
    unittest.main()
