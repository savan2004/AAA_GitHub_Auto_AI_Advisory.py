# Portfolio Doctor — consolidated history and rewrite draft

Status: DRAFT / customer access remains OFF. Collected 18 November 2026 (IST).

Repository: savan2004/AAA_GitHub_Auto_AI_Advisory.py
Baseline: [e82120a1565d52099a2ff82c5f85392aa5fac81c](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/e82120a1565d52099a2ff82c5f85392aa5fac81c)
Scope: Portfolio Doctor only. No AiAutoUpdate, other menus/crons, legacy ledger restoration, public Telegram posts or trades.

## Collection coverage

- Examined all 176 repository PR records across three pages (third empty).
- Identified 10 direct Doctor PRs; excluded #184 because it changes only sector files despite referring to Doctor in its body.
- Read all 24 files in their union at the exact baseline (350151 source characters).
- Collected 88 PR commit entries (88 distinct SHAs), 57 changed-file revision entries, 10 conversation comments, and all available reviews (none submitted).
- The conversation comments are automated CodeRabbit notices, not completed code reviews or independent certification.
- Earliest report precursor: [19-Oct-2026 portfolio_report.py creation](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/8a586cfe952a9cbeda7f8cd025a1381d18f67b5b). Doctor-specific lineage starts with #172 on 17-Nov.
- Raw file contents and PR patches were collected for analysis. This draft indexes exact GitHub source revisions instead of republishing old customer-like fixture holdings or secrets.
- This is a repository-backed draft, maintained with the rewrite in one draft PR. Source-linked files remain authoritative.

## Established facts and corrected interpretation

