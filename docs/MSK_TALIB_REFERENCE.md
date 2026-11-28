# Independent mathematical indicator reference (MSK only)

TA-Lib is replaced in the CI reference by a separately implemented scalar
oracle using Python arithmetic and statistics. No external indicator library,
new production dependency, numerical production changes or signal changes.
The old Python module and test filenames are kept for compatibility. The
redundant standalone workflow is retired; its full test suite and artifact
are now mandatory in the existing Core Data Acceptance independent-indicators
job. No accuracy check was removed. The job uses the existing NumPy/Pandas
pins, without TA-Lib.

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

Engineering references (methodology only, no copied third-party code):
- https://github.com/rhysd/actionlint — workflow static validation guidance.
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax — trigger and path rules.
- https://developers.openai.com/api/docs/guides/evaluation-best-practices — continuous, edge-case and regression evaluations.
The previous standalone run had no jobs; exact startup cause is unavailable.
On c04af927 the existing independent-indicators job logged 65 MATCH, zero
DIFFERENCE, but still installed unused TA-Lib. The consolidated job must
pass on its new exact commit before release. No ASI or external AI service
is needed to validate deterministic numerical calculations.
