"""Optional Groq editorial reviewer for completed overnight index reports.

Groq selects from approved qualitative observations only. Python owns all figures,
ratings, ranking and risk levels; no AI text or numerical claims are trusted.
"""
import json
import logging
import os
import re

LOG = logging.getLogger(__name__)
ALLOWED = {
    "data_gaps": "Some companies have incomplete financial evidence; interpret the rankings cautiously.",
    "risk_plan": "Stops and targets are illustrative risk-management levels, not return forecasts.",
    "diversify": "Avoid concentrating exposure in one industry; review portfolio concentration.",
    "technical": "Technical scores describe chart conditions, not a probability of winning.",
    "fundamental": "Fundamental quality needs financial statement verification and current valuations.",
    "none": "No additional editorial observation.",
}


def groq_editorial(profile, trade_date, universe_size, validated, ranked_count):
    """Return safe, approved text; None when disabled, unavailable or invalid."""
    if os.environ.get("MSK_GROQ_REPORT_ENABLED", "0") != "1":
        return None
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("MSK_GROQ_REPORT_MODEL", "llama-3.3-70b-versatile")
    facts = {"profile": profile, "trade_date": str(trade_date),
             "index_companies": universe_size, "validated_fundamentals": validated,
             "ranked_candidates": ranked_count}
    try:
        from groq import Groq
        client = Groq(api_key=key, timeout=20, max_retries=1)
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=100,
            messages=[
                {"role": "system", "content": (
                    "You are an editorial classifier, not a financial-data provider. "
                    "Select up to 3 suitable observation codes from: "
                    + ", ".join(ALLOWED) + ". "
                    "Return ONLY a JSON object with a 'codes' array. "
                    "Do not create any facts, figures, securities or trading calls.")},
                {"role": "user", "content": json.dumps(facts)}
            ],
        )
        raw = (response.choices[0].message.content or "").strip()
        parsed = json.loads(raw)
        codes = parsed.get("codes")
        if not isinstance(codes, list) or len(codes) > 3 or any(
                not isinstance(c, str) or c not in ALLOWED for c in codes):
            return None
        codes = list(dict.fromkeys(codes))
        if not codes:
            return None
        return "🤖 Groq editorial notes (non-numeric):\\n" + "\\n".join(
            "• " + ALLOWED[c] for c in codes)
    except Exception as exc:
        LOG.warning("Groq editorial failed (%s); deterministic report retained", type(exc).__name__)
        return None