1. Render recorded `MSK_PORTFOLIO_DOCTOR_DELIVERY state=SENT receipt_verified=true` on 17-Nov-2026 at 16:44:20.680 IST (11:14:20.680 UTC). This was a genuine Bot API document receipt.
2. The live revision then was `e84875a1f276f067e2849825ed5cd69f2e89d602` (#175). The log has no PDF message ID, page count or financial contents. It is not proof of the latest revision's delivery or price freshness.
3. The older worker accepted closes 0–3 calendar days old. Current code requires the latest completed NSE session. A successful old PDF and a later STALE_PRICES failure are therefore not contradictory.
4. General `resolve_symbol` is byte-for-byte unchanged between that successful version and the pre-rewrite baseline. Doctor uses its own holdings parser, not that company-name resolver.
5. #185 repaired a demonstrated privacy route: an expired/disabled Doctor row could be echoed and sent to generic Yahoo search. The user's original triggering text is unknown, so the specific historic warning cannot be attributed conclusively.
6. Prior baseline: #183/#185 passed 95 Doctor tests, real public 1/5-holding PDF checks and 17 post-merge workflows. This is controlled evidence, not a new live Telegram send.
7. Render deployment `dep-dbui7vfb9u1s738l1rdg` is LIVE on the baseline. Customer access was visually checked as 0 on 18-Nov. Both customer/test access were set to 0 during release. No test user ID appeared in the expanded service environment key inventory; no environment group is linked.
8. Historical Doctor ledger environment keys still exist, but no active Doctor handler imports the ledger/old runner. The separate legacy portfolio_data.json startup error is not this Doctor path and must not trigger restoration of holdings storage.

## PR chronology

| PR | Created (UTC) | Merged (UTC) | Scope/result |
|---|---|---|---|
| [#172](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/172) | 2026-11-17T07:14:37Z | 2026-11-17T09:10:40Z | Introduced private upload/subprocess flow, XLSX dependency and a separate metadata ledger; original worker produced files in a private temporary workspace. |
| [#173](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/173) | 2026-11-17T09:58:59Z | 2026-11-17T10:04:35Z | Added typed holdings, summary PDF fallback, safe failure codes and support for symbols containing X; reduced unnecessary per-holding research work. |
| [#174](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/174) | 2026-11-17T10:32:44Z | 2026-11-17T10:33:42Z | Replaced the active customer path with stdin/stdout, in-memory PDF bytes and no Doctor ledger or saved holdings. |
| [#175](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/175) | 2026-11-17T10:37:33Z | 2026-11-17T10:38:28Z | Removed customer-like example holdings from current fixtures and messages. |
| [#176](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/176) | 2026-11-17T11:34:56Z | 2026-11-17T11:46:47Z | Added optional advanced evidence/PDF modules and state isolation; enforced completed-session checks, retaining basic fallback. |
| [#177](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/177) | 2026-11-17T11:56:10Z | 2026-11-17T11:59:21Z | Matched financial statement/ratio scope; warned that broker corporate-action cost basis is unverified; tightened builder constraints. |
| [#178](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/178) | 2026-11-17T12:04:23Z | 2026-11-17T12:07:33Z | Added a separate optional private allocation builder and public issuer probe, OFF by default. |
| [#179](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/179) | 2026-11-17T20:10:09Z | 2026-11-17T20:23:49Z | Recovered stale Yahoo EOD using verified official current/prior NSE bhavcopy, overlap and continuity checks. |
| [#183](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/183) | 2026-11-17T21:26:06Z | 2026-11-17T22:08:32Z | Corrected Wilder RSI, preserved advanced/basic indicators, prevented XLSX truncation, added calendar/cache/audit/timeout protections and real public-worker checks. |
| [#185](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/185) | 2026-11-18T04:03:22Z | 2026-11-18T04:04:27Z | Blocked holdings-shaped text from disabled/expired Doctor sessions falling into general Yahoo stock search. |

## Every collected file at baseline

| File | Role | Blob SHA |
|---|---|---|
| [.github/workflows/msk-portfolio-doctor-advanced.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/.github/workflows/msk-portfolio-doctor-advanced.yml) | CI workflow | `20162599550ec2d7d84d63ad45d33916257a0559` |
| [.github/workflows/msk-portfolio-doctor-safety.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/.github/workflows/msk-portfolio-doctor-safety.yml) | CI workflow | `c221d83bca15938c152fef4dd802672ec6af6ba7` |
| [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/main.py) | Telegram entry, access flags, request state and delivery | `eb9b65d9b2df209c2e600628a259b3773cc032d0` |
| [portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_advanced.py) | Optional advanced report | `7ada3d223846b38196c7a4f38b332ed458e25113` |
| [portfolio_doctor_advanced_pdf.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_advanced_pdf.py) | Optional advanced report | `ed47d16d31c4ef41f3b815397b7265f8dc4c7c03` |
| [portfolio_doctor_bhavcopy.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_bhavcopy.py) | Official public daily-price recovery | `6b8750cc2af1a406e320304e798f872a0b99f085` |
| [portfolio_doctor_builder_private.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_builder_private.py) | Optional builder | `a05c3369f916090d772f78942b794c69162bce4e` |
| [portfolio_doctor_ledger.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_ledger.py) | Historical/inactive in current Doctor customer path | `2cb0df84781319d3217bdf611b7b4a93004d1842` |
| [portfolio_doctor_live.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_live.py) | Active in-memory Doctor parser, pricing, PDF and worker | `19dd4cd4a62d4bf213afba2e15f84dad54be7cc0` |
| [portfolio_doctor_runner.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_runner.py) | Historical/inactive in current Doctor customer path | `cb3c5ab03fe669e99f016119f16b1422262c9669` |
| [portfolio_report.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_report.py) | Historical/inactive in current Doctor customer path | `f37e4dccdd9f802d1ff703906d0e6577271cfa48` |
| [report_charts.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/report_charts.py) | Shared legacy chart support | `1af9c9a7938af804eb06078ae0458a8797657c41` |
| [requirements.txt](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/requirements.txt) | Pinned runtime dependencies | `b453e5110d6349b9abc231e4c96eb55912462598` |
| [scripts/msk_doctor_public_eod_probe.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/scripts/msk_doctor_public_eod_probe.py) | Public-only provider/integration evidence | `009a60646da0a9cee41b35070c8038187cc01a1a` |
| [scripts/msk_doctor_public_memory_probe.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/scripts/msk_doctor_public_memory_probe.py) | Public-only provider/integration evidence | `62f4f17ee31c42c271f469fc30a347a68b213b14` |
| [scripts/msk_doctor_public_provider_probe.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/scripts/msk_doctor_public_provider_probe.py) | Public-only provider/integration evidence | `7c714da824b7b52d534d2a4e9337c6e4686729a9` |
| [tests/test_menu_end_to_end.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_menu_end_to_end.py) | Regression/acceptance test | `b14234bd082f25cb01f4dc8e0b339a8f27869963` |
| [tests/test_portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_advanced.py) | Regression/acceptance test | `38b22febc5ecc441c367fa8391fdff22b7b018e9` |
| [tests/test_portfolio_doctor_bhavcopy.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_bhavcopy.py) | Regression/acceptance test | `d826fdd26bf7f18e8d19e33911f5360fbd677735` |
| [tests/test_portfolio_doctor_builder_private.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_builder_private.py) | Regression/acceptance test | `a52871c5051809b3fcea5f3dba3cc6fa7cbb6fad` |
| [tests/test_portfolio_doctor_deep_repair.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_deep_repair.py) | Regression/acceptance test | `774d9f943b127472ed6db016e39df4ef27ea03cb` |
| [tests/test_portfolio_doctor_private_integration.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_private_integration.py) | Regression/acceptance test | `acd87ae70648d8d9c22e5eb89c914813c79b8470` |
| [tests/test_portfolio_doctor_safety.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_safety.py) | Regression/acceptance test | `337fc776311739020b68eca4fe8f1554ffbd9d62` |
| [tests/test_portfolio_doctor_stale_recovery.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_stale_recovery.py) | Regression/acceptance test | `0cbd13b7ba0e3a3c5fa0e5c785f4d59782505a4f` |

## Current request flow

Private Telegram entry -> access/session check -> typed/CSV/XLSX parse -> completed-session history -> verified NSE bhavcopy/alternate recovery -> calculations -> basic in-memory PDF -> optional advanced report within a separate budget -> PDF acceptance -> one private Bot API send -> clear transient state.

The general company-name resolver is a separate path. Private holdings must never reach it.

## Root-cause register

| ID | Finding | Status before rewrite | Evidence |
|---|---|---|---|
| H01 | Old file-based runner/ledger and newer stateless path coexist | Inactive legacy files retained; requires explicit architecture boundary | #172–174; main.py imports/calls |
| H02 | Primary nonempty but stale data was treated as fetch success | Repaired via latest-session gate and official recovery | #179; public probes |
| H03 | RSI14 used 15 changes; advanced overwrote fixed indicators | Repaired | #183; arithmetic and advanced consistency tests |
| H04 | XLSX first-eight-row reading silently omitted extra holdings | Repaired | #183; bounded worksheet scan tests |
| H05 | yfinance ticker SQLite cache persisted symbol choices | Dummy caches and Python audit guard added | #183; actual cache/write tests |
| H06 | Expired/disabled holdings could reach general stock search | Repaired for recognised holdings shapes; review malformed rows too | #185; catch-all tests |
| H07 | Entire parent environment copied to worker except Telegram token | OPEN: unnecessary database/API credentials still inherited | run_isolated_memory env copy |
| H08 | Parser is spread over duplicated CSV/XLSX handling; error/type edge cases | OPEN: audit malformed CSV, boolean cells, sparse XML and header bounds | parse_holdings |
| H09 | Basic PDF silently slices every line to 107 characters | OPEN: financial text can be truncated rather than wrapped | build_pdf_bytes line helper |
| H10 | Recovery can accept a full raw NSE history for indicators without verified corporate-action adjustment | OPEN: make recovery/source policy explicit; do not invent split/bonus adjustments | recover_fresh_daily_for_doctor |
| H11 | Public input/freshness/financial transformations, provider I/O and worker orchestration live in one large module | OPEN: refactor into explicit pure stages with one compatibility facade | portfolio_doctor_live.py |
| H12 | Human-entered current quantity/cost basis cannot be verified against broker records from three fields | Fundamental input limitation; must remain disclosed, never auto-restated | #177 and current PDF disclosure |
| H13 | Latest production private Telegram delivery and exhaustive runtime privacy proof | OPEN release gates; cannot replace with mock/CI claims | no authorised test chat |

## Rewrite plan — not a release approval

1. Preserve working public APIs, 1–5 complete holdings, baseline arithmetic/indicators, strict latest completed session, verified bhavcopy and private delivery behavior.
2. Rewrite the core as small explicit stages: safe input decoding, validation, provider/recovery policy, calculations, PDF rendering, and isolated worker environment. Keep optional advanced work independently bounded.
3. Remove sensitive application credentials from worker environment; retain only required runtime/network configuration and explicit Doctor flags.
4. Use one strict parser path for typed/CSV/XLSX; reject booleans/nonfinite/nonpositive values and malformed/oversized archives with safe codes.
5. Replace silent PDF text slicing with measured wrapping and pagination. No partial totals or missing holding may be labelled complete.
6. Make adjustment/source assumptions explicit. Preserve official NSE/Yahoo overlap; do not accept unchecked alternate adjustment conventions or silently change customer quantities/cost basis.
7. Extend regression tests to reproduce each open defect before certifying the rewrite. Run real public-data worker/PDF probes in addition to synthetic privacy-safe tests.
8. Keep all work in this one draft PR until its validation is complete. No customer flag enable or legacy ledger activation. Live delivery remains a separate gate requiring an authorised private chat.

## PR evidence and all changed-file revisions

### PR #172: fix(portfolio doctor): install XLSX parser safely; keep customer button disabled

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/172

Introduced private upload/subprocess flow, XLSX dependency and a separate metadata ledger; original worker produced files in a private temporary workspace.

Changed files (9):

- [.github/workflows/msk-portfolio-doctor-safety.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/.github/workflows/msk-portfolio-doctor-safety.yml) — added; +28/-0; blob `e3cff10d74e09373a034ba0242408937e552fde4`.
- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/main.py) — modified; +119/-1; blob `bdb9181b9c31cb3617433969ad62250e02242dc8`.
- [portfolio_doctor_ledger.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/portfolio_doctor_ledger.py) — added; +116/-0; blob `2cb0df84781319d3217bdf611b7b4a93004d1842`.
- [portfolio_doctor_runner.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/portfolio_doctor_runner.py) — added; +113/-0; blob `54a57e28161ce82c6fa21b6c25c28a242543fbf4`.
- [portfolio_report.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/portfolio_report.py) — modified; +38/-10; blob `787c3cc559acee7af76fdfe7033255c67f672bd0`.
- [report_charts.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/report_charts.py) — modified; +4/-3; blob `1af9c9a7938af804eb06078ae0458a8797657c41`.
- [requirements.txt](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/requirements.txt) — modified; +1/-0; blob `b453e5110d6349b9abc231e4c96eb55912462598`.
- [tests/test_menu_end_to_end.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/tests/test_menu_end_to_end.py) — modified; +6/-1; blob `396bf3d3f61ae7024533eef365ae02e9d629e008`.
- [tests/test_portfolio_doctor_safety.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/b4a262cc67383564dad82678f18b43eee4eac841/tests/test_portfolio_doctor_safety.py) — added; +137/-0; blob `1fe5a59d187ecb1acb43baaee0da955981d2bdda`.

Commits (1; messages are descriptions, not proof of successful execution):

- [527fb1b081cee147273c8f47a3156397a8b61b31](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/527fb1b081cee147273c8f47a3156397a8b61b31) — 2026-11-17T09:09:31Z — Rebase PR #172 Portfolio Doctor customer release on deployed MSK menu (#171); preserve unrelated production fixes.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/172).

### PR #173: Fix Portfolio Doctor typed holdings and valid summary PDF fallback

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/173

Added typed holdings, summary PDF fallback, safe failure codes and support for symbols containing X; reduced unnecessary per-holding research work.

Changed files (4):

- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/ffd99fb6d27cf0cce1c35f291d6fb8f1843daa6d/main.py) — modified; +56/-16; blob `66aa67f137c301c60b52addedb00961367acc8f9`.
- [portfolio_doctor_runner.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/ffd99fb6d27cf0cce1c35f291d6fb8f1843daa6d/portfolio_doctor_runner.py) — modified; +45/-12; blob `cb3c5ab03fe669e99f016119f16b1422262c9669`.
- [portfolio_report.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/ffd99fb6d27cf0cce1c35f291d6fb8f1843daa6d/portfolio_report.py) — modified; +20/-8; blob `f37e4dccdd9f802d1ff703906d0e6577271cfa48`.
- [tests/test_portfolio_doctor_safety.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/ffd99fb6d27cf0cce1c35f291d6fb8f1843daa6d/tests/test_portfolio_doctor_safety.py) — modified; +133/-0; blob `744efb39fade8f7b5f3c7bf55492d790fcd9cf59`.

Commits (15; messages are descriptions, not proof of successful execution):

- [a74f3e5d2dde050be7e6faf4832b002539a24337](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/a74f3e5d2dde050be7e6faf4832b002539a24337) — 2026-11-17T09:56:02Z — fix(doctor): allow verified-price summary when optional stock PDFs fail.
- [b888789fafdb226004ed07748145e77954092ad9](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/b888789fafdb226004ed07748145e77954092ad9) — 2026-11-17T09:57:24Z — fix(doctor): accept typed holdings through existing private CSV/PDF path.
- [0ed4dce19b08d7673e185dc2d199f6a4fb0ccbbe](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/0ed4dce19b08d7673e185dc2d199f6a4fb0ccbbe) — 2026-11-17T09:57:56Z — fix(doctor): record privacy-safe worker failure codes.
- [0007b2012a7f130d5143d014924a2cf170b6b9a6](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/0007b2012a7f130d5143d014924a2cf170b6b9a6) — 2026-11-17T09:58:05Z — fix(doctor): log safe worker failure code, not private holdings.
- [a2c198ca8cc645ec104cff7b4174f0fb68dd2596](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/a2c198ca8cc645ec104cff7b4174f0fb68dd2596) — 2026-11-17T09:58:42Z — test(doctor): typed holdings, private routing, fresh-only partial summary.
- [570c4cc3c08b625d6da06b5aa3a325ba0acdbbfe](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/570c4cc3c08b625d6da06b5aa3a325ba0acdbbfe) — 2026-11-17T09:59:44Z — fix(doctor): avoid process-global tempdir leak in worker verification.
- [a2c41cfa62c56a2954fe519eb18fec1284c4e43a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/a2c41cfa62c56a2954fe519eb18fec1284c4e43a) — 2026-11-17T10:00:14Z — fix(doctor): render introduction line break correctly.
- [f1c56c978a047017b34bfe7af0e37bb7167b6f1e](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/f1c56c978a047017b34bfe7af0e37bb7167b6f1e) — 2026-11-17T10:00:39Z — fix(doctor): explain verified-price and timeout blocks to customers.
- [f87170a8902c9df7f951c2a4e355d8b7956beecb](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/f87170a8902c9df7f951c2a4e355d8b7956beecb) — 2026-11-17T10:00:41Z — fix(doctor): label hard timeout in private error receipt.
- [890003fcf336bda26b8989e5c923dd66a4700499](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/890003fcf336bda26b8989e5c923dd66a4700499) — 2026-11-17T10:01:26Z — fix(doctor): keep X in NSE ticker names when parsing text positions.
- [4a9e74f1dbbc4cb80d4e76c91448c6e795d31deb](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/4a9e74f1dbbc4cb80d4e76c91448c6e795d31deb) — 2026-11-17T10:01:39Z — test(doctor): guard NSE tickers containing X in typed positions.
- [56e70674ae7f815430c4c7f7c131d3ec9722c2f0](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/56e70674ae7f815430c4c7f7c131d3ec9722c2f0) — 2026-11-17T10:02:34Z — test(doctor): include typing annotations in isolated parser test.
- [f5a98bad6b6f72d032b89a881e06de0cf726d4cf](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/f5a98bad6b6f72d032b89a881e06de0cf726d4cf) — 2026-11-17T10:03:05Z — perf(doctor): skip unused per-stock research PDF rendering.
- [45d281a19438954956282b7f094a5c33ee3f2f5d](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/45d281a19438954956282b7f094a5c33ee3f2f5d) — 2026-11-17T10:03:08Z — perf(doctor): request summary-only bounded portfolio report.
- [cde8caf463b4047b4d4f5eda5b2a0b8991ddf4a0](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/cde8caf463b4047b4d4f5eda5b2a0b8991ddf4a0) — 2026-11-17T10:03:30Z — fix(doctor): accurately label summary-only PDF without fake attachments.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/173).

### PR #174: Fix Portfolio Doctor live PDF: no saved customer holdings or ledger

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/174

Replaced the active customer path with stdin/stdout, in-memory PDF bytes and no Doctor ledger or saved holdings.

Changed files (4):

- [.github/workflows/msk-portfolio-doctor-safety.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/19d76a773733a0ee493e1b0f260506f4e6dd7e12/.github/workflows/msk-portfolio-doctor-safety.yml) — modified; +8/-9; blob `8bb15e4e69d4ddb1946ad42225424e64a60e12a4`.
- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/19d76a773733a0ee493e1b0f260506f4e6dd7e12/main.py) — modified; +66/-69; blob `de045d0e3177f8ef87234b215427d84048dbb5f3`.
- [portfolio_doctor_live.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/19d76a773733a0ee493e1b0f260506f4e6dd7e12/portfolio_doctor_live.py) — added; +302/-0; blob `5671581e7b15bfb20bbb984e23746da31ea2adc3`.
- [tests/test_portfolio_doctor_safety.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/19d76a773733a0ee493e1b0f260506f4e6dd7e12/tests/test_portfolio_doctor_safety.py) — modified; +111/-260; blob `6d226c3fc7eab03652b8955434ffd994fa66b529`.

Commits (4; messages are descriptions, not proof of successful execution):

- [2e9d3877e68870e4a4ba29e6be51fcb0e517dd6b](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/2e9d3877e68870e4a4ba29e6be51fcb0e517dd6b) — 2026-11-17T10:30:51Z — feat(doctor): in-memory private holdings PDF with fresh prices and no database.
- [cf67f2f34766327b8215220deba81c4d73b94218](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/cf67f2f34766327b8215220deba81c4d73b94218) — 2026-11-17T10:31:31Z — fix(doctor): remove persistence and produce one-time RAM-only PDF per request.
- [461ef98ca839976f856f7be9ddbf077a849bf99f](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/461ef98ca839976f856f7be9ddbf077a849bf99f) — 2026-11-17T10:32:19Z — test(doctor): verify stateless reports, price gates and typed positions.
- [46c40ccdd863aa9e2eec08d8a0856e173e689f1a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/46c40ccdd863aa9e2eec08d8a0856e173e689f1a) — 2026-11-17T10:32:33Z — ci(doctor): require RAM-only report and privacy regression tests.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/174).

### PR #175: Privacy cleanup: replace customer example holdings in Doctor code/tests

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/175

Removed customer-like example holdings from current fixtures and messages.

Changed files (2):

- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e84875a1f276f067e2849825ed5cd69f2e89d602/main.py) — modified; +3/-3; blob `7355c3e6bad0e6cac458c4422ce5f65dc98bf613`.
- [tests/test_portfolio_doctor_safety.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e84875a1f276f067e2849825ed5cd69f2e89d602/tests/test_portfolio_doctor_safety.py) — modified; +24/-24; blob `c2e328913a2d3a3bddf4406b25abd88c45736a99`.

Commits (2; messages are descriptions, not proof of successful execution):

- [c7ec1487a0ba94a99f3d4f9787ee6f1fc0b66fe5](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/c7ec1487a0ba94a99f3d4f9787ee6f1fc0b66fe5) — 2026-11-17T10:37:07Z — privacy(doctor): replace customer-like test inputs with fictional fixtures.
- [cdc6ee25dca4c6055490db2175b6f1d37e49acb5](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/cdc6ee25dca4c6055490db2175b6f1d37e49acb5) — 2026-11-17T10:37:24Z — privacy(doctor): generic public examples not user-submitted portfolio values.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/175).

### PR #176: Portfolio Doctor Advanced: evidence-gated fundamentals, sectors, technicals, cash-aware builder, RAM-only PDF

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/176

Added optional advanced evidence/PDF modules and state isolation; enforced completed-session checks, retaining basic fallback.

Changed files (9):

- [.github/workflows/msk-portfolio-doctor-advanced.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/.github/workflows/msk-portfolio-doctor-advanced.yml) — added; +34/-0; blob `05df66affeb2d4e719b06690bc53a9791668b2b8`.
- [.github/workflows/msk-portfolio-doctor-safety.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/.github/workflows/msk-portfolio-doctor-safety.yml) — modified; +1/-1; blob `cb743607b562da21d443f7295cd45f3d06381236`.
- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/main.py) — modified; +24/-9; blob `08043d9ebae27b464f3ef9268a044eb281e1003d`.
- [portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/portfolio_doctor_advanced.py) — added; +423/-0; blob `32b593c31e629136a5f21da154129150ccbc9247`.
- [portfolio_doctor_advanced_pdf.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/portfolio_doctor_advanced_pdf.py) — added; +285/-0; blob `c4e49ebe20e9e02d42050d93325902bda4d6cab6`.
- [portfolio_doctor_live.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/portfolio_doctor_live.py) — modified; +51/-10; blob `3764f307245c87c1531e46a7fb6cbe0f9fecceb3`.
- [tests/test_menu_end_to_end.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/tests/test_menu_end_to_end.py) — modified; +2/-2; blob `b14234bd082f25cb01f4dc8e0b339a8f27869963`.
- [tests/test_portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/tests/test_portfolio_doctor_advanced.py) — added; +332/-0; blob `171c0a94a6c018215b5f5e92e1902f43aff82cc9`.
- [tests/test_portfolio_doctor_private_integration.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/76f92151dfdb23b300c682b432f8db10c79a2f24/tests/test_portfolio_doctor_private_integration.py) — added; +149/-0; blob `900381814a37eb7137b62bca1adc6f54d3505f8d`.

Commits (18; messages are descriptions, not proof of successful execution):

- [6d137ebc89fda14200ca61aaf54d815b1bdeaa3c](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/6d137ebc89fda14200ca61aaf54d815b1bdeaa3c) — 2026-11-17T11:30:42Z — feat(doctor): evidence-gated portfolio analytics, fundamentals and scenario builder without persistence.
- [8725afa175ac0de572344a8a969aa54703c25c5c](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/8725afa175ac0de572344a8a969aa54703c25c5c) — 2026-11-17T11:32:02Z — feat(doctor): readable six-page advanced report with charts in RAM and evidence disclosures.
- [1c216f8e21bfc65b6c0605eb8193d8f00c8b2b2b](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/1c216f8e21bfc65b6c0605eb8193d8f00c8b2b2b) — 2026-11-17T11:32:28Z — feat(doctor): optional advanced memory-only report behind OFF-by-default flag, preserve basic fallback.
- [6f8d4db52edfb52ebcf3647c21c004a6c1ead7bc](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/6f8d4db52edfb52ebcf3647c21c004a6c1ead7bc) — 2026-11-17T11:33:00Z — fix(doctor): use actual validated support/resistance fields from research_engine.
- [4dbe6650dcbe7500a687bfefda3c15bfcb611c2d](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/4dbe6650dcbe7500a687bfefda3c15bfcb611c2d) — 2026-11-17T11:34:20Z — test(doctor): financial provenance, allocations, opinions, builder, privacy and six-page PDF gates.
- [817e50f6c9560a37153cadf156738f555803778f](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/817e50f6c9560a37153cadf156738f555803778f) — 2026-11-17T11:34:40Z — ci(doctor): require advanced research accuracy and privacy tests, preserve base suite.
- [21bedeaf51ecf8b6b811bf87cb796cec419d2373](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/21bedeaf51ecf8b6b811bf87cb796cec419d2373) — 2026-11-17T11:37:02Z — fix(doctor): align latest benchmark dates and validate existing holdings during added-capital scenarios.
- [0e7459df872735a2898341b5fba7bb8d4485a5a8](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/0e7459df872735a2898341b5fba7bb8d4485a5a8) — 2026-11-17T11:37:31Z — test(doctor): added-capital existing sector and stale benchmark alignment contracts.
- [ee5e6015778437f866a2fbb94c04db0e12d40b18](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/ee5e6015778437f866a2fbb94c04db0e12d40b18) — 2026-11-17T11:38:17Z — fix(doctor): expire waiting sessions, clear malformed request state, block in-flight holdings leaking to generic lookup.
- [d4deb44da089e540ecb7ede29931ae03bd33653a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/d4deb44da089e540ecb7ede29931ae03bd33653a) — 2026-11-17T11:39:08Z — fix(doctor): clear request waiting state when all worker slots are busy.
- [355c42a9da689c45fc0bfca9bc7fd3e9e8934708](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/355c42a9da689c45fc0bfca9bc7fd3e9e8934708) — 2026-11-17T11:39:55Z — test(doctor): actual isolated Telegram handler sends, concurrency, failed-state cleanup and no cross-user leak.
- [2c375c592350d53ab764cc0b164444c6b357947a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/2c375c592350d53ab764cc0b164444c6b357947a) — 2026-11-17T11:40:08Z — ci(doctor): prove actual Telegram handler isolation, PDF sends, timeout and request-state cleanup.
- [24aafc049c325b88cbb39eb53219759ce1a98322](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/24aafc049c325b88cbb39eb53219759ce1a98322) — 2026-11-17T11:41:07Z — test(menu): reflect Doctor's expiring dict state and monotonic clock in isolated menu fixture.
- [260db20e44f8ed33ea7b22eb5ff6136123c3e3b3](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/260db20e44f8ed33ea7b22eb5ff6136123c3e3b3) — 2026-11-17T11:43:02Z — fix(doctor): reject incomplete daily candles and align optional Nifty benchmark with verified session.
- [8106d4ce8508f365decceb203dcc27a4aa0954b7](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/8106d4ce8508f365decceb203dcc27a4aa0954b7) — 2026-11-17T11:43:28Z — fix(doctor): allow validated multi-page PDF receipts only under advanced feature flag.
- [772a8681c9046011af428775da88883d5b6eb9e4](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/772a8681c9046011af428775da88883d5b6eb9e4) — 2026-11-17T11:43:50Z — test(doctor): prove completed-session cutoff and advanced PDF receipt acceptance.
- [dc86c871b373dbbda6f58256b71b398875c9f8d0](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/dc86c871b373dbbda6f58256b71b398875c9f8d0) — 2026-11-17T11:45:02Z — fix(doctor): reject absent price frames before importing optional freshness engine.
- [a7fa2e41c08fa48424795f9d52a94d8166337372](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/a7fa2e41c08fa48424795f9d52a94d8166337372) — 2026-11-17T11:45:06Z — ci(doctor): install required requests for shared completed-session date checks.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/176).

### PR #177: Portfolio Doctor: same-page financial evidence, corporate action warnings, strict builder

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/177

Matched financial statement/ratio scope; warned that broker corporate-action cost basis is unverified; tightened builder constraints.

Changed files (3):

- [portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/f9d52e8f5cd36def737e67ad507047edbf58ccb5/portfolio_doctor_advanced.py) — modified; +130/-25; blob `32ac1e46cff9b0d58f7ebcda48f9fe6d7fa1bb4f`.
- [portfolio_doctor_advanced_pdf.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/f9d52e8f5cd36def737e67ad507047edbf58ccb5/portfolio_doctor_advanced_pdf.py) — modified; +13/-2; blob `6e92bc292c3add8793948681988cca52c4368a76`.
- [tests/test_portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/f9d52e8f5cd36def737e67ad507047edbf58ccb5/tests/test_portfolio_doctor_advanced.py) — modified; +81/-1; blob `38b22febc5ecc441c367fa8391fdff22b7b018e9`.

Commits (7; messages are descriptions, not proof of successful execution):

- [a0c4780d308abf9ea349476135199ff95836bc5f](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/a0c4780d308abf9ea349476135199ff95836bc5f) — 2026-11-17T11:53:54Z — fix(doctor): single-page financial provenance, matched consolidated/standalone scope, period-safe metrics.
- [e1b8fd945c27f6b445dd104550f925236d19ac41](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/e1b8fd945c27f6b445dd104550f925236d19ac41) — 2026-11-17T11:54:42Z — fix(doctor): flag unverified corporate actions, prevent unknown sectors and overlarge builder universes.
- [730930d0d786036d755afc0970c8f50b6a41310a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/730930d0d786036d755afc0970c8f50b6a41310a) — 2026-11-17T11:55:09Z — fix(doctor): show per-holding sourced statement scope and audit timestamp; disclose corporate-action cost-basis risk.
- [cdcfd5fefecb3b9a038efd2660eea8e991cdabb1](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/cdcfd5fefecb3b9a038efd2660eea8e991cdabb1) — 2026-11-17T11:55:57Z — test(doctor): prove statement/ratio same-page provenance, corporate-action non-certification, strict builder caps.
- [2f42f3ba0d4adaeebd199cb99cda3765195ad8e4](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/2f42f3ba0d4adaeebd199cb99cda3765195ad8e4) — 2026-11-17T11:57:26Z — fix(doctor): isolate same-page ratios parser from unrelated financial module dependencies.
- [e0f37512704a20a7d701e90aa6294c32b32bbc71](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/e0f37512704a20a7d701e90aa6294c32b32bbc71) — 2026-11-17T11:57:49Z — fix(doctor): parse decimal top ratios correctly from public HTML.
- [8c80b41c4335e2837eabe64ec81fb1563535a289](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/8c80b41c4335e2837eabe64ec81fb1563535a289) — 2026-11-17T11:58:02Z — fix(doctor): show reporting-scope verification status clearly in uppercase.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/177).

### PR #178: Add optional private Portfolio Doctor builder with verified prices and no storage

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/178

Added a separate optional private allocation builder and public issuer probe, OFF by default.

Changed files (5):

- [.github/workflows/msk-portfolio-doctor-advanced.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/91188d3bfea2a6033af4b83156865fcbd532be09/.github/workflows/msk-portfolio-doctor-advanced.yml) — modified; +20/-1; blob `445ec21070629617b4561aae536760ba8521002a`.
- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/91188d3bfea2a6033af4b83156865fcbd532be09/main.py) — modified; +72/-0; blob `6a089ced5240181b677f3e498c187f8a79fc80b3`.
- [portfolio_doctor_builder_private.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/91188d3bfea2a6033af4b83156865fcbd532be09/portfolio_doctor_builder_private.py) — added; +279/-0; blob `a05c3369f916090d772f78942b794c69162bce4e`.
- [scripts/msk_doctor_public_provider_probe.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/91188d3bfea2a6033af4b83156865fcbd532be09/scripts/msk_doctor_public_provider_probe.py) — added; +17/-0; blob `7c714da824b7b52d534d2a4e9337c6e4686729a9`.
- [tests/test_portfolio_doctor_builder_private.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/91188d3bfea2a6033af4b83156865fcbd532be09/tests/test_portfolio_doctor_builder_private.py) — added; +230/-0; blob `a52871c5051809b3fcea5f3dba3cc6fa7cbb6fad`.

