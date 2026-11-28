# Independent mathematical indicator reference (MSK only)

TA-Lib is replaced in the CI reference by a separately implemented scalar
oracle using Python arithmetic and statistics. No external indicator library,
new production dependency, numerical production changes or signal changes.
The old module and workflow filenames are kept for compatibility.

References are canonical SMA-seeded EMA and Wilder smoothing, RSI flat=50
(MSK convention), true range, directional movement, SMA, MACD and population
standard deviation Bollinger Bands. Existing first-observation EMA/Wilder
seeding differs from the reference; compare mature 600-bar tails rather
than concealing the differences at warmup. Tolerances are half a displayed
rounding unit plus floating point allowance. The earlier test asserting a
sample/population Bollinger difference is obsolete: current MSK uses ddof=0.

Five synthetic patterns (flat, rising, falling, volatile, gaps), 13 outputs,
hand-checkable reference values, malformed inputs and deliberate regression
detection. Any mismatch fails CI. Zero-range directional inputs are withheld
rather than assumed to be valid. This is mathematical consistency, not
independent external-vendor parity, not real-data validation and not
predictive trading accuracy. Real-data and warmup/seed comparison gates
remain separate. No deployment or database changes.
