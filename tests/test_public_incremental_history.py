"""Synthetic fixtures; verify independent saved history, no live prices."""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock
import pandas as pd

from msk_public_incremental_history import refresh_public_history
from portfolio_doctor_live import IST

PRIOR = date(2026, 11, 25)
DAY = date(2026, 11, 26)

def sample(day):
    return pd.DataFrame(dict(Open=100., High=103., Low=99., Close=102., Volume=1000.),
                        index=pd.bdate_range(end=day, periods=240))

def reference(day, symbols):
    return {s: dict(Open=100., High=103., Low=99., Close=102.,
                    Volume=1000., PreviousClose=102.) for s in symbols}

class PublicIncrementalTests(unittest.TestCase):
    def refresh(self, day, path, fetch, ref=reference, symbol="SBIN"):
        return refresh_public_history(symbol, day, path,
            now=datetime(day.year,day.month,day.day,18,tzinfo=IST),
            fetch=fetch, reference=ref)

    def test_separate_session_snapshot_and_only_new_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SBIN.json"
            first = Mock(return_value=sample(PRIOR))
            old = self.refresh(PRIOR, path, first)
            self.assertIsNotNone(old)
            first.assert_called_once_with("1y")
            delta = pd.concat([old.tail(4), sample(DAY).tail(1)])
            fetch = Mock(return_value=delta)
            got = self.refresh(DAY, path, fetch)
            fetch.assert_called_once_with("5d")
            self.assertEqual(str(got.index[-1].date()), str(DAY))
            pd.testing.assert_frame_equal(got.loc[old.index], old, check_freq=False)

    def test_changed_overlap_forces_full_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SBIN.json"
            old = self.refresh(PRIOR, path, Mock(return_value=sample(PRIOR)))
            delta = pd.concat([old.tail(4), sample(DAY).tail(1)])
            delta.iloc[0,0] = 101
            fetch = Mock(side_effect=[delta, sample(DAY)])
            got = self.refresh(DAY, path, fetch)
            self.assertIsNotNone(got)
            self.assertEqual([c.args[0] for c in fetch.call_args_list], ["5d","1y"])

    def test_missing_reference_and_private_symbol_fail_without_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SBIN.json"
            fetch = Mock()
            self.assertIsNone(self.refresh(DAY, path, fetch, ref=lambda *_: {}))
            self.assertIsNone(self.refresh(DAY, path, fetch, symbol="TCS"))
            fetch.assert_not_called()
            self.assertFalse(path.exists())

    def test_mismatched_official_volume_rejects_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SBIN.json"
            def bad(day, symbols):
                return {"SBIN": dict(reference(day,symbols)["SBIN"], Volume=999)}
            self.assertIsNone(self.refresh(DAY, path, Mock(return_value=sample(DAY)), ref=bad))
            self.assertFalse(path.exists())

    def test_corrupt_cache_and_wrong_session_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SBIN.json"
            path.write_text('{"symbol":"DLF"}')
            fetch = Mock(return_value=sample(DAY))
            self.assertIsNotNone(self.refresh(DAY, path, fetch))
            fetch.assert_called_once_with("1y")

if __name__ == "__main__":
    unittest.main()
