"""Pruebas sin red (unittest): python -m unittest discover -s tests -v

Cubren las funciones puras que mas se rompen al tocar el codigo: parseo de respuestas de
TikTok, redaccion de secretos, nombres de archivo, ajustes cifrados y el limite de peticiones.
"""
import asyncio
import json
import shutil
import sqlite3
import sys
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import helpers  # noqa: E402

helpers.prepare()
import app  # noqa: E402
from helpers import tmpdir  # noqa: E402


class PureTests(unittest.TestCase):
    def test_parse_username(self):
        self.assertEqual(app.parse_username("@ManolITO64"), "manolito64")
        self.assertEqual(app.parse_username("https://www.tiktok.com/@pepe.lopez?lang=es"), "pepe.lopez")
        self.assertEqual(app.parse_username("  @user.name.  "), "user.name")
        self.assertIsNone(app.parse_username("a"))
        self.assertIsNone(app.parse_username(".."))
        self.assertIsNone(app.parse_username(""))
        self.assertIsNone(app.parse_username("nombre con espacios"))

    def test_analysis_fields(self):
        fields = app.analysis_fields({"codec": "h264", "size": "1920x1080", "fps": "30",
                                      "frames": 900, "frozen_secs": 2.5, "ts_bad": True})
        self.assertEqual(fields[0], "h264")
        self.assertEqual(fields[1:3], (1920, 1080))
        self.assertEqual(fields[3], 30.0)
        self.assertEqual(fields[4], 900)
        self.assertEqual(fields[5], 2.5)
        self.assertEqual(fields[6], 1)
        self.assertTrue(fields[7])

    def test_engine_contract_accepts_bound_methods(self):
        class Dummy:
            def two(self, a, b):
                pass

            def one(self, a):
                pass

            def optional(self, a, b=None):
                pass

        self.assertTrue(app._accepts_two_args(Dummy().two))
        self.assertFalse(app._accepts_two_args(Dummy().one))
        self.assertTrue(app._accepts_two_args(Dummy().optional))

    def test_session_payload(self):
        ok = app._parse_session_payload({"message": "success",
                                         "data": {"username": "pepe", "nickname": "Pepe"}})
        self.assertEqual(ok, {"state": "ok", "account": "pepe", "nickname": "Pepe"})
        expired = app._parse_session_payload({"message": "error",
                                             "data": {"description": "Session expired", "error_code": 1}})
        self.assertEqual(expired["state"], "expired")
        self.assertEqual(app._parse_session_payload({"message": "error", "data": {}})["state"], "unknown")
        self.assertEqual(app._parse_session_payload("no es un dict")["state"], "unknown")

    def test_redact(self):
        out = app.redact("GET https://x.test/a?sessionid=SECRETO&x=1")
        self.assertNotIn("SECRETO", out)
        self.assertIn("<redactado>", out)
        self.assertIn("<redactado>", app.redact("Cookie: sessionid=abc123; tt-target-idc=useast5"))
        self.assertIn("bot<redactado>", app.redact("https://api.telegram.org/bot123456:AAHfoo/sendMessage"))
        self.assertNotIn("clave", app.redact("http://usuario:clave@proxy.test:8080"))
        self.assertIn("Bearer <redactado>", app.redact("Authorization: Bearer abc.def.ghi"))

    def test_extract_sessionid(self):
        self.assertEqual(app.extract_sessionid("sessionid=abc123; tt-target-idc=useast5"), "abc123")
        self.assertEqual(app.extract_sessionid("abc123"), "abc123")
        self.assertEqual(app.extract_sessionid('"abc123"'), "abc123")
        self.assertEqual(app.extract_sessionid(""), "")

    def test_unique_path(self):
        folder = tmpdir("files")
        first = app.unique_path(folder, "u_2026-01-01_00-00-00_000000", ".mp4")
        first.write_bytes(b"x")
        second = app.unique_path(folder, "u_2026-01-01_00-00-00_000000", ".mp4")
        self.assertNotEqual(first, second)
        self.assertTrue(second.name.endswith("_2.mp4"))

    def test_clean_nick(self):
        self.assertEqual(app.clean_nick("  Pepe\x00 \u2764\ufe0f "), "Pepe \u2764\ufe0f")
        self.assertEqual(app.clean_nick(None), "")
        self.assertLessEqual(len(app.clean_nick("a" * 200)), 60)

    def test_sniff_image(self):
        self.assertEqual(app.sniff_image(b"\xff\xd8\xff\xe0..."), "image/jpeg")
        self.assertEqual(app.sniff_image(b"\x89PNG\r\n\x1a\n..."), "image/png")
        self.assertEqual(app.sniff_image(b"RIFF0000WEBPVP8 "), "image/webp")
        self.assertEqual(app.sniff_image(b"GIF89a..."), "image/gif")
        self.assertEqual(app.sniff_image(b"<html>"), "")

    def test_fmt_and_int(self):
        self.assertEqual(app.fmt_secs(3661), "1:01:01")
        self.assertEqual(app.to_int("42"), 42)
        self.assertIsNone(app.to_int("x"))
        self.assertIsNone(app.to_int(None))

    def test_classify_exception(self):
        self.assertEqual(app.classify_exception(TimeoutError("t")).kind, "network")
        self.assertEqual(app.classify_exception(ValueError("HTTP 429 too many")).kind, "blocked")
        self.assertEqual(app.classify_exception(KeyError("data")).kind, "flaky")
        self.assertEqual(app.classify_exception(RuntimeError("raro")).kind, "unknown")
        e = app.EngineError("no_stream", "x")
        self.assertIs(app.classify_exception(e), e)

    def test_parse_user_room(self):
        data = {"data": {"liveRoom": {"status": 2, "title": "Hola", "liveRoomStats": {"userCount": 42}},
                         "user": {"roomId": "123", "nickname": "Nick",
                                  "avatarLarger": "https://x.test/a.jpg?sign=1"}}}
        st = app.parse_user_room(data)
        self.assertTrue(st.is_live)
        self.assertEqual(st.room_id, "123")
        self.assertEqual(st.viewers, 42)
        self.assertEqual(st.nickname, "Nick")
        self.assertEqual(st.title, "Hola")
        self.assertFalse(app.parse_user_room({"data": {"liveRoom": {"status": 4},
                                                       "user": {"roomId": "0"}}}).is_live)

    def test_parse_candidates_order(self):
        sd = {"data": {"origin": {"main": {"flv": "http://o/flv", "hls": "http://o/hls",
                                           "cmaf": "http://o/cmaf"}},
                       "hd": {"main": {"flv": "http://h/flv"}}}}
        info = {"stream_url": {"live_core_sdk_data": {"pull_data": {"stream_data": json.dumps(sd)}}}}
        labels = [c.label() for c in app.parse_candidates(info, "max")]
        self.assertEqual(labels[:4], ["origin/flv", "origin/hls", "origin/cmaf", "hd/flv"])
        self.assertEqual(app.parse_candidates(info, "hd")[0].label(), "hd/flv")
        self.assertGreaterEqual(len(app.parse_candidates(info, "sd")), 4)

    def test_parse_candidates_legacy(self):
        info = {"stream_url": {"flv_pull_url": {"HD1": "http://legacy-hd.flv"},
                               "hls_pull_url_map": {"FULL_HD1": "http://legacy-origin.m3u8"}}}
        labels = [c.label() for c in app.parse_candidates(info, "max")]
        self.assertEqual(labels[:2], ["origin/hls", "hd/flv"])

    def test_parse_profile_html(self):
        payload = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {
            "uniqueId": "pepe", "nickname": "Pepe \u2764", "avatarLarger": "https://a.test/b.jpg"}}}}}
        html = ('<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
                + json.dumps(payload) + "</script>")
        self.assertEqual(app.parse_profile_html(html, "pepe"), ("Pepe \u2764", "https://a.test/b.jpg"))
        self.assertEqual(app.parse_profile_html(html, "otro"), ("", ""))


class DBSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tmpdir("db")
        self.db = app.DB(self.tmp / "t.db")
        self.s = app.Settings(self.db)

    def tearDown(self):
        self.db.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults(self):
        self.assertEqual(self.s.get("check_interval"), "45")
        self.assertEqual(self.s.get("quality"), "max")
        self.assertEqual(self.s.get("min_free_gb"), "2")
        self.assertEqual(self.s.get("push_ended"), "1")

    def test_roundtrip_and_public(self):
        self.s.set_many({"check_interval": "90", "session_id": "abc123"})
        self.assertEqual(self.s.get("check_interval"), "90")
        self.assertEqual(self.s.get("session_id"), "abc123")
        pub = self.s.public()
        self.assertEqual(pub["session_id"], "")
        self.assertTrue(pub["session_id_set"])
        self.s.set_many({"session_id": ""})
        self.assertEqual(self.s.get("session_id"), "")
        self.assertFalse(self.s.public()["session_id_set"])

    def test_secret_at_rest(self):
        self.s.set_many({"session_id": "abc123"})
        row = self.db.q("SELECT value FROM settings WHERE key='session_id'")[0]["value"]
        self.assertEqual(self.s.get("session_id"), "abc123")
        if app.WIN and app._dpapi(True, b"x"):
            self.assertTrue(row.startswith("dpapi:"), "los secretos deben guardarse cifrados")
            self.assertNotIn("abc123", row)
        else:
            self.assertEqual(row, "abc123")

    def test_plaintext_secrets_are_migrated(self):
        token = "123456:ABCdefghijklmnopqrstuvwxyz"
        self.db.x("INSERT INTO settings(key,value) VALUES('push_tg_token',?)", (token,))
        s2 = app.Settings(self.db)
        self.assertEqual(s2.get("push_tg_token"), token)
        row = self.db.q("SELECT value FROM settings WHERE key='push_tg_token'")[0]["value"]
        if app.WIN and app._dpapi(True, b"x"):
            self.assertTrue(row.startswith("dpapi:"), "el secreto antiguo debe quedar cifrado")

    def test_int_clamps(self):
        self.s.set_many({"retention_days": "99999"})
        self.assertEqual(self.s.int("retention_days", 0, 3650), 3650)
        self.assertEqual(self.s.int("min_free_gb", 0, 10_000), 2)

    def test_indexes(self):
        names = {r["name"] for r in self.db.q("PRAGMA index_list(recordings)")}
        self.assertIn("idx_rec_user", names)
        self.assertIn("idx_rec_status", names)
        self.assertIn("idx_rec_session", names)
        cols = {r["name"] for r in self.db.q("PRAGMA table_info(recordings)")}
        self.assertIn("session_id", cols)
        ucols = {r["name"] for r in self.db.q("PRAGMA table_info(users)")}
        self.assertIn("notified_room", ucols)
        self.assertEqual(self.db.q("PRAGMA user_version")[0]["user_version"], app.SCHEMA_VERSION)

    def test_backup_and_quick_check(self):
        self.assertEqual(self.db.quick_check(), "ok")
        self.db.x("INSERT INTO users(username, added_at) VALUES('bk','2026-01-01T00:00:00')")
        folder = tmpdir("backups")
        paths = [self.db.backup(folder, keep=2) for _ in range(3)]
        self.assertTrue(all(p is not None for p in paths), paths)
        self.assertTrue(paths[-1].exists() and paths[-2].exists())
        self.assertFalse(paths[0].exists(), "la copia mas antigua se poda")
        self.assertEqual(len(list(folder.glob("tkrec-*.db"))), 2, "debe conservar solo las 2 ultimas")
        import sqlite3
        conn = sqlite3.connect(str(paths[-1]))
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)
        finally:
            conn.close()
        self.assertFalse(list(folder.glob("*.tmp")), "no deben quedar temporales")

    def test_notified_room_persisted(self):
        self.db.x("INSERT INTO users(username, added_at, notified_room) "
                  "VALUES('u1','2026-01-01T00:00:00','123')")
        m = app.Manager(self.db, self.s, app.Engine(self.s))
        m.load_users()
        u = m.users["u1"]
        self.assertEqual(u.notified_room, "123")
        m._mark_notified(u, "456")
        row = self.db.q("SELECT notified_room FROM users WHERE username='u1'")[0]
        self.assertEqual(row["notified_room"], "456")

    def test_migrates_old_database(self):
        p = Path(tmpdir("old")) / "old.db"
        import sqlite3
        conn = sqlite3.connect(str(p))
        conn.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                           added_at TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                           auto_record INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE recordings(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                                path TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
                                duration_sec REAL DEFAULT 0, size_bytes INTEGER DEFAULT 0,
                                status TEXT NOT NULL DEFAULT 'recording', quality TEXT, note TEXT);
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        conn.execute("INSERT INTO users(username, added_at) VALUES('viejo','2026-01-01T00:00:00')")
        conn.execute("INSERT INTO recordings(user_id,path,started_at,status) "
                     "VALUES(1,'x.mp4','2026-01-01T00:00:00','done')")
        conn.commit()
        conn.close()
        db = app.DB(p)
        try:
            rcols = {r["name"] for r in db.q("PRAGMA table_info(recordings)")}
            ucols = {r["name"] for r in db.q("PRAGMA table_info(users)")}
            self.assertIn("session_id", rcols)
            self.assertIn("notified_room", ucols)
            self.assertEqual(db.q("SELECT COUNT(*) n FROM users")[0]["n"], 1)
            self.assertIsNone(db.q("SELECT session_id FROM recordings")[0]["session_id"])
            self.assertEqual(db.q("PRAGMA user_version")[0]["user_version"], app.SCHEMA_VERSION)
            self.assertIn("idx_rec_session",
                          {r["name"] for r in db.q("PRAGMA index_list(recordings)")})
        finally:
            db.conn.close()


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_limit(self):
        gate = app.NetGate(2)
        active = peak = 0

        async def work():
            nonlocal active, peak
            async with gate:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*[work() for _ in range(6)])
        self.assertLessEqual(peak, 2)

    async def test_blocked(self):
        gate = app.NetGate(1)
        gate.block_until = time.time() + 5
        with self.assertRaises(app.EngineError):
            async with gate:
                pass


