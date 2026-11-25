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
- Integrate sector using sector_report_snapshot.py and its own date/content checks; integrate magazine in its own PR/workflow (#265/#279/#280), not through the stock report generator. This document records the extension design, not completed sector or magazine integration.
