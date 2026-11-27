# MSK AI connector V3 acceptance candidate

One network implementation remains in msk_ai_connector_v2.py; v3 adds shared output and market-context modules, routes the legacy wrapper through the same spending/fallback policy, preserves existing interfaces, and replaces false readiness with UNKNOWN. News is for display only and timestamps remain unverified. The market-context compatibility force flag does not bypass the quote cache.

Offline tests cover legacy contracts, explicit incomplete completions, Gemini thought isolation, credential echoes, assistant/system number contamination, null facts, quote provenance and wrapper delegation. Numeric matching is not semantic verification. No provider model IDs changed; live model availability, quota, independently verified field-level financial evidence and private production acceptance are still required. No autonomous publishing or deployment authorized by this change. No new dependencies.

Run: python -m unittest discover -s tests -p 'test_msk_ai_connector_v*.py' -v

24 November 2026: official provider documentation lists the configured Groq and Gemini model IDs (https://console.groq.com/docs/models and https://ai.google.dev/gemini-api/docs/generate-content/computer-use?authuser=0). The PR-only metadata job uses existing GitHub secrets, fixed official GET endpoints and no inference, no redirects, no raw output or secret logs. Metadata success alone proves neither free quota nor production Render credentials nor private Telegram output; missing keys are not proof that Render is unconfigured. No paid model requests are authorized.
