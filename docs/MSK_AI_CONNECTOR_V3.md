# MSK AI connector V3 acceptance candidate

One network implementation remains in msk_ai_connector_v2.py; v3 adds shared output and market-context modules, routes the legacy wrapper through the same spending/fallback policy, preserves existing interfaces, and replaces false readiness with UNKNOWN. News is for display only and timestamps remain unverified. The market-context compatibility force flag does not bypass the quote cache.

Offline tests cover legacy contracts, explicit incomplete completions, Gemini thought isolation, credential echoes, assistant/system number contamination, null facts, quote provenance and wrapper delegation. Numeric matching is not semantic verification. No provider model IDs changed; live model availability, quota, independently verified field-level financial evidence and private production acceptance are still required. No autonomous publishing or deployment authorized by this change. No new dependencies.

Run: python -m unittest discover -s tests -p 'test_msk_ai_connector_v*.py' -v
