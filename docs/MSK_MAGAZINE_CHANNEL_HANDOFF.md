# MSK Monthly Magazine — AiAutoUpdate read-only Telegram handoff

**Scope:** MSK Bot repo ONLY. AiAutoUpdate repository and publisher are unchanged.
**Status:** Draft integration in PR #95. The feature is **disabled by default**. No PDF is approved, no channel access is verified, no live copy test was run.

## Architecture

1. AiAutoUpdate continues collecting research, performing evidence checks, and separately creating/publishing its approved monthly magazine PDF to its own Telegram channel.
2. A designated reviewer verifies the actual posted message is the intended PDF, checks the downloaded bytes and calculates SHA-256, reviews editorial/content accuracy and signs off on the issue.
3. Only after approval, a reviewed MSK-only release manifest is committed in `data/msk_magazines/YYYY-MM.json`. (Alternatively an audited deployment artifact process could be designed later.)
4. MSK's new `/magazine` or gated 📘 Monthly Magazine button calls Telegram `copyMessage` from the channel message to the requesting user's private chat. `/magazine YYYY-MM` requests an archived approved edition. The handler NEVER reads AiAutoUpdate database or edits AiAutoUpdate code.
5. Copy errors fail closed, with no message-id retry loop or duplicate publication.

## Required configuration on MSK Render ONLY
- `MSK_MAGAZINE_ENABLED=true` activates the button. Default unset/false keeps it hidden and the handler informational only.
- `MSK_MAGAZINE_SOURCE_CHAT` must be the verified Telegram channel ID (preferred `-100...`) or channel @username. MSK bot must have Telegram access to copy that channel's posts, and protected-content restrictions must permit it.
- Do not provide either bot token to the other project. Never use a direct connection to AiAutoUpdate's Neon database or Render environment.

Do not enable until an **actual, approved PDF** has been copied in staging with the correct source channel permissions and release manifest.

## Example manifest schema — DRAFT ONLY

```json
{
  "issue": "2026-11",
  "source_project": "AiAutoUpdate",
  "title": "MSK Monthly Market Outlook — November 2026",
  "approved": false,
  "approved_by": "Editorial reviewer after independent inspection",
  "published_at": "2026-11-02",
  "as_of": "2026-10-30",
  "source_chat": "@REPLACE_WITH_VERIFIED_CHANNEL",
  "source_message_id": 0,
  "mime_type": "application/pdf",
  "verified_pdf_sha256": "REPLACE_WITH_REAL_64_HEX_SHA256"
}
```

The sample is NOT publishable. Manifest values MUST be sourced from the real approved Telegram PDF, not guessed. The approval flag alone is insufficient evidence of financial accuracy or that a Telegram message contains the expected PDF.

## Release testing
- [ ] Confirm AiAutoUpdate really published a verified 16-page financial magazine PDF (not just data snapshots).
- [ ] Independently compare file hash against the source message's downloaded attachment.
- [ ] Confirm both Telegram bot memberships and copy permissions with a controlled private-chat staging test.
- [ ] Verify public channel/private channel source ID and channel history permissions.
- [ ] Validate code reports unavailable for no edition, unapproved draft, corrupt metadata, missing permissions and future date.
- [ ] Test current and archived edition commands, full existing stock-search and portfolio regression, and webhook.
- [ ] Verify Render health and collect copy-message acceptance evidence.
- [ ] Keep no credentials, market feeds or backups shared between projects.

**Important:** AiAutoUpdate currently has equity research snapshots and a channel delivery system, but source inspection has NOT verified a 16-page approved monthly magazine. This handoff does not create one. The first-Monday generation pipeline and editorial approval remain separate work.
