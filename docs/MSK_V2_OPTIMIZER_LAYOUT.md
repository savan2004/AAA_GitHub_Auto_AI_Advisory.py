# MSK V2 optimizer and future report layout

Draft, isolated from production. Existing equal-weight schema 1 remains
unchanged by default. Install the optional requirements in a test environment
and set MSK_BUILDER_V2_OPTIMIZER_ENABLED=1 only with a separately approved
schema 2 artifact. No production data, flags, scheduler or deployment changed.
The artifact adds risk_history: status APPROVED_ADJUSTED_DAILY, sessions
(126–252 strictly ascending completed session dates ending on close_date),
and returns_by_symbol (decimal daily adjusted returns, exactly the approved
company symbols). Dates and values are checked, but the publisher must verify
the official trading calendar, provenance, issuer identity, corporate actions,
and complete return history before approval. This patch does not create an
approved artifact or promote staging data. Input remains capped at 64,000
characters; oversized artifacts are withheld. Minimize historical variance
with 20% diagonal shrinkage, preserve the earlier invested sleeve, avoid
shorting/leverage, keep the existing per-stock cap and industry diversity,
round down whole shares and retain cash. No assumed return or target price.
Allocation is not investment advice or a prediction.

## Layout references (future change, not implemented)
- PyPortfolioOpt plotting docs: https://pyportfolioopt.readthedocs.io/en/latest/Plotting.html
  Adopt allocation bars and correlation heatmap. Do not display an
  efficient frontier implying expected returns unless separately validated.
- QuantStats: https://github.com/ranaroussi/quantstats
  Adopt a benchmark-relative growth chart, underwater drawdown, monthly
  returns heatmap and rolling volatility for Portfolio Doctor only when
  dated cash-flow-aware history and benchmark coverage support them.
- Preserve existing MSK navy/blue PDF identity and Telegram workflow.
  Page 1: capital, cash reserve, holdings, dated data, allocation summary.
  Page 2: stock and sector allocation bars, actual rupee weights and prices.
  Page 3: correlation matrix, historical volatility and methodology.
  Subsequent pages: company evidence and source links; distinguish verified,
  modeled and unavailable fields. No fabricated performance chart.
- Keep charts compact, static and accessible; use existing PDF generator
  instead of embedding heavy HTML or requiring a browser at runtime.
- Do not change the current PDF design as part of optimizer code.

Release gates: approved historical-data producer, independent data QA,
solver tests on target runtime, measured Render memory, real report
and protected Telegram receipt, legal/regulatory review. No auto-merge.
