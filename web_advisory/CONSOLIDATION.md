# Website-only consolidation

Base: website branch web/msk-web-advisory-mvp-20261027 at d3dd3641c4566bc3b6fececfc14e4c8edaa1094d. Never merge this branch to Telegram main.

- #47: existing website baseline retained.
- #51, #52, #54, #55, #81, #82, #84: integrate website hunks and tests, preserving newer base changes.
- #85: report JS identical on base, expanded stylesheet and both report regression tests already present.
- #97: newer backend and tests already on base, original README identical.
- #55 root handoff document and trial-only CI workflow intentionally not copied; historical branch retained. Review the old trial workflow and existing website-specific CI before release.

No production deployment, no live data or Telegram sends. Browser tests, dedicated page behavior, backend checks and provider accuracy must pass before merge. No paid services.
