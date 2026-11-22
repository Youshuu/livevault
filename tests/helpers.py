"""Utilidades comunes de las pruebas: datos dentro del proyecto y carpetas limpias."""
import os
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_DATA = HERE / ".test-data"


def prepare() -> Path:
    """Crea la carpeta de datos de prueba y apunta TKREC_DATA ahi (antes de importar app)."""
    TEST_DATA.mkdir(parents=True, exist_ok=True)
    os.environ["TKREC_DATA"] = str(TEST_DATA)      # forzado: nunca tocar los datos reales del usuario
    return TEST_DATA


def tmpdir(name: str) -> Path:
    d = TEST_DATA / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d
