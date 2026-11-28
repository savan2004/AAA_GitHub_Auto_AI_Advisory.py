import io
import unittest
import zipfile
from datetime import date
from scripts.msk_central_500_evidence import roster_rows, archive_rows, probe, ROSTERS

DAY = date(2026, 11, 27)
ROSTER = {f"S{i}": f"IN{i:010d}" for i in range(500)}
ROSTER_BYTES = ("Symbol,ISIN Code\n" + "\n".join(f"{s},{i}" for s,i in ROSTER.items())).encode()


def archive(*, missing=None, wrong=None, duplicate=None, session=DAY):
    head = "TRADDT,TCKRSYMB,SCTYSRS,ISIN,OPNPRIC,HGHPRIC,LWPRIC,CLSPRIC,TTLTRADGVOL\n"
    rows = [f"{session},{s},EQ,{i if s!=wrong else 'IN9999999999'},100,110,90,105,100"
            for s,i in ROSTER.items() if s!=missing]
    if duplicate:
        rows.append(next(r for r in rows if f",{duplicate}," in r))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("final.csv", head + "\n".join(rows))
    return b.getvalue()


class FiveHundredTests(unittest.TestCase):
    def test_exact_roster(self):
        self.assertEqual(roster_rows(ROSTER_BYTES), ROSTER)
        with self.assertRaises(ValueError):
            roster_rows(ROSTER_BYTES + b"\nOTHER,IN0000000999")

    def test_exact_session_and_identity(self):
        good, bad = archive_rows(archive(), ROSTER, DAY)
        self.assertEqual(len(good), 500)
        self.assertEqual(bad, set())
        good, bad = archive_rows(archive(wrong="S3", missing="S4", duplicate="S5"), ROSTER, DAY)
        self.assertEqual(len(good), 497)
        self.assertEqual(bad, {"S3","S5"})
        with self.assertRaises(ValueError):
            archive_rows(archive(session=date(2026,11,26)), ROSTER, DAY)

    def test_probe_counts_and_hash(self):
        calls = []
        def get(url, limit):
            calls.append(url)
            return ROSTER_BYTES if url in ROSTERS else archive()
        report = probe(DAY, get=get)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["verified_eod_count"], 500)
        self.assertEqual(len(report["archive_sha256"]), 64)
        self.assertEqual(len(calls), 2)

    def test_missing_is_not_success(self):
        report = probe(DAY, get=lambda url,_: ROSTER_BYTES if url in ROSTERS else archive(missing="S4"))
        self.assertEqual(report["status"], "withheld_partial")
        self.assertEqual(report["missing"], ["S4"])
        self.assertEqual(report["verified_eod_count"], 499)


if __name__ == "__main__":
    unittest.main()
