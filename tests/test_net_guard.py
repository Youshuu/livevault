"""TikTok devolviendo vacio/HTML: clasificacion y pausa global (no 11 usuarios en rojo)."""
import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import helpers  # noqa: E402

helpers.prepare()
import app  # noqa: E402
from helpers import tmpdir  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_empty_json_body_is_blocked(self):
        err = app.classify_exception(json.JSONDecodeError("Expecting value", "", 0))
        self.assertEqual(err.kind, "blocked")

    def test_html_json_body_is_blocked(self):
        err = app.classify_exception(json.JSONDecodeError(
            "Expecting value", "<html><body>verify</body></html>", 0))
        self.assertEqual(err.kind, "blocked")

    def test_garbage_json_is_still_flaky(self):
        err = app.classify_exception(json.JSONDecodeError("Expecting value", "hola", 0))
        self.assertEqual(err.kind, "flaky")


class NetGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("netguard")
        self.db = app.DB(self.tmp / "t.db")
        self.s = app.Settings(self.db)
        self.m = app.Manager(self.db, self.s, app.Engine(self.s))
        app.GATE.block_until = 0.0
        self.m._strikes, self.m._block_level = 0, 0

    def tearDown(self):
        app.GATE.block_until = 0.0
        self.db.conn.close()

    def test_three_flaky_failures_pause_everyone(self):
        for _ in range(2):
            self.m._net_fail("flaky")
        self.assertEqual(app.GATE.blocked_for(), 0.0, "uno o dos fallos sueltos no pausan")
        self.m._net_fail("flaky")
        self.assertGreaterEqual(app.GATE.blocked_for(), 55.0)
        self.assertLessEqual(app.GATE.blocked_for(), 61.0)

    def test_blocked_pauses_immediately(self):
        self.m._net_fail("blocked")
        self.assertGreater(app.GATE.blocked_for(), 50.0)

    def test_success_resets_the_counter(self):
        self.m._net_fail("flaky")
        self.m._net_fail("flaky")
        self.m._net_ok()
        self.assertEqual(self.m._strikes, 0)
        self.m._net_fail("flaky")
        self.assertEqual(app.GATE.blocked_for(), 0.0, "tras un exito vuelve a hacer falta repetir")

    def test_paused_check_does_not_blame_the_user(self):
        self.db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("demo", "2026-01-01T00:00:00"))
        self.m.load_users()
        u = self.m.users["demo"]
        u.fail, u.error, u.error_kind = 3, "fallo viejo", "flaky"
        app.GATE.block_until = time.time() + 30
        asyncio.run(self.m._check(u))
        self.assertEqual(u.fail, 0)
        self.assertIsNone(u.error)
        self.assertIsNone(u.error_kind)
        self.assertGreater(u.next_check, time.time() + 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
