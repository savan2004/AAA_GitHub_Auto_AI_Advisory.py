"""Synthetic fixtures for exact public history transfer; no actual market data."""
import json
import os
import tempfile
import unittest
import pandas as pd
from smart_chart_render_handoff import encode_handoff,decode_handoff,read_inherited_handoff

SESSION="2026-11-26"

def candidate():
    frame = pd.DataFrame(dict(Open=100.25,High=105.,Low=99.,Close=102.125,Volume=15000.),
                         index=pd.bdate_range(end=SESSION,periods=75))
    reason="Breakout confirmed: 20-session high + volume"
    return dict(sym="TCS.NS",name="TCS",score=15,close=102.125,reason=reason,
                scope_note=f"Sample: 1/100 qualifying NSE setups from 100; full-universe scan; Setup: {reason}; EOD {SESSION}",
                setup_confirmed=True,verified_trade_plan=False,_daily_history=frame)

class RenderHandoffTests(unittest.TestCase):
    def test_exact_candles_and_metadata_roundtrip(self):
        original=candidate()
        recovered=decode_handoff(encode_handoff(original,SESSION),SESSION)
        pd.testing.assert_frame_equal(original["_daily_history"],recovered["_daily_history"],check_freq=False)
        self.assertEqual(recovered["score"],15)
        self.assertFalse(recovered["verified_trade_plan"])

    def test_wrong_session_and_changed_close_rejected(self):
        raw=encode_handoff(candidate(),SESSION)
        with self.assertRaises(ValueError):
            decode_handoff(raw,"2026-11-27")
        for key,val in (("close",999),("sym","TCS.BO"),("score",5),("setup_confirmed",False)):
            data=json.loads(raw)
            data[key]=val
            with self.subTest(key=key),self.assertRaises(ValueError):
                decode_handoff(json.dumps(data).encode(),SESSION)

    def test_duplicate_or_changed_price_candle_rejected(self):
        raw=encode_handoff(candidate(),SESSION)
        data=json.loads(raw)
        data["bars"][-1][0]=data["bars"][-2][0]
        with self.assertRaises(ValueError):
            decode_handoff(json.dumps(data).encode(),SESSION)
        data=json.loads(raw)
        data["bars"][-1][2]=1
        with self.assertRaises(ValueError):
            decode_handoff(json.dumps(data).encode(),SESSION)

    def test_inherited_regular_file_closed_after_read(self):
        with tempfile.TemporaryFile(mode="w+b") as stream:
            stream.write(encode_handoff(candidate(),SESSION))
            stream.flush()
            stream.seek(0)
            fd=os.dup(stream.fileno())
            self.assertEqual(read_inherited_handoff(fd,SESSION)["sym"],"TCS.NS")
            with self.assertRaises(OSError):
                os.fstat(fd)

if __name__=="__main__":
    unittest.main()
