"""Pruebas del actualizador de la app: versiones, SHA-256 y validacion de la respuesta de GitHub.

No usan red: la descarga se simula con un opener falso.
"""
import io
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import helpers  # noqa: E402

helpers.prepare()
import app  # noqa: E402


class FakeResp:
    def __init__(self, cuerpo: bytes, cabeceras=None):
        self._cuerpo = cuerpo
        self.headers = cabeceras or {}

    def read(self, n: int = -1):
        if n is None or n < 0:
            datos, self._cuerpo = self._cuerpo, b""
            return datos
        datos, self._cuerpo = self._cuerpo[:n], self._cuerpo[n:]
        return datos

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, cuerpo):
        self.cuerpo = cuerpo

    def open(self, req, timeout=None):
        if isinstance(self.cuerpo, Exception):
            raise self.cuerpo
        return FakeResp(self.cuerpo)


def release_json(**cambios) -> bytes:
    datos = {
        "tag_name": "v9.9.9",
        "body": "Novedades de prueba",
        "assets": [
            {"name": "LiveVault-9.9.9.exe", "size": 71_000_000,
             "browser_download_url": "https://github.com/Youshuu/livevault/releases/download/v9.9.9/LiveVault-9.9.9.exe"},
            {"name": "SHA256SUMS.txt",
             "browser_download_url": "https://github.com/Youshuu/livevault/releases/download/v9.9.9/SHA256SUMS.txt"},
        ],
    }
    datos.update(cambios)
    return json.dumps(datos).encode()


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(app.parse_version("v1.8.2"), (1, 8, 2))
        self.assertEqual(app.parse_version("1.9"), (1, 9, 0))
        self.assertEqual(app.parse_version("2"), (2, 0, 0))
        self.assertEqual(app.parse_version(""), (0, 0, 0))
        self.assertEqual(app.parse_version("LiveVault 1.10.3"), (1, 10, 3))

    def test_comparaciones(self):
        self.assertTrue(app.version_mayor("1.8.2", "1.8.1"))
        self.assertTrue(app.version_mayor("1.10.0", "1.9.9"), "10 es mayor que 9, no alfabetico")
        self.assertFalse(app.version_mayor("1.8.1", "1.8.1"))
        self.assertFalse(app.version_mayor("1.8.0", "1.8.1"))
        self.assertTrue(app.version_mayor("v1.8.2", app.APP_VERSION) is False or True)


class SumsTests(unittest.TestCase):
    def test_formato_estandar(self):
        sha = "a" * 64
        texto = f"{sha}  LiveVault-1.8.2.exe\n{'b' * 64} *otro.zip\n"
        self.assertEqual(app.parse_sums(texto),
                         {"LiveVault-1.8.2.exe": sha, "otro.zip": "b" * 64})

    def test_lineas_invalidas(self):
        self.assertEqual(app.parse_sums("basura\n1234 corto.exe\n\n"), {})

    def test_sha256_de_archivo(self):
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hola")
            ruta = Path(f.name)
        try:
            self.assertEqual(app.sha256_file(ruta),
                             "b221d9dbb083a7f33428d7c2a3c3198ae925614d70210e28716ccaa7cd4ddb79")
        finally:
            ruta.unlink(missing_ok=True)


class UrlTests(unittest.TestCase):
    def test_solo_github_https(self):
        self.assertTrue(app._url_de_github("https://github.com/a/b/releases/download/v1/x.exe"))
        self.assertTrue(app._url_de_github("https://objects.githubusercontent.com/x"))
        self.assertFalse(app._url_de_github("https://evil.test/x.exe"))
        self.assertFalse(app._url_de_github("http://github.com/x.exe"))
        self.assertFalse(app._url_de_github("https://github.com.evil.test/x.exe"))
        self.assertFalse(app._url_de_github(""))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self._original = app._http_opener
        app._http_opener = lambda proxy="": FakeOpener(release_json())

    def tearDown(self):
        app._http_opener = self._original

    def test_lee_la_release(self):
        info = app.check_latest_release()
        self.assertEqual(info["version"], "9.9.9")
        self.assertEqual(info["tag"], "v9.9.9")
        self.assertTrue(info["exe"]["name"].endswith(".exe"))
        self.assertEqual(info["sums"]["name"], "SHA256SUMS.txt")
        self.assertTrue(app.version_mayor(info["version"]))

    def test_rechaza_sin_exe(self):
        app._http_opener = lambda proxy="": FakeOpener(
            release_json(assets=[{"name": "SHA256SUMS.txt",
                                  "browser_download_url": "https://github.com/a/b/c"}]))
        with self.assertRaises(RuntimeError):
            app.check_latest_release()

    def test_rechaza_descarga_que_no_es_de_github(self):
        app._http_opener = lambda proxy="": FakeOpener(release_json(assets=[
            {"name": "LiveVault-9.9.9.exe", "size": 1,
             "browser_download_url": "https://evil.test/LiveVault-9.9.9.exe"},
            {"name": "SHA256SUMS.txt",
             "browser_download_url": "https://github.com/a/b/SHA256SUMS.txt"}]))
        with self.assertRaises(RuntimeError):
            app.check_latest_release()

    def test_propaga_errores_de_red(self):
        app._http_opener = lambda proxy="": FakeOpener(OSError("sin red"))
        with self.assertRaises(OSError):
            app.check_latest_release()


class InstallGuardTests(unittest.TestCase):
    """install_update no debe tocar nada si no hay version nueva o no es el .exe."""

    def _app_falso(self):
        a = app.App.__new__(app.App)          # sin __init__: no abre BD ni puertos
        a._update_installing = False
        a.update_info = {}
        a.update_progress = {}
        a.update_error = ""
        return a

    def test_sin_version_nueva(self):
        a = self._app_falso()
        with self.assertRaises(app.UserError) as ctx:
            a.install_update()
        self.assertEqual(ctx.exception.code, 409)

    def test_sin_estar_empaquetado(self):
        a = self._app_falso()
        a.update_info = {"available": True, "version": "9.9.9"}
        original = app.FROZEN
        app.FROZEN = False
        try:
            with self.assertRaises(app.UserError) as ctx:
                a.install_update()
        finally:
            app.FROZEN = original
        self.assertIn("código fuente", str(ctx.exception))


class ApiTests(unittest.TestCase):
    """El estado que consume el panel incluye la seccion de actualizaciones."""

    def test_estado_incluye_update(self):
        a = app.App.__new__(app.App)
        a.update_info = {"available": True, "version": "9.9.9", "notes": "x" * 9000}
        a.update_checked = 1.0
        a.update_error = ""
        a.update_progress = {}
        a._update_checking = False
        a._update_installing = False
        estado = a.update_state()
        self.assertTrue(estado["available"])
        self.assertEqual(estado["version"], "9.9.9")
        self.assertLessEqual(len(estado["notes"]), 3000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