Commits (7; messages are descriptions, not proof of successful execution):

- [92198cd575363aabbc497b63fad597e7d2ea5918](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/92198cd575363aabbc497b63fad597e7d2ea5918) — 2026-11-17T12:01:42Z — feat(doctor): standalone private one-message scenario builder with price-verified whole-share PDF.
- [d1e153042b38efd944a5c6dceae68b9e176e04d6](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/d1e153042b38efd944a5c6dceae68b9e176e04d6) — 2026-11-17T12:02:22Z — feat(doctor): optional private /doctor_builder command, separate no-storage worker and safe delivery.
- [3ac4cd08efb7f0aeba127672799e22470362b132](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/3ac4cd08efb7f0aeba127672799e22470362b132) — 2026-11-17T12:03:31Z — test(doctor): one-shot builder inputs, fresh prices, cash rounding, private handler send and privacy.
- [6a08e9742b7636ab18f0f796b9c612ea08614548](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/6a08e9742b7636ab18f0f796b9c612ea08614548) — 2026-11-17T12:03:54Z — test(doctor): use Python 3.11-safe parenthesized context managers.
- [27cc8f4beec53fc023c5b71bcf1f983dbb5266b8](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/27cc8f4beec53fc023c5b71bcf1f983dbb5266b8) — 2026-11-17T12:04:09Z — ci(doctor): gate optional builder release on full private PDF and whole-share acceptance.
- [0ce165a70bc5c52b5dcd0e24bef7b520c6dcc77c](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/0ce165a70bc5c52b5dcd0e24bef7b520c6dcc77c) — 2026-11-17T12:06:04Z — diagnostics(doctor): no-customer public issuer-source smoke for provider accuracy.
- [4c563677f6b1c85c667efd4c0725dd057bd6e047](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/4c563677f6b1c85c667efd4c0725dd057bd6e047) — 2026-11-17T12:06:19Z — ci(doctor): add non-gating real Screener provider availability check, no customer inputs.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/178).

