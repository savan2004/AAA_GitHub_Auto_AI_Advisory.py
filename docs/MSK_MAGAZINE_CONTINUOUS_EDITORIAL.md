# MSK Monthly Outlook — Continuous editorial rotation design

Status: **prototype only**, in central review PR #95. Nothing runs in the live Telegram bot.
Scope: one rotating "MSK Advanced Strategy Lab" story per monthly volume, not a finished full-magazine generator.

## Purpose
- Preserve a fixed professional 16-page magazine design while choosing new research subjects each month.
- Give each published volume a unique topic+editorial-angle key.
- Rotate editorial formats after every configurable **12–15 published volumes** (default 15).
- Never reset the unique-key ledger silently when content ideas run out.
- Detect exact and close-to-identical article drafts where previous content is available.
- Keep all drafts **unapproved** until independent factual, editorial, suitability and risk review.

## API and example
`plan_volume("2026-11", published_history, refresh_every=15)` returns issue,
first-Monday target, article key, selected topic, angle, editorial format and
whether a new style cycle begins.

`draft_article(plan, source_facts, published_history)` creates an unapproved,
source-labelled outline. Each claim must have a valid date and HTTPS source URL.
URL shape checks **are not fact checking**; every factual assertion requires
independent verification before release.

This module deliberately has no side effects, API keys, provider calls, auto-post
or production database operations. It does not modify PR #93 or its first-weekday
schedule. First-Monday scheduling is **not deployed**.

## Important: persistence is required
The caller MUST load **all published issues** from a durable, MSK-only ledger;
it MUST atomically save newly approved editions and their content hashes.
GitHub Actions artifacts and Render's ephemeral local files are not durable
publication history. Until a persistent ledger and proper publication gate
are connected, do not advertise automatic no-repeat delivery.

## "Forever" means indefinite operation with human-quality gates
The initial 32 reviewed themes x 8 angles offer 256 distinct article keys,
or over 21 years at one monthly article each. This is a finite seed library,
not 256 guaranteed quality articles. Novel data, new concepts, actual
historical market examples and revised reporting requirements must be supplied
continuously. When exhausted, the engine refuses to repeat an article and
requires more reviewed research topics.

## Future integration with #93
1. Complete shared source-date/quality collectors, first-Monday event calendar,
   approved PDF cover, article rendering and publication ledger.
2. Choose topic using full, durable publication history; never treat a failed
   draft as a published volume.
3. Require independently validated market claims; check semantic similarity
   with previous editions and produce draft artefacts.
4. Add special-event research as a separate event-ID-ledger pipeline with
   deduplication; avoid retroactively altering published monthly editions.
5. Test across multi-year mock histories, real data outages, duplicate triggers,
   corrections and scheduled action retries.
6. Publish only after edition-specific quality and permission checks.

## Safety
No invented numerical accuracy, fabricated options chain delta, guessed CVD
from candle volume, auto-execution or claims of trading profitability.
