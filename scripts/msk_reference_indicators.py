"""CI-only independent indicator comparison; never runs in Render."""
import os
os.environ.setdefault("MSK_RESEARCH_ONLY", "1")
import numpy as np
import pandas as pd
import talib
import technical_indicators as ti

def main():
    rng = np.random.default_rng(42)
    c = 100+np.cumsum(rng.normal(.04,.9,600))
    h = c+rng.uniform(.1,2,len(c))
    l = c-rng.uniform(.1,2,len(c))
    s = pd.Series(c)
    df = pd.DataFrame({"High":h,"Low":l,"Close":c})
    got = ti.rsi_series(s).to_numpy()
    expected = talib.RSI(c,timeperiod=14)
    np.testing.assert_allclose(got[14:],expected[14:],rtol=1e-9,atol=1e-9)
    # Compare mature tails; existing public scalars are rounded and EMA
    # seed conventions may differ early in the series.
    checks = [
        ("EMA",ti.calc_ema(s,20),talib.EMA(c,20)[-1],.011),
        ("ATR",ti.calc_atr(df),talib.ATR(h,l,c,14)[-1],.011),
        ("ADX",ti.calc_adx(df)[0],talib.ADX(h,l,c,14)[-1],.11),
    ]
    m, sig, hist = talib.MACD(c,12,26,9)
    checks.extend((name,v,ref[-1],.011) for name,v,ref in zip(
        ("MACD","signal","hist"),ti.calc_macd(s),(m,sig,hist)))
    upper,mid,lower = talib.BBANDS(c,timeperiod=20)
    # Existing pandas sample std differs from TA-Lib population std;
    # record it explicitly rather than silently claim parity.
    for name,v,ref,tol in checks:
        if not np.isfinite(v) or abs(v-ref)>tol:
            raise AssertionError(f"{name}: mismatch {v} versus {ref}")
        print(f"{name}: PASS")
    print("RSI: PASS; flat-window convention separately tested")
    print("Bollinger population reference:",upper[-1],mid[-1],lower[-1],
          "existing sample-standard-deviation implementation:",ti.calc_bollinger(s))

if __name__=="__main__":
    main()