### PR #179: Fix Portfolio Doctor STALE_PRICES: recover completed OHLCV from alternate sources

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/179

Recovered stale Yahoo EOD using verified official current/prior NSE bhavcopy, overlap and continuity checks.

Changed files (9):

- [.github/workflows/msk-portfolio-doctor-advanced.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/.github/workflows/msk-portfolio-doctor-advanced.yml) — modified; +23/-1; blob `883fb2f3bf7817d4e4c5485cfc7758d9ae6cd68a`.
- [.github/workflows/msk-portfolio-doctor-safety.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/.github/workflows/msk-portfolio-doctor-safety.yml) — modified; +8/-1; blob `fff7a2a513537f6c6d7e29f09057118405ae4464`.
- [portfolio_doctor_advanced_pdf.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/portfolio_doctor_advanced_pdf.py) — modified; +4/-2; blob `ed47d16d31c4ef41f3b815397b7265f8dc4c7c03`.
- [portfolio_doctor_bhavcopy.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/portfolio_doctor_bhavcopy.py) — added; +178/-0; blob `51c36e3df5b0f47df288ae17165e5f5261bccf66`.
- [portfolio_doctor_live.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/portfolio_doctor_live.py) — modified; +94/-3; blob `f3427dde96ed4022574379acd5156fc7f60a4cec`.
- [scripts/msk_doctor_public_eod_probe.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/scripts/msk_doctor_public_eod_probe.py) — added; +69/-0; blob `009a60646da0a9cee41b35070c8038187cc01a1a`.
- [tests/test_portfolio_doctor_bhavcopy.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/tests/test_portfolio_doctor_bhavcopy.py) — added; +135/-0; blob `d826fdd26bf7f18e8d19e33911f5360fbd677735`.
- [tests/test_portfolio_doctor_safety.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/tests/test_portfolio_doctor_safety.py) — modified; +6/-2; blob `337fc776311739020b68eca4fe8f1554ffbd9d62`.
- [tests/test_portfolio_doctor_stale_recovery.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/7c8b9ba79dd24cc043ddf7767a0f8d62f4406dff/tests/test_portfolio_doctor_stale_recovery.py) — added; +167/-0; blob `0cbd13b7ba0e3a3c5fa0e5c785f4d59782505a4f`.

