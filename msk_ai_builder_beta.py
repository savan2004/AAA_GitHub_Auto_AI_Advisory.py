"""Private fictional preview, using the actual whole-share allocator."""
from datetime import date
from decimal import Decimal
from msk_ai_builder_allocation import ApprovedCandidate, allocate_portfolio
from msk_ai_builder_demo import synthetic_companies, score
from msk_ai_builder_infographic import allocation_pdf

FIXTURE_SESSION = date(2026, 11, 20)


def beta_model(profile):
    candidates = [
        ApprovedCandidate(f"DEMO{c.number:02d}", f"IN{c.number:010d}",
                          c.sector, FIXTURE_SESSION, Decimal(100+c.number*25),
                          Decimal(score(c)))
        for c in synthetic_companies() if score(c)>=75
    ]
    plan = allocate_portfolio(profile, candidates, expected_session=FIXTURE_SESSION)
    return plan, {"publication_status": "FICTIONAL_ONLY",
                  "companies": [{"symbol": c.symbol} for c in candidates]}


def beta_pdf(profile):
    plan, artifact = beta_model(profile)
    return allocation_pdf(plan, artifact, fictional=True)
