# MSK Codex automation — disabled by default

The GitHub scheduler polls every 5 minutes (GitHub scheduling may be delayed or skipped).
It does **not** run paid Codex every 5 minutes. At most one scheduled Codex
attempt is eligible per UTC day, at 00:00–00:04 UTC, when all gates are enabled.
Manual workflow_dispatch is for repository administrators; use carefully.

## Activate after review only

1. Merge the controller PR after workflow review. Until merged, schedule does not run.
2. Create GitHub **Actions secret** `OPENAI_API_KEY` (API billing separate from ChatGPT).
3. Set GitHub **Actions repository variables** `MSK_CODEX_ENABLED=true`
   and `MSK_CODEX_COST_APPROVED=true` only after you expressly approve spending.
4. Create narrowly scoped GitHub issues with both labels
   `msk-codex-approved` and `msk-codex-ready`. Only label reviewed tasks.
5. Add a low hard-limit / budget alert in the OpenAI API dashboard.
6. Ensure GitHub Actions can create PRs, otherwise the publish job will fail.
7. Revoke activation by setting either variable to false or deleting the API key.

## Security and operations

- Global concurrency group prevents two MSK workflow runs from overlapping.
- Implement job timeout 30 minutes, publish timeout 10 minutes.
- Codex gets read-only GitHub permission; PR publisher gets write but no API secret.
- Output is a draft PR, never automatic merge or deployment.
- GitHub Actions schedule has no hard execution-time guarantee.
- The one-per-day gate is based on a UTC time slot, *not* a server-enforced token
  budget. Manual dispatch bypasses that schedule slot and can incur extra costs.
- Bot-generated patches need code review, complete CI, and credential scanning
  before merge. Existing Render auto-deploy settings must be checked before merging
  application PRs; merging code can indirectly deploy to production.
- Please review whether your GitHub Actions plan and API provider support the expected usage.
