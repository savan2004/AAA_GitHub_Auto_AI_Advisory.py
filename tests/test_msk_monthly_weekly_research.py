"""Offline contracts for the monthly AI + weekly technical research cycle."""
from datetime import date, datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock

from msk_monthly_weekly_research import (
    CycleUnavailable, groq_one_monthly_call, make_monthly,
    make_weekly, validate_ai_ideas,
)
NOW=datetime(2026,11,24,6,0,tzinfo=timezone.utc)
PROFILES={"large":"conservative","mid":"moderate","small":"aggressive"}


def univ(profile):
    groups={"conservative":"L","moderate":"M","aggressive":"S"}
    tag=groups[profile]
    return {"symbols":[f"{tag}{i:03}" for i in range(100)],
            "industries":{f"{tag}{i:03}":f"Industry {i%5}" for i in range(100)},
            "source":"Official NSE constituent CSV","asof":"24 Nov 2026 11:30 IST"}


def proposals(us):
    return {tier:list(u["symbols"][:10]) for tier,u in us.items()}


def fundamentals(symbol, industry, profile, today):
    return {"fund_status":"VALIDATED","fund_score":56,
            "fund_source":"Secondary public source",
            "quarter":"2026-09-30"}


def tech(symbol, bars, profile, now):
    return {"sym":symbol, "tech_score":61,
            "signal":"WAIT","asof":"2026-11-23"}


