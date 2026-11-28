# MSK-5 source receipt contract (main-based draft)

This branch adds a **structure and local-byte-consistency** check for read-only source receipts.

Each receipt must match a validated observation and include the exact issue, official URL, source filename and SHA-256; timezone-aware fetch/review timestamps no later than validation time and issue cutoff; HTTP 200; reviewer declaration; and cross-check notes.

**A passing structural check is not proof of authenticity.** The validator cannot authenticate the download, source URL, reviewer identity or reviewer work. The release manifest continues to set `source_authenticity_verified=false` and `publication_allowed=false`. Independent source acquisition and human evidence remain required.

Regression tests use synthetic fixtures only and are not financial data or research.


## Empty or candidate-only source packs
A pack with no `observations` rows is explicitly data-withheld. Candidate lists and dry-run metadata (including `publishable: false`) are not accepted as financial observations; each accepted figure still needs its own dated official-source bytes and matching receipt. Regression fixtures are synthetic and do not certify a real handoff.

## Edition-level read-only handoff manifest

In addition to one receipt per source file, an edition pack must contain a
`handoff_receipt` object with: matching `issue`; `source_project` equal to
`AiAutoUpdate`; non-empty `source_chat`; positive integer
`source_message_id`; timezone-aware `published_at` and
`approval_timestamp`; ISO `as_of`; non-empty `approved_by`;
`editorial_signoff=true`; source document IDs and/or safe HTTPS URLs;
`coverage` counts (`universe_size`, `verified_count`, `missing_count`);
and a `data_failures` list. Coverage counts must reconcile exactly.

Missing coverage and listed data failures remain visible as partial coverage;
they are never converted into verified observations. A structurally valid
handoff is still only a self-reported manifest: source-chat identity,
publisher approval, original document authenticity, and reviewer independence
must be checked separately. The magazine's authenticity, editorial approval,
and publication flags remain false until those independent checks are recorded.
