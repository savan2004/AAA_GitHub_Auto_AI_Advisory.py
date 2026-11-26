# MSK Builder — optional external source trial

**Status: code in an isolated research experiment; NOT a customer data feed.**

## Decision

- **No mass copying or vendoring** of GitHub repositories into MSK. Instead use a narrow adapter over the public `finstack` Python package. Its code is MIT-licensed. Its upstream NSE/BSE/Screener/Moneycontrol/Tickertape market data and redistribution rights are **not** granted by MIT.
- **No new dependency in `requirements.txt`**, production environment, Telegram app or Render worker. The upstream library's import may wrap process-wide HTTP clients, so only import and invoke it in a separate, optional research job.
- Tej Bazaar and FilingForge are candidates for separately approved EOD and BSE-source probes; they are **not copied or connected** at this stage. Rust/TypeScript rewrites add no value to the existing MSK bot.

## Reusable interface

`msk_builder_external_source_probe.py` has two functions:

- `quarter_observation(symbol, date, provider=...)`: one exact dated quarterly revenue/profit observation, with source name, unit validation, identity check and explicit accounting-basis caveat.
- `eod_observation(symbol, date, provider=...)`: one exact dated closing-price observation with source name, no guessed dates or future/partial ticks.

An operator may run this **in a separate Python environment** after installing only `finstack==0.8.0` and the required provider extras, for example:

```bash
python -m msk_builder_external_source_probe quarter MCX 2026-09-30
python -m msk_builder_external_source_probe eod RELIANCE 2026-11-23
```

Provider errors produce a bounded code. Results retain `publication_status=WITHHELD` and `source_verification=UNVERIFIED_EXTERNAL_CANDIDATE`.

The adapter does **not** claim that fetching latest-quarter values is proof of issuer authentication. It is a faster **source discovery / cross-check**, NOT replacement of official signed/dated financial statements. A BSE PDF must still be authenticated and parsed using its own tested source contract.

## Integration boundaries

`External probe → candidate research observation → independent official-exchange validation → human-reviewed release → existing MSK Builder allocation/PDF`.

No path in this PR writes the approved registry, publishes research, sends Telegram messages, changes live source priority, or touches existing chart logic.

Until specific source permissions and primary-document reconciliations are established, **keep customer selection blocked**. To enable real-data publication later, write the verified issuer source adapter and release process on a separately reviewed branch.

## Third-party attribution

- Finstack, <https://github.com/omparekh54-lgtm/Finstack>, MIT, upstream revision from Nov 2026; the adapter only calls the public `fetch` API, **does not copy code**.
- FilingForge, <https://github.com/nandanpugalia/FilingForge>, MIT; candidate document extraction, not integrated.
- Tej Bazaar, <https://github.com/tejhq/tej-bazaar>, MIT code; candidate bhavcopy ingestion, not integrated. Exchange data use is separately licensed.
