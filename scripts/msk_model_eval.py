"""Offline model output acceptance harness. Never contacts any provider.
Feed independently collected model responses for the same synthetic fixtures.
"""
import json
import sys
from pathlib import Path
from decimal import Decimal

CASES = (
    {"id":"metric-binding","facts":{"price":100,"rsi":65,"roe":15.5},
     "expected":{"price":100,"rsi":65,"roe":15.5}},
    {"id":"missing-target","facts":{"price":100,"target":None},
     "expected":{"price":100,"target":None}},
    {"id":"negative-profit","facts":{"net_profit":-10,"revenue":500},
     "expected":{"net_profit":-10,"revenue":500}},
    {"id":"period-and-unit","facts":{"revenue":123.45,"unit":"INR crore","period_end":"2026-09-30","scope":"consolidated"},
     "expected":{"revenue":123.45,"unit":"INR crore","period_end":"2026-09-30","scope":"consolidated"}},
    {"id":"unknown-scope","facts":{"scope":None,"eps":None},
     "expected":{"scope":None,"eps":None}},
)

def prompt(case):
    return ("Synthetic test only. Copy the exact values in the following facts as "
            "a JSON object, preserve keys, units and nulls, no commentary: "
            + json.dumps(case["facts"],allow_nan=False))

def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key]=value
    return result

def exact(expected,actual):
    if type(expected) is dict:
        return type(actual) is dict and set(expected)==set(actual) and all(exact(v,actual[k]) for k,v in expected.items())
    if type(expected) in (int,float):
        return type(actual) is Decimal and actual.is_finite() and actual==Decimal(str(expected))
    return type(actual) is type(expected) and actual==expected

def score(outputs):
    rows=[]
    for case in CASES:
        raw=outputs.get(case["id"])
        try:
            if not isinstance(raw,str) or len(raw)>14000:
                raise ValueError("missing output")
            obj=json.loads(raw,parse_float=Decimal,parse_int=Decimal,
                           object_pairs_hook=unique_object)
            ok=exact(case["expected"],obj)
        except (ValueError,TypeError):
            ok=False
        rows.append({"case":case["id"],"passed":ok})
    passed=sum(r["passed"] for r in rows)
    return {"passed":passed,"total":len(rows),"score":100*passed//len(rows),"cases":rows}

def main():
    if len(sys.argv)==1:
        print(json.dumps([{"id":c["id"],"prompt":prompt(c)} for c in CASES],indent=2))
        return 0
    # File is {model_id: {case_id: raw model output}}. No model calls or secrets.
    models=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not isinstance(models,dict) or not models:
        raise ValueError("no model outputs")
    result={name:score(out if isinstance(out,dict) else {}) for name,out in models.items()}
    print(json.dumps(result,indent=2))
    return int(any(r["score"]!=100 for r in result.values()))

if __name__=="__main__":
    sys.exit(main())