Commits (22; messages are descriptions, not proof of successful execution):

- [060b59d5a0f4fe31fda14dbb9d53a95fad315d77](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/060b59d5a0f4fe31fda14dbb9d53a95fad315d77) — 2026-11-17T20:07:48Z — fix(doctor): retry stale primary OHLCV via independent completed-session providers without storing client data.
- [550ff9d684c814c2ca860be419d86892c96d6b3a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/550ff9d684c814c2ca860be419d86892c96d6b3a) — 2026-11-17T20:08:14Z — test(doctor): keep stale-price unit tests fully offline when provider fallback is available.
- [251dcdca61ba81ed97b5fabdd525b082828cd844](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/251dcdca61ba81ed97b5fabdd525b082828cd844) — 2026-11-17T20:08:50Z — test(doctor): reject stale data unless fallback has full fresh OHLCV; verify privacy and totals.
- [3b289bd334ff69430df814f3b6a30de8957a6378](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/3b289bd334ff69430df814f3b6a30de8957a6378) — 2026-11-17T20:09:51Z — ci(doctor): require full stale-EOD recovery and privacy regressions before release.
- [1152ef2a7f25dce8c1bca7ce1d696e7a9de14109](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/1152ef2a7f25dce8c1bca7ce1d696e7a9de14109) — 2026-11-17T20:09:54Z — ci(doctor): require full stale-EOD recovery and privacy regressions before release.
- [39bbd054eab7e2504ac6b1e0a3d5c798cee88bfd](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/39bbd054eab7e2504ac6b1e0a3d5c798cee88bfd) — 2026-11-17T20:10:57Z — fix(doctor): exactly 100% single-holding weight after verified historical recovery.
- [9c29cf01197cee9f11697b733628090de2b86a96](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/9c29cf01197cee9f11697b733628090de2b86a96) — 2026-11-17T20:12:07Z — diagnostics(doctor): test public INFY completed EOD freshness without customer data.
- [135397d6d9e35b83a29011f5630db1e585e85088](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/135397d6d9e35b83a29011f5630db1e585e85088) — 2026-11-17T20:12:19Z — ci(doctor): nonblocking real public EOD fallback probe without customer input.
- [10c1b8d7a72825c690c60645722d54de4867beb5](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/10c1b8d7a72825c690c60645722d54de4867beb5) — 2026-11-17T20:13:47Z — security(doctor): use existing read-only market mode without Telegram token in ephemeral worker.
- [c7d10058f5015c38a3a234de22d2ff360f35c02b](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/c7d10058f5015c38a3a234de22d2ff360f35c02b) — 2026-11-17T20:14:00Z — ci(doctor): run public EOD source probe using existing read-only config, no bot token.
- [fb7df7b563028819db53620522371b2d86ef3427](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/fb7df7b563028819db53620522371b2d86ef3427) — 2026-11-17T20:14:11Z — test(doctor): assert ephemeral market worker receives no Telegram credentials.
- [b9d7d2819d87a13d64b14c7711a07875b8d49922](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/b9d7d2819d87a13d64b14c7711a07875b8d49922) — 2026-11-17T20:15:44Z — diagnostics(doctor): report only public provider dates and row counts when EOD recovery fails.
- [82a900854811e48f996d323e09a2e12cd26f8b8c](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/82a900854811e48f996d323e09a2e12cd26f8b8c) — 2026-11-17T20:18:56Z — fix(doctor): verify NSE EOD bhavcopy against prior-day historical OHLC before repairing stale session.
- [7d75f611674a65aa73139ccd191ff4de67f04e18](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/7d75f611674a65aa73139ccd191ff4de67f04e18) — 2026-11-17T20:19:23Z — fix(doctor): use official NSE latest-day bhavcopy after strict overlap check before alternate history.
- [cf59e971bb4d9212ff1c72636cbc4ca1d180a71e](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/cf59e971bb4d9212ff1c72636cbc4ca1d180a71e) — 2026-11-17T20:20:05Z — test(doctor): isolate official bhavcopy fallback and prove customer totals are never partial.
- [69ca96a0d7929c2ebfc0944f250f1395bd575249](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/69ca96a0d7929c2ebfc0944f250f1395bd575249) — 2026-11-17T20:20:08Z — test(doctor): isolate official bhavcopy fallback and prove customer totals are never partial.
- [722c6746290c5b71bc2da67685cc8a1a02a5f50f](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/722c6746290c5b71bc2da67685cc8a1a02a5f50f) — 2026-11-17T20:20:42Z — test(doctor): official day and prior OHLC continuity required for NSE bhavcopy recovery.
- [e3d037c449adbf03df724aacce032800230db4ce](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/e3d037c449adbf03df724aacce032800230db4ce) — 2026-11-17T20:20:57Z — test(doctor): synthetic bhavcopy meets minimum official-file size gate.
- [c23c880101f9d7b519314a977834a4ce9f70e7b2](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/c23c880101f9d7b519314a977834a4ce9f70e7b2) — 2026-11-17T20:21:12Z — ci(doctor): gate official NSE bhavcopy integration on overlap tests and privacy.
- [ca68d715e67f88753bcd237373dba774b4d0704a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/ca68d715e67f88753bcd237373dba774b4d0704a) — 2026-11-17T20:21:16Z — ci(doctor): gate official NSE bhavcopy integration on overlap tests and privacy.
- [6397577a74b773d54819f195f74a0119404c4368](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/6397577a74b773d54819f195f74a0119404c4368) — 2026-11-17T20:21:34Z — diagnostics(doctor): verify official NSE prior-day-aligned bhavcopy rescue on public INFY.
- [46c3e43b7971dc1eb1cb1ffd6e25ec15473a8c62](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/46c3e43b7971dc1eb1cb1ffd6e25ec15473a8c62) — 2026-11-17T20:22:54Z — fix(doctor): disclose official NSE EOD recovery provenance in advanced PDF.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/179).

