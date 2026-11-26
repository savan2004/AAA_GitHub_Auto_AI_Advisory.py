# MSK Research Cloud — private Drive layout

Account: aiautofintech@gmail.com. Existing Drive connector access in ChatGPT is NOT a Render credential.

| Archive | Folder ID | Policy |
|---|---|---|
| Equity research (direct child PDFs only) | 1ue3u-6-CCpromy4hHX24g_m0uHWDkY6x | 500 managed PDFs FIFO by generation time; 24h and same completed-session reuse |
| Sector reports | 1kuPEh2-Ysx9CoMjRHBgWVlO2LYjoGuYS | Reuse only current verified session; retention not yet approved |
| Magazine | 1nOiraLejVbO7kEyrAtAnKcMdIwucfOVL | Separate monthly issues/revisions; publish only approved edition; no equity FIFO |

Do not use the stock report archive validator for sector or magazine PDFs. Their existing dedicated gates and workflows must remain independent. Never archive private portfolio/customer data. No public sharing. The stock FIFO can delete only tagged PDFs that are direct children of the pinned equity root, never subfolder contents.

## Activation (not yet performed)
- Use OAuth credentials belonging to the verified owner, in Render secret environment only. The OAuth app must have authorized access to the pinned folder; drive.file scope requires explicit folder authorization for that app. Do not copy ChatGPT tokens or put secrets in GitHub.
- Configure MSK_DRIVE_CLIENT_ID, MSK_DRIVE_CLIENT_SECRET, MSK_DRIVE_REFRESH_TOKEN, MSK_RESEARCH_DRIVE_FOLDER_ID. Keep MSK_RESEARCH_DRIVE_ENABLED off until the read/upload/readback test succeeds.
- Require exactly one archive writer across processes and jobs. The in-process lock is not distributed arbitration. Do not run a second daily worker while bot writes are enabled until shared ownership is implemented.
- FIFO deletion is permanent and only after verified new upload. A transient 501-file state is possible; ambiguous cleanup blocks new uploads and requires reconciliation. No manual bulk deletion.
- Test without real deletes first. Verify actual free quota and per-report runtime; avoid paid runner minutes. No automatic customer posting from archive warm-up.
- Sector archive code now uses sector_report_snapshot.py's own completed-session coverage and PDF checks. Configure MSK_SECTOR_DRIVE_ENABLED=1 and MSK_SECTOR_DRIVE_FOLDER_ID only after acceptance. Sector files are never deleted by the stock FIFO; uploads stop at 500 sector files pending a separate retention decision.
- Stock warmup code runs in the existing webhook process, weekdays 17:30–19:00 IST, only with MSK_RESEARCH_DAILY_ENABLED=1 and an explicit comma-separated MSK_RESEARCH_DAILY_SYMBOLS list (maximum 20). Begin with a small verified list and measure Render load. Free Render sleep/restarts may prevent full completion; no additional keep-alive service is assumed. No new Telegram send path.
- Magazine private draft archival is developed separately in #280, with its own folder, manifest SHA and 16-page gate. It never approves, publishes or posts drafts. Existing December preview schedule changes in #280 still need owner review. Approved-edition retrieval/posting remains a separate release step.
- Existing Neon snapshot workflows are retained until cloud acceptance; measure duplicate work before retiring any old scheduler. No hosted CI rerun or production activation is performed by this change.
