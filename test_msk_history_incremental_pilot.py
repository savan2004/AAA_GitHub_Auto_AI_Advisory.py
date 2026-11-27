"""Synthetic unit fixtures only; not actual market prices."""
import unittest
import pandas as pd
from msk_history_incremental_pilot import merge_verified_history


class IncrementalHistoryTests(unittest.TestCase):
    def setUp(self):
        self.old = pd.DataFrame(
            {"Open": 100., "High": 105., "Low": 95., "Close": 102., "Volume": 15000.},
            index=pd.bdate_range(end="2026-11-20", periods=60))
        self.delta = self.old.tail(1).copy()
        self.delta = pd.concat([self.delta, self.old.tail(1).set_axis(
            pd.DatetimeIndex(["2026-11-23"]))])

    def merge(self, delta):
        return merge_verified_history(self.old, delta, "2026-11-20",
                                      "2026-11-23", ["2026-11-23"])

    def test_append(self):
        result = self.merge(self.delta)
        self.assertEqual(len(result), 61)
        pd.testing.assert_frame_equal(result.iloc[:-1], self.old, check_freq=False)

    def test_missing_session(self):
        self.assertIsNone(self.merge(self.delta.iloc[:1]))

    def test_conflicting_overlap(self):
        delta = self.delta.copy()
        delta.iloc[0, 0] = 101.
        self.assertIsNone(self.merge(delta))

    def test_invalid_new_candle(self):
        delta = self.delta.copy()
        delta.iloc[-1, 1] = 90.
        self.assertIsNone(self.merge(delta))

    def test_duplicate(self):
        self.assertIsNone(self.merge(pd.concat([self.delta, self.delta.tail(1)])))

    def test_missing_overlap(self):
        self.assertIsNone(self.merge(self.delta.tail(1)))

    def test_future_row(self):
        future = self.delta.tail(1).set_axis(pd.DatetimeIndex(["2026-11-24"]))
        self.assertIsNone(self.merge(pd.concat([self.delta, future])))


if __name__ == "__main__":
    unittest.main()
