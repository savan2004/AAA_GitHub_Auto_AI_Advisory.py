# MSK-5 source receipt contract (read-only handoff)

The magazine generator accepts source observations only when source bytes, official URL, date, excerpt and numeric value match. The new `source_receipts` list is an additional **structure/byte-consistency check**, not independent proof of authenticity.

Each receipt must match an accepted observation and contain:
- `issue` — exact `YYYY-MM`
- `source_url`, `source_file`, `source_sha256` — exact observation match
- `fetched_at_utc` — timezone-aware timestamp no later than the issue cutoff
- `http_status` — integer 200
- `reviewer_verified` — true
- `reviewer` — named reviewer
- `reviewed_at_utc` — timezone-aware, not before retrieval and no later than cutoff
- `review_notes` — at least 20 characters describing cross-check

Example **schema only** (not a real receipt; never use as financial evidence):
```json
{
  "issue": "2026-11",
  "source_receipts": [
    {
      "issue": "2026-11",
      "source_url": "https://example.org/official-release.pdf",
      "source_file": "release.pdf",
      "source_sha256": "replace-with-real-64-character-sha256",
      "fetched_at_utc": "2026-11-23T08:00:00Z",
      "http_status": 200,
      "reviewer_verified": true,
      "reviewer": "actual reviewer",
      "reviewed_at_utc": "2026-11-23T08:15:00Z",
      "review_notes": "Describe independent original-source and figure cross-check."
    }
  ]
}
```

A passing schema check is not independent authentication: the fields can be self-asserted and reviewer identity is not authenticated by code. `source_authenticity_verified` remains **false**. Release still requires actual source-byte retrieval, independent reviewer proof, exact issue/coverage check, editorial and visual QA, archive, Telegram staging, and owner approval.