### PR #183: Doctor: repair financial indicators, private persistence and timeout recovery

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/183

Corrected Wilder RSI, preserved advanced/basic indicators, prevented XLSX truncation, added calendar/cache/audit/timeout protections and real public-worker checks.

Changed files (9):

- [.github/workflows/msk-portfolio-doctor-advanced.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/.github/workflows/msk-portfolio-doctor-advanced.yml) — modified; +10/-4; blob `20162599550ec2d7d84d63ad45d33916257a0559`.
- [.github/workflows/msk-portfolio-doctor-safety.yml](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/.github/workflows/msk-portfolio-doctor-safety.yml) — modified; +4/-0; blob `c221d83bca15938c152fef4dd802672ec6af6ba7`.
- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/main.py) — modified; +5/-4; blob `9ad60e3eaa6d15be56b376a4aa9069713a9c1305`.
- [portfolio_doctor_advanced.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/portfolio_doctor_advanced.py) — modified; +3/-2; blob `7ada3d223846b38196c7a4f38b332ed458e25113`.
- [portfolio_doctor_bhavcopy.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/portfolio_doctor_bhavcopy.py) — modified; +11/-5; blob `6b8750cc2af1a406e320304e798f872a0b99f085`.
- [portfolio_doctor_live.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/portfolio_doctor_live.py) — modified; +171/-29; blob `b4156bb552b9bb85e9310333d49058f847f065c9`.
- [scripts/msk_doctor_public_memory_probe.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/scripts/msk_doctor_public_memory_probe.py) — added; +76/-0; blob `62f4f17ee31c42c271f469fc30a347a68b213b14`.
- [tests/test_portfolio_doctor_deep_repair.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/tests/test_portfolio_doctor_deep_repair.py) — added; +266/-0; blob `774d9f943b127472ed6db016e39df4ef27ea03cb`.
- [tests/test_portfolio_doctor_private_integration.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/c2ba3903a27a3d039977467cebc3184e6a882126/tests/test_portfolio_doctor_private_integration.py) — modified; +54/-1; blob `b11d7fb15e54d3b498a8ba27be62ad68d698ab91`.

