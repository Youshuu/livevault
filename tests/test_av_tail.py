"""Video que termina antes que el audio (TikTok deja de mandar imagen): diagnostico y recorte."""
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


class AudioTailTests(unittest.TestCase):
    def setUp(self):
        self.exe = app.find_ffmpeg()
        if not self.exe:
            self.skipTest("ffmpeg no disponible")
        self.dir = tmpdir("av-tail")
        video = self.dir / "v.mp4"
        audio = self.dir / "a.m4a"
        self.src = self.dir / "live.mp4"
        subprocess.run([self.exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=30", "-t", "6",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(video)],
                       check=True, timeout=120)
        subprocess.run([self.exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "10",
                        "-c:a", "aac", str(audio)], check=True, timeout=120)
        subprocess.run([self.exe, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(video), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
                        "-c", "copy", str(self.src)], check=True, timeout=120)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_detects_audio_without_video(self):
        stats = app._framecrc_stats(self.exe, self.src, 120)
        self.assertIsNotNone(stats)
        self.assertAlmostEqual(stats["tail_secs"], 4.0, delta=0.6)
        self.assertAlmostEqual(stats["video_span"], 6.0, delta=0.6)
        self.assertAlmostEqual(stats["video_fps"], 30.0, delta=2.0)
        self.assertTrue(stats["ts_bad"], "el desfase de 4 s debe marcarse")

    def test_repair_trims_the_silent_video_tail(self):
        stats = app._framecrc_stats(self.exe, self.src, 120)
        dst = self.dir / "reparado.mp4"
        ok = app.repair_video(self.exe, self.src, dst, stats["frames"], 10.0,
                              progress=lambda f, secs: None,
                              video_secs=stats["video_span"], tail_secs=stats["tail_secs"])
        self.assertTrue(ok)
        fixed = app._framecrc_stats(self.exe, dst, 120)
        self.assertIsNotNone(fixed)
        self.assertLess(fixed["tail_secs"], 0.8, "la copia no debe conservar el audio sin imagen")
        self.assertAlmostEqual(app.probe_duration(self.exe, dst), 6.0, delta=0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
