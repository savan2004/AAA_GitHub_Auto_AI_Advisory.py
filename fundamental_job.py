#!/usr/bin/env python3
"""CLI used by the Telegram scheduler. Prints ONE JSON object as the last stdout line; logs go to stderr."""
import sys
import json
import logging
import argparse

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main(argv=None) -> int:
    import fundamental_pipeline as fp
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").add_argument("--channel", required=True)
    for name in ("begin-send", "confirm", "fail"):
        s = sub.add_parser(name)
        for a in ("channel", "day", "symbol", "trade-date"):
            s.add_argument(f"--{a}", required=True)
        if name == "confirm":
            s.add_argument("--message-id", required=True)
        if name == "fail":
            s.add_argument("--reason", default="send failed")
            s.add_argument("--ambiguous", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            out = fp.prepare(a.channel)
        elif a.cmd == "begin-send":
            fp.begin_send(a.channel, a.day, a.symbol, a.trade_date)
            out = {"status": "OK"}
        elif a.cmd == "confirm":
            fp.confirm_sent(a.channel, a.day, a.symbol, a.trade_date, a.message_id)
            out = {"status": "OK"}
        else:
            out = {"status": "OK", "state": fp.record_failure(a.channel, a.day, a.symbol, a.trade_date, a.reason, a.ambiguous)}
    except Exception as e:                                   # never crash silently: the caller must see a status
        out = {"status": "ERROR", "reason": str(e)[:300], "alert": True}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("status") != "ERROR" else 1


if __name__ == "__main__":
    sys.exit(main())