class LeanCycleTests(unittest.TestCase):
    def test_monthly_one_ai_call_and_existing_fundamental_reuse(self):
        ai=Mock(side_effect=proposals)
        f=Mock(side_effect=fundamentals)
        output=make_monthly(NOW,universe_provider=univ,
                            ai_provider=ai,fundamental_provider=f)
        self.assertEqual(ai.call_count,1)
        self.assertEqual(f.call_count,30)
        self.assertEqual(output["ai_calls"],1)
        self.assertEqual(output["publication_status"],"WITHHELD")
        self.assertEqual(set(output["reviewed"]),set(PROFILES))
        self.assertEqual([len(output["reviewed"][t]) for t in PROFILES],[10,10,10])
        self.assertEqual(output["month"],"2026-11")

    def test_one_real_ai_provider_call_for_three_groups(self):
        class Message:
            def __init__(self,value):self.content=json.dumps(value)
        class Completion:
            def __init__(self,value):self.choices=[type("Choice",(),{"message":Message(value)})()]
        mock=Mock(return_value=Completion(proposals({k:univ(p) for k,p in PROFILES.items()})))
        out=groq_one_monthly_call({k:univ(p) for k,p in PROFILES.items()},
                                  key="not-a-real-key",create=mock)
        self.assertEqual(mock.call_count,1)
        self.assertEqual(set(out),set(PROFILES))
        args=mock.call_args.kwargs
        self.assertEqual(args["model"],"openai/gpt-oss-120b")
        self.assertEqual(args["max_completion_tokens"],1100)

    def test_ai_hallucination_wrong_index_duplicates_are_rejected(self):
        us={k:univ(p) for k,p in PROFILES.items()}
        valid=proposals(us)
        for tier, value in (
            ("large",["NOTREAL"]+valid["large"][1:]),
            ("large",valid["large"][:2]),
            ("mid",[valid["large"][0]]+valid["mid"][1:]),
            ("small",[valid["small"][0]]*10),
        ):
            altered={key:list(items) for key,items in valid.items()}
            altered[tier]=value
            with self.subTest(tier=tier,value=value[0]),self.assertRaises(CycleUnavailable):
                validate_ai_ideas(altered,us)

    def test_partial_fundamentals_are_skipped_without_inventing(self):
        def partial(s,industry,profile,today):
            return fundamentals(s,industry,profile,today) if not s.endswith(("005","006")) else None
        out=make_monthly(NOW,universe_provider=univ,
                         ai_provider=proposals,fundamental_provider=partial)
        self.assertEqual(len(out["reviewed"]["mid"]),8)
        self.assertEqual(out["skipped"]["mid"]["M005"],"FUNDAMENTALS_NOT_VALIDATED")
        self.assertEqual(out["publication_status"],"WITHHELD")

    def test_weekly_has_no_ai_or_fundamental_network_calls(self):
        monthly=make_monthly(NOW,universe_provider=univ,
                             ai_provider=proposals,fundamental_provider=fundamentals)
        prices=Mock(return_value={row["symbol"]:object()
                                  for pool in monthly["reviewed"].values() for row in pool})
        technical=Mock(side_effect=tech)
        result=make_weekly(monthly,NOW,price_provider=prices,technical_provider=technical)
        self.assertEqual(prices.call_count,1)
        self.assertEqual(technical.call_count,30)
        self.assertEqual(result["ai_calls"],0)
        self.assertEqual(result["fundamental_fetches"],0)
        self.assertEqual([len(result["technical"][t]) for t in PROFILES],[5,5,5])
        self.assertEqual(result["publication_status"],"WITHHELD")
        self.assertEqual(prices.call_args.args[1],"6mo")

    def test_stale_monthly_seed_is_blocked(self):
        monthly=make_monthly(NOW,universe_provider=univ,
                             ai_provider=proposals,fundamental_provider=fundamentals)
        with self.assertRaisesRegex(CycleUnavailable,"MONTHLY_SEED_MISSING_OR_STALE"):
            make_weekly(monthly,datetime(2026,12,1,tzinfo=timezone.utc),
                        price_provider=Mock(side_effect=AssertionError("network")))
        self.assertEqual(monthly["publication_status"],"WITHHELD")

    def test_missing_completed_bars_are_excluded_not_forced(self):
        monthly=make_monthly(NOW,universe_provider=univ,
                             ai_provider=proposals,fundamental_provider=fundamentals)
        out=make_weekly(monthly,NOW,price_provider=lambda *a,**k:{},
                        technical_provider=Mock(side_effect=AssertionError("should not be called")))
        self.assertTrue(all(not picks for picks in out["technical"].values()))
        self.assertEqual(out["skipped"]["small"]["S000"],"COMPLETED_BARS_UNAVAILABLE")

    def test_stale_or_malformed_technical_data_never_selected(self):
        monthly=make_monthly(NOW,universe_provider=univ,
                             ai_provider=proposals,fundamental_provider=fundamentals)
        def invalid(s,b,p,now):
            return dict(tech(s,b,p,now),asof="2026-10-29") if s.endswith("000") else None
        out=make_weekly(monthly,NOW,
                        price_provider=lambda symbols,*a,**k:{s:object() for s in symbols},
                        technical_provider=invalid)
        self.assertEqual(out["skipped"]["large"]["L000"],"TECHNICAL_STALE")
        self.assertEqual(out["technical"]["large"],[])

    def test_index_provider_stale_or_fake_fails_before_ai(self):
        fake=Mock()
        def wrong(profile):
            v=univ(profile)
            v["source"]="LLM"
            return v
        with self.assertRaisesRegex(CycleUnavailable,"OFFICIAL_INDEX_UNAVAILABLE"):
            make_monthly(NOW,universe_provider=wrong,ai_provider=fake,
                         fundamental_provider=fundamentals)
        fake.assert_not_called()

    def test_missing_ai_key_does_not_fall_back_to_paid_llm(self):
        with self.assertRaisesRegex(CycleUnavailable,"MONTHLY_AI_KEY_MISSING"):
            groq_one_monthly_call({k:univ(p) for k,p in PROFILES.items()},key="")

    def test_unsupported_model_or_provider_failure_is_not_silently_retried(self):
        calls=Mock(side_effect=RuntimeError("api_key=secret"))
        with self.assertRaisesRegex(CycleUnavailable,"MONTHLY_AI_PROVIDER_FAILED") as ctx:
            groq_one_monthly_call({k:univ(p) for k,p in PROFILES.items()},
                                  key="local-test",create=calls)
        self.assertEqual(calls.call_count,1)
        self.assertNotIn("secret",str(ctx.exception))


if __name__=="__main__":
    unittest.main()
