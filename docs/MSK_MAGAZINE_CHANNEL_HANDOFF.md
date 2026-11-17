# MSK Monthly Magazine — AiAutoUpdate read-only Telegram handoff

**Scope:** MSK Bot repo ONLY. AiAutoUpdate repository and publisher are unchanged.
**Status:** Draft continuation of closed PRs #95/#103; tracked by issue #96 and Linear MSK-5. The feature is **disabled by default**. No PDF is approved, no channel access is verified, no live copy test was run.

## Architecture

1. AiAutoUpdate continues collecting research, performing evidence checks, and separately creating/publishing its approved monthly magazine PDF to its own Telegram channel.
2. A designated reviewer verifies the actual posted message is the intended PDF, checks the downloaded bytes and calculates SHA-256, reviews editorial/content accuracy and signs off on the issue.
3. Only after approval, a reviewed MSK-only release manifest is committed in `data/msk_magazines/YYYY-MM.json`. (Alternatively an audited deployment artifact process could be designed later.)
4. An independently authenticated source attachment loader must retrieve metadata and bytes for the exact source channel/message. Delivery validates channel, message ID, MIME type, file size and SHA-256 before any copy. Missing loader or failed retrieval/verification blocks delivery. The current command does not configure a loader and therefore remains blocked even if the feature flag is enabled.
5. MSK's new `/magazine` or gated 📘 Monthly Magazine button calls Telegram `copyMessage` from the channel message to the requesting user's private chat. `/magazine YYYY-MM` requests an archived approved edition. The handler NEVER reads AiAutoUpdate database or edits AiAutoUpdate code.
6. Copy errors fail closed, with no message-id retry loop or duplicate publication.

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

## Source verification contract (13 November 2026)

`copy_approved_magazine(..., source_attachment_loader=loader)` requires a trusted
read-only loader taking `(source_chat, source_message_id)` and returning
`(document_metadata, pdf_bytes)`. Metadata must include `source_chat`,
`source_message_id`, `mime_type`, and integer `file_size` from the actual source.
Do not synthesize metadata from release-manifest claims or use a local PDF as
proof of the remote attachment. Loader failures are sanitized and never retried
as a send. Disabled, missing, and unapproved editions do not invoke the loader.

This change enforces the existing verifier in the delivery path; it does not
implement or establish authenticated source retrieval. No live source adapter,
approved financial artifact, editorial acceptance or Telegram receipt is claimed.
The copy API also does not pin a document against a concurrent source-message
edit; end-to-end release acceptance must address that before enabling delivery.
Keep the feature OFF. Unit fixtures are synthetic and not publishable.