Commits (11; messages are descriptions, not proof of successful execution):

- [851f0629a98ed1d9548790f5ef20072be0b6de35](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/851f0629a98ed1d9548790f5ef20072be0b6de35) — 2026-11-17T21:25:52Z — Fix Doctor Wilder RSI14 and reject truncated XLSX holdings; restrict recovery providers.
- [17267c308fb46c6f8c04d00f73acfd5d4e7e6657](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/17267c308fb46c6f8c04d00f73acfd5d4e7e6657) — 2026-11-17T21:31:09Z — Keep verified Doctor RSI and EMA values in advanced reports.
- [eff1102878195ed523817015ab0d8ce7efd99030](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/eff1102878195ed523817015ab0d8ce7efd99030) — 2026-11-17T21:32:01Z — Disable yfinance ticker and cookie disk caches in private Doctor workers.
- [9f75016ccf801b88e3f2459a2748a9fea21de481](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/9f75016ccf801b88e3f2459a2748a9fea21de481) — 2026-11-17T21:33:48Z — Use reviewed NSE 2026 holidays and special sessions for Doctor freshness and recovery.
- [bef70883720321ece8559e8c7f726a24cc19e39a](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/bef70883720321ece8559e8c7f726a24cc19e39a) — 2026-11-17T21:34:33Z — Require real Doctor worker PDF and no-cache probes for one and five synthetic holdings.
- [38127a7c26f1eb037b39513b6b0078d26db8f007](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/38127a7c26f1eb037b39513b6b0078d26db8f007) — 2026-11-17T21:36:46Z — Cross-check real PDF valuations against NSE closes and disclose cost-basis assumptions.
- [564a326fefaeedb018ed03ad53ce9b0133a1581d](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/564a326fefaeedb018ed03ad53ce9b0133a1581d) — 2026-11-17T21:38:44Z — Protect private Doctor PDF delivery, bound send timeout and close report streams.
- [c19f71e6f5f4a678fa05fd249892ee0b03d2e053](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/c19f71e6f5f4a678fa05fd249892ee0b03d2e053) — 2026-11-17T21:39:42Z — Deny audited filesystem writes and database opens before Doctor reads private input.
- [73c9f3949ce963ed7761ef37c306ac68f7ed2216](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/73c9f3949ce963ed7761ef37c306ac68f7ed2216) — 2026-11-17T21:41:23Z — Bound optional Doctor research so timeouts fall back to the verified basic PDF.
- [b139f3fc425e9ab3c8d21078be2963688ca83239](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/b139f3fc425e9ab3c8d21078be2963688ca83239) — 2026-11-17T21:42:46Z — Verify actual worker termination, next-request recovery and basic PDF after optional timeout.
- [14e08fe871cf5e1907d86742dd2c8b83316a7eeb](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/14e08fe871cf5e1907d86742dd2c8b83316a7eeb) — 2026-11-17T22:06:55Z — Merge current main into Doctor repair while preserving the separate sector-report fixes.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/183).

### PR #185: Doctor: stop expired or disabled holdings from leaking into stock search

Source: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/185

Blocked holdings-shaped text from disabled/expired Doctor sessions falling into general Yahoo stock search.

Changed files (3):

- [main.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/main.py) — modified; +7/-0; blob `eb9b65d9b2df209c2e600628a259b3773cc032d0`.
- [portfolio_doctor_live.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/portfolio_doctor_live.py) — modified; +29/-0; blob `19dd4cd4a62d4bf213afba2e15f84dad54be7cc0`.
- [tests/test_portfolio_doctor_private_integration.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/e82120a1565d52099a2ff82c5f85392aa5fac81c/tests/test_portfolio_doctor_private_integration.py) — modified; +35/-0; blob `acd87ae70648d8d9c22e5eb89c914813c79b8470`.

Commits (1; messages are descriptions, not proof of successful execution):

- [e3ba6a9eaf95360c4b5e6f3bf5ecb33e0ceeba4d](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/e3ba6a9eaf95360c4b5e6f3bf5ecb33e0ceeba4d) — 2026-11-18T04:03:08Z — Prevent expired or disabled Doctor holdings from reaching generic stock-provider search.

Discussion/reviews: 1 automated conversation notice; 0 submitted reviews. [Conversation source](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/185).

## Validation ledger

Baseline controlled tests: PASS (95 Doctor tests); baseline real public-data 1/5-holding PDF: PASS; old 17-Nov receipt: PASS for old version only; rewritten code: IMPLEMENTED and controlled validation PASS; rewrite CI: PASS on 17a86aec06cf6b63e58acddda20723a91aa8084f; rewrite deployment: NOT DEPLOYED; latest private Telegram delivery: NOT TESTED; customer release: NO-GO.

This document must be updated with exact rewrite commit, CI URLs, observed failures/fixes and deployment evidence. Do not carry historical PASS labels forward without rerunning the relevant gates.

## Rewrite implementation, first candidate

- `portfolio_doctor_input.py`: one local typed/CSV/XLSX parser and private-row classifier; rejects booleans, malformed CSV, macros, encrypted/duplicate/traversal ZIP entries, nonfinite/invalid numbers and over-limit sheets. Adds explicit NSE: canonicalisation without treating BSE symbols as NSE.
- `portfolio_doctor_calculations.py`: pure Wilder RSI/EMA and Decimal-based position arithmetic/portfolio weights, with compatible public record fields.
- `portfolio_doctor_report.py`: pure RAM-only basic PDF; independently recomputes displayed monetary values with Decimal, wraps text to measured page width and retains all holdings/disclosures.
- `portfolio_doctor_live.py`: compatibility facade + provider/worker orchestration; allows only runtime/public-network settings in child environment. No Telegram, database, ledger, AI or SMTP credentials are inherited. Raw NSE full-history fallback is removed because split/bonus adjustment equivalence is not established; official overlap-verified NSE bhavcopy remains available, with direct Yahoo history retry.
- Dedicated rewrite tests cover parser rejection, malformed private-row classification, credential boundary, adjustment-source policy, decimal subtraction and measured PDF bounds. All original suites and real public-market worker probes remain required.

