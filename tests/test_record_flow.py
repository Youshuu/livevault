"""Flujo completo de grabacion sin TikTok (necesita ffmpeg): python -m unittest discover -s tests -v

Simula un directo sirviendo un archivo local como si fuera el stream: comprueba que se graba con
nombre unico, en MP4 fragmentado, que el vigilante no lo corta, que se finaliza y queda en la BD.
"""
import asyncio
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import helpers  # noqa: E402

helpers.prepare()
import app  # noqa: E402
from helpers import tmpdir  # noqa: E402


class FakeEngine:
    """Sustituye a TikTokLive: el 'directo' es un archivo local."""

    def __init__(self, src: Path):
        self.src = src

    async def status(self, username):
        return app.LiveStatus(False, "", None, "", "", "")

    async def streams(self, username, room_id):
        return app.StreamInfo([app.Candidate("hd", "flv", str(self.src))], viewers=7, title="Demo")

    async def drop(self, username=None):
        pass

    async def profile_html(self, username):
        return "", ""

    async def fetch_image(self, username, url):
        return None


class RecordFlowTests(unittest.TestCase):
    def test_full_recording(self):
        exe = app.find_ffmpeg()
        if not exe:
            self.skipTest("ffmpeg no disponible")
        tmp = tmpdir("flow")
        out = tmp / "grabaciones"
        src = tmp / "live.mp4"
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(src)], check=True, timeout=180)

        db = app.DB(tmp / "t.db")
        s = app.Settings(db)
        s.set_many({"output_dir": str(out), "final_format": "mp4", "min_free_gb": "0",
                    "min_keep_kb": "1", "check_interval": "45"})
        db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("demo", "2026-01-01T00:00:00"))
        m = app.Manager(db, s, FakeEngine(src))
        m.load_users()
        u = m.users["demo"]
        # un archivo local se copia a toda velocidad; "-re" lo hace ir en tiempo real como un directo
        original = m._build_cmd

        def slow_cmd(exe, cand, out):
            cmd = original(exe, cand, out)
            cmd.insert(cmd.index("-i"), "-re")
            return cmd

        m._build_cmd = slow_cmd
        notices: list[tuple] = []
        m.notify = lambda title, msg, kind="info", username=None, image=None: notices.append((kind, msg))

        async def run() -> None:
            m._start_recording(u)
            assert u.task is not None
            await asyncio.wait_for(asyncio.shield(u.task), 120)
            for _ in range(120):
                pending = [t for t in m.bg if not t.done()]
                if not pending:
                    break
                await asyncio.sleep(0.5)

        asyncio.run(run())
        rows = db.q("SELECT * FROM recordings ORDER BY id")
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["status"], "done", row)
        self.assertTrue(row["path"].endswith(".mp4"), row["path"])
        self.assertTrue(row["session_id"], "la grabacion debe pertenecer a una sesion")
        self.assertIn("_", Path(row["path"]).stem)
        self.assertGreaterEqual(row["duration_sec"] or 0, 15)
        self.assertGreater(row["size_bytes"] or 0, 1000)
        self.assertFalse(any(out.rglob("*.mkv")), "no debe quedar ningun .mkv")
        self.assertFalse(any(out.rglob("*.tmp.mp4")), "no debe quedar temporales")
        self.assertTrue((app.THUMB_DIR / f"{row['id']}.jpg").exists())
        self.assertTrue(any(k == "saved" for k, _ in notices), notices)
        self.assertTrue(any(k == "ended" for k, _ in notices), notices)
        db.conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


    def test_stop_saves_the_recording(self):
        exe = app.find_ffmpeg()
        if not exe:
            self.skipTest("ffmpeg no disponible")
        tmp = tmpdir("flow-stop")
        out = tmp / "grabaciones"
        src = tmp / "live.mp4"
        subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(src)], check=True, timeout=180)
        db = app.DB(tmp / "t.db")
        s = app.Settings(db)
        s.set_many({"output_dir": str(out), "final_format": "mp4", "min_free_gb": "0",
                    "min_keep_kb": "1"})
        db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("demo", "2026-01-01T00:00:00"))
        m = app.Manager(db, s, FakeEngine(src))
        m.load_users()
        u = m.users["demo"]
        notices: list[tuple] = []
        m.notify = lambda title, msg, kind="info", username=None, image=None: notices.append((kind, msg))
        original = m._build_cmd

        def slow_cmd(e, cand, o):
            cmd = original(e, cand, o)
            cmd.insert(cmd.index("-i"), "-re")
            return cmd

        m._build_cmd = slow_cmd

        async def run() -> None:
            m._start_recording(u)
            await asyncio.sleep(5)
            self.assertTrue(u.recording, "deberia estar grabando")
            await m.stop_user("demo")
            for _ in range(120):
                if not [t for t in m.bg if not t.done()]:
                    break
                await asyncio.sleep(0.5)

        asyncio.run(run())
        rows = db.q("SELECT * FROM recordings ORDER BY id")
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["status"], "done", row)
        self.assertTrue(Path(row["path"]).exists())
        self.assertGreater(row["size_bytes"] or 0, 1000)
        self.assertGreaterEqual(row["duration_sec"] or 0, 2)
        self.assertLessEqual(row["duration_sec"] or 0, 15)
        self.assertTrue(row["session_id"])
        self.assertFalse(u.recording)
        self.assertFalse(any(k == "ended" for k, _ in notices),
                         "una parada manual no debe anunciar fin de directo")
        db.conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
