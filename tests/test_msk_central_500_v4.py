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

    def test_official_transitional_roster_and_dummy(self):
        from scripts.msk_central_500_evidence import _roster_details
        data=("Company Name,Symbol,ISIN Code,Series\n"+
              "\n".join(f"Company {s},{s},{i},EQ" for s,i in ROSTER.items())+
              "\nExtra,OTHER,INE123456789,BE\nDummy HEG Ltd.,DUMMYHEG,DUM545A01024,EQ").encode()
        roster,series,placeholders=_roster_details(data)
        self.assertEqual(len(roster),501)
        self.assertEqual(series["OTHER"],"BE")
        self.assertEqual(placeholders,["DUMMYHEG"])
        self.assertNotIn("DUMMYHEG",roster)
        self.assertNotIn("DUMMYHEG",series)
        with self.assertRaises(ValueError):
            _roster_details(data+b"\nInvalid,DUMMY2,DUM545A01024,EQ")

    def test_series_exact_and_full_transitional_coverage(self):
        data=("Company Name,Symbol,ISIN Code,Series\n"+
              "\n".join(f"Company {s},{s},{i},EQ" for s,i in ROSTER.items())+
              "\nExtra,OTHER,INE123456789,BE").encode()
        original=archive()
        with zipfile.ZipFile(io.BytesIO(original)) as z:
            text=z.read("final.csv").decode()
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w") as z:
            z.writestr("final.csv",text+"\n"+f"{DAY},OTHER,BE,INE123456789,100,110,90,105,100")
        report=probe(DAY,get=lambda url,_:data if url in ROSTERS else buf.getvalue())
        self.assertEqual((report["roster_count"],report["verified_eod_count"]),(501,501))
        self.assertEqual(report["status"],"verified")
        self.assertEqual(report["operating_core_limit"],500)
        self.assertEqual(report["total_limit"],700)
        self.assertEqual(report["admission_status"],"NOT_EVALUATED")
        self.assertEqual(report["non_eq_members"],["OTHER"])
        report=probe(DAY,get=lambda url,_:data if url in ROSTERS else original)
        self.assertEqual(report["status"],"withheld_partial")
        self.assertEqual(report["missing"],["OTHER"])

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
