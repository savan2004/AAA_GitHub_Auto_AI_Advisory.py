# MSK Research V3 — Business Model Before Market Timing

**Priority:** Explain how the company generates revenue and profits **before** discussing chart oscillators. All reported facts need an official dated source; every projected number must carry an explicit assumption. This PR is a safe independent module, not a live-deployment approval.

## Verified pilot: RELIANCE, FY2025-26

Official sources:
- [Reliance FY2025-26 Annual Report](https://www.ril.com/ar2025-26/index.html)
- [Reliance Management Discussion and Analysis](https://www.ril.com/ar2025-26/mda.html)

Reported FY2026 segment values (INR crore, **not additive across segments to consolidated totals**):

| Segment | Revenue | EBITDA | Main earnings driver |
| --- | ---: | ---: | --- |
| Oil to Chemicals | 662,401 | 60,546 | Refining/petrochemical margins and product mix |
| Retail | 371,085 | 27,034 | Consumer demand and store productivity |
| Digital Services | 176,164 | 76,560 | Subscriber/ARPU and broadband monetisation |
| Oil and Gas | 23,861 | 19,050 | Gas production and realisations |
| Media and Entertainment | 40,682 | 5,842 | Advertising, subscriptions and content economics |

Figures are from the annual report. New Energy is a strategic business but is **not independently modelled here** absent verified standalone segment reporting. Media growth cannot be blindly extrapolated because the reporting history includes structural changes.

## Projection engine

1. Verify a complete company business profile, official source URL and FY2026 segment revenue/EBITDA.
2. List segment revenue mechanisms, competitive economics, growth drivers, threat factors and capital allocation checks.
3. Create three **hypothetical** scenarios with transparent per-segment assumptions for annual revenue growth and EBITDA margin. Each year:
   - Revenue(FY+n) = FY2026 revenue × (1 + assumed growth)^n.
   - EBITDA(FY+n) = Revenue(FY+n) × assumed EBITDA margin.
4. Mark results **ILLUSTRATIVE / NOT COMPANY GUIDANCE**; state the FY2026 official reference and date.
5. **Never sum operating-business metrics into group forecasts without intercompany eliminations**, or present these segment projections as forecasts of group PAT/EPS, DCF, FCF or share-price targets.
6. If no official verified business profile exists, clearly mark **Business Model & Forecast data not verified**, instead of invented management claims.

## Next incremental coding milestones

- File and validate business-model profiles for each daily research-list company: business lines, economic moat supported by operating evidence, five-year mix trend, unit economics, customer demand/competition, pricing power, project capex and monitoring KPIs.
- Add last-quarter results and management guidance separately from scenario assumptions; catch merger, split, demerger and reporting restatements.
- For consolidated financial forecasts: introduce taxes, capex, depreciation, minority interest, net debt and working-capital assumptions; reconcile historical cash flow first. Separate financial-sector models for lenders.
- Add **sensitivity tables, thesis invalidation, milestones, source timestamps and forecast accuracy/back-testing**, without promising returns or guaranteeing one PDF on a failed-data day.

## Safe testing

```bash
TELEGRAM_TOKEN=123:offline-test-only python -m unittest discover -s tests -p test_business_model_forecast.py -v
```

The tests use invented fixture prices **only to check PDF layout**; they are not marketed as real Reliance trading prices. No paid AI API call, no Telegram send, no auto-deploy.
