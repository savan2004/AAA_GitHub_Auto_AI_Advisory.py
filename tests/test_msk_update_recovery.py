"""Offline recovery-report tests; no real DB, Telegram calls or holdings."""
import contextlib
import io
import unittest
from datetime import datetime, timezone
from msk_update_recovery import recovery_report, main


class Connection:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self
    def fetchall(self): return self.rows


class RecoveryTests(unittest.TestCase):
    def test_aggregate_read_only_report(self):
        conn = Connection([('claimed', 2, datetime(2026, 11, 1, tzinfo=timezone.utc)), ('failed', 1, None)])
        result = recovery_report('synthetic', connector=lambda _: conn)
        self.assertEqual(result['aged_unresolved_count'], 3)
        self.assertFalse(result['automatic_replay_allowed'])
        self.assertFalse(result['portfolio_execution_verified'])
        self.assertEqual(conn.calls[0][0], 'SET TRANSACTION READ ONLY')
        self.assertTrue(conn.calls[1][0].startswith('SELECT '))
        self.assertEqual(conn.calls[1][1], (15,))
        self.assertNotIn('user_id', conn.calls[1][0])
    def test_no_aged_updates_is_not_execution_proof(self):
        result = recovery_report('synthetic', connector=lambda _: Connection([]))
        self.assertEqual(result['status'], 'no_aged_unresolved_updates')
        self.assertFalse(result['portfolio_execution_verified'])
    def test_invalid_age_never_connects(self):
        for value in (0, -1, True, 1.5, 525601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recovery_report('synthetic', minimum_age_minutes=value, connector=lambda _: self.fail('DB accessed'))
    def test_errors_do_not_expose_credentials(self):
        def fail(*args, **kwargs): raise RuntimeError('secret-credential')
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main([], environ={'MSK_STATE_DATABASE_URL': 'secret-credential'}, reporter=fail)
        self.assertEqual(code, 2)
        self.assertNotIn('secret-credential', stream.getvalue())
    def test_missing_dsn_fails_closed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([], environ={}), 2)
    def test_exit_codes(self):
        for count, expected in ((0, 0), (2, 1)):
            with contextlib.redirect_stdout(io.StringIO()):
                code = main([], environ={'MSK_STATE_DATABASE_URL': 'synthetic'}, reporter=lambda *a, **k: {'aged_unresolved_count': count})
            self.assertEqual(code, expected)


if __name__ == '__main__':
    unittest.main()
