# MSK V2 infographic report (draft)

The report renders actual validated allocation results, not illustrative metrics.
A4 vector PDF uses existing ReportLab and pypdf dependencies, memory-only
delivery, navy/white MSK branding, and no new browser or plotting process.
Includes capital and cash, position values/quantities/weights, sector bars,
methodology, and clickable filing and exchange-price references. Supports
0–20 positions with pagination. CI creates a conspicuously fictional QA PDF.

MSK_BUILDER_V2_INFOGRAPHIC_ENABLED=1 opts into the new document delivery
instead of the text allocation. The base V2 flag and research approval gate
still apply. No flags enabled by this code change. The optimizer separately
requires approved adjusted history and optional solver dependencies.
No automatic Telegram retry. A missing receipt remains unconfirmed.

The report omits unsupported fundamental metrics, benchmark performance and
predictions. Before release, review the rendered artifact visually, obtain
real approved research and independently source-verified historical returns,
verify actual Render memory, and test protected Telegram delivery.