H07–H10 have candidate repairs; they are not marked PASS until CI results are inspected. Live customer access is unchanged (disabled).

## Rewrite validation and resulting file map

Validated implementation commit: [17a86aec06cf6b63e58acddda20723a91aa8084f](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/commit/17a86aec06cf6b63e58acddda20723a91aa8084f).

Single work PR: [#186](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/pull/186), still DRAFT. The collection-only draft was committed first at `8c4d1c490f6ec8f5c8c566d3dfbc588b4cfd61c3`; code was rewritten only after that record was created.

| Gate | Verified result | Proof / limit |
|---|---|---|
| Existing Doctor regression suites | PASS, 95 tests | Old financial, privacy, handler, bhavcopy, optional builder and timeout safeguards retained |
| Dedicated rewrite suite | PASS, 9 tests | Booleans/malformed archives/CSV, canonical symbols, malformed private rows, worker credentials, alternate adjustment policy, Decimal arithmetic and actual PDF text bounds |
| Total Doctor tests | PASS, 104 | CI advanced-evidence job |
| Actual public-data worker: 1 holding | PASS: 1 page, 1.22s, cache_files=0 | Synthetic holdings, public INFY prices, actual isolated worker |
| Actual public-data worker: 5 holdings | PASS: 2 pages, 1.46s, cache_files=0 | All positions and financial totals checked against public official closes |
| Latest completed-session filtering | PASS: expected/got 2026-11-17 | Provider included 18-Nov forming bar; it was excluded |
| GitHub workflows | PASS: 5 applicable workflows | Sixth workflow explicitly skipped opt-in live integration; no live claim |
| Live Telegram send of rewrite | NOT TESTED | No authorised test chat established |
| Deployment / customer availability | NOT DEPLOYED / DISABLED | Baseline e82120a1565d52099a2ff82c5f85392aa5fac81c remains the last verified LIVE code |

CI: https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/actions/runs/44237128664

New source files:
- [portfolio_doctor_input.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/17a86aec06cf6b63e58acddda20723a91aa8084f/portfolio_doctor_input.py)
- [portfolio_doctor_calculations.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/17a86aec06cf6b63e58acddda20723a91aa8084f/portfolio_doctor_calculations.py)
- [portfolio_doctor_report.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/17a86aec06cf6b63e58acddda20723a91aa8084f/portfolio_doctor_report.py)
- [tests/test_portfolio_doctor_rewrite.py](https://github.com/savan2004/AAA_GitHub_Auto_AI_Advisory.py/blob/17a86aec06cf6b63e58acddda20723a91aa8084f/tests/test_portfolio_doctor_rewrite.py)

The rewritten facade shrank from 606 to 420 lines while moving pure parsing, indicators/valuation and rendering into explicit modules. Existing public APIs remain available to the handler and optional builder. Historical ledger/runner/report files are retained for history and unrelated compatibility, not activated.

H07–H10 candidate repairs now pass controlled regression and real public-worker validation. H11 separation is implemented. H12 broker cost-basis verification is still an input limitation, H13 production delivery/privacy acceptance is still OPEN. These are release gates, not reasons to relabel old CI or delivery evidence as current production proof.

**Customer release: NO-GO.** This draft is the consolidated implementation/evidence record requested by the owner. It does not enable customer/test flags or claim a 9/10 product.

## Deep repair and unused-code audit — 18 November 2026

Owner requested a deeper review of the rewrite, removal of unused code and repair of remaining defects.

Repository-wide collection covered all 243 current text files, including 172 Python files. Before deletion, references were traced across code, workflows, tests and documentation.

| Removed current file/helper | Reference finding | Preservation |
|---|---|---|
| portfolio_doctor_runner.py | No active caller; one stale CI py_compile entry; imports old portfolio_report | Historical blob links above retained |
| portfolio_doctor_ledger.py | No active import/caller; only historical documentation | Historical blob links above retained |
| portfolio_report.py | Imported only by the unused old runner; no live bot caller | October precursor and all PR revisions retained |
| previous_weekday in portfolio_doctor_bhavcopy.py | Definition only; current recovery uses exchange-aware previous_doctor_session | Removal does not change calendar policy |
| csv/re/zipfile imports and duplicated size/stock constants in live facade | No remaining use; common limits now imported from parser | Public MAX_BYTES/MAX_STOCKS aliases preserved |

Shared report_charts.py stays: active sector/report modules still import it. No legacy portfolio, database migration, other menu or cron is altered.

Newly confirmed repair points:
- Expired/disabled private sessions relied on identifying holdings-shaped text. Arbitrary malformed private input could still fall through; session intent now blocks generic routing before content classification.
- Typed CSV with a header plus five positions had six lines and was rejected before the parser; the parser now owns the 1–5 holding limit.
- A missing/nonfinite latest close or conflicting duplicate daily rows could be normalised away at a shared boundary. The Doctor boundary now rejects incomplete/conflicting completed evidence.
- Basic PDF acceptance did not independently reject mixed price dates, invalid/future dates or nonfinite/out-of-range indicators. These now fail closed.
- Numeric text could lose precision on float conversion; unsupported precision is now rejected instead of silently rounding user cost/quantity.
- Incomplete/malformed private rows need local classification too; index-name queries such as NIFTY 50 remain available.

Additional validation introduced: regression for every above defect; AST/reference checks for removed modules; actual public-data CSV and XLSX worker PDFs; two genuinely concurrent disposable workers with distinct synthetic holdings and cross-request leakage checks. Private live Telegram delivery remains unverified.

Candidate status: implemented in this draft; new validation pending. The earlier 104-test PASS applies to the earlier candidate only.


## 18-Nov-2026: numbered input and exact NSE/BSE selection

Owner requested identical handling for `SBIN 100 840\nBhel 150 150` and numbered `1. SBIN 100 840\n2. BHEL 150 150`. List markers are removed before strict three-column parsing and private-input classification; case is normalized. Numeric BSE scrip codes are preserved. Optional `.NS`, `.BO`, `NSE:` and `BSE:` forms are supported; conflicting exchange markers and duplicate canonical symbols are rejected.

NSE remains preferred. An absent NSE history may use BSE only after exact public provider search identifies one matching BSE equity and no exact NSE ticker; stale existing NSE histories are never silently replaced with BSE. Explicit BSE goes directly to BSE and bypasses NSE bhavcopy/recovery. BSE chart identity must match exact ticker, exchange and INR currency, with >=20 valid unique completed daily OHLCV bars. No fuzzy company-name guess or NSE/BSE history splicing. BSE reports use the basic renderer to avoid NSE-only optional research enrichment. The existing conservative expected-session gate still applies; unavailable/ambiguous evidence blocks the entire report.

Validation added: both requested real public-stock input forms, case/list-marker parsing, invalid/duplicate input, BSE identity/currency guards, NSE priority, explicit BSE valuation/PDF. The real-worker probe includes SBIN/BHEL plain and numbered requests and an explicit SBIN.BO request. CI and deployment evidence will be recorded on the PR. Admin-only access configuration is not broadened.
