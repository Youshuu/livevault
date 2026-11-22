"""Pruebas de la parte de video (necesitan ffmpeg): python -m unittest discover -s tests -v

Comprueban el analisis barato, el remux de una grabacion antigua .mkv a .mp4, la miniatura,
la duracion y la reparacion con progreso.
"""
import asyncio
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import helpers  # noqa: E402

helpers.prepare()
import app  # noqa: E402
from helpers import tmpdir  # noqa: E402


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = app.find_ffmpeg()
        if not cls.exe:
            raise unittest.SkipTest("ffmpeg no disponible")
        cls.dir = tmpdir("media")
        cls.src = cls.dir / "clip.mp4"
        subprocess.run([cls.exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                        "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(cls.src)], check=True, timeout=180)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_duration(self):
        self.assertAlmostEqual(app.probe_duration(self.exe, self.src), 8.0, delta=0.6)

    def test_thumb(self):
        out = self.dir / "thumb.jpg"
        self.assertTrue(app.make_thumb(self.exe, self.src, 8.0, out))
        self.assertGreater(out.stat().st_size, 500)
        self.assertEqual(app.sniff_image(out.read_bytes()), "image/jpeg")

    def test_analyze_plain(self):
        h = app.analyze_video(self.exe, self.src, 8.0)
        self.assertEqual(h["codec"], "h264")
        self.assertEqual(h["size"], "320x240")
        self.assertEqual(h["fps"], "30")
        self.assertGreaterEqual(h["frames"], 220)
        self.assertFalse(h["frozen"])
        self.assertFalse(h["ts_bad"])

    def test_analyze_fragmented_mp4(self):
        frag = self.dir / "frag.mp4"
        subprocess.run([self.exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.src),
                        "-map", "0", "-c", "copy", "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
                        "-f", "mp4", str(frag)], check=True, timeout=120)
        h = app.analyze_video(self.exe, frag, 8.0)
        self.assertEqual(h["codec"], "h264")
        self.assertGreaterEqual(h["frames"], 220)
        self.assertFalse(h["frozen"])

    def test_finalize_old_mkv_to_mp4(self):
        tmp = tmpdir("finalize")
        db = app.DB(tmp / "t.db")
        s = app.Settings(db)
        s.set_many({"output_dir": str(tmp / "out")})
        m = app.Manager(db, s, app.Engine(s))

        mkv = tmp / "u_2026-01-01_00-00-00_000000.mkv"
        subprocess.run([self.exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.src),
                        "-c", "copy", str(mkv)], check=True, timeout=120)
        uid = db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("u", "2026-01-01T00:00:00"))
        rid = db.x("INSERT INTO recordings(user_id,path,started_at,status,quality) VALUES(?,?,?,?,?)",
                   (uid, str(mkv), "2026-01-01T00:00:00", "recording", "hd/flv"))
        asyncio.run(m._finalize(rid, mkv, datetime.now(), 8.0, "hd/flv", "@u", "u"))

        row = db.q("SELECT * FROM recordings WHERE id=?", (rid,))[0]
        self.assertEqual(row["status"], "done")
        self.assertTrue(row["path"].endswith(".mp4"), row["path"])
        self.assertFalse(mkv.exists(), "el .mkv debe desaparecer tras el remux")
        self.assertTrue(Path(row["path"]).exists())
        self.assertGreater(row["size_bytes"], 1000)
        self.assertTrue((app.THUMB_DIR / f"{rid}.jpg").exists())
        self.assertTrue(row["analyzed_at"], "el analisis debe quedar guardado en la BD")
        self.assertTrue(row["codec"])
        self.assertGreater(row["frames"] or 0, 0)
        db.conn.close()

    def test_merge_segments(self):
        a, b = self.dir / "seg1.mp4", self.dir / "seg2.mp4"
        for src, dst, ss in ((self.src, a, "0"), (self.src, b, "4")):
            subprocess.run([self.exe, "-hide_banner", "-loglevel", "error", "-y",
                            "-ss", ss, "-t", "4", "-i", str(src), "-c", "copy", str(dst)],
                           check=True, timeout=120)
        out = self.dir / "unido.mp4"
        steps: list[float] = []
        merged = app.merge_segments(self.exe, [a, b], out, 8.0, progress=steps.append)
        self.assertIsNotNone(merged)
        self.assertTrue(merged.exists() and merged.stat().st_size > 1000)
        self.assertAlmostEqual(app.probe_duration(self.exe, merged), 8.0, delta=1.2)
        self.assertTrue(steps and steps[-1] == 1.0)
        self.assertIsNone(app.merge_segments(self.exe, [a], out, 4.0), "un solo archivo no se une")

    def test_repair_with_progress(self):
        dst = self.dir / "reparado.mp4"
        steps: list[float] = []
        ok = app.repair_video(self.exe, self.src, dst, 240, 8.0,
                              progress=lambda f, secs: steps.append(f))
        self.assertTrue(ok)
        self.assertTrue(dst.exists() and dst.stat().st_size > 1000)
        self.assertTrue(steps and steps[-1] == 1.0)
        self.assertAlmostEqual(app.probe_duration(self.exe, dst), 8.0, delta=1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
