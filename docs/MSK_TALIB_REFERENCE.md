# TA-Lib reference audit (MSK only)

CI-only dependency: TA-Lib Python 0.6.8, NumPy 2.2.6, Pandas 2.2.3.
Production requirements and signal code remain unchanged. The current
production NumPy 1.26.4 pin is incompatible with the supported 0.6.x
reference release line; no production upgrade is authorized by this patch.

Upstream: https://github.com/TA-Lib/ta-lib-python
License: https://github.com/TA-Lib/ta-lib-python/blob/master/LICENSE
Native library: https://github.com/TA-Lib/ta-lib
Preserve upstream and transitive license notices and verify exact packaged
versions before distributing software. This audit imports the wheel in CI;
it does not vendor third-party code or redistribute exchange data.

Compare thirteen indicator outputs on four synthetic series. Differences
are not automatically bugs: SMA seeding, sample vs population standard
deviation, flat series handling and warmup conventions must be reviewed.
The existing Bollinger calculation uses Pandas sample standard deviation;
TA-Lib uses population standard deviation. No signal thresholds changed.

Next release gates: verified dated and adjusted real price data, independent
numerical/parity decisions, no-lookahead backtests, exact dependency and
memory checks, review of every impacted button, supervised release.
Synthetic tests cannot establish predictive accuracy.
