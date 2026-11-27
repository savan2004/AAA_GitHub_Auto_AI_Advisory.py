# Consolidated MSK Portfolio Doctor — safety-first candidate

Base: the previously deployed MSK production `main` commit `1c1af8c4107dcf0dce3a53cbfb798baacf478b8b`.

This **draft** introduces an isolated, privacy-safe incomplete-price diagnostic
encoder/decoder from draft #221. It is deliberately **not connected to the live
worker or Telegram handler** until its whole end-to-end data path can be
verified. The existing Portfolio Doctor entry, private sessions, CSV/XLSX
uploads, verified-price worker, PDFs and Telegram receipt handling in main.py
and portfolio_doctor_live.py are unchanged in this PR.

## Previous Doctor PR review

- #221: partial-price diagnostic is useful, but its old branch also changes the
  worker protocol and Telegram response; only the standalone validated helper
  is carried here. Complete report still fails closed on any missing price.
- #205: layout changes are not imported blindly over the current live
  four-page visual report. The original preview requires pixel/phone approval
  and page-extraction tests before merging any layout edits.
- #200: DLF publishing error belongs to the separate company-research
  PDF publishing script, not the live Portfolio Doctor; excluded here.
- #201: documentation-only audit brief, no runnable Doctor feature; excluded.
- #251/#206: old manual Builder state must stay retired; the current
  version of main already fixes this in #252.
- #219: database-backed manual portfolio idempotency is a distinct
  infrastructure concern; no PostgreSQL changes here.

## Non-negotiable acceptance

1. Doctor remains available in enabled private chats, independent of Builder.
2. Typed holdings, CSV and XLSX keep working; full PDF is refused when any
   verified completed daily close is missing.
3. No private holdings, customer IDs, prices or raw uploads in persisted logs,
   cached state or diagnostic child-process envelopes.
4. No simulated Telegram send counts as live customer delivery.
5. Run existing Doctor/Builder regression checks plus new strict diagnostic
   contract; require all green before consideration.

**Current scope: offline safety module, tests and documentation only.**
There is no Doctor runtime behavior change or automatic deployment.