class MaintenanceTests(unittest.TestCase):
    """Retencion y pausa por poco espacio (sin tocar la grabacion en curso)."""

    def setUp(self):
        self.tmp = tmpdir("maint")
        self.db = app.DB(self.tmp / "t.db")
        self.s = app.Settings(self.db)
        self.out = self.tmp / "out"
        self.s.set_many({"output_dir": str(self.out), "min_free_gb": "0"})
        self.uid = self.db.x("INSERT INTO users(username, added_at) VALUES(?,?)",
                             ("u", datetime.now().isoformat(timespec="seconds")))
        self.m = app.Manager(self.db, self.s, app.Engine(self.s))

    def tearDown(self):
        self.db.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add(self, name: str, started: datetime, status: str = "done", favorite: int = 0) -> Path:
        p = self.out / "u" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 4096)
        self.db.x("INSERT INTO recordings(user_id,path,started_at,status,duration_sec,size_bytes,favorite) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (self.uid, str(p), started.isoformat(timespec="seconds"), status, 30, 4096, favorite))
        return p

    def test_retention_by_days(self):
        old = self._add("viejo.mp4", datetime.now() - timedelta(days=3))
        new = self._add("nuevo.mp4", datetime.now())
        self.s.set_many({"retention_days": "1"})
        asyncio.run(self.m._storage_tick())
        self.assertFalse(old.exists(), "la grabacion antigua debe borrarse")
        self.assertTrue(new.exists(), "la reciente se conserva")
        self.assertEqual(self.db.q("SELECT COUNT(*) n FROM recordings")[0]["n"], 1)

    def test_retention_skips_recording_in_progress(self):
        live = self._add("en_curso.mp4", datetime.now() - timedelta(days=10), status="recording")
        self.s.set_many({"retention_days": "1"})
        asyncio.run(self.m._storage_tick())
        self.assertTrue(live.exists(), "nunca se borra una grabacion en curso")
        self.assertEqual(self.db.q("SELECT COUNT(*) n FROM recordings")[0]["n"], 1)

    def test_retention_skips_favorites(self):
        old = self._add("viejo.mp4", datetime.now() - timedelta(days=3))
        fav = self._add("favorita.mp4", datetime.now() - timedelta(days=3), favorite=1)
        self.s.set_many({"retention_days": "1"})
        asyncio.run(self.m._storage_tick())
        self.assertFalse(old.exists(), "la no favorita se borra")
        self.assertTrue(fav.exists(), "la favorita no se borra con la retencion")
        self.assertEqual(self.db.q("SELECT COUNT(*) n FROM recordings")[0]["n"], 1)

    def test_low_space_pauses_and_recovers(self):
        self.s.set_many({"min_free_gb": "10000"})       # imposible: siempre activa la pausa
        asyncio.run(self.m._storage_tick())
        self.assertTrue(self.m.low_space)
        self.s.set_many({"min_free_gb": "0"})
        asyncio.run(self.m._storage_tick())
        self.assertFalse(self.m.low_space)


class MigrationTests(unittest.TestCase):
    def test_schema4_migrates_to_5(self):
        tmp = tmpdir("mig")
        p = tmp / "old.db"
        conn = sqlite3.connect(p)
        conn.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
                               added_at TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                               auto_record INTEGER NOT NULL DEFAULT 1, notified_room TEXT);
            CREATE TABLE recordings(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                                    path TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
                                    duration_sec REAL DEFAULT 0, size_bytes INTEGER DEFAULT 0,
                                    status TEXT NOT NULL DEFAULT 'recording', quality TEXT,
                                    note TEXT, session_id TEXT);
            INSERT INTO users(username, added_at) VALUES('u', '2026-01-01T00:00:00');
            INSERT INTO recordings(user_id, path, started_at, status)
                VALUES(1, 'x.mp4', '2026-01-01T00:00:00', 'done');
        """)
        conn.commit()
        conn.close()
        db = app.DB(p)
        row = db.q("SELECT favorite, analyzed_at FROM recordings")[0]
        self.assertEqual(row["favorite"], 0)
        self.assertIsNone(row["analyzed_at"])
        db.conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


class FavoritesTests(unittest.TestCase):
    def test_set_favorite_and_delete_all(self):
        tmp = tmpdir("fav")
        db = app.DB(tmp / "t.db")
        uid = db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("u", "2026-01-01T00:00:00"))
        rows = {}
        for label, status, favorite in (("a", "done", 0), ("b", "done", 1), ("c", "recording", 0)):
            p = tmp / f"{label}.mp4"
            p.write_bytes(b"x")
            rid = db.x("INSERT INTO recordings(user_id,path,started_at,status,favorite) "
                       "VALUES(?,?,?,?,?)", (uid, str(p), "2026-01-01T00:00:00", status, favorite))
            rows[label] = (rid, p)
        stub = types.SimpleNamespace(db=db, manager=None)
        self.assertTrue(app.App.set_favorite(stub, rows["a"][0], True))
        self.assertEqual(db.q("SELECT favorite FROM recordings WHERE id=?", (rows["a"][0],))[0]["favorite"], 1)
        with self.assertRaises(app.UserError):
            app.App.set_favorite(stub, 99999, True)

        removed, kept = app.App.delete_recordings(stub, "u", keep_favorites=True)
        self.assertEqual((removed, kept), (0, 2))
        self.assertTrue(rows["a"][1].exists() and rows["b"][1].exists())
        self.assertTrue(rows["c"][1].exists(), "una grabacion en curso no se toca")

        removed, kept = app.App.delete_recordings(stub, "u", keep_favorites=False)
        self.assertEqual((removed, kept), (2, 0))
        self.assertFalse(rows["a"][1].exists() or rows["b"][1].exists())
        self.assertTrue(rows["c"][1].exists())
        self.assertEqual({r["id"] for r in db.q("SELECT id FROM recordings")}, {rows["c"][0]})
        db.conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
