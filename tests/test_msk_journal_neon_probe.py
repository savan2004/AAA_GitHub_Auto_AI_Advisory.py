"""Offline unit checks for the manual Neon staging probe (no network/secrets)."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.verify_msk_journal_neon import (
    CONFIRM_ENV,
    DSN_ENV,
    PROBE_ENV,
    STAGING_DATABASE,
    STAGING_HOST,
    ProbeError,
    load_config,
    main,
    synthetic_events,
)

RUN_ID = "20261122223010"
SAMPLE_DSN = (
    "postgresql://probe:FAKE-NOT-A-SECRET@" + STAGING_HOST + "/" +
    STAGING_DATABASE + "?sslmode=require&channel_binding=require"
)


def env(**changed):
    result = {
        CONFIRM_ENV: "YES_STAGING_ONLY",
        PROBE_ENV: RUN_ID,
        DSN_ENV: SAMPLE_DSN,
    }
    result.update(changed)
    return result


class StagingProbeTests(unittest.TestCase):
    def test_only_exact_staging_host_and_db_allowed(self):
        dsn, key = load_config(env())
        self.assertEqual(dsn, SAMPLE_DSN)
        self.assertEqual(key, RUN_ID)

    def test_rejects_production_host_even_with_temporary_secret(self):
        with self.assertRaisesRegex(ProbeError, "NOT the approved"):
            load_config(env(**{DSN_ENV: SAMPLE_DSN.replace(
                STAGING_HOST, "ep-production-main.c-4.ap-southeast-1.aws.neon.tech"
            )}))

    def test_accepts_postgres_scheme_alias(self):
        alias = SAMPLE_DSN.replace("postgresql://", "postgres://")
        dsn, key = load_config(env(**{DSN_ENV: alias}))
        self.assertEqual(dsn, alias)
        self.assertEqual(key, RUN_ID)

    def test_accepts_sqlalchemy_psycopg_schemes_and_normalizes_for_driver(self):
        for driver in ("psycopg", "psycopg2", "psycopg3"):
            with self.subTest(driver=driver):
                alias = SAMPLE_DSN.replace("postgresql://", f"postgresql+{driver}://")
                normalized, key = load_config(env(**{DSN_ENV: alias}))
                self.assertEqual(normalized, SAMPLE_DSN)
                self.assertEqual(key, RUN_ID)


    def test_accepts_pinned_libpq_conninfo_and_psql_wrapper(self):
        conninfo = (
            "host=" + STAGING_HOST + " port=5432 dbname=" + STAGING_DATABASE +
            " user=probe password=FAKE-NOT-A-SECRET sslmode=require channel_binding=require"
        )
        for raw in (conninfo, "psql " + conninfo):
            with self.subTest(wrapper=raw.startswith("psql ")):
                self.assertEqual(load_config(env(**{DSN_ENV: raw})), (SAMPLE_DSN, RUN_ID))

    def test_libpq_conninfo_still_rejects_wrong_host_database_and_tls(self):
        conninfo = (
            "host=" + STAGING_HOST + " port=5432 dbname=" + STAGING_DATABASE +
            " user=probe password=FAKE-NOT-A-SECRET sslmode=require"
        )
        bad_values = (
            conninfo.replace(STAGING_HOST, "other.example"),
            conninfo.replace(STAGING_DATABASE, "production"),
            conninfo.replace("sslmode=require", "sslmode=disable"),
            conninfo + " host=other.example",
        )
        for bad in bad_values:
            with self.subTest(bad_case="sanitized"):
                with self.assertRaises(ProbeError) as caught:
                    load_config(env(**{DSN_ENV: bad}))
                self.assertNotIn("FAKE-NOT-A-SECRET", str(caught.exception))
                self.assertNotIn(STAGING_HOST, str(caught.exception))

    def test_rejects_other_driver_schemes(self):
        bad = SAMPLE_DSN.replace("postgresql://", "postgresql+asyncpg://")
        with self.assertRaisesRegex(ProbeError, r"^Staging DSN scheme unsupported: unrecognized") as caught:
            load_config(env(**{DSN_ENV: bad}))
        self.assertNotIn("FAKE-NOT-A-SECRET", str(caught.exception))
        self.assertNotIn(STAGING_HOST, str(caught.exception))

    def test_neon_copy_formats_are_normalized_without_shell_execution(self):
        for wrapped in ("  "+SAMPLE_DSN+"\n", "'"+SAMPLE_DSN+"'", 
                        '"'+SAMPLE_DSN+'"', "psql '"+SAMPLE_DSN+"'"):
            self.assertEqual(load_config(env(**{DSN_ENV:wrapped})),(SAMPLE_DSN,RUN_ID))
        for bad in ("FAKE-NOT-A-SECRET", "psql", "psql '"+SAMPLE_DSN+"' -c anything",
                    "psql '"+SAMPLE_DSN, "FAKESECRET:invalid"):
            with self.assertRaises(ProbeError) as caught:
                load_config(env(**{DSN_ENV:bad}))
            self.assertNotIn("FAKESECRET",str(caught.exception))
            self.assertNotIn("FAKE-NOT-A-SECRET",str(caught.exception))

    def test_rejects_libpq_query_overrides_and_other_ports(self):
        for option in ("&host=other.example", "&hostaddr=127.0.0.1", "&dbname=production",
                       "&sslmode=disable", "&user=other", "&%68ost=other.example",
                       "&channel_binding=disable", "&options=-csearch_path%3Dpublic"):
            with self.assertRaises(ProbeError):
                load_config(env(**{DSN_ENV:SAMPLE_DSN+option}))
        for host in (STAGING_HOST+":5433",STAGING_HOST+":invalid"):
            with self.assertRaises(ProbeError):
                load_config(env(**{DSN_ENV:SAMPLE_DSN.replace(STAGING_HOST,host)}))

    def test_rejects_different_database_and_ssl_disable(self):
        for bad in (
            SAMPLE_DSN.replace("/" + STAGING_DATABASE, "/production"),
            SAMPLE_DSN.replace("sslmode=require", "sslmode=disable"),
            SAMPLE_DSN.replace("?sslmode=require", "?sslmode=prefer"),
            SAMPLE_DSN.replace("postgresql://", "file://"),
        ):
            with self.subTest(bad_class=bad.split("?")[-1]):
                with self.assertRaises(ProbeError):
                    load_config(env(**{DSN_ENV: bad}))

    def test_requires_private_staging_secret_and_explicit_approval(self):
        for config in (
            env(**{DSN_ENV: ""}),
            env(**{CONFIRM_ENV: ""}),
            env(**{CONFIRM_ENV: "YES"}),
            env(**{PROBE_ENV: "not-a-number"}),
            env(**{PROBE_ENV: ""}),
            env(**{PROBE_ENV: "202611222230100000"}),
        ):
            with self.assertRaises(ProbeError):
                load_config(config)

    def test_payloads_are_synthetic_and_stable(self):
        observed, opened, closed = synthetic_events(RUN_ID)
        self.assertEqual(observed["kind"], "OBSERVED")
        self.assertEqual(opened["kind"], "PAPER_OPEN")
        self.assertEqual(closed["kind"], "PAPER_CLOSE")
        self.assertEqual([observed["entry"], opened["entry"], closed["entry"]],
                         ["100", "101", "101"])
        self.assertEqual(len({x["event_id"] for x in (observed, opened, closed)}), 3)
        self.assertEqual(len({x["signal_id"] for x in (observed, opened, closed)}), 1)
        for item in (observed, opened, closed):
            self.assertEqual(item["symbol"], "MSKPROBE")
            self.assertIn("synthetic", item["source_ref"])
        self.assertEqual(observed, synthetic_events(RUN_ID)[0])

    def test_no_production_connection_or_secret_printed_when_unconfigured(self):
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), redirect_stdout(out):
            code = main(["write"])
        self.assertEqual(code, 1)
        self.assertNotIn("postgresql://", out.getvalue())
        self.assertNotIn("FAKE-NOT-A-SECRET", out.getvalue())


if __name__ == "__main__":
    unittest.main()
