"""Backward-compatible import path; TA-Lib replaced by scalar reference."""
from msk_math_reference_audit import compare_indicators, fixture, main

if __name__=="__main__":
    raise SystemExit(main())
