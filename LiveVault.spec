# -*- mode: python ; coding: utf-8 -*-
"""Empaquetado de LiveVault con PyInstaller.

Es la unica definicion del contenido del .exe: la usan tanto GitHub Actions (release)
como Crear_EXE.bat en local, para que el binario que se publica y el que se prueba sean
el mismo.

    pyinstaller --noconfirm --clean LiveVault.spec                     # un solo .exe
    $env:LIVEVAULT_ONEDIR="1"; pyinstaller --noconfirm --clean LiveVault.spec   # carpeta
"""
import os
import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH)
APP_NAME = "LiveVault"
ONEDIR = os.environ.get("LIVEVAULT_ONEDIR") == "1"

_src = (ROOT / "app.py").read_text(encoding="utf-8")
APP_VERSION = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', _src, re.M).group(1)

# --- contenido ---------------------------------------------------------------------
datas = [(str(ROOT / "ui"), "ui"), (str(ROOT / "wheels"), "wheels")]
binaries = []
hiddenimports = ["pystray._win32", "protobuf_to_dict"]

# El motor (TikTokLive y sus dependencias), ffmpeg y pip se empaquetan enteros: el .exe
# tiene que funcionar sin Python y poder actualizar su propio motor con pip.
for paquete in ("TikTokLive", "TikTokLiveProto", "EulerApiSdk", "betterproto2",
                "imageio_ffmpeg", "yt_dlp", "pip"):
    try:
        d, b, h = collect_all(paquete)
    except Exception as e:                                  # paquete opcional que falte
        print(f"AVISO: no se pudo empaquetar {paquete}: {e}")
        continue
    datas += d
    binaries += b
    hiddenimports += h

for dist in ("TikTokLive", "imageio-ffmpeg", "yt-dlp"):
    try:
        datas += copy_metadata(dist)
    except Exception as e:
        print(f"AVISO: sin metadatos de {dist}: {e}")

# --- version del archivo .exe (propiedades de Windows) ------------------------------
_nums = [int(n) for n in re.findall(r"\d+", APP_VERSION)[:3]]
while len(_nums) < 4:
    _nums.append(0)
_vers = tuple(_nums[:4])
_build = ROOT / ".build"
_build.mkdir(exist_ok=True)
_version_file = _build / "version_info.txt"
_version_file.write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={_vers}, prodvers={_vers}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040A04B0', [
        StringStruct('CompanyName', 'LiveVault'),
        StringStruct('FileDescription', 'Grabador automatico de directos de TikTok'),
        StringStruct('FileVersion', '{APP_VERSION}'),
        StringStruct('InternalName', 'LiveVault'),
        StringStruct('LegalCopyright', 'MIT License - ver LICENSE'),
        StringStruct('OriginalFilename', 'LiveVault.exe'),
        StringStruct('ProductName', 'LiveVault'),
        StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1034, 1200])])
  ]
)
""",
    encoding="utf-8",
)

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

_common = dict(
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                      # UPX dispara falsos positivos de antivirus
    console=False,                  # es una app de ventana/bandeja, sin consola
    disable_windowed_traceback=False,
    icon=str(ROOT / "icon.ico"),
    version=str(_version_file),
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_common)
    COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **_common)
