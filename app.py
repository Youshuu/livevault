#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LiveVault - grabador automatico de directos de TikTok
=====================================================
Monitorea usuarios de TikTok, graba sus lives automaticamente (varios a la vez),
muestra estado / espectadores y guarda un historial por usuario.

Motor de deteccion: libreria TikTokLive (se puede actualizar desde la propia app).
Motor de grabacion : ffmpeg (copia directa del stream, sin recodificar).
Respaldo           : yt-dlp, solo para obtener la URL del stream si el motor principal falla.

Interfaz: pagina web local en http://127.0.0.1:8765  (solo accesible desde tu PC).
"""
from __future__ import annotations

import argparse
import asyncio
import atexit
import base64
import collections
import ctypes
import hmac
import io
import ipaddress
import json
import logging
import logging.handlers
import mimetypes
import os
import platform
import random
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import webbrowser
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

APP_VERSION = "1.8.0"
FROZEN = bool(getattr(sys, "frozen", False))
WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if WIN else 0
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
WORKER_MODE = len(sys.argv) > 1 and sys.argv[1] in ("--pip-worker", "--selftest")


def _on_onedrive(p: Path) -> bool:
    """¿La carpeta esta dentro de OneDrive? Ahi SQLite en WAL y los MP4 dan bloqueos y subidas."""
    try:
        s = str(p.resolve()).lower()
    except OSError:
        return False
    for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        v = os.environ.get(k)
        if v:
            try:
                if s.startswith(str(Path(v).resolve()).lower()):
                    return True
            except OSError:
                continue
    return False


DATA_DIR_FALLBACK = False


def _pick_data_dir() -> Path:
    global DATA_DIR_FALLBACK
    env = os.environ.get("TKREC_DATA")
    if env:
        return Path(env)
    d = APP_DIR / "datos"
    try:                                     # junto al programa (portable); si no se puede escribir, en AppData
        d.mkdir(parents=True, exist_ok=True)
        t = d / ".escritura"
        t.write_text("1")
        t.unlink()
        return d
    except OSError:
        pass
    DATA_DIR_FALLBACK = True
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    new = Path(base) / "LiveVault" / "datos"
    old = Path(base) / "TikTokLiveRecorder" / "datos"      # carpeta de versiones anteriores
    try:
        if old.is_dir() and not new.exists():              # conservar usuarios, ajustes y grabaciones
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
    except OSError:
        if old.is_dir():
            return old
    return new


DATA_DIR = _pick_data_dir()
LOG_DIR = DATA_DIR / "logs"
AVATAR_DIR = DATA_DIR / "avatars"
THUMB_DIR = DATA_DIR / "thumbs"
BACKUP_DIR = DATA_DIR / "backups"
for _d in (DATA_DIR, LOG_DIR, AVATAR_DIR, THUMB_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_panel_token() -> str:
    """Clave aleatoria del panel: la exige el servidor a TODAS las peticiones (salvo /api/ping).

    Sin esto, cualquier otro programa (u otro usuario de Windows, porque 127.0.0.1 es comun a
    toda la maquina) podria leer tus grabaciones o cambiar los ajustes del programa.
    Se guarda en datos/ para que una pestana abierta siga funcionando tras reiniciar.
    """
    p = DATA_DIR / "panel.token"
    try:
        t = p.read_text(encoding="utf-8").strip()
        if len(t) >= 32:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(32)
    try:
        p.write_text(t, encoding="utf-8")
    except OSError:
        pass
    return t


PANEL_TOKEN = _load_panel_token()


def _pick_output_dir() -> Path:
    """Carpeta de grabaciones por defecto: junto al programa si se puede escribir; si no, junto a datos."""
    for cand in (APP_DIR / "grabaciones", DATA_DIR.parent / "grabaciones"):
        try:
            cand.mkdir(parents=True, exist_ok=True)
            t = cand / ".escritura"
            t.write_text("1")
            t.unlink()
            return cand
        except OSError:
            continue
    return DATA_DIR.parent / "grabaciones"


DEFAULT_OUTPUT_DIR = _pick_output_dir()

# --- Motor actualizable --------------------------------------------------------------
# Cada actualizacion se instala en una carpeta nueva (motor-pyXY/<fecha>) y un archivo
# "actual.txt" apunta a la vigente. Nunca se renombra la carpeta en uso (en Windows los
# .pyd cargados quedan bloqueados). Es por version de Python: los binarios no son intercambiables.
PYTAG = f"py{sys.version_info.major}{sys.version_info.minor}"
ENGINE_ROOT = DATA_DIR / f"motor-{PYTAG}"


def current_engine_dir() -> Optional[Path]:
    try:
        name = (ENGINE_ROOT / "actual.txt").read_text(encoding="utf-8").strip()
        d = ENGINE_ROOT / name
        return d if name and d.is_dir() else None
    except OSError:
        return None


def set_current_engine(name: str) -> None:
    ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = ENGINE_ROOT / "actual.tmp"
    tmp.write_text(name, encoding="utf-8")
    os.replace(tmp, ENGINE_ROOT / "actual.txt")


def cleanup_old_engines() -> None:
    cur = current_engine_dir()
    if ENGINE_ROOT.is_dir():
        for d in ENGINE_ROOT.iterdir():
            if d.is_dir() and d != cur:
                shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(DATA_DIR / "motor_nuevo", ignore_errors=True)
    shutil.rmtree(DATA_DIR / "motor_anterior", ignore_errors=True)


class _EngineFirst:
    """Meta-path finder: el motor actualizado gana siempre al que viene empaquetado en el .exe."""
    root = ""
    tops: set = set()

    @classmethod
    def find_spec(cls, name, path=None, target=None):
        from importlib.machinery import PathFinder
        if path is None:
            return PathFinder.find_spec(name, [cls.root]) if name in cls.tops else None
        try:
            if any(str(x).startswith(cls.root) for x in path):
                return PathFinder.find_spec(name, list(path))
        except Exception:
            pass
        return None


def activate_engine(d: Path) -> None:
    tops = set()
    for e in d.iterdir():
        n = e.name
        if n.startswith(("__", "bin")) or n.endswith((".dist-info", ".egg-info")):
            continue
        if e.is_dir():
            tops.add(n)
        elif e.suffix in (".py", ".pyd", ".so"):
            tops.add(n.split(".")[0])
    _EngineFirst.root, _EngineFirst.tops = str(d), tops
    if _EngineFirst not in sys.meta_path:
        sys.meta_path.insert(0, _EngineFirst)
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))


if not WORKER_MODE:
    try:
        _legacy = DATA_DIR / "motor"         # carpeta de la version 1.0 (solo si se usa con Python instalado)
        if not FROZEN and _legacy.is_dir() and not ENGINE_ROOT.exists():
            ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
            _legacy.rename(ENGINE_ROOT / "heredado")
            set_current_engine("heredado")
        cleanup_old_engines()
        _cur = current_engine_dir()
        if _cur:
            activate_engine(_cur)
    except Exception:
        pass                                 # sin motor actualizado se usa el instalado/empaquetado

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def sp_run(cmd, **kw):
    """subprocess.run que nunca hereda handles invalidos (necesario en un .exe sin consola)."""
    kw.setdefault("stdin", subprocess.DEVNULL)
    if WIN:
        kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(cmd, **kw)


# --------------------------------------------------------------------------- hijos (ffmpeg)
# Sin esto, si el programa se cierra de golpe (o lo mata el Administrador de tareas), ffmpeg sigue
# grabando en segundo plano. El Job Object de Windows los mata al cerrar el proceso, incluso al crashear.
_CHILD_PIDS: set[int] = set()
_CHILD_LOCK = threading.Lock()
_JOB_HANDLE: Optional[int] = None
_JOB_TRIED = False


def _job_handle() -> Optional[int]:
    global _JOB_HANDLE, _JOB_TRIED
    if not WIN or _JOB_TRIED:
        return _JOB_HANDLE
    _JOB_TRIED = True
    try:
        from ctypes import wintypes

        class _BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class _IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", _BasicLimit),
                        ("IoInfo", _IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        h = kernel32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(int(h), 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(int(h))
            return None
        _JOB_HANDLE = int(h)
    except Exception:
        log.warning("No se pudo crear el Job Object; los ffmpeg podrian quedar vivos al crashear\n%s",
                    traceback.format_exc())
        _JOB_HANDLE = None
    return _JOB_HANDLE


def _assign_to_job(pid: int) -> None:
    if not WIN or not pid:
        return
    h = _job_handle()
    if not h:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        ph = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)   # SET_QUOTA | TERMINATE
        if not ph:
            return
        try:
            kernel32.AssignProcessToJobObject(ctypes.c_void_p(h), ctypes.c_void_p(ph))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(ph))
    except Exception:
        log.debug("No se pudo asignar el proceso %s al Job Object", pid)


def register_child(pid: int) -> None:
    with _CHILD_LOCK:
        _CHILD_PIDS.add(pid)
    _assign_to_job(pid)


def unregister_child(pid: int) -> None:
    with _CHILD_LOCK:
        _CHILD_PIDS.discard(pid)


def kill_children() -> None:
    """Termina los procesos hijos que sigan vivos (cierre ordenado o fin del proceso)."""
    with _CHILD_LOCK:
        pids = list(_CHILD_PIDS)
        _CHILD_PIDS.clear()
    for pid in pids:
        try:
            if WIN:
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.OpenProcess.restype = ctypes.c_void_p
                h = kernel32.OpenProcess(0x0001, False, pid)
                if h:
                    kernel32.TerminateProcess(ctypes.c_void_p(h), 1)
                    kernel32.CloseHandle(ctypes.c_void_p(h))
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass


atexit.register(kill_children)


# --------------------------------------------------------------------------- logging
log = logging.getLogger("tkrec")


class _RedactFilter(logging.Filter):
    """Redaccion en el propio handler: vale para cualquier log, incluido un traceback ajeno."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            clean = redact(msg)
            if clean != msg:
                record.msg, record.args = clean, ()
        except Exception:
            pass
        return True


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log.setLevel(logging.INFO)
    for h in list(log.handlers):            # cerrar los anteriores: si no, quedan descriptores abiertos
        try:
            h.close()
        except Exception:
            pass
    log.handlers.clear()
    err = logging.handlers.RotatingFileHandler(LOG_DIR / "errores.log", maxBytes=2_000_000,
                                               backupCount=3, encoding="utf-8")
    err.setLevel(logging.WARNING)
    err.setFormatter(fmt)
    act = logging.handlers.RotatingFileHandler(LOG_DIR / "actividad.log", maxBytes=2_000_000,
                                               backupCount=2, encoding="utf-8")
    act.setLevel(logging.INFO)
    act.setFormatter(fmt)
    handlers = [err, act]
    if sys.stderr is not None:              # un .exe sin consola no tiene stderr
        con = logging.StreamHandler()
        con.setLevel(logging.INFO)
        con.setFormatter(fmt)
        handlers.append(con)
    for h in handlers:
        h.addFilter(_RedactFilter())
        log.addHandler(h)


_URL_Q = re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*")
_SESS = re.compile(r"(sessionid[a-z_]*=)[^\s;&\"']+", re.I)
_PROXY_CRED = re.compile(r"(\w+://)[^/@\s]+:[^/@\s]+@")
_TG_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_AUTH = re.compile(r"(authorization\s*[:=]\s*(?:bearer|bot)\s+)[^\s,\"']+", re.I)
_HOOK_PATH = re.compile(r"(discord(?:app)?\.com/api/webhooks/|slack\.com/services/|hooks\.slack\.com/services/)"
                        r"[^\s\"']+", re.I)


def redact(text: str) -> str:
    """Quita tokens de URLs, sessionid y credenciales de proxy antes de mostrar/guardar."""
    text = _URL_Q.sub(r"\1?<redactado>", text)
    text = _SESS.sub(r"\1<redactado>", text)
    text = _TG_TOKEN.sub("bot<redactado>", text)
    text = _AUTH.sub(r"\1<redactado>", text)
    text = _HOOK_PATH.sub(r"\1<redactado>", text)      # el secreto del webhook va en la ruta
    return _PROXY_CRED.sub(r"\1<redactado>@", text)


def scrub_paths(text: str) -> str:
    """Sustituye carpetas locales (que delatan el nombre de usuario de Windows) por etiquetas.

    Se aplica al reporte de diagnostico y al paquete de soporte, que el usuario comparte en
    publico al pedir ayuda.
    """
    reps = [(str(DATA_DIR), "<carpeta de datos>"),
            (str(APP_DIR), "<carpeta del programa>"),
            (str(Path.home()), "<tu usuario>")]
    for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "USERNAME"):
        v = os.environ.get(k)
        if v and len(v) > 2:
            reps.append((v, "<tu usuario>"))
    for a, b in sorted({a for a in reps if a}, key=lambda x: -len(x)):
        text = text.replace(a, b)
    return text


# --------------------------------------------------------------------------- base de datos
SCHEMA_VERSION = 5


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._last_backup = 0.0
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA busy_timeout=10000")
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                added_at TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                auto_record INTEGER NOT NULL DEFAULT 1,
                notified_room TEXT);
            CREATE TABLE IF NOT EXISTS recordings(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_sec REAL DEFAULT 0,
                size_bytes INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'recording',
                quality TEXT,
                note TEXT,
                session_id TEXT,
                favorite INTEGER NOT NULL DEFAULT 0,
                codec TEXT,
                width INTEGER,
                height INTEGER,
                fps REAL,
                frames INTEGER,
                frozen_secs REAL DEFAULT 0,
                ts_bad INTEGER DEFAULT 0,
                analyzed_at TEXT);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS bot_state(key TEXT PRIMARY KEY, value TEXT);
            CREATE INDEX IF NOT EXISTS idx_rec_user   ON recordings(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_rec_status ON recordings(status);
            """)
            ucols = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
            for name, ddl in (("nickname", "TEXT"), ("avatar_url", "TEXT"), ("avatar_ver", "INTEGER DEFAULT 0"),
                              ("notified_room", "TEXT")):
                if name not in ucols:                # BD de una version anterior
                    self.conn.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
            rcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(recordings)")}
            for name, ddl in (("session_id", "TEXT"),
                              ("favorite", "INTEGER NOT NULL DEFAULT 0"),
                              ("codec", "TEXT"), ("width", "INTEGER"), ("height", "INTEGER"),
                              ("fps", "REAL"), ("frames", "INTEGER"),
                              ("frozen_secs", "REAL DEFAULT 0"), ("ts_bad", "INTEGER DEFAULT 0"),
                              ("analyzed_at", "TEXT")):
                if name not in rcols:                # BD de una version anterior
                    self.conn.execute(f"ALTER TABLE recordings ADD COLUMN {name} {ddl}")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_session ON recordings(session_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_fav ON recordings(favorite)")
            self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.commit()

    def q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # -- salud y copias de seguridad -------------------------------------
    def quick_check(self) -> str:
        try:
            with self.lock:
                row = self.conn.execute("PRAGMA quick_check").fetchone()
            return str(row[0]) if row else "?"
        except Exception as e:
            return f"error: {e}"

    def backup(self, folder: Path, keep: int = 7) -> Optional[Path]:
        """Copia consistente con la API de SQLite (con WAL, copiar el archivo no basta)."""
        tmp: Optional[Path] = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / f"tkrec-{datetime.now():%Y-%m-%d_%H%M%S_%f}.db"
            tmp = dest.with_name(dest.name + ".tmp")
            with self.lock:
                dst = sqlite3.connect(str(tmp))
                try:
                    self.conn.backup(dst)
                finally:
                    dst.close()
            os.replace(tmp, dest)
            keep = max(1, keep)
            old = sorted(folder.glob("tkrec-*.db"), key=lambda p: p.stat().st_mtime)[:-keep]
            for p in old:
                try:
                    p.unlink()
                except OSError:
                    pass
            return dest
        except Exception:
            log.warning("No se pudo crear la copia de seguridad\n%s", traceback.format_exc())
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            return None

    def backup_if_due(self, folder: Path, keep: int = 7, every: float = 86400.0) -> Optional[Path]:
        """Una copia al dia como mucho; si ya hay una reciente, no hace nada."""
        now = time.time()
        if not self._last_backup and folder.is_dir():
            self._last_backup = max((p.stat().st_mtime for p in folder.glob("tkrec-*.db")), default=0.0)
        if now - self._last_backup < every:
            return None
        dest = self.backup(folder, keep)
        if dest is not None:
            self._last_backup = now
        return dest


# --------------------------------------------------------------------------- secretos en reposo
_SECRET_PREFIX = "dpapi:"


def _dpapi(protect: bool, data: bytes) -> Optional[bytes]:
    """Cifra/descifra con el usuario de Windows (DPAPI). None si no esta disponible."""
    if not WIN or not data:
        return None
    try:
        class _Blob(ctypes.Structure):
            _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

        src_buf = ctypes.create_string_buffer(data, len(data))
        src = _Blob(len(data), ctypes.cast(src_buf, ctypes.POINTER(ctypes.c_char)))
        dst = _Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst))  # UI_FORBIDDEN
        if not ok:
            return None
        try:
            return ctypes.string_at(dst.pbData, dst.cbData)
        finally:
            ctypes.WinDLL("kernel32", use_last_error=True).LocalFree(ctypes.cast(dst.pbData, ctypes.c_void_p))
    except Exception:
        log.debug("DPAPI no disponible\n%s", traceback.format_exc())
        return None


_PLAIN_WARNED = False


def encrypt_secret(value: str) -> str:
    global _PLAIN_WARNED
    if not value or value.startswith(_SECRET_PREFIX):
        return value
    enc = _dpapi(True, value.encode("utf-8"))
    if enc:
        return _SECRET_PREFIX + base64.b64encode(enc).decode()
    if not _PLAIN_WARNED:                       # no dejar un secreto en claro sin avisar
        _PLAIN_WARNED = True
        log.warning("No se pudo cifrar con DPAPI: los secretos se guardaran en claro en la base de datos. "
                    "En Windows esto pasa si el perfil de usuario esta dañado o si el programa corre con "
                    "otro usuario. En Linux/macOS no hay cifrado del sistema: protege la carpeta datos/.")
    return value


def decrypt_secret(value: str) -> str:
    if not value or not value.startswith(_SECRET_PREFIX):
        return value
    try:
        raw = _dpapi(False, base64.b64decode(value[len(_SECRET_PREFIX):]))
    except Exception:
        raw = None
    if raw is None:
        log.warning("No se pudo descifrar un secreto guardado (¿otro usuario de Windows?). "
                    "Vuelve a escribirlo en Ajustes.")
        return ""
    return raw.decode("utf-8", "replace")


DEFAULTS = {
    "output_dir": str(DEFAULT_OUTPUT_DIR),
    "check_interval": "45",       # segundos entre revisiones de cada usuario
    "quality": "max",             # max | hd | sd
    "final_format": "mp4",        # mp4 | mkv
    "session_id": "",             # opcional: cookie sessionid (lives +18)
    "proxy": "",                  # opcional: http://host:puerto
    "min_keep_kb": "300",         # grabaciones menores a esto se descartan
    "min_free_gb": "2",           # si queda menos espacio libre, no se inician grabaciones
    "retention_days": "0",        # 0 = no borrar por antiguedad
    "max_total_gb": "0",          # 0 = sin limite de tamano total
    "auto_update_on_start": "0",
    "ytdlp_fallback": "1",
    "ffmpeg_path": "",            # opcional: ruta propia de ffmpeg
    "notifications": "1",         # avisos de Windows al detectar/grabar
    "push_ntfy_server": "https://ntfy.sh",
    "push_ntfy_topic": "",        # secreto: quien conozca el tema recibe tus avisos
    "push_tg_token": "",          # secreto: token del bot de Telegram
    "push_tg_chat": "",
    "push_webhook": "",           # secreto: URL a la que se envia un JSON con el aviso
    "push_live": "1", "push_recording": "1", "push_saved": "1", "push_problem": "1", "push_session": "1",
    "push_ended": "1",
}
SECRET_KEYS = {"session_id", "proxy", "push_ntfy_topic", "push_tg_token", "push_webhook"}


class Settings:
    def __init__(self, db: DB):
        self.db = db
        self._cache: dict[str, str] = {}
        self._migrate_secrets()
        self.reload()

    def _migrate_secrets(self) -> None:
        """Cifra los secretos que quedaron en claro de una version anterior (una sola vez)."""
        try:
            for r in self.db.q("SELECT key,value FROM settings"):
                if (r["key"] in SECRET_KEYS and r["value"]
                        and not r["value"].startswith(_SECRET_PREFIX)):
                    enc = encrypt_secret(r["value"])
                    if enc != r["value"]:
                        self.db.x("UPDATE settings SET value=? WHERE key=?", (enc, r["key"]))
        except Exception:
            log.warning("No se pudieron cifrar los secretos antiguos\n%s", traceback.format_exc())

    def reload(self) -> None:
        rows = {r["key"]: r["value"] for r in self.db.q("SELECT key,value FROM settings")}
        self._cache = {**DEFAULTS, **{k: decrypt_secret(v) for k, v in rows.items()}}

    def get(self, key: str) -> str:
        return self._cache.get(key, DEFAULTS.get(key, ""))

    def int(self, key: str, lo: int, hi: int) -> int:
        try:
            v = int(float(self.get(key)))
        except (TypeError, ValueError):
            v = int(DEFAULTS[key])
        return max(lo, min(hi, v))

    def set_many(self, values: dict[str, str]) -> None:
        for k, v in values.items():
            if k in DEFAULTS:
                stored = encrypt_secret(str(v)) if k in SECRET_KEYS else str(v)
                self.db.x("INSERT INTO settings(key,value) VALUES(?,?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, stored))
        self.reload()

    def public(self) -> dict:
        d = dict(self._cache)
        for k in SECRET_KEYS:
            d[k + "_set"] = bool(d.get(k))
            d[k] = ""          # nunca se devuelve al navegador
        return d


def extract_sessionid(text: str) -> str:
    """Acepta la cookie pegada entera ('sessionid=...; tt-target-idc=...') o solo el valor."""
    text = (text or "").strip().strip('"\'')
    m = re.search(r"(?:^|[;\s])sessionid=([^;\s\"']+)", text)
    return (m.group(1) if m else text).strip()


# --------------------------------------------------------------------------- utilidades ffmpeg
_FFMPEG_CACHE: dict[str, Any] = {}
_FFMPEG_EXE: dict[str, str] = {}


def find_ffmpeg(settings: Optional[Settings] = None) -> Optional[str]:
    """Localiza ffmpeg una sola vez por configuracion (imageio_ffmpeg es lento si se llama en cada poll)."""
    custom = settings.get("ffmpeg_path").strip() if settings else ""
    if custom and Path(custom).exists():
        return custom
    cached = _FFMPEG_EXE.get(custom)
    if cached and Path(cached).exists():
        return cached
    exe: Optional[str] = None
    try:
        import imageio_ffmpeg  # type: ignore
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = None
    if not exe or not Path(exe).exists():
        exe = shutil.which("ffmpeg")
    if exe:
        _FFMPEG_EXE[custom] = exe
    return exe


def ffmpeg_version(exe: Optional[str]) -> str:
    if not exe:
        return "no encontrado"
    if exe in _FFMPEG_CACHE:
        return _FFMPEG_CACHE[exe]
    try:
        out = sp_run([exe, "-version"], capture_output=True, text=True, timeout=15).stdout.splitlines()[0]
        _FFMPEG_CACHE[exe] = out.replace("ffmpeg version ", "")[:60]
    except Exception as e:
        _FFMPEG_CACHE[exe] = f"error: {e}"
    return _FFMPEG_CACHE[exe]


_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(exe: str, path: Path) -> Optional[float]:
    try:
        r = sp_run([exe, "-hide_banner", "-i", str(path)], capture_output=True,
                   text=True, timeout=60, errors="replace")
        m = _DUR.search(r.stderr)
        if m:
            return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("No se pudo leer la duracion de %s (%s)", path.name, type(e).__name__)
    return None


# --------------------------------------------------------------------------- motor (TikTokLive)
TELEGRAM_API = "https://api.telegram.org"
SESSION_CHECK_URL = ("https://www.tiktok.com/passport/web/account/info/?aid=1459&app_language=en&app_name=tiktok_web"
                     "&device_platform=web_pc")
VIDEO_JOB_LOCK = threading.Lock()      # reparar o unir: un trabajo de video pesado a la vez
PUSH_KINDS = {"live": ("red_circle", 3), "recording": ("movie_camera", 3), "saved": ("white_check_mark", 3),
              "ended": ("checkered_flag", 3), "problem": ("warning", 3), "session": ("key", 4)}


def _http_opener(proxy: str):
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
    return urllib.request.build_opener(*handlers)


def _http_error_text(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")
            d = json.loads(body)
            return f"HTTP {e.code}: {d.get('description') or d.get('error') or body[:120]}"
        except Exception:
            return f"HTTP {e.code}"
    return f"{type(e).__name__}: {e}"


class Pusher:
    """Avisos al celular: ntfy y/o Telegram. Nunca lanza excepciones hacia el resto del programa."""

    def __init__(self, settings: Settings):
        self.s = settings

    def configured(self) -> dict[str, bool]:
        return {"ntfy": bool(self.s.get("push_ntfy_topic").strip()),
                "telegram": bool(self.s.get("push_tg_token").strip() and self.s.get("push_tg_chat").strip()),
                "webhook": bool(self.s.get("push_webhook").strip())}

    def send(self, kind: str, title: str, msg: str, image: Optional[Path] = None) -> None:
        if kind not in PUSH_KINDS or self.s.get("push_" + kind) != "1" or not any(self.configured().values()):
            return
        threading.Thread(target=self.send_sync, args=(kind, title, msg, image), daemon=True, name="push").start()

    @staticmethod
    def _retry(fn, attempts: int = 2) -> str:
        """Un fallo de red puntual no debe perder el aviso."""
        res = ""
        for i in range(attempts):
            res = fn()
            if res == "ok":
                return res
            if i + 1 < attempts:
                time.sleep(1.5 * (i + 1))
        return res

    def send_sync(self, kind: str, title: str, msg: str, image: Optional[Path] = None) -> dict[str, str]:
        res: dict[str, str] = {}
        opener = _http_opener(self.s.get("proxy").strip())
        conf = self.configured()
        if conf["ntfy"]:
            res["ntfy"] = self._retry(lambda: self._ntfy(opener, kind, title, msg))
        if conf["telegram"]:
            res["telegram"] = self._retry(lambda: self._telegram(opener, title, msg, image))
        if conf["webhook"]:
            res["webhook"] = self._retry(lambda: self._webhook(opener, kind, title, msg))
        for k, v in res.items():
            if v != "ok":
                log.warning("Aviso al celular (%s) fallo: %s", k, redact(v))
        return res

    def _webhook(self, opener, kind: str, title: str, msg: str) -> str:
        try:
            url = self.s.get("push_webhook").strip()
            body = json.dumps({"kind": kind, "title": title, "message": msg}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "LiveVault"})
            with opener.open(req, timeout=20) as r:
                return "ok" if 200 <= r.status < 300 else f"HTTP {r.status}"
        except Exception as e:
            return _http_error_text(e)

    def _ntfy(self, opener, kind: str, title: str, msg: str) -> str:
        try:
            server = (self.s.get("push_ntfy_server").strip() or "https://ntfy.sh").rstrip("/")
            tag, prio = PUSH_KINDS.get(kind, ("bell", 3))
            payload = {"topic": self.s.get("push_ntfy_topic").strip(), "title": title, "message": msg,
                       "tags": [tag], "priority": prio}
            req = urllib.request.Request(server, data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json", "User-Agent": "LiveVault"})
            with opener.open(req, timeout=20) as r:
                return "ok" if r.status == 200 else f"HTTP {r.status}"
        except Exception as e:
            return _http_error_text(e)

    def _telegram(self, opener, title: str, msg: str, image: Optional[Path]) -> str:
        token, chat = self.s.get("push_tg_token").strip(), self.s.get("push_tg_chat").strip()
        text = f"{title}\n{msg}"
        try:
            if image and image.exists():
                boundary = "----tkrec" + uuid.uuid4().hex
                body = b"".join([
                    f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'.encode(),
                    f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{text[:1000]}\r\n'.encode(),
                    f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="m.jpg"\r\n'
                    f'Content-Type: image/jpeg\r\n\r\n'.encode(), image.read_bytes(), b"\r\n",
                    f"--{boundary}--\r\n".encode()])
                req = urllib.request.Request(f"{TELEGRAM_API}/bot{token}/sendPhoto", data=body,
                                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            else:
                req = urllib.request.Request(
                    f"{TELEGRAM_API}/bot{token}/sendMessage",
                    data=json.dumps({"chat_id": chat, "text": text}).encode("utf-8"),
                    headers={"Content-Type": "application/json"})
            with opener.open(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            return "ok" if d.get("ok") else str(d.get("description") or "error")
        except Exception as e:
            return _http_error_text(e)

    def telegram_detect_chat(self) -> tuple[str, str]:
        """Busca el chat al que le escribiste al bot. Devuelve (chat_id, nombre)."""
        token = self.s.get("push_tg_token").strip()
        opener = _http_opener(self.s.get("proxy").strip())
        try:
            with opener.open(f"{TELEGRAM_API}/bot{token}/getUpdates?limit=20", timeout=25) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            raise RuntimeError(redact(_http_error_text(e)))
        for upd in reversed(d.get("result") or []):
            m = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
            chat = m.get("chat") or {}
            if chat.get("id") is None:
                continue
            text = str(m.get("text") or "").strip().lower()
            if text and not text.startswith("/start"):
                continue                    # solo /start autoriza un chat nuevo
            name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) \
                or chat.get("username") or ""
            return str(chat["id"]), name
        raise RuntimeError("No hay mensajes nuevos. Abre tu bot en Telegram, escríbele /start y vuelve a pulsar el botón.")


def _parse_session_payload(body: Any) -> dict:
    """Respuesta de passport -> {'state': ok|expired|unknown, 'account', 'nickname'}."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return {"state": "unknown", "account": "", "nickname": ""}
    account = str(data.get("username") or data.get("screen_name") or "")
    nickname = clean_nick(data.get("nickname"))
    if body.get("message") == "success" and (data.get("user_id_str") or account):
        return {"state": "ok", "account": account, "nickname": nickname}
    desc = str(data.get("description") or data.get("desc") or "").lower()
    if body.get("message") == "error" and (data.get("error_code") is not None
                                           or any(w in desc for w in ("session", "login", "expire"))):
        return {"state": "expired", "account": account, "nickname": nickname}
    return {"state": "unknown", "account": account, "nickname": nickname}


def check_session_info(sid: str, proxy: str) -> dict:
    """Comprueba la cookie y, si TikTok lo devuelve, de que cuenta es."""
    req = urllib.request.Request(SESSION_CHECK_URL, headers={
        "User-Agent": USER_AGENT, "Referer": "https://www.tiktok.com/", "Accept": "application/json",
        "Cookie": f"sessionid={sid}; sessionid_ss={sid}"})
    try:
        with _http_opener(proxy).open(req, timeout=25) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log.info("Chequeo de sessionid sin resultado (%s)", type(e).__name__)
        return {"state": "unknown", "account": "", "nickname": ""}
    return _parse_session_payload(body)


def check_session_sync(sid: str, proxy: str) -> str:
    """Compatibilidad: solo el estado."""
    return check_session_info(sid, proxy)["state"]


class EngineError(Exception):
    """kind: not_found | blocked | flaky | age | network | no_stream | missing | unknown"""

    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind


class NetGate:
    """Un solo punto de salida hacia TikTok: concurrencia maxima, bloqueo global y separacion minima.

    Antes solo `_check` usaba un semaforo, pero `_watch` y `_record` seguian consultando durante
    un bloqueo de TikTok y lo alargaban. Todo pasa ahora por aqui.
    """

    def __init__(self, limit: int = 4, min_gap: float = 0.0):
        self._limit = limit
        self._min_gap = min_gap
        self._sem: Optional[asyncio.Semaphore] = None
        self.block_until = 0.0
        self._last = 0.0

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:                       # se crea dentro del bucle en uso
            self._sem = asyncio.Semaphore(self._limit)
        return self._sem

    def blocked_for(self) -> float:
        return max(0.0, self.block_until - time.time())

    async def __aenter__(self) -> "NetGate":
        if self.blocked_for() > 0:
            raise EngineError("blocked", "TikTok esta limitando las consultas; se reanuda en "
                                         f"{int(self.blocked_for())} s.")
        await self._semaphore().acquire()
        if self._min_gap:
            wait = self._last + self._min_gap - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.time()
        return self

    async def __aexit__(self, *exc) -> None:
        self._semaphore().release()


GATE = NetGate(4)


class LiveStatus:
    def __init__(self, is_live=False, room_id="", viewers=None, title="", nickname="", avatar_url=""):
        self.is_live, self.room_id, self.viewers, self.title = is_live, room_id, viewers, title
        self.nickname, self.avatar_url = nickname, avatar_url


class Candidate:
    def __init__(self, quality: str, fmt: str, url: str):
        self.quality, self.fmt, self.url = quality, fmt, url

    def label(self) -> str:
        return f"{self.quality}/{self.fmt}"


class StreamInfo:
    def __init__(self, candidates: list[Candidate], viewers=None, title=""):
        self.candidates, self.viewers, self.title = candidates, viewers, title


def dig(d: Any, *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def to_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def find_key(obj: Any, key: str, depth: int = 0) -> Any:
    """Busca recursivamente la primera clave no vacia (las respuestas de TikTok cambian de forma)."""
    if depth > 8:
        return None
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        children = list(obj.values())
    elif isinstance(obj, list):
        children = obj[:20]
    else:
        return None
    for v in children:
        r = find_key(v, key, depth + 1)
        if r:
            return r
    return None


def key_paths(obj: Any, prefix: str = "", depth: int = 0, out: Optional[list] = None) -> list:
    """Estructura de una respuesta (solo claves, tipos y tamanos; nunca valores) para diagnostico."""
    out = [] if out is None else out
    if depth > 4 or len(out) > 90 or not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        size = f"[{len(v)}]" if isinstance(v, (str, list, dict)) else ""
        out.append(f"{path}:{type(v).__name__}{size}")
        key_paths(v, path, depth + 1, out)
    return out


def parse_user_room(data: dict) -> LiveStatus:
    """Respuesta de /api-live/user/room/ -> LiveStatus."""
    d = data.get("data") or {}
    room = d.get("liveRoom") or {}
    user = d.get("user") or {}
    status = to_int(room.get("status", user.get("status")))
    room_id = str(user.get("roomId") or room.get("roomId") or "")
    has_room = room_id not in ("", "0", "None")
    live = status == 2 or (status not in (None, 4) and has_room)
    viewers = None
    for cand in (dig(room, "liveRoomStats", "userCount"), room.get("userCount"),
                 dig(d, "liveRoomStats", "userCount")):
        viewers = to_int(cand)
        if viewers is not None:
            break
    nick = clean_nick(user.get("nickname") or dig(room, "owner", "nickname"))
    avatar = str(user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb") or "")
    return LiveStatus(live, room_id if has_room else "", viewers, str(room.get("title") or ""), nick, avatar)


def clean_nick(v: Any) -> str:
    """Nick tal como lo ve la gente (emojis incluidos); solo se quitan caracteres de control."""
    t = "".join(ch for ch in str(v or "") if ch.isprintable() or ch in "\u200d\ufe0f").strip()
    return t[:60]


def _json_str(raw: str) -> str:
    try:
        return json.loads('"' + raw + '"')
    except ValueError:
        return raw


def parse_profile_html(html: str, username: str) -> tuple[str, str]:
    """Pagina publica del perfil -> (nick, url del avatar). Solo acepta el bloque del propio usuario."""
    nick, avatar = "", ""
    m = re.search(r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            info = find_key(json.loads(m.group(1)), "userInfo")
            u = (info or {}).get("user") or {}
            if str(u.get("uniqueId", "")).lower() == username.lower():
                nick = clean_nick(u.get("nickname"))
                avatar = str(u.get("avatarLarger") or u.get("avatarMedium") or u.get("avatarThumb") or "")
        except Exception:
            pass
    if not nick:
        m = re.search(r'"uniqueId":"%s"[^{}]{0,600}?"nickname":"((?:[^"\\]|\\.)*)"' % re.escape(username), html, re.I)
        if m:
            nick = clean_nick(_json_str(m.group(1)))
    if not avatar:
        m = re.search(r'"uniqueId":"%s"[^{}]{0,900}?"avatarLarger":"([^"]+)"' % re.escape(username), html, re.I) \
            or re.search(r'"avatarLarger":"([^"]+)"', html)
        if m:
            avatar = _json_str(m.group(1))
    return nick, avatar


def make_thumb(exe: str, video: Path, duration: float, out: Path) -> bool:
    """Un fotograma del video (a ~10 % de la duracion) reducido a 480 px de ancho."""
    for t in (min(max(duration * 0.1, 2.0), 90.0) if duration else 2.0, 1.0, 0.0):
        try:
            r = sp_run([exe, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "5", str(out)],
                       capture_output=True, text=True, errors="replace", timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if r.returncode == 0 and out.exists() and out.stat().st_size > 500:
            return True
    out.unlink(missing_ok=True)
    return False


_TB_LINE = re.compile(r"#tb (\d+): (\d+)/(\d+)")
_MT_LINE = re.compile(r"#media_type (\d+): (\w+)")
_FREEZE_START = re.compile(r"freeze_start: ([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end: ([\d.]+)")


def _framecrc_stats(exe: str, video: Path, budget: int) -> Optional[dict]:
    """Estadisticas de tiempos leyendo `framecrc` por tuberia: sin archivos temporales ni RAM.

    Devuelve None si ffmpeg falla o se agota el tiempo. En ese caso no se puede concluir que el
    video no tenga imagen, solo que no se pudo analizar (antes se daba por "sin video").
    """
    cmd = [exe, "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(video),
           "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-f", "framecrc", "-"]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace",
                                creationflags=NO_WINDOW if WIN else 0)
    except OSError as e:
        log.warning("No se pudo analizar %s: %s", video.name, e)
        return None
    register_child(proc.pid)
    err: collections.deque = collections.deque(maxlen=20)

    def drain() -> None:
        try:
            assert proc.stderr is not None
            for l in proc.stderr:
                err.append(l.rstrip())
        except Exception:
            pass

    threading.Thread(target=drain, daemon=True, name="analyze-err").start()
    timer = threading.Timer(budget, lambda: proc.poll() is None and proc.kill())
    timer.daemon = True
    timer.start()
    tb: dict[int, float] = {}
    kind: dict[int, str] = {}
    first: dict[int, float] = {}
    prev: dict[int, float] = {}
    last: dict[int, float] = {}
    counts: dict[int, int] = {}
    backwards, maxgap = 0, 0.0
    rc = -1
    try:
        assert proc.stdout is not None
        for l in proc.stdout:
            m = _TB_LINE.match(l)
            if m:
                tb[int(m[1])] = int(m[2]) / max(1, int(m[3]))
                continue
            m = _MT_LINE.match(l)
            if m:
                kind[int(m[1])] = m[2]
                continue
            if not l or l.startswith("#"):
                continue
            parts = l.split(",")
            try:
                i = int(parts[0])
                ts = int(parts[1]) * tb.get(i, 0.001)
            except (ValueError, IndexError):
                continue
            counts[i] = counts.get(i, 0) + 1
            if i in prev:
                if ts < prev[i] - 0.001:
                    backwards += 1
                gap = ts - prev[i]
                if gap > maxgap:
                    maxgap = gap
            else:
                first[i] = ts
            prev[i] = ts
            last[i] = ts
        rc = proc.wait()
    finally:
        timer.cancel()
        unregister_child(proc.pid)
        for f in (proc.stdout, proc.stderr):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass
    if rc != 0:
        log.warning("No se pudieron leer los tiempos de %s (ffmpeg %s): %s", video.name, rc,
                    redact("\n".join(err)[-400:]))
        return None
    vi = next((i for i, k in kind.items() if k == "video"), None)
    ai = next((i for i, k in kind.items() if k == "audio"), None)
    frames = counts.get(vi, 0) if vi is not None else 0
    stats: dict = {"frames": frames, "ts_backwards": backwards, "ts_maxgap": maxgap,
                   "av_start": 0.0, "av_end": 0.0, "ts_bad": False}
    if vi is not None and ai is not None:
        stats["av_start"] = first[vi] - first[ai]
        stats["av_end"] = last[vi] - last[ai]
    stats["ts_bad"] = bool(frames) and (
        backwards > 5 or maxgap > 8
        or abs(stats["av_end"] - stats["av_start"]) > 3 or abs(stats["av_start"]) > 5)
    return stats


def analyze_video(exe: str, video: Path, duration: float) -> dict:
    """Revisa el video sin cargar todo el flujo en memoria.

    - Cabecera: codec, resolucion y fps (rapido).
    - Tiempos: `framecrc` leido por tuberia (memoria constante; antes se guardaban cientos de
      miles de lineas en RAM y se parseaban con el GIL tomado).
    - Congelados: solo fotogramas clave. Decodificar todo el video costaba ~1,2 s por minuto
      de metraje; con keyframes son ~0,1 s por minuto (medido con ffmpeg 8 en 1080p30).
    """
    res: dict = {"codec": "?", "size": "?", "fps": "?", "frames": None, "frozen": False,
                 "freezes": [], "frozen_secs": 0.0, "ts_backwards": 0, "ts_maxgap": 0.0,
                 "av_start": 0.0, "av_end": 0.0, "ts_bad": False}
    err = ""
    try:
        r = sp_run([exe, "-hide_banner", "-i", str(video)], capture_output=True, text=True,
                   errors="replace", timeout=90)
        err = r.stderr or ""
    except (subprocess.TimeoutExpired, OSError):
        log.warning("No se pudo leer la cabecera de %s", video.name)
    line = next((l for l in err.splitlines() if "Video:" in l), "")
    m = re.search(r"Video: (\w+)", line)
    if m:
        res["codec"] = m[1]
    m = re.search(r"\b(\d{2,5}x\d{2,5})\b", line)
    if m:
        res["size"] = m[1]
    m = re.search(r"([\d.]+) fps", line)
    if m:
        res["fps"] = m[1]

    # marcas de tiempo por paquete: saltos hacia atras, huecos y desfase audio/video (causa tipica de
    # "la imagen se queda quieta y el audio sigue" aunque el video este completo)
    budget = int(max(120, min(1800, (duration or 0) * 0.5 + 120)))
    stats = _framecrc_stats(exe, video, budget)
    if stats:
        res.update(stats)
    if err and not line and res["frames"] is None:
        res["frames"] = 0                           # la cabecera se leyo y no hay pista de video

    res["frozen_secs"], res["freezes"] = 0.0, []
    if duration and duration > 10 and res["frames"] != 0:
        # freezedetect solo en fotogramas clave: ~12x mas barato que decodificar todo el video
        budget = int(max(120, min(1800, (duration or 0) * 1.5)))
        out = ""
        try:
            q = sp_run([exe, "-hide_banner", "-nostats", "-loglevel", "info", "-skip_frame", "nokey",
                        "-threads", "2", "-i", str(video), "-map", "0:v:0?", "-an", "-filter_threads", "1",
                        "-vf", "scale=64:-2,freezedetect=n=0.001:d=6", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace", timeout=budget)
            out = q.stderr or ""
        except (subprocess.TimeoutExpired, OSError):
            log.warning("La deteccion de congelados de %s tardo demasiado; se omite", video.name)
        start = None
        for l in out.splitlines():
            m = _FREEZE_START.search(l)
            if m:
                start = float(m[1])
            m = _FREEZE_END.search(l)
            if m and start is not None:
                res["freezes"].append((start, float(m[1])))
                start = None
        if start is not None:                       # sigue congelado hasta el final
            res["freezes"].append((start, duration))
        res["frozen_secs"] = sum(max(0.0, e - st) for st, e in res["freezes"])
        res["frozen"] = res["frozen_secs"] >= max(20.0, 0.25 * duration)
    return res


def analysis_fields(h: dict) -> tuple:
    """Convierte el resultado de analyze_video en columnas de la BD."""
    width = height = None
    m = re.fullmatch(r"(\d+)x(\d+)", str(h.get("size") or ""))
    if m:
        width, height = int(m[1]), int(m[2])
    try:
        fps = float(h.get("fps")) if h.get("fps") not in (None, "", "?") else None
    except (TypeError, ValueError):
        fps = None
    return (str(h.get("codec") or "?"), width, height, fps, h.get("frames"),
            float(h.get("frozen_secs") or 0), 1 if h.get("ts_bad") else 0,
            datetime.now().isoformat(timespec="seconds"))


def repair_video(exe: str, src: Path, dst: Path, frames: int, duration: float,
                 progress=None) -> bool:
    """Copia con las marcas de tiempo regeneradas (video a ritmo constante, audio re-sincronizado).
    Recodifica el video, asi que tarda; el original no se toca. `progress(fraccion, segundos)`
    permite mostrar el avance real."""
    fps = frames / duration if frames and duration else 24.0
    fps = max(10.0, min(60.0, fps))
    tmp = dst.with_name(dst.stem + ".tmp.mp4")
    tmp.unlink(missing_ok=True)
    cmd = [exe, "-hide_banner", "-nostats", "-loglevel", "error", "-y", "-i", str(src),
           "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", f"setpts=N/({fps:.4f}*TB)", "-r", f"{fps:.3f}",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-af", "aresample=async=1:first_pts=0",
           "-movflags", "+faststart", "-progress", "pipe:1", str(tmp)]
    tail: collections.deque = collections.deque(maxlen=30)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace",
                                creationflags=NO_WINDOW if WIN else 0)
    except OSError as e:
        log.warning("No se pudo lanzar la reparacion de %s: %s", src.name, e)
        return False
    register_child(proc.pid)
    max_secs = max(600, min(6 * 3600, (duration or 0) * 3))
    timer = threading.Timer(max_secs, lambda: proc.poll() is None and proc.kill())
    timer.daemon = True
    timer.start()

    def drain() -> None:
        try:
            assert proc.stderr is not None
            for l in proc.stderr:
                tail.append(l.rstrip())
        except Exception:
            pass

    threading.Thread(target=drain, daemon=True, name="repair-err").start()
    rc = -1
    try:
        assert proc.stdout is not None
        for l in proc.stdout:
            k, _, v = l.strip().partition("=")
            if k in ("out_time_us", "out_time_ms") and progress and duration:
                try:
                    secs = int(v) / 1e6
                except ValueError:
                    continue
                progress(min(0.99, max(0.0, secs / duration)), secs)
        rc = proc.wait()
    finally:
        timer.cancel()
        unregister_child(proc.pid)
        for f in (proc.stdout, proc.stderr):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass
    if rc != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
        log.warning("Reparacion fallo para %s\n%s", src.name, redact("\n".join(tail)))
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, dst)
    if progress:
        progress(1.0, duration or 0.0)
    return True


def _run_with_progress(cmd: list[str], total: float = 0.0, progress=None,
                       max_secs: float = 3600) -> tuple[int, str]:
    """Ejecuta ffmpeg leyendo `-progress`. Devuelve (codigo, ultimas lineas de error)."""
    tail: collections.deque = collections.deque(maxlen=30)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace",
                                creationflags=NO_WINDOW if WIN else 0)
    except OSError as e:
        return -1, str(e)
    register_child(proc.pid)

    def drain() -> None:
        try:
            assert proc.stderr is not None
            for l in proc.stderr:
                tail.append(l.rstrip())
        except Exception:
            pass

    threading.Thread(target=drain, daemon=True, name="ffmpeg-err").start()
    timer = threading.Timer(max_secs, lambda: proc.poll() is None and proc.kill())
    timer.daemon = True
    timer.start()
    rc = -1
    try:
        assert proc.stdout is not None
        for l in proc.stdout:
            k, _, v = l.strip().partition("=")
            if k in ("out_time_us", "out_time_ms") and progress and total:
                try:
                    secs = int(v) / 1e6
                except ValueError:
                    continue
                progress(min(0.99, max(0.0, secs / total)))
        rc = proc.wait()
    finally:
        timer.cancel()
        unregister_child(proc.pid)
        for f in (proc.stdout, proc.stderr):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass
    return rc, "\n".join(tail)


def _concat_quote(p: Path) -> str:
    return str(p).replace("\\", "/").replace("'", "'\\''")


def merge_segments(exe: str, segments: list[Path], out: Path, duration: float = 0.0,
                   progress=None) -> Optional[Path]:
    """Une los trozos de un directo sin recodificar. Prueba MP4 y, si falla, MKV.

    No toca los originales; devuelve el archivo creado o None si no se pudo.
    """
    if len(segments) < 2:
        return None
    listfile = out.with_name(f".concat-{uuid.uuid4().hex[:8]}.txt")
    try:
        listfile.write_text("".join(f"file '{_concat_quote(p)}'\n" for p in segments), encoding="utf-8")
    except OSError as e:
        log.warning("No se pudo preparar la union de %s: %s", out.name, e)
        return None
    try:
        for suffix in (".mp4", ".mkv"):
            target = out.with_suffix(suffix)
            target.unlink(missing_ok=True)
            cmd = [exe, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
                   "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy"]
            cmd += ["-movflags", "+faststart", "-f", "mp4"] if suffix == ".mp4" else ["-f", "matroska"]
            cmd += ["-progress", "pipe:1", str(target)]
            budget = int(max(300, min(6 * 3600, (duration or 0) * 0.5 + 300)))
            rc, tail = _run_with_progress(cmd, duration, progress, budget)
            if rc == 0 and target.exists() and target.stat().st_size > 1000:
                if progress:
                    progress(1.0)
                return target
            log.warning("No se pudo unir en %s (codigo %s): %s", suffix, rc, redact(tail[-600:]))
            target.unlink(missing_ok=True)
        return None
    finally:
        listfile.unlink(missing_ok=True)


def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    """Nombre que no pisa un archivo existente (dos intentos en el mismo segundo, reinicios...)."""
    p = folder / f"{stem}{suffix}"
    n = 2
    while p.exists():
        p = folder / f"{stem}_{n}{suffix}"
        n += 1
    return p


def avatar_path(username: str) -> Path:
    return AVATAR_DIR / f"{username}.img"


def sniff_image(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return ""


# Solo se descargan imagenes de los CDN de TikTok: una URL de otra parte podria apuntar a la
# red interna del usuario (SSRF) y el servidor la devolveria como avatar.
_IMG_HOSTS = (".tiktok.com", ".tiktokcdn.com", ".tiktokcdn-us.com", ".tiktokcdn-eu.com",
              ".tiktokv.com", ".ttwstatic.com", ".ibytedtos.com", ".byteoversea.com", ".ttcdn-us.com")


def _safe_remote_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https":
        return False
    host = (u.hostname or "").lower().rstrip(".")
    if not host or host == "localhost" or host.endswith((".local", ".internal", ".lan", ".home")):
        return False
    try:
        ipaddress.ip_address(host)
        return False                       # direcciones IP: ni internas ni publicas
    except ValueError:
        pass
    return host.endswith(_IMG_HOSTS)


QUALITY_ORDER = {
    "max": ["origin", "uhd", "hd", "sd", "ld"],
    "hd": ["hd", "sd", "ld", "uhd", "origin"],
    "sd": ["sd", "ld", "hd", "uhd", "origin"],
}
LEGACY_KEYS = {"FULL_HD1": "origin", "HD1": "hd", "SD2": "sd", "SD1": "ld"}


def parse_candidates(info: dict, pref: str = "max") -> list[Candidate]:
    """Extrae todas las URLs de stream de /room/info/ ordenadas por calidad preferida."""
    order = QUALITY_ORDER.get(pref, QUALITY_ORDER["max"])
    found: dict[tuple[str, str], str] = {}
    try:
        raw = dig(info, "stream_url", "live_core_sdk_data", "pull_data", "stream_data")
        sd = json.loads(raw) if isinstance(raw, str) else (raw or {})
        for q, v in (sd.get("data") or {}).items():
            main = (v or {}).get("main") or {}
            for fmt in ("flv", "hls", "cmaf"):
                if main.get(fmt):
                    found.setdefault((q, fmt), main[fmt])
    except Exception:
        log.warning("stream_data no se pudo interpretar\n%s", traceback.format_exc())
    su = info.get("stream_url") or {}
    for fmt, key in (("flv", "flv_pull_url"), ("hls", "hls_pull_url_map")):
        for k, q in LEGACY_KEYS.items():
            url = (su.get(key) or {}).get(k)
            if url:
                found.setdefault((q, fmt), url)
    cands: list[Candidate] = []
    for q in order:
        for fmt in ("flv", "hls", "cmaf"):
            if (q, fmt) in found:
                cands.append(Candidate(q, fmt, found[(q, fmt)]))
    for (q, fmt), url in found.items():          # calidades desconocidas al final
        if q not in order:
            cands.append(Candidate(q, fmt, url))
    return cands


def candidates_from_raw(data: Any, pref: str) -> list[Candidate]:
    """Algunas respuestas de /user/room/ ya traen stream_data: sirve de via alternativa a /room/info/."""
    raw = find_key(data, "stream_data")
    if not raw:
        return []
    return parse_candidates({"stream_url": {"live_core_sdk_data": {"pull_data": {"stream_data": raw}}}}, pref)


def classify_exception(e: Exception) -> EngineError:
    if isinstance(e, EngineError):
        return e
    name = type(e).__name__
    msg = f"{name}: {e}"
    low = msg.lower()
    if name == "UserNotFoundError":
        return EngineError("not_found", "El usuario no existe o nunca ha hecho un live.")
    if name == "AgeRestrictedError":
        return EngineError("age", "Live +18: hace falta configurar tu sessionid en Ajustes.")
    if name in ("ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutException", "ProxyError",
                "RemoteProtocolError", "ReadError", "TransportError", "NetworkError",
                "TimeoutError", "ConnectionError", "gaierror"):
        return EngineError("network", f"Sin conexion con TikTok ({name}). Se reintentara.")
    if (name.startswith("Signature") or name in ("WebcastBlockedError", "SignAPIError")
            or "429" in low or "403" in low or "blocked" in low or "captcha" in low):
        return EngineError("blocked", f"TikTok limita las consultas ({name}). Se reintentara mas despacio.")
    if name in ("JSONDecodeError", "FailedParseRoomIdError", "KeyError", "FailedFetchRoomInfoError"):
        return EngineError("flaky", f"TikTok devolvio una respuesta vacia o inesperada ({name}); se reintenta.")
    return EngineError("unknown", f"Fallo inesperado al consultar a TikTok ({name}). "
                                  "Si se repite, usa Motor > Buscar y actualizar motor.")


class Engine:
    """Capa fina sobre TikTokLive. Todo error sale como EngineError."""

    MAX_CLIENTS = 12             # httpx mantiene un pool de conexiones por cliente: no crear uno por usuario

    def __init__(self, settings: Settings):
        self.s = settings
        self._clients: collections.OrderedDict[str, Any] = collections.OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._raw: dict[str, Any] = {}
        self._diag_logged: set[str] = set()

    # -- disponibilidad ---------------------------------------------------
    @staticmethod
    def available() -> tuple[bool, str]:
        try:
            import TikTokLive  # noqa: F401
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def _lock(self, username: str) -> asyncio.Lock:
        """Un candado por usuario: evita cerrar un cliente mientras otra consulta lo esta usando."""
        l = self._locks.get(username)
        if l is None:
            l = self._locks[username] = asyncio.Lock()
        return l

    async def _client(self, username: str):
        c = self._clients.get(username)
        if c is not None:
            self._clients.move_to_end(username)
            return c
        try:
            from TikTokLive import TikTokLiveClient  # type: ignore
        except Exception as e:
            raise EngineError("missing", f"El motor TikTokLive no esta instalado ({e}). Usa Motor > Actualizar.")
        proxy = self.s.get("proxy").strip()
        web_proxy = None
        if proxy:
            import httpx
            web_proxy = httpx.Proxy(proxy)
        c = TikTokLiveClient(unique_id="@" + username, web_proxy=web_proxy)
        sid = self.s.get("session_id").strip()
        if sid:
            c.web.set_session(sid, None)
        self._clients[username] = c
        await self._trim_clients()
        return c

    async def _trim_clients(self) -> None:
        while len(self._clients) > self.MAX_CLIENTS:
            victim = next((n for n in self._clients
                           if not (self._locks.get(n) is not None and self._locks[n].locked())), None)
            if victim is None:
                break
            await self._close_client(victim)

    async def _close_client(self, username: str) -> None:
        c = self._clients.pop(username, None)
        self._raw.pop(username, None)
        if c is not None:
            try:
                await c.web.close()
            except Exception:
                pass

    async def drop(self, username: Optional[str] = None) -> None:
        names = [username] if username else list(self._clients)
        for n in names:
            lock = self._locks.get(n)
            if lock is not None:
                async with lock:
                    await self._close_client(n)
            else:
                await self._close_client(n)

    # -- consultas --------------------------------------------------------
    async def _fetch_status(self, username: str) -> LiveStatus:
        from TikTokLive.client.web.routes.fetch_room_id_api import FetchRoomIdAPIRoute  # type: ignore
        c = await self._client(username)
        data = await FetchRoomIdAPIRoute.fetch_user_room_data(web=c.web, unique_id=username)
        self._raw[username] = data
        return parse_user_room(data)

    async def status(self, username: str) -> LiveStatus:
        async with GATE:
            async with self._lock(username):
                err: Optional[EngineError] = None
                for attempt in (1, 2):
                    try:
                        return await self._fetch_status(username)
                    except Exception as e:
                        err = classify_exception(e)
                        err.__cause__ = e
                        if err.kind in ("unknown", "network", "blocked", "flaky"):
                            await self._close_client(username)
                        if attempt == 1 and err.kind == "flaky":
                            await asyncio.sleep(1.5)   # respuesta vacia puntual: reintento inmediato
                            continue
                        raise err
                raise err  # pragma: no cover

    async def profile_html(self, username: str) -> tuple[str, str]:
        """Respaldo: nick y avatar desde la pagina publica del perfil (la misma via que usa TikTokLive)."""
        async with GATE:
            async with self._lock(username):
                c = await self._client(username)
                resp = await c.web.get(url=f"https://www.tiktok.com/@{username}", base_params=False,
                                       base_headers=False,
                                       extra_headers={"User-Agent": USER_AGENT,
                                                      "Accept-Language": "en-US,en;q=0.9"})
                return parse_profile_html(resp.text, username)

    async def fetch_image(self, username: str, url: str) -> Optional[bytes]:
        if not _safe_remote_url(url):            # no descargar de localhost / red interna
            return None
        async with GATE:
            async with self._lock(username):
                c = await self._client(username)
                resp = await c.web.get(url=url, base_params=False, base_headers=False,
                                       extra_headers={"User-Agent": USER_AGENT,
                                                      "Referer": "https://www.tiktok.com/"})
                data = resp.content
                if resp.status_code == 200 and 200 < len(data) < 4_000_000 and sniff_image(data):
                    return data
                return None

    async def streams(self, username: str, room_id: str) -> StreamInfo:
        async with GATE:
            async with self._lock(username):
                return await self._streams_locked(username, room_id)

    async def _streams_locked(self, username: str, room_id: str) -> StreamInfo:
        pref = self.s.get("quality")
        cands: list[Candidate] = []
        viewers, title, first_err = None, "", None
        try:
            c = await self._client(username)
            rid = int(room_id) if room_id else int(await c.web.fetch_room_id_from_api(username))
            info = await c.web.fetch_room_info(room_id=rid)
            cands = parse_candidates(info, pref)
            viewers = to_int(info.get("user_count"))
            if viewers is None:
                viewers = to_int(dig(info, "stats", "user_count_str")) or to_int(dig(info, "stats", "total_user"))
            title = str(info.get("title") or "")
        except Exception as e:
            first_err = classify_exception(e)
            first_err.__cause__ = e
            if first_err.kind in ("unknown", "network", "blocked", "flaky"):
                await self._close_client(username)
        if not cands and (first_err is None or first_err.kind != "missing"):
            try:                                   # via alternativa: stream_data dentro de /user/room/
                if username not in self._raw:
                    await self._fetch_status(username)     # ya tenemos el candado: no volver a pedirlo
                cands = candidates_from_raw(self._raw.get(username), pref)
                if cands:
                    log.warning("[%s] room/info no dio stream (%s); se usa el de user/room.", username,
                                first_err.kind if first_err else "vacio")
            except Exception:
                log.warning("[%s] Via user/room tambien fallo\n%s", username, redact(traceback.format_exc()))
        if not cands and self.s.get("ytdlp_fallback") == "1" and (first_err is None or first_err.kind != "missing"):
            try:
                cands = await asyncio.to_thread(ytdlp_candidates, username, pref)
                if cands:
                    log.warning("[%s] Motor principal sin stream; usando yt-dlp como respaldo.", username)
            except Exception:
                log.warning("[%s] Respaldo yt-dlp tambien fallo\n%s", username, traceback.format_exc())
        if not cands:
            if username not in self._diag_logged:   # una sola vez: forma de la respuesta (sin valores) para diagnostico
                self._diag_logged.add(username)
                log.warning("[%s] Sin URL de stream. Estructura de user/room: %s", username,
                            " ".join(key_paths(self._raw.get(username) or {})) or "(vacia)")
            self._raw.pop(username, None)
            raise first_err or EngineError("no_stream", "TikTok no devolvio ninguna URL de stream.")
        return StreamInfo(cands, viewers, title)


def ytdlp_candidates(username: str, pref: str) -> list[Candidate]:
    import yt_dlp  # type: ignore
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 20}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(f"https://www.tiktok.com/@{username}/live", download=False)
    fmts = [f for f in (info.get("formats") or []) if f.get("url")]
    fmts.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=(pref != "sd"))
    out = []
    for f in fmts:
        proto = str(f.get("protocol") or "")
        out.append(Candidate(f"yt{f.get('height') or ''}", "hls" if "m3u8" in proto else "flv", f["url"]))
    if not out and info.get("url"):
        out.append(Candidate("yt", "hls" if ".m3u8" in info["url"] else "flv", info["url"]))
    return out


# --------------------------------------------------------------------------- estado por usuario
class UserState:
    def __init__(self, row: dict):
        self.id = row["id"]
        self.username = row["username"]
        self.enabled = bool(row["enabled"])
        self.auto_record = bool(row["auto_record"])
        self.nickname = row.get("nickname") or ""
        self.avatar_url = row.get("avatar_url") or ""      # parte estable de la URL (sin firma)
        self.avatar_ver = int(row.get("avatar_ver") or 0)
        self.profile_busy = False
        self.profile_next = 0.0
        self.avoid_origin = False      # la ultima grabacion en calidad "origin" salio con video congelado
        self.live: Optional[bool] = None
        self.room_id = ""
        self.viewers: Optional[int] = None
        self.title = ""
        self.last_check = 0.0
        self.next_check = 0.0
        self.last_live_at = 0.0        # ultima vez visto en vivo (para espaciar revisiones)
        self.checking = False
        self.fail = 0
        self.error: Optional[str] = None
        self.error_kind: Optional[str] = None
        self.last_err_sig = ""
        self.recording = False
        self.stop_req = False
        self.suppress = False          # el usuario detuvo la grabacion: no reiniciar hasta que termine el live
        self.force = False
        self.cooldown_until = 0.0
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.task: Optional[asyncio.Task] = None
        self.rec_started = 0.0         # inicio de la grabacion en curso (monotonic wall)
        self.rec_path: Optional[Path] = None
        self.rec_size = 0
        self.rec_quality = ""
        self.rec_note = ""
        self.rec_out_time = 0.0        # segundos de video escritos (ffmpeg -progress)
        self.rec_bitrate = ""
        self.rec_speed = ""
        self.notified_live = False     # ya se aviso de este live (se reinicia al terminar)
        self.notified_err = False
        self.notified_room = row.get("notified_room") or ""   # persiste entre reinicios
        # estado de la sesion (un directo puede quedar en varios archivos si se corta)
        self.session_id = ""
        self.session_room = ""
        self.session_rec_ids: list[int] = []
        self.session_peak: Optional[int] = None
        self.session_reported = ""


class Manager:
    def __init__(self, db: DB, settings: Settings, engine: Engine):
        self.db, self.s, self.engine = db, settings, engine
        self.users: dict[str, UserState] = {}
        self.bg: set[asyncio.Task] = set()
        self.global_block_until = 0.0
        self.stopping = False
        self.ready = asyncio.Event()
        self.session_state = "none"        # none | unknown | ok | expired
        self.session_due = 0.0
        self.session_checked = 0.0
        self.session_strikes = 0
        self.session_alerted = False
        self.session_account = ""
        self.session_nickname = ""
        self.notify = lambda title, msg, kind="info", username=None, image=None: None   # lo conecta la App
        self._stop = asyncio.Event()       # despierta los bucles largos al cerrar
        self.free_gb: Optional[float] = None
        self.total_gb: Optional[float] = None
        self.low_space = False
        self._totals: dict[int, dict] = {}
        self._totals_at = 0.0
        self._finalizing: set[int] = set()

    @property
    def global_block_until(self) -> float:
        return GATE.block_until

    @global_block_until.setter
    def global_block_until(self, value: float) -> None:
        GATE.block_until = float(value)

    # -- usuarios ---------------------------------------------------------
    def load_users(self) -> None:
        """Reconstruye el diccionario y lo intercambia de golpe.

        Antes se modificaba in place desde los hilos HTTP mientras `snapshot()` lo recorria desde
        la bandeja: un RuntimeError intermitente. Ahora nadie ve un diccionario a medio cambiar.
        """
        rows = {r["username"]: r for r in self.db.q("SELECT * FROM users")}
        current = self.users
        new: dict[str, UserState] = {}
        interval = self.s.int("check_interval", 15, 3600)
        for name, row in rows.items():
            u = current.get(name)
            if u is not None:
                u.enabled, u.auto_record = bool(row["enabled"]), bool(row["auto_record"])
                u.notified_room = row.get("notified_room") or u.notified_room
            else:
                u = UserState(row)
                u.next_check = time.time() + random.uniform(0, min(interval, 30))   # repartir el primer chequeo
            new[name] = u
        self.users = new

    # -- bucle principal --------------------------------------------------
    async def run(self) -> None:
        await self.ready.wait()
        await self.recover_unfinished()
        log.info("Monitor iniciado.")
        self._spawn(self._session_loop())
        self._spawn(self._backfill_thumbs())
        self._spawn(self._housekeeping())
        while not self.stopping:
            try:
                now = time.time()
                if now >= self.global_block_until:
                    for u in list(self.users.values()):
                        if u.enabled and not u.checking and now >= u.next_check:
                            u.checking = True
                            self._spawn(self._check(u))
            except Exception:
                log.error("Error en el bucle principal\n%s", traceback.format_exc())
            await asyncio.sleep(1)

    def session_reset(self) -> None:
        """Se llama al cambiar el sessionid: volver a comprobar ya y permitir un nuevo aviso."""
        self.session_state = "unknown" if self.s.get("session_id").strip() else "none"
        self.session_due, self.session_strikes, self.session_alerted = 0.0, 0, False
        self.session_account, self.session_nickname = "", ""

    async def check_session_now(self) -> dict:
        """Comprobacion manual desde el panel; ademas identifica la cuenta de la cookie."""
        sid = self.s.get("session_id").strip()
        if not sid:
            self.session_state = "none"
            self.session_checked = time.time()
            return {"state": "none", "account": "", "nickname": ""}
        try:
            async with GATE:
                info = await asyncio.to_thread(check_session_info, sid, self.s.get("proxy").strip())
        except EngineError:
            info = {"state": "unknown", "account": "", "nickname": ""}
        self.session_checked = time.time()
        if info.get("account"):
            self.session_account = info["account"]
        if info.get("nickname"):
            self.session_nickname = info["nickname"]
        state = info.get("state", "unknown")
        if state == "ok":
            self.session_state, self.session_strikes, self.session_alerted = "ok", 0, False
            self.session_due = time.time() + 1800
        elif state == "expired":
            self.session_state = "expired"
            self.session_due = time.time() + 1800
        return info

    async def _nap(self, secs: float) -> None:
        """Espera interrumpible: al cerrar no hay que aguantar 10 minutos de limpieza."""
        try:
            await asyncio.wait_for(self._stop.wait(), secs)
        except asyncio.TimeoutError:
            pass

    async def _session_loop(self) -> None:
        self.session_reset()
        while not self.stopping:
            try:
                sid = self.s.get("session_id").strip()
                if not sid:
                    self.session_state, self.session_alerted, self.session_strikes = "none", False, 0
                elif self.session_state == "none":
                    self.session_reset()
                elif time.time() >= self.session_due:
                    try:
                        async with GATE:
                            info = await asyncio.to_thread(check_session_info, sid,
                                                           self.s.get("proxy").strip())
                    except EngineError:
                        info = {"state": "unknown", "account": "", "nickname": ""}
                    res = info.get("state", "unknown")
                    if info.get("account"):
                        self.session_account = info["account"]
                    if info.get("nickname"):
                        self.session_nickname = info["nickname"]
                    self.session_checked = time.time()
                    if res == "ok":
                        self.session_state, self.session_strikes, self.session_alerted = "ok", 0, False
                        self.session_due = time.time() + 1800
                    elif res == "expired":
                        self.session_strikes += 1
                        if self.session_strikes >= 3:          # tres comprobaciones seguidas: no es un error puntual
                            self.session_state = "expired"
                            if not self.session_alerted:
                                self.session_alerted = True
                                log.warning("La sessionid de TikTok parece vencida.")
                                self.notify("🔑 Sessionid vencida",
                                            "Tu sesión de TikTok caducó: los lives +18 no se podrán grabar. "
                                            "Copia la nueva sessionid y pégala en Ajustes.", "session")
                            self.session_due = time.time() + 1800
                        else:
                            self.session_due = time.time() + 120
                    else:
                        self.session_due = time.time() + 900
            except Exception:
                log.error("Error al comprobar la sessionid\n%s", traceback.format_exc())
            await self._nap(5)

    async def _backfill_thumbs(self) -> None:
        """Miniaturas para grabaciones antiguas que todavia no tienen (paginado, sin dejar fuera las viejas)."""
        try:
            exe = find_ffmpeg(self.s)
            if not exe:
                return
            last, done = 1 << 62, 0
            while not self.stopping and done < 2000:
                rows = self.db.q("SELECT id, path, duration_sec FROM recordings WHERE status='done' AND id<? "
                                 "ORDER BY id DESC LIMIT 200", (last,))
                if not rows:
                    return
                for r in rows:
                    if self.stopping:
                        return
                    last = min(last, r["id"])
                    out = THUMB_DIR / f"{r['id']}.jpg"
                    if not out.exists() and Path(r["path"]).exists():
                        await asyncio.to_thread(make_thumb, exe, Path(r["path"]), r["duration_sec"] or 0, out)
                        done += 1
        except Exception:
            log.warning("No se pudieron generar miniaturas antiguas\n%s", traceback.format_exc())

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)

        def _done(task: asyncio.Task) -> None:
            self.bg.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:            # antes se perdia: en el .exe no hay consola donde verlo
                log.error("Tarea en segundo plano fallo: %s", type(exc).__name__,
                          exc_info=(type(exc), exc, exc.__traceback__))

        self.bg.add(t)
        t.add_done_callback(_done)
        return t

    async def _check(self, u: UserState) -> None:
        interval = self.s.int("check_interval", 15, 3600)
        delay: float = interval
        try:
            s = await asyncio.wait_for(self.engine.status(u.username), 45)
            now = time.time()
            u.fail, u.error, u.error_kind, u.last_err_sig = 0, None, None, ""
            u.last_check = now
            u.live, u.room_id = s.is_live, s.room_id
            if s.is_live and s.room_id:
                if s.room_id != u.notified_room:
                    u.notified_live = False          # directo nuevo (aunque el programa se reiniciara)
                    u.notified_err = False
                else:
                    u.notified_live = True           # ya se aviso de este mismo directo
            self._on_profile(u, s)
            if s.viewers is not None:
                u.viewers = s.viewers
            if s.title:
                u.title = s.title
            if not s.is_live:
                u.viewers, u.suppress, u.title = None, False, ""
                u.notified_live = u.notified_err = False
                # quien lleva horas sin emitir no necesita una consulta cada 45 s
                delay = min(interval * 3, 300) if u.last_live_at and now - u.last_live_at > 3600 else interval
            else:
                u.last_live_at = now
                if u.recording and u.session_id and u.viewers:
                    u.session_peak = max(u.session_peak or 0, u.viewers)
                if ((u.auto_record or u.force) and not u.recording and not u.suppress
                        and now >= u.cooldown_until):
                    if self.low_space:
                        u.error = (f"Quedan {self.free_gb:.1f} GB libres: no se inicia la grabacion. "
                                   "Libera espacio o cambia la retencion en Ajustes.")
                        u.error_kind = "disk"
                    else:
                        self._start_recording(u)   # el aviso sale cuando la grabacion realmente arranca
                elif not (u.auto_record or u.force) and not u.recording and not u.notified_live:
                    u.notified_live = True
                    self._mark_notified(u, s.room_id)
                    self.notify("🔴 En vivo", f"{self.who(u)} está transmitiendo (grabación automática desactivada).", "live", u.username)
                delay = interval if u.recording else min(interval, 25)
        except Exception as e:
            err = classify_exception(e)
            if err.kind == "age" and self.s.get("session_id").strip() and self.session_state != "expired":
                self.session_due = 0.0          # un +18 fallo con sessionid puesta: comprobar si vencio
            u.fail += 1
            u.last_check = time.time()
            self._log_failure(u, err, e)
            if err.kind == "flaky" and u.fail < 3:
                pass                                  # fallo puntual: no se muestra como problema
            else:
                u.error, u.error_kind = err.args[0], err.kind
                u.live = None if err.kind != "not_found" else False
            if err.kind == "flaky":
                delay = min(interval, 20)
            elif err.kind == "not_found":
                delay = max(interval * 4, 300)
            elif err.kind == "blocked":
                delay = min(600, interval * 2 ** min(u.fail, 4))
                self.global_block_until = max(self.global_block_until, time.time() + random.uniform(20, 45))
            elif err.kind == "network":
                delay = min(interval, 30)      # sin internet: volver a probar pronto (p. ej. al encender el PC)
            elif err.kind in ("missing", "age"):
                delay = max(interval * 4, 120)
            else:
                delay = min(300, interval * 2 ** min(u.fail, 3))
        finally:
            u.checking = False
            u.force = False
            u.next_check = time.time() + delay * random.uniform(0.85, 1.2)

    async def check_now(self, name: str) -> Optional[UserState]:
        """Comprueba un usuario ya mismo (lo usa el bot al añadirlo)."""
        u = self.users.get(name)
        if u is None:
            return None
        for _ in range(80):                        # esperar a una revision en curso
            if not u.checking:
                break
            await asyncio.sleep(0.25)
        if u.checking:
            return u
        u.checking = True
        try:
            await self._check(u)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("[%s] Comprobacion manual fallo\n%s", name, traceback.format_exc())
        finally:
            u.checking = False
        return u

    @staticmethod
    def who(u: UserState) -> str:
        return f"{u.nickname} (@{u.username})" if u.nickname else f"@{u.username}"

    def _on_profile(self, u: UserState, s: LiveStatus) -> None:
        """Con lo que devolvio la consulta de estado: guardar el nick y, si hace falta, bajar la foto."""
        if s.nickname and s.nickname != u.nickname:
            u.nickname = s.nickname
            self.db.x("UPDATE users SET nickname=? WHERE id=?", (u.nickname, u.id))
        stable = s.avatar_url.split("?")[0] if s.avatar_url else ""
        has_file = avatar_path(u.username).exists()
        need = (not has_file) or (stable and stable != u.avatar_url)
        if (need or not u.nickname) and not u.profile_busy and time.time() >= u.profile_next:
            u.profile_busy = True
            self._spawn(self._refresh_profile(u, s.avatar_url))

    async def _refresh_profile(self, u: UserState, api_avatar: str) -> None:
        got = False
        try:
            avatar = api_avatar
            if not u.nickname or not avatar:
                nick, html_avatar = await asyncio.wait_for(self.engine.profile_html(u.username), 40)
                if nick and not u.nickname:
                    u.nickname = nick
                    self.db.x("UPDATE users SET nickname=? WHERE id=?", (nick, u.id))
                avatar = avatar or html_avatar
            if avatar:
                data = await asyncio.wait_for(self.engine.fetch_image(u.username, avatar), 40)
                if data:
                    tmp = avatar_path(u.username).with_suffix(".tmp")
                    tmp.write_bytes(data)
                    os.replace(tmp, avatar_path(u.username))
                    u.avatar_url, u.avatar_ver = avatar.split("?")[0], int(time.time())
                    self.db.x("UPDATE users SET avatar_url=?, avatar_ver=? WHERE id=?", (u.avatar_url, u.avatar_ver, u.id))
                    got = True
        except Exception as e:
            log.warning("[%s] No se pudo obtener el perfil (%s: %s)", u.username, type(e).__name__, redact(str(e))[:160])
        finally:
            u.profile_next = time.time() + (6 * 3600 if (got or u.nickname) and avatar_path(u.username).exists() else 900)
            u.profile_busy = False

    def _log_failure(self, u: UserState, err: EngineError, raw: Exception) -> None:
        sig = f"{err.kind}:{err.args[0]}"
        if sig != u.last_err_sig or u.fail % 20 == 0:
            u.last_err_sig = sig
            tb = "".join(traceback.format_exception(type(raw), raw, raw.__traceback__)) if raw is not err else ""
            log.warning("[%s] Fallo al consultar (%s, intento %d): %s\n%s",
                        u.username, err.kind, u.fail, err.args[0], redact(tb))

    def _mark_notified(self, u: UserState, room_id: str) -> None:
        """Guarda de que directo se avisó: evita repetir el aviso tras reiniciar el programa."""
        u.notified_room = room_id or ""
        try:
            self.db.x("UPDATE users SET notified_room=? WHERE id=?", (u.notified_room, u.id))
        except Exception:
            log.debug("[%s] No se pudo guardar el aviso del directo\n%s", u.username, traceback.format_exc())

    async def _session_report(self, u: UserState, session_id: str, rec_ids: list[int],
                              peak: Optional[int]) -> None:
        """Aviso al terminar el directo, cuando ya estan procesados todos sus archivos."""
        try:
            for _ in range(180):
                if self.stopping:
                    return
                if not any(rid in self._finalizing for rid in rec_ids):
                    break
                await asyncio.sleep(1)
            if u.session_reported == session_id:
                return
            rows = self.db.q("SELECT id, duration_sec, size_bytes FROM recordings "
                             "WHERE session_id=? AND status='done' ORDER BY id", (session_id,))
            if not rows:
                return
            u.session_reported = session_id
            total_s = sum(float(r["duration_sec"] or 0) for r in rows)
            total_b = sum(int(r["size_bytes"] or 0) for r in rows)
            viewers = f" · 👁 pico {peak:,}".replace(",", ".") if peak else ""
            files = f" · {len(rows)} archivo(s)" if len(rows) > 1 else ""
            thumb = THUMB_DIR / f"{rows[-1]['id']}.jpg"
            self.notify("🏁 Directo terminado",
                        f"{self.who(u)} · {fmt_secs(total_s)} · {total_b / 1e9:.2f} GB{files}{viewers}",
                        "ended", u.username, thumb if thumb.exists() else None)
            log.info("[%s] Directo terminado: %s en %d archivo(s)", u.username, fmt_secs(total_s), len(rows))
        except Exception:
            log.warning("[%s] No se pudo resumir el directo\n%s", u.username, traceback.format_exc())

    # -- grabacion --------------------------------------------------------
    def _start_recording(self, u: UserState) -> None:
        if u.recording or self.users.get(u.username) is not u:
            return                              # ya graba, o el usuario se borro mientras se consultaba
        u.recording = True
        u.stop_req = False
        u.task = self._spawn(self._record(u))

    async def _record(self, u: UserState) -> None:
        fails = 0
        # un directo = una sesion; si se corta y vuelve, los trozos comparten session_id
        if (not u.session_id or u.session_room != u.room_id
                or u.session_reported == u.session_id):
            u.session_id = uuid.uuid4().hex[:12]
            u.session_room = u.room_id
            u.session_rec_ids = []
            u.session_peak = u.viewers
            u.session_reported = ""
        else:
            u.session_peak = max(u.session_peak or 0, u.viewers or 0)
        try:
            while not u.stop_req and not self.stopping:
                try:
                    info = await asyncio.wait_for(self.engine.streams(u.username, u.room_id), 60)
                    if info.viewers is not None:
                        u.viewers = info.viewers
                        u.session_peak = max(u.session_peak or 0, info.viewers)
                    if info.title:
                        u.title = info.title
                except Exception as e:
                    fails += 1
                    err = classify_exception(e)
                    self._log_failure(u, err, e)
                    if err.kind in ("not_found", "age", "missing") or fails >= 6:
                        u.error, u.error_kind = err.args[0], err.kind
                        break
                    await self._sleep(u, min(60, 6 * fails))
                    st = await self._safe_status(u)
                    if st is not None and not st.is_live:
                        u.live = False
                        break
                    continue
                ok = False
                cands = list(info.candidates)
                log.info("[%s] Calidades disponibles: %s", u.username, ", ".join(c.label() for c in cands) or "ninguna")
                if u.avoid_origin:                      # el origin de este usuario salio dañado: probar HD primero
                    cands = [c for c in cands if c.quality != "origin"] + [c for c in cands if c.quality == "origin"]
                for cand in cands[:6]:
                    if u.stop_req or self.stopping:
                        break
                    secs = await self._run_ffmpeg(u, cand)
                    if secs >= 15 or u.stop_req:
                        ok = True
                        break
                if u.stop_req or self.stopping:
                    break
                if ok:
                    fails = 0
                else:
                    st = await self._safe_status(u)
                    if st is not None and not st.is_live:     # el live acababa de terminar
                        u.live = False
                        break
                    fails += 1
                    log.warning("[%s] Ninguna URL de stream pudo grabarse (intento %d/6).", u.username, fails)
                    if fails >= 6:
                        u.error, u.error_kind = "No se pudo grabar el stream tras varios intentos.", "no_stream"
                        break
                    await self._sleep(u, min(60, 8 * fails))
                    continue
                if await self._confirm_offline(u):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("[%s] Error inesperado en la grabacion\n%s", u.username, redact(traceback.format_exc()))
            u.error, u.error_kind = "Error interno al grabar (ver log).", "unknown"
        finally:
            if u.error and not u.stop_req and not self.stopping and not u.notified_err:
                u.notified_err = True
                self.notify("⚠ No se pudo grabar", f"{self.who(u)} está en vivo pero: {u.error}", "problem", u.username)
            ended = (u.live is False and not u.stop_req and not self.stopping
                     and bool(u.session_id) and bool(u.session_rec_ids))
            if ended:
                self._spawn(self._session_report(u, u.session_id, list(u.session_rec_ids), u.session_peak))
            u.recording = False
            u.proc = None
            u.rec_path = None
            u.rec_size = 0
            u.cooldown_until = time.time() + 60
            u.next_check = min(u.next_check, time.time() + 5)

    async def _sleep(self, u: UserState, secs: float) -> None:
        end = time.time() + secs
        while time.time() < end and not u.stop_req and not self.stopping:
            await asyncio.sleep(0.5)

    async def _safe_status(self, u: UserState) -> Optional[LiveStatus]:
        try:
            s = await asyncio.wait_for(self.engine.status(u.username), 45)
            u.last_check = time.time()
            if s.viewers is not None:
                u.viewers = s.viewers
            if s.room_id:
                u.room_id = s.room_id
            return s
        except Exception:
            return None

    async def _confirm_offline(self, u: UserState) -> bool:
        """Tras terminar ffmpeg: ¿de verdad acabo el live? (TikTok a veces corta y reconecta)."""
        for _ in range(3):
            s = await self._safe_status(u)
            if s is not None and s.is_live:
                return False
            await self._sleep(u, 6)
            if u.stop_req or self.stopping:
                return True
        u.live = False
        return True

    def _build_cmd(self, exe: str, cand: Candidate, out: Path) -> list[str]:
        cmd = [exe, "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1", "-y"]
        if cand.url.startswith("http"):
            cmd += ["-user_agent", USER_AGENT, "-headers", "Referer: https://www.tiktok.com/\r\n",
                    "-rw_timeout", "20000000"]
            if cand.fmt in ("flv", "hls"):
                cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        cmd += ["-i", cand.url, "-map", "0:v?", "-map", "0:a?", "-c", "copy"]
        if out.suffix.lower() == ".mp4":
            # mp4 fragmentado: se puede reproducir mientras se graba y queda valido si el proceso muere.
            cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4"]
        else:
            cmd += ["-f", "matroska"]
        cmd += [str(out)]
        return cmd

    async def _run_ffmpeg(self, u: UserState, cand: Candidate) -> float:
        exe = find_ffmpeg(self.s)
        if not exe:
            u.error, u.error_kind = ("No se pudo iniciar el grabador: falta ffmpeg. En el .exe deberia venir "
                                     "incluido; abre Diagnóstico y envia el paquete de soporte.", "missing")
            log.error("[%s] ffmpeg no encontrado", u.username)
            u.stop_req = True
            return 0
        out_dir = Path(self.s.get("output_dir")) / u.username
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            u.error = f"No se puede escribir en la carpeta de grabaciones ({e}). Revisala en Ajustes."
            u.error_kind = "missing"
            u.stop_req = True
            log.error("[%s] No se puede escribir en %s: %s", u.username, out_dir, e)
            return 0
        started = datetime.now()
        ext = ".mp4" if self.s.get("final_format") == "mp4" else ".mkv"
        # microsegundos + comprobacion de existencia: dos intentos en el mismo segundo ya no comparten
        # archivo (antes el segundo truncaba el primero y su _finalize podia borrarlo en plena grabacion)
        out_path = unique_path(out_dir, f"{u.username}_{started:%Y-%m-%d_%H-%M-%S_%f}", ext)
        rec_id = self.db.x("INSERT INTO recordings(user_id,path,started_at,status,quality,session_id) "
                           "VALUES(?,?,?,?,?,?)",
                           (u.id, str(out_path), started.isoformat(timespec="seconds"), "recording",
                            cand.label(), u.session_id or None))
        u.session_rec_ids.append(rec_id)
        if u.viewers:
            u.session_peak = max(u.session_peak or 0, u.viewers)
        cmd = self._build_cmd(exe, cand, out_path)
        log.info("[%s] Grabando (%s) -> %s", u.username, cand.label(), out_path.name)
        tail: collections.deque = collections.deque(maxlen=40)
        t0 = time.time()
        u.rec_out_time, u.rec_bitrate, u.rec_speed, u.rec_size = 0.0, "", "", 0
        try:
            extra = {"creationflags": NO_WINDOW} if WIN else {"start_new_session": True}
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, **extra)
        except Exception:
            log.error("[%s] No se pudo lanzar ffmpeg\n%s", u.username, traceback.format_exc())
            self.db.x("DELETE FROM recordings WHERE id=?", (rec_id,))
            u.error, u.error_kind = "No se pudo iniciar ffmpeg (ver log).", "unknown"
            return 0
        register_child(proc.pid)
        u.proc, u.rec_path, u.rec_started, u.rec_quality = proc, out_path, t0, cand.label()
        # aviso una sola vez por directo (tambien evita repetirlo si el programa se reinicio)
        if (u.room_id and u.room_id != u.notified_room) or (not u.room_id and not u.notified_live):
            u.notified_live = True
            if u.room_id:
                self._mark_notified(u, u.room_id)
            viewers = f" · 👁 {u.viewers:,}".replace(",", ".") if u.viewers else ""
            self.notify("🔴 Grabando en vivo", f"{self.who(u)} acaba de empezar un directo{viewers}. Grabación iniciada.",
                        "recording", u.username)

        async def read_err() -> None:
            assert proc.stderr is not None
            async for line in proc.stderr:
                tail.append(line.decode(errors="replace").rstrip())

        async def read_progress() -> None:
            """ffmpeg -progress: tamano, bitrate y segundos escritos reales (mejor que mirar el archivo)."""
            assert proc.stdout is not None
            async for raw in proc.stdout:
                key, _, val = raw.decode(errors="replace").strip().partition("=")
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        u.rec_out_time = int(val) / 1e6
                    except ValueError:
                        pass
                elif key == "total_size":
                    try:
                        u.rec_size = int(val)
                    except ValueError:
                        pass
                elif key == "bitrate":
                    u.rec_bitrate = val.strip()
                elif key == "speed":
                    u.rec_speed = val.strip()

        readers = [asyncio.create_task(read_err()), asyncio.create_task(read_progress())]
        watcher = asyncio.create_task(self._watch(u, proc, out_path))
        try:
            rc = await proc.wait()
        except asyncio.CancelledError:              # cierre/cancelacion: no dejar ffmpeg vivo
            await self._quit_proc(proc, graceful=False)
            raise
        finally:
            watcher.cancel()
            for t in readers:
                t.cancel()
            try:
                await asyncio.gather(*readers, return_exceptions=True)
            except Exception:
                pass
            unregister_child(proc.pid)
            u.proc = None
        secs = time.time() - t0
        if rc not in (0, 255) and not (u.stop_req or self.stopping):
            txt = "\n".join(tail)
            low = txt.lower()
            if "no space left" in low or "disk full" in low or "not enough space" in low:
                u.error = ("El disco se quedo sin espacio: la grabacion se corto. "
                           "Libera espacio o ajusta la retencion en Ajustes.")
                u.error_kind = "disk"
            log.warning("[%s] ffmpeg termino con codigo %s tras %.0fs (%s)\n%s", u.username, rc, secs,
                        cand.label(), redact(txt))
        self._spawn(self._finalize(rec_id, out_path, started, secs, cand.label(), self.who(u), u.username))
        return secs

    async def _watch(self, u: UserState, proc, path: Path) -> None:
        """Mientras graba: progreso, fin del live y streams congelados.

        El estado (fin de live) se consulta cada 60 s en vez de cada 10 s: durante una grabacion el
        propio ffmpeg ya nos dice si sigue entrando datos.
        """
        last_size, last_change, last_status, offline_hits = 0, time.time(), 0.0, 0
        last_out = 0.0
        try:
            while proc.returncode is None:
                await asyncio.sleep(10)
                now = time.time()
                try:
                    size = path.stat().st_size
                except OSError:
                    size = last_size
                if size > u.rec_size:
                    u.rec_size = size
                # progreso real: tamano o segundos de video escritos por ffmpeg
                if size > last_size + 4096 or u.rec_out_time > last_out + 0.25:
                    last_change = now
                last_size, last_out = max(last_size, size), max(last_out, u.rec_out_time)
                if now - last_change > 60:
                    log.warning("[%s] Sin datos nuevos en 60s; se reinicia la captura.", u.username)
                    await self._quit_proc(proc, graceful=False)
                    return
                if now - last_status >= 60:
                    last_status = now
                    s = await self._safe_status(u)
                    if s is not None:
                        if s.viewers:
                            u.session_peak = max(u.session_peak or 0, s.viewers)
                        offline_hits = 0 if s.is_live else offline_hits + 1
                        if offline_hits >= 2:
                            log.info("[%s] El live termino; cerrando grabacion.", u.username)
                            await self._quit_proc(proc, graceful=True)
                            return
        except asyncio.CancelledError:
            pass
        except Exception:
            log.error("[%s] Error en el vigilante de grabacion\n%s", u.username, traceback.format_exc())

    async def _quit_proc(self, proc, graceful: bool = True) -> None:
        if proc.returncode is not None:
            return
        try:
            if graceful and proc.stdin:
                proc.stdin.write(b"q\n")
                await proc.stdin.drain()
                try:
                    await asyncio.wait_for(proc.wait(), 15)
                    return
                except asyncio.TimeoutError:
                    pass
        except Exception:
            pass
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    async def _finalize(self, rec_id: int, path: Path, started: Optional[datetime], secs: float,
                        quality: str = "", username: str = "", raw_user: str = "") -> None:
        """Valida, mide y deja el archivo final. Con mp4 fragmentado no hay remux: se graba ya en mp4."""
        if rec_id in self._finalizing:              # recuperacion y cierre a la vez: no duplicar
            return
        self._finalizing.add(rec_id)
        try:
            exe = find_ffmpeg(self.s)
            min_bytes = self.s.int("min_keep_kb", 0, 10_000_000) * 1024
            if not path.exists() or path.stat().st_size < max(min_bytes, 1):
                log.info("Grabacion demasiado corta/vacia, se descarta: %s", path.name)
                try:
                    path.unlink()
                except OSError:
                    pass
                self.db.x("DELETE FROM recordings WHERE id=?", (rec_id,))
                self._totals_at = 0.0
                return
            self.db.x("UPDATE recordings SET status='processing' WHERE id=?", (rec_id,))
            dur = await asyncio.to_thread(probe_duration, exe, path) if exe else None
            final, note = path, ""
            if path.suffix.lower() == ".mkv" and self.s.get("final_format") == "mp4" and exe:
                # solo para grabaciones antiguas o si se cambio el formato a mitad: hoy se graba en mp4
                mp4 = path.with_suffix(".mp4")
                tmp = mp4.with_name(mp4.stem + ".tmp.mp4")
                tmp.unlink(missing_ok=True)
                timeout = int(max(300, min(3600, (dur or secs or 0) * 2 + 120)))
                try:
                    r = await asyncio.to_thread(
                        sp_run,
                        [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-c", "copy",
                         "-movflags", "+faststart", str(tmp)],
                        capture_output=True, text=True, errors="replace", timeout=timeout)
                except subprocess.TimeoutExpired:
                    r = None
                if r is not None and r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                    os.replace(tmp, mp4)
                    final = mp4
                else:
                    note = "No se pudo convertir a mp4; se conserva el .mkv"
                    if r is not None:
                        log.warning("Remux a mp4 fallo para %s\n%s", path.name, redact((r.stderr or "")[-1500:]))
                    tmp.unlink(missing_ok=True)
            size = final.stat().st_size
            # primero la BD, despues borrar el original: si se corta aqui queda un huerfano recuperable,
            # nunca una fila apuntando a un archivo que ya no existe
            self.db.x("UPDATE recordings SET path=?, ended_at=?, duration_sec=?, size_bytes=?, status='done', note=? "
                      "WHERE id=?",
                      (str(final), datetime.now().isoformat(timespec="seconds"), dur if dur else secs, size, note, rec_id))
            if final != path:
                try:
                    path.unlink()
                except OSError:
                    pass
            self._totals_at = 0.0
            log.info("Grabacion lista: %s (%.0f MB, %s)", final.name, size / 1e6, fmt_secs(dur or secs))
            if not self.db.q("SELECT 1 FROM recordings WHERE id=?", (rec_id,)):
                return                              # la borraron mientras se procesaba
            thumb = THUMB_DIR / f"{rec_id}.jpg"
            try:
                if exe:
                    await asyncio.to_thread(make_thumb, exe, final, dur or secs, thumb)
            except Exception:
                log.warning("No se pudo crear la miniatura\n%s", traceback.format_exc())
            if username:
                self.notify("✅ Grabación guardada", f"{username} · {fmt_secs(dur or secs)} · {size / 1e6:.0f} MB",
                            "saved", raw_user or username, thumb if thumb.exists() else None)
            health = ""
            try:
                if exe and dur and dur > 5:
                    h = await asyncio.to_thread(analyze_video, exe, final, dur)
                    self.db.x("UPDATE recordings SET codec=?, width=?, height=?, fps=?, frames=?, "
                              "frozen_secs=?, ts_bad=?, analyzed_at=? WHERE id=?",
                              (*analysis_fields(h), rec_id))
                    fz = ", ".join(f"{fmt_secs(a)}-{fmt_secs(b)}" for a, b in h["freezes"][:8])
                    log.info("Analisis de %s: codec=%s %s fps=%s fotogramas=%s congelado=%s (%.0f s en %d tramos: %s) "
                             "tiempos: retrocesos=%d hueco_max=%.1fs desfase_av=%.2f->%.2fs (calidad %s)",
                             final.name, h["codec"], h["size"], h["fps"], h["frames"], h["frozen"], h["frozen_secs"],
                             len(h["freezes"]), fz or "-", h["ts_backwards"], h["ts_maxgap"], h["av_start"], h["av_end"], quality)
                    if h["ts_bad"] and not h["frozen"] and h["frames"]:
                        health = (f"⚠ Tiempos del video irregulares (hueco {h['ts_maxgap']:.0f} s): puede verse congelado en el "
                                  "reproductor. Usa 🛠 en «Directos» para crear una copia reparada")
                        self.db.x("UPDATE recordings SET note=? WHERE id=?", ((note + " · " if note else "") + "⚠ Tiempos irregulares (usa 🛠)", rec_id))
                    if h["frames"] == 0 or h["frozen"]:
                        health = ("⚠ La grabación no tiene video" if h["frames"] == 0 else
                                  f"⚠ Imagen congelada {fmt_secs(h['frozen_secs'])} de {fmt_secs(dur)}")
                        health += f" (calidad {quality}, {h['size']})"
                        self.db.x("UPDATE recordings SET note=? WHERE id=?", ((note + " · " if note else "") + health, rec_id))
                        us = self.users.get(raw_user)
                        if us is not None and quality.startswith("origin"):
                            us.avoid_origin = True
                            health += ". La próxima vez se probará HD primero."
                        log.warning("Video dañado en %s: %s", final.name, health)
            except Exception:
                log.warning("No se pudo analizar el video\n%s", traceback.format_exc())
            if health:
                self.notify("⚠ Video dañado", f"{username}: {health}", "problem", raw_user or username)
        except Exception:
            log.error("Error al finalizar la grabacion %s\n%s", path, traceback.format_exc())
            try:
                self.db.x("UPDATE recordings SET status='error', note=? WHERE id=?",
                          ("Error al finalizar (ver log)", rec_id))
            except Exception:
                pass
        finally:
            self._finalizing.discard(rec_id)

    async def recover_unfinished(self) -> None:
        """Si el programa se cerro mientras grababa, rescata lo que quedo en disco."""
        for r in self.db.q("SELECT * FROM recordings WHERE status IN ('recording','processing')"):
            p = Path(r["path"])
            if not p.exists():
                for alt in (p.with_suffix(".mp4"), p.with_suffix(".mkv")):
                    if alt.exists():
                        p = alt
                        break
            if not p.exists():
                log.warning("Grabacion ya no esta en disco; se quita de la lista: %s", p.name)
                self.db.x("DELETE FROM recordings WHERE id=?", (r["id"],))
                continue
            try:
                started = datetime.fromisoformat(r["started_at"])
            except Exception:
                started = None
            log.warning("Recuperando grabacion interrumpida: %s", p.name)
            self._spawn(self._finalize(r["id"], p, started, 0))
        await self._import_orphans()

    async def _import_orphans(self) -> None:
        """Archivos que quedaron sin fila en la BD (corte entre crear el archivo y apuntarlo)."""
        exe = find_ffmpeg(self.s)
        out = Path(self.s.get("output_dir"))
        known: set[str] = set()
        for r in self.db.q("SELECT path FROM recordings"):
            p = Path(r["path"])
            for variant in (p, p.with_suffix(".mkv"), p.with_suffix(".mp4")):
                known.add(os.path.normcase(os.path.abspath(str(variant))))
        imported = 0
        for u in list(self.users.values()):
            folder = out / u.username
            if not folder.is_dir():
                continue
            try:
                entries = sorted(folder.iterdir())
            except OSError:
                continue
            for p in entries:
                if self.stopping or imported >= 50:
                    return
                try:
                    if not p.is_file():
                        continue
                    if p.name.endswith(".tmp.mp4"):
                        if time.time() - p.stat().st_mtime > 3600:
                            p.unlink(missing_ok=True)
                        continue
                    if p.suffix.lower() not in (".mkv", ".mp4"):
                        continue
                    key = os.path.normcase(os.path.abspath(str(p)))
                    st = p.stat()
                except OSError:
                    continue
                if key in known or time.time() - st.st_mtime < 600:
                    continue
                if st.st_size < max(self.s.int("min_keep_kb", 0, 10_000_000) * 1024, 1):
                    continue
                if self.db.q("SELECT 1 FROM recordings WHERE path=?", (str(p),)):
                    continue
                dur = await asyncio.to_thread(probe_duration, exe, p) if exe else None
                if not dur or dur < 5:
                    continue
                rid = self.db.x(
                    "INSERT INTO recordings(user_id,path,started_at,ended_at,duration_sec,size_bytes,status,quality,note) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (u.id, str(p), datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                     datetime.now().isoformat(timespec="seconds"), dur, st.st_size, "done", "",
                     "Recuperada al iniciar"))
                if exe:
                    await asyncio.to_thread(make_thumb, exe, p, dur, THUMB_DIR / f"{rid}.jpg")
                known.add(key)
                imported += 1
                log.info("Grabacion huerfana recuperada: %s", p.name)
        if imported:
            self._totals_at = 0.0

    # -- mantenimiento: espacio y retencion -------------------------------
    async def _housekeeping(self) -> None:
        while not self.stopping:
            try:
                await self._storage_tick()
            except Exception:
                log.warning("Error en la limpieza automatica\n%s", traceback.format_exc())
            await self._nap(600)

    async def _storage_tick(self) -> None:
        out = Path(self.s.get("output_dir"))
        try:
            out.mkdir(parents=True, exist_ok=True)   # sin la carpeta no se puede saber el espacio libre
            usage = shutil.disk_usage(str(out))
            self.free_gb: Optional[float] = usage.free / 1e9
            self.total_gb = usage.total / 1e9
        except OSError:
            self.free_gb = None
        min_free = float(self.s.int("min_free_gb", 0, 10_000))
        low = self.free_gb is not None and self.free_gb < min_free
        if low != self.low_space:
            self.low_space = low
            if low:
                log.warning("Poco espacio libre: %.1f GB", self.free_gb or 0)
                self.notify("💾 Poco espacio en disco",
                            f"Quedan {self.free_gb:.1f} GB libres. Las grabaciones automaticas quedan en pausa; "
                            "libera espacio o cambia la retencion en Ajustes.", "problem")
            elif self.free_gb is not None:
                self.notify("💾 Espacio recuperado", f"Quedan {self.free_gb:.1f} GB libres; se reanudan las grabaciones.")
        dest = self.db.backup_if_due(BACKUP_DIR, 7)
        if dest is not None:
            log.info("Copia de seguridad de la base de datos: %s", dest.name)
        max_gb = float(self.s.int("max_total_gb", 0, 1_000_000))
        days = self.s.int("retention_days", 0, 3650)
        if max_gb <= 0 and days <= 0:
            return
        rows = self.db.q("SELECT id, path, size_bytes, started_at FROM recordings "
                         "WHERE status='done' AND favorite=0 ORDER BY id ASC")
        cutoff = time.time() - days * 86400 if days > 0 else None
        total = sum(r["size_bytes"] or 0 for r in rows)
        removed = 0
        for r in rows:
            if self.stopping:
                return
            too_old = False
            if cutoff:
                try:
                    too_old = datetime.fromisoformat(r["started_at"]).timestamp() < cutoff
                except ValueError:
                    pass
            too_big = max_gb > 0 and total > max_gb * 1e9
            if not (too_old or too_big):
                continue
            self._purge_recording(r["id"], Path(r["path"]))
            total -= r["size_bytes"] or 0
            removed += 1
        if removed:
            log.info("Limpieza automatica: %d grabaciones borradas", removed)

    def _purge_recording(self, rec_id: int, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            log.warning("No se pudo borrar %s: %s", path, e)
        (THUMB_DIR / f"{rec_id}.jpg").unlink(missing_ok=True)
        self.db.x("DELETE FROM recordings WHERE id=?", (rec_id,))
        self._totals_at = 0.0

    # -- comandos desde la interfaz --------------------------------------
    async def record_now(self, name: str) -> dict:
        u = self.users.get(name)
        if not u:
            return {"ok": False, "msg": "Usuario no encontrado"}
        if u.recording:
            return {"ok": True, "msg": "Ya esta grabando"}
        if self.low_space:
            return {"ok": False, "msg": f"Quedan {self.free_gb:.1f} GB libres: no se inicia la grabacion."}
        u.suppress, u.force, u.cooldown_until = False, True, 0
        for _ in range(60):                       # esperar a que termine una revision en curso
            if not u.checking:
                break
            await asyncio.sleep(0.5)
        u.checking = True
        await self._check(u)
        if u.recording:
            return {"ok": True, "msg": "Grabacion iniciada"}
        if u.error:
            return {"ok": False, "msg": u.error}
        return {"ok": False, "msg": "Ahora mismo no esta transmitiendo."}

    async def stop_user(self, name: str) -> dict:
        u = self.users.get(name)
        if not u or not u.recording:
            return {"ok": True}
        u.stop_req, u.suppress = True, True
        if u.proc:
            await self._quit_proc(u.proc, graceful=True)
        if u.task:
            try:
                await asyncio.wait_for(asyncio.shield(u.task), 30)
            except Exception:
                pass
        return {"ok": True}

    async def shutdown(self) -> None:
        self.stopping = True
        self._stop.set()
        for u in list(self.users.values()):
            if u.recording:
                u.stop_req = True
                if u.proc:
                    await self._quit_proc(u.proc, graceful=True)
        pending = [t for t in self.bg if not t.done()]
        if pending:
            _, still = await asyncio.wait(pending, timeout=60)
            for t in still:
                t.cancel()
            if still:
                await asyncio.wait(still, timeout=10)
        await self.engine.drop()
        kill_children()                            # nada de ffmpeg vivos al salir

    # -- vista para la interfaz ------------------------------------------
    def snapshot(self) -> list[dict]:
        now = time.time()
        if now - self._totals_at > 2.0:            # antes era un GROUP BY en cada sondeo (cada 2 s)
            self._totals = {r["user_id"]: r for r in self.db.q(
                "SELECT user_id, COUNT(*) n, COALESCE(SUM(size_bytes),0) sz FROM recordings "
                "WHERE status IN ('done','processing') GROUP BY user_id")}
            self._totals_at = now
        totals = self._totals
        out = []
        # copia atomica: load_users intercambia el dict entero, nunca lo muta a mitad
        for u in sorted(self.users.values(), key=lambda x: x.username):
            if not u.enabled:
                status = "paused"
            elif u.recording:
                status = "recording"
            elif u.live:
                status = "live"
            elif u.error:
                status = "error"
            elif u.live is False:
                status = "offline"
            else:
                status = "checking"
            t = totals.get(u.id, {})
            out.append({
                "username": u.username, "nickname": u.nickname, "status": status, "enabled": u.enabled,
                "avatar": f"/avatars/{u.username}?v={u.avatar_ver}" if avatar_path(u.username).exists() else None,
                "auto_record": u.auto_record, "live": u.live, "viewers": u.viewers,
                "title": u.title, "error": u.error, "error_kind": u.error_kind,
                "last_check_ago": int(now - u.last_check) if u.last_check else None,
                "next_check_in": max(0, int(u.next_check - now)) if u.enabled and not u.checking else 0,
                "rec_seconds": int(now - u.rec_started) if u.recording and u.rec_started else 0,
                "rec_size": u.rec_size if u.recording else 0, "rec_quality": u.rec_quality if u.recording else "",
                "rec_bitrate": u.rec_bitrate if u.recording else "", "rec_speed": u.rec_speed if u.recording else "",
                "suppressed": u.suppress,
                "total_recordings": t.get("n", 0), "total_size": t.get("sz", 0),
            })
        return out


def fmt_secs(s: float) -> str:
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# --------------------------------------------------------------------------- actualizador del motor
ENGINE_PACKAGES = ["TikTokLive", "imageio-ffmpeg", "yt-dlp"]


_VERSIONS_CACHE: dict = {}


def _module_version(pkg: str) -> Optional[str]:
    try:
        if pkg == "TikTokLive":
            from TikTokLive.__version__ import PACKAGE_VERSION  # type: ignore
            return str(PACKAGE_VERSION)
        if pkg == "imageio-ffmpeg":
            import imageio_ffmpeg  # type: ignore
            return str(imageio_ffmpeg.__version__)
        if pkg == "yt-dlp":
            from yt_dlp.version import __version__  # type: ignore
            return str(__version__)
    except Exception:
        pass
    return None


def engine_versions() -> dict:
    """Versiones cargadas en este proceso (no cambian hasta reiniciar, por eso se cachean)."""
    if _VERSIONS_CACHE:
        return dict(_VERSIONS_CACHE)
    from importlib.metadata import PackageNotFoundError, version
    out = {}
    for pkg in ENGINE_PACKAGES:
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = _module_version(pkg)
    _VERSIONS_CACHE.update(out)
    return out


def engine_origin() -> str:
    try:
        import TikTokLive  # type: ignore
        p = Path(TikTokLive.__file__).resolve()
        cur = current_engine_dir()
        if cur and str(p).startswith(str(cur.resolve())):
            return f"motor actualizado ({cur.name})"
        return "motor empaquetado/instalado de fabrica"
    except Exception as e:
        return f"no carga: {type(e).__name__}: {e}"


class EngineUpdater:
    """Descarga las ultimas versiones del motor en una carpeta nueva, las prueba en un proceso
    aparte y solo entonces las activa. Si algo falla, el motor anterior sigue intacto."""

    def __init__(self):
        self.running = False
        self.lines: list[str] = []
        self.result: Optional[str] = None      # None | ok | error
        self.restart_needed = False
        self._tail = ""

    def _say(self, msg: str) -> None:
        self.lines.append(msg)
        log.info("[motor] %s", msg)

    @staticmethod
    def _system_python() -> Optional[list[str]]:
        """Python instalado en el PC con la MISMA version que el .exe (los binarios deben coincidir)."""
        maj, mn = sys.version_info[:2]
        cands = ([["py", f"-{maj}.{mn}"]] if WIN else []) + [[f"python{maj}.{mn}"]]
        for c in cands:
            exe = shutil.which(c[0])
            if not exe:
                continue
            try:
                r = sp_run([exe, *c[1:], "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
                           capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.split() == [str(maj), str(mn)]:
                    return [exe, *c[1:]]
            except Exception:
                pass
        return None

    @staticmethod
    def _worker(*args: str) -> list[str]:
        """Comando que ejecuta este mismo programa en modo auxiliar (pip / prueba)."""
        if FROZEN:
            return [sys.executable, *args]
        return [sys.executable, str(Path(__file__).resolve()), *args]

    def start(self) -> bool:
        if self.running:
            return False
        self.running, self.lines, self.result = True, [], None
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def run_blocking(self) -> None:
        self.running, self.lines, self.result = True, [], None
        self._run()

    def _run(self) -> None:
        new_dir: Optional[Path] = None
        try:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            ENGINE_ROOT.mkdir(parents=True, exist_ok=True)
            new_dir = ENGINE_ROOT / stamp
            self._say("Descargando ultimas versiones: " + ", ".join(ENGINE_PACKAGES))
            args = ["install", "--upgrade", "--target", str(new_dir),
                    "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
                    "--no-warn-script-location"]
            wheels = RES_DIR / "wheels"            # wheels propios (paquetes que solo existen como codigo fuente)
            if wheels.is_dir():
                args += ["--find-links", str(wheels)]
            # En el .exe no hay compilador ni Python: solo se admiten paquetes ya compilados.
            args += ["--only-binary=:all:"] if FROZEN else ["--prefer-binary"]
            args += ENGINE_PACKAGES
            # El .exe no tiene consola: los procesos auxiliares escriben su salida en un archivo.
            pip_log, test_log = LOG_DIR / "motor-pip.log", LOG_DIR / "motor-prueba.log"
            r = sp_run(self._worker("--pip-worker", str(pip_log), *args), capture_output=True, timeout=900)
            out = read_text(pip_log)
            if r.returncode != 0 and FROZEN:
                alt = self._system_python()
                if alt:
                    self._say("El instalador integrado fallo; se reintenta con el Python instalado en este PC...")
                    log.warning("pip integrado fallo (codigo %s):\n%s", r.returncode, redact(out[-1500:]))
                    r = sp_run([*alt, "-m", "pip", *args], capture_output=True, text=True, errors="replace",
                               timeout=900)
                    out = (r.stdout or "") + (r.stderr or "")
            self._tail = out[-3000:]
            for ln in [x for x in out.strip().splitlines() if x.strip()][-14:]:
                self._say(redact(ln))
            if r.returncode != 0:
                raise RuntimeError(f"No se pudo descargar/instalar el motor (codigo {r.returncode}). "
                                   "Si el detalle de arriba no habla de internet, envia el reporte de Diagnostico.")
            self._say("Verificando que el motor nuevo funciona...")
            v = sp_run(self._worker("--selftest", str(new_dir), str(test_log)), capture_output=True, timeout=240)
            vout = read_text(test_log)
            self._tail = vout[-3000:]
            line = next((x for x in vout.splitlines() if x.startswith("OK ")), "")
            if v.returncode != 0 or not line:
                raise RuntimeError("El motor nuevo no supero la comprobacion (importacion, contrato de "
                                   "TikTokLive o ffmpeg); se mantiene el anterior.\n" + redact(vout[-700:]))
            parts = line.split()
            if len(parts) < 5 or parts[4] != "nuevo":
                raise RuntimeError("El motor nuevo no tomo prioridad sobre el empaquetado; se mantiene el anterior.")
            self._say(f"Motor listo: TikTokLive {parts[1]}, imageio-ffmpeg {parts[2]}, yt-dlp {parts[3]}")
            set_current_engine(stamp)
            self._say("Reinicia el programa para usar la version nueva.")
            self.result = "ok"
            self.restart_needed = True
        except subprocess.TimeoutExpired:
            self.result = "error"
            self._say("La actualizacion tardo demasiado (revisa tu conexion).")
            log.error("Actualizacion del motor: timeout")
            if new_dir:
                shutil.rmtree(new_dir, ignore_errors=True)
        except Exception as e:
            self.result = "error"
            self._say(f"ERROR: {e}")
            log.error("Actualizacion del motor fallo\n%s\n--- salida de pip/prueba ---\n%s",
                      redact(traceback.format_exc()), redact(self._tail))
            if new_dir:
                shutil.rmtree(new_dir, ignore_errors=True)
        finally:
            self.running = False


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _redirect_std(outfile: str):
    """Sin consola sys.stdout/stderr son None: todo lo que se imprima va a este archivo."""
    fh = open(outfile, "w", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = sys.stderr = fh
    return fh


def pip_prepare_frozen() -> None:
    """pip dentro de PyInstaller: distlib (que pip usa en Windows) no conoce el cargador de modulos
    empaquetado y falla con 'Unable to locate finder'. Se le ensena a tratarlo como uno normal."""
    try:
        from pip._vendor.distlib import resources as dres  # type: ignore
        loader = getattr(dres, "__loader__", None)
        if loader is not None and type(loader) not in dres._finder_registry:
            dres._finder_registry[type(loader)] = dres.ResourceFinder
    except Exception:
        traceback.print_exc()


def pip_worker(args: list[str]) -> int:
    """Modo auxiliar: ejecuta pip dentro de este mismo programa (sirve en el .exe sin Python).
    args[0] = archivo de salida; el resto son los argumentos de pip."""
    fh = _redirect_std(args[0])
    code = 1
    try:
        if FROZEN:
            pip_prepare_frozen()
        from pip._internal.cli.main import main as pip_main  # type: ignore
        code = int(pip_main(args[1:]) or 0)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:
        traceback.print_exc()
    fh.flush()
    return code


def _accepts_two_args(fn) -> bool:
    """True si `fn` se puede llamar con dos argumentos posicionales (metodos ya ligados)."""
    try:
        import inspect
        inspect.signature(fn).bind(None, None)
        return True
    except (TypeError, ValueError):
        return False


def _api_contract() -> list[str]:
    """Comprueba las firmas que el programa usa de verdad, no solo que los modulos importen.

    TikTokLive cambia sus rutas internas entre versiones: un motor que importa pero al que le
    cambiaron `fetch_user_room_data` deja el programa sin detectar lives. Mejor saberlo antes
    de activarlo (y quedarse con el motor anterior) que despues.
    """
    problems: list[str] = []
    try:
        import inspect
        from TikTokLive import TikTokLiveClient
        from TikTokLive.client.web.routes.fetch_room_id_api import FetchRoomIdAPIRoute

        fn = getattr(FetchRoomIdAPIRoute, "fetch_user_room_data", None)
        if not callable(fn):
            problems.append("Falta FetchRoomIdAPIRoute.fetch_user_room_data")
        else:
            params = set(inspect.signature(fn).parameters)
            if not {"web", "unique_id"} <= params:
                problems.append(f"fetch_user_room_data cambio de firma: {sorted(params)}")

        async def _probe() -> None:
            client = TikTokLiveClient(unique_id="@tkrec_selftest")
            try:
                web = client.web
                for name in ("fetch_room_info", "fetch_room_id_from_api", "get", "set_session"):
                    if not hasattr(web, name):
                        problems.append(f"web.{name} no existe")
                if hasattr(web, "get") and "extra_headers" not in inspect.signature(web.get).parameters:
                    problems.append("web.get ya no acepta extra_headers")
                if hasattr(web, "set_session") and not _accepts_two_args(web.set_session):
                    problems.append("web.set_session cambio de firma")
            finally:
                try:
                    await client.web.close()
                except Exception:
                    pass

        asyncio.run(_probe())
    except Exception as e:
        problems.append(f"{type(e).__name__}: {e}")
    return problems


def selftest(target: str, outfile: str) -> int:
    """Modo auxiliar: carga el motor de `target` con prioridad, comprueba el contrato y confirma
    de donde salio. Devuelve 0 solo si el motor nuevo es realmente usable."""
    fh = _redirect_std(outfile)
    d = Path(target)
    activate_engine(d)
    try:
        import TikTokLive  # type: ignore
        from TikTokLive import TikTokLiveClient  # noqa: F401
        from TikTokLive.client.web.routes.fetch_room_id_api import FetchRoomIdAPIRoute  # noqa: F401
        import imageio_ffmpeg  # type: ignore  # noqa: F401
        import yt_dlp  # type: ignore  # noqa: F401
    except Exception:
        traceback.print_exc()
        fh.flush()
        return 2
    problems = _api_contract()
    if problems:
        for p in problems:
            print("CONTRATO:", p)
        fh.flush()
        return 3
    try:
        import imageio_ffmpeg  # type: ignore
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        if not ff or sp_run([ff, "-version"], capture_output=True, timeout=30).returncode != 0:
            print("CONTRATO: el ffmpeg del motor no se puede ejecutar")
            fh.flush()
            return 3
    except Exception as e:
        print("CONTRATO: ffmpeg del motor fallo:", type(e).__name__, e)
        fh.flush()
        return 3
    here = str(Path(TikTokLive.__file__).resolve()).startswith(str(d.resolve()))
    print("OK", _module_version("TikTokLive"), _module_version("imageio-ffmpeg"), _module_version("yt-dlp"),
          "nuevo" if here else "empaquetado")
    fh.flush()
    return 0


# --------------------------------------------------------------------------- reporte de diagnostico
def tail_file(path: Path, n: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(collections.deque(f, maxlen=n))
    except OSError:
        return ""


def backup_summary() -> str:
    try:
        files = sorted(BACKUP_DIR.glob("tkrec-*.db"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return "copias: ?"
    if not files:
        return "copias: ninguna"
    last = datetime.fromtimestamp(files[-1].stat().st_mtime)
    return f"copias: {len(files)}, ultima {last:%Y-%m-%d %H:%M}"


def build_report(app: "App") -> str:
    ex = find_ffmpeg(app.settings)
    ok, why = Engine.available()
    mgr = app.manager
    bot = app.bot_status()
    bot_line = ("escuchando" if bot.get("running")
                else ("configurado, sin escuchar" if bot.get("enabled") else "no configurado"))
    if bot.get("error"):
        bot_line += f" · {redact(str(bot['error']))}"
    if bot.get("chat"):
        chat = str(bot["chat"])
        bot_line += f" · chat {('…' + chat[-4:]) if len(chat) > 4 else '…'}"
    disk = ""
    if mgr and mgr.free_gb is not None:
        disk = f" | Disco: {mgr.free_gb:.1f} GB libres de {mgr.total_gb:.1f} GB"
        if mgr.low_space:
            disk += " (PAUSA por poco espacio)"
    lines = [
        "=== LiveVault - reporte de diagnostico ===",
        f"Fecha: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"App: {APP_VERSION}  |  SO: {platform.platform()}  |  Python: {platform.python_version()}"
        f"{' (exe)' if FROZEN else ''}",
        f"Motor: {json.dumps(engine_versions())}  |  disponible: {ok} {why}",
        f"Motor cargado desde: {engine_origin()}",
        f"Bandeja: {'si' if app.tray else 'no'}  |  Inicio con Windows: "
        f"{(autostart_get() or 'no') if WIN else 'n/a'}  |  Datos: {DATA_DIR}",
        f"ffmpeg: {ffmpeg_version(ex)}  ({ex}){disk}",
        f"Base de datos: {getattr(app, 'db_health', '?')}  |  {backup_summary()}",
        f"Secretos: {'cifrados con DPAPI' if WIN else 'sin cifrado del sistema'}  |  trabajos activos: {app.active_jobs()}",
        f"Bot Telegram: {bot_line}",
        "", "--- Ajustes ---",
    ]
    for k, v in app.settings._cache.items():
        lines.append(f"{k} = {'<configurado>' if k in SECRET_KEYS and v else ('' if k in SECRET_KEYS else v)}")
    lines += ["", f"Sessionid: {app.manager.session_state if app.manager else '?'}  |  avisos celular: "
                  f"{json.dumps(app.pusher.configured())}", "", "--- Usuarios ---"]
    for u in app.manager_snapshot():
        lines.append(f"@{u['username']} (nick={'si' if u['nickname'] else 'no'}, foto={'si' if u['avatar'] else 'no'}): {u['status']} viewers={u['viewers']} err={u['error']!r} "
                     f"grabaciones={u['total_recordings']}")
    lines += ["", "--- Ultimas grabaciones ---"]
    for r in app.db.q("SELECT r.*, u.username FROM recordings r JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT 10"):
        lines.append(f"#{r['id']} @{r['username']} {r['started_at']} {r['status']} {r['quality']} "
                     f"{r['size_bytes']}B {fmt_secs(r['duration_sec'])} {r['note'] or ''}")
    act = [l for l in tail_file(LOG_DIR / "actividad.log", 600).splitlines()
           if "Analisis de" in l or "Calidades disponibles" in l or "Video dañado" in l]
    lines += ["", "--- Calidad y analisis de las ultimas grabaciones ---", redact("\n".join(act[-24:])) or "(todavia sin datos)"]
    lines += ["", "--- Ultimos errores (errores.log) ---", redact(tail_file(LOG_DIR / "errores.log", 300)) or "(sin errores)"]
    return scrub_paths("\n".join(lines))


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    return '"' + s.replace('"', '""') + '"'


def build_support(app: "App") -> bytes:
    """ZIP con lo necesario para diagnosticar, sin secretos (sessionid, tokens, proxy)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEEME.txt",
                   "Paquete de soporte de LiveVault.\n"
                   "Incluye el reporte, los logs y un resumen de usuarios y grabaciones.\n"
                   "Las claves (sessionid, tokens, proxy y webhook) NO se incluyen, y las rutas de\n"
                   "tu PC aparecen sustituidas por <etiquetas>.\n"
                   "OJO: si lo publicas, revisalo antes: incluye la lista de usuarios que vigilas.\n")
        z.writestr("reporte.txt", build_report(app))
        for name in ("errores", "actividad"):
            z.writestr(f"logs/{name}.log", scrub_paths(redact(tail_file(LOG_DIR / f"{name}.log", 1500))))
        z.writestr("ajustes.json", json.dumps(app.settings.public(), ensure_ascii=False, indent=2))
        users = app.db.q("SELECT username, nickname, enabled, auto_record, added_at, notified_room "
                         "FROM users ORDER BY username")
        z.writestr("usuarios.json", json.dumps(users, ensure_ascii=False, indent=2))
        recs = app.db.q("SELECT r.id, u.username, r.started_at, r.ended_at, r.duration_sec, r.size_bytes, "
                        "r.status, r.quality, r.session_id, r.note FROM recordings r "
                        "JOIN users u ON u.id=r.user_id ORDER BY r.id DESC")
        cols = ("id", "username", "started_at", "ended_at", "duration_sec", "size_bytes",
                "status", "quality", "session_id", "note")
        lines = ["id,usuario,inicio,fin,duracion_seg,bytes,estado,calidad,sesion,nota"]
        lines += [",".join(_csv_cell(r[k]) for k in cols) for r in recs]
        z.writestr("grabaciones.csv", "\n".join(lines))
    return buf.getvalue()


# --------------------------------------------------------------------------- instancia unica, ventana, inicio con Windows
INSTANCE_FILE = DATA_DIR / "instancia.json"
PANEL_TITLE = "LiveVault"


class Server(ThreadingHTTPServer):
    allow_reuse_address = not WIN      # en Windows permitiria que dos programas compartan el puerto
    daemon_threads = True


def probe_instance(port: int) -> bool:
    """¿Hay ya un LiveVault respondiendo en ese puerto?

    Sin desactivar el proxy: si el usuario tiene HTTP_PROXY (VPN, empresa), `urlopen` hacia
    127.0.0.1 saldria por el proxy, fallaria y se abriria una segunda instancia.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=2) as r:
            return json.loads(r.read().decode()).get("app") == "tkrec"
    except Exception:
        return False


def open_app_window(url: str) -> None:
    """Abre el panel como ventana propia (Edge/Chrome en modo app); si no hay, el navegador normal."""
    if WIN:
        cands: list[Path] = []
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                          Path(base) / "Google/Chrome/Application/chrome.exe"]
        for exe in cands:
            if exe.exists():
                try:
                    subprocess.Popen([str(exe), f"--app={url}", "--window-size=1240,860"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue
    try:
        webbrowser.open(url)
    except Exception:
        log.warning("No se pudo abrir el navegador\n%s", traceback.format_exc())


def focus_panel_window() -> bool:
    """Si el panel ya esta abierto, lo trae al frente en vez de abrir otro. Solo Windows."""
    if not WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        found: list = []
        proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _lp):
            n = user32.GetWindowTextLengthW(hwnd)
            if n and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith(PANEL_TITLE):
                    cls = ctypes.create_unicode_buffer(64)
                    user32.GetClassNameW(hwnd, cls, 64)
                    if cls.value.startswith("Chrome_WidgetWin"):
                        found.append(hwnd)
            return True

        user32.EnumWindows(proc_t(cb), 0)
        if not found:
            return False
        user32.ShowWindow(found[0], 9)          # SW_RESTORE
        user32.SetForegroundWindow(found[0])
        return True
    except Exception:
        log.warning("No se pudo enfocar el panel\n%s", traceback.format_exc())
        return False


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "LiveVault"
LEGACY_RUN_NAME = "TikTokLiveRecorder"          # versiones anteriores del programa


def autostart_command() -> str:
    if FROZEN:
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")             # sin ventana de consola
    return f'"{w if w.exists() else exe}" "{Path(__file__).resolve()}" --minimized'


def autostart_get() -> Optional[str]:
    if not WIN:
        return None
    import winreg  # type: ignore
    for name in (RUN_NAME, LEGACY_RUN_NAME):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            continue
    return None


def autostart_set(enabled: bool) -> None:
    import winreg  # type: ignore
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        for name in (RUN_NAME, LEGACY_RUN_NAME) if not enabled else (LEGACY_RUN_NAME,):
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


def wait_for_network(timeout: float = 90) -> bool:
    """Al encender el PC la red puede tardar: esperar antes de empezar a consultar a TikTok."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("www.tiktok.com", 443), timeout=4).close()
            return True
        except OSError:
            time.sleep(3)
    return False


# --------------------------------------------------------------------------- icono y bandeja del sistema
ICON_COLORS = {"idle": (139, 147, 167), "live": (255, 159, 26), "recording": (255, 59, 87)}


def make_icon_image(state: str = "idle", size: int = 64):
    from PIL import Image, ImageDraw  # type: ignore
    big = size * 4
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = ICON_COLORS.get(state, ICON_COLORS["idle"]) + (255,)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 4, fill=(23, 26, 33, 255))
    pad = big * 0.2
    d.ellipse((pad, pad, big - pad, big - pad), outline=col, width=max(2, int(big * 0.07)))
    c, r = big / 2, big * 0.17
    d.ellipse((c - r, c - r, c + r, c + r), fill=col)
    return img.resize((size, size), Image.LANCZOS)


def tray_available() -> bool:
    try:
        import pystray  # type: ignore  # noqa: F401
        from PIL import Image  # type: ignore  # noqa: F401
        return True
    except Exception as e:
        log.info("Sin bandeja del sistema (%s: %s); el programa corre en modo consola.", type(e).__name__, e)
        return False


class Tray:
    def __init__(self, app: "App"):
        import pystray  # type: ignore
        from pystray import Menu, MenuItem as Item  # type: ignore
        self.app = app
        self.state = "idle"
        self._stop = threading.Event()
        items = [
            Item("Abrir panel", lambda icon, item: app.open_panel(), default=True),
            Item(lambda item: self.status_text(), None, enabled=False),
            Menu.SEPARATOR,
            Item("Abrir carpeta de grabaciones", lambda icon, item: app.open_recordings()),
            Item("Notificaciones", lambda icon, item: self.toggle_notifications(),
                 checked=lambda item: app.settings.get("notifications") == "1"),
        ]
        if WIN:
            items.append(Item("Iniciar con Windows", lambda icon, item: self.toggle_autostart(),
                              checked=lambda item: bool(autostart_get())))
        items += [Menu.SEPARATOR, Item("Salir", lambda icon, item: app.request_quit())]
        self.icon = pystray.Icon("tkrec", make_icon_image("idle"), PANEL_TITLE, Menu(*items))

    # -- textos / estado
    def counts(self) -> tuple[int, int, int]:
        snap = self.app.manager_snapshot()
        return (sum(u["status"] == "recording" for u in snap),
                sum(u["status"] in ("live", "recording") for u in snap), len(snap))

    def status_text(self) -> str:
        rec, live, n = self.counts()
        return f"Grabando {rec} · En vivo {live} · Vigilando {n}"

    def toggle_notifications(self) -> None:
        s = self.app.settings
        s.set_many({"notifications": "0" if s.get("notifications") == "1" else "1"})

    def toggle_autostart(self) -> None:
        try:
            autostart_set(not autostart_get())
        except Exception:
            log.error("No se pudo cambiar el inicio con Windows\n%s", traceback.format_exc())

    def notify(self, title: str, msg: str) -> None:
        try:
            if getattr(self.icon, "HAS_NOTIFICATION", True):
                self.icon.notify(msg[:250], title[:60])
        except Exception:
            log.warning("No se pudo mostrar la notificacion\n%s", traceback.format_exc())

    # -- ciclo de vida
    def _refresh(self) -> None:
        while not self._stop.wait(3):
            try:
                rec, live, n = self.counts()
                state = "recording" if rec else ("live" if live else "idle")
                if state != self.state:
                    self.state = state
                    self.icon.icon = make_icon_image(state)
                self.icon.title = f"{PANEL_TITLE}\n{self.status_text()}"[:120]
            except Exception:
                log.warning("Error al refrescar la bandeja\n%s", traceback.format_exc())

    def _setup(self, icon) -> None:
        icon.visible = True
        threading.Thread(target=self._refresh, daemon=True, name="tray-refresh").start()

    def run(self) -> None:
        self.icon.run(setup=self._setup)          # bloquea hasta stop()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.icon.stop()
        except Exception:
            pass


# --------------------------------------------------------------------------- aplicacion + servidor web
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{2,24}$")
# Nombres reservados de Windows: crear la carpeta del usuario fallaria.
_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


def parse_username(raw: str) -> Optional[str]:
    raw = (raw or "").strip()
    m = re.search(r"tiktok\.com/@([A-Za-z0-9._]+)", raw)
    name = m.group(1) if m else raw.lstrip("@").strip().strip("/")
    name = name.lower().rstrip(".")
    return name if USERNAME_RE.match(name) and name.split(".")[0] not in _WIN_RESERVED else None


class UserError(Exception):
    """Error de una operacion sobre usuarios (el codigo imita al de la API HTTP)."""

    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


AddUserError = UserError          # compatibilidad con el nombre anterior


class Job:
    """Trabajo largo (p. ej. reparar un video) con estado y progreso consultable desde la UI."""

    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.state = "running"          # running | done | error
        self.progress = 0.0
        self.message = ""
        self.result: Any = None
        self.error = ""
        self.created = time.time()

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state,
                "progress": round(self.progress, 3), "message": self.message,
                "result": self.result, "error": self.error,
                "age": int(time.time() - self.created)}


class App:
    def __init__(self, port: int, open_browser: bool = True, minimized: bool = False, restarted: bool = False):
        self.port, self.open_browser = port, open_browser
        self.minimized, self.restarted = minimized, restarted
        self.db = DB(DATA_DIR / "tkrec.db")
        self.db_health = self.db.quick_check()
        if self.db_health != "ok":
            log.error("La base de datos no paso la comprobacion de integridad: %s", self.db_health)
        self.settings = Settings(self.db)
        self.pusher = Pusher(self.settings)
        self.engine = Engine(self.settings)
        self.updater = EngineUpdater()
        self.loop = asyncio.new_event_loop()
        self.manager: Manager = None  # type: ignore
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.tray: Optional[Tray] = None
        self.url = ""
        self.panel_url = ""
        self.quitting = False
        self._down = False
        self.quit_event = threading.Event()
        self.jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self.bot: Optional[Any] = None

    def bot_status(self) -> dict:
        if self.bot is not None:
            return self.bot.status()
        return {"enabled": bool(self.settings.get("push_tg_token").strip()),
                "running": False, "error": "", "chat": self.settings.get("push_tg_chat").strip()}

    def restart_bot(self) -> None:
        """(Re)inicia el bot de Telegram; se llama al arrancar y al cambiar token o chat."""
        old, self.bot = self.bot, None
        if old is not None:
            old.stop()
        if not self.settings.get("push_tg_token").strip():
            return
        try:
            self.bot = TelegramBot(self)
            self.bot.start()
        except Exception:
            log.error("No se pudo iniciar el bot de Telegram\n%s", traceback.format_exc())
            self.bot = None

    def manager_snapshot(self) -> list[dict]:
        return self.manager.snapshot() if self.manager else []

    def call(self, coro, timeout: float = 60):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def add_user(self, raw: str) -> str:
        """Alta compartida por el panel y el bot: misma validacion y mismos errores."""
        name = parse_username(raw)
        if not name:
            raise UserError("Usuario no valido. Usa el @usuario o el enlace de su perfil.")
        if self.db.q("SELECT 1 FROM users WHERE username=?", (name,)):
            raise UserError(f"@{name} ya esta en la lista.", 409)
        try:
            self.db.x("INSERT INTO users(username, added_at) VALUES(?,?)",
                      (name, datetime.now().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:
            raise UserError(f"@{name} ya esta en la lista.", 409)
        if self.manager is not None:
            self.manager.load_users()
        return name

    def remove_user(self, name: str, delete_files: bool = False) -> None:
        """Quita del panel y del bot. Sin delete_files conserva los videos en disco."""
        row = self.db.q("SELECT id FROM users WHERE username=?", (name,))
        if not row:
            raise UserError(f"@{name} no esta en la lista.", 404)
        user_id = int(row[0]["id"])
        if self.manager is not None and self.manager.users.get(name) is not None:
            try:
                self.call(self.manager.stop_user(name))
            except Exception:
                log.warning("No se pudo detener la grabacion de @%s al quitarlo", name)
        for r in self.db.q("SELECT id, path FROM recordings WHERE user_id=?", (user_id,)):
            (THUMB_DIR / f"{r['id']}.jpg").unlink(missing_ok=True)
            if delete_files:
                try:
                    Path(r["path"]).unlink(missing_ok=True)
                except OSError as e:
                    log.warning("No se pudo borrar %s: %s", r["path"], e)
        if delete_files:
            shutil.rmtree(Path(self.settings.get("output_dir")) / name, ignore_errors=True)
        avatar_path(name).unlink(missing_ok=True)
        (THUMB_DIR / f"live_{name}.jpg").unlink(missing_ok=True)
        self.db.x("DELETE FROM recordings WHERE user_id=?", (user_id,))
        self.db.x("DELETE FROM users WHERE id=?", (user_id,))
        if self.manager is not None:
            self.manager._totals_at = 0.0
            self.manager.load_users()
        try:
            self.call(self.engine.drop(name))
        except Exception:
            log.debug("No se pudo cerrar el cliente de @%s al quitarlo", name)

    def set_user_flag(self, name: str, field: str, value: bool) -> bool:
        """Activa/desactiva 'enabled' o 'auto_record'. Devuelve si habia una grabacion en curso."""
        if field not in ("enabled", "auto_record"):
            raise UserError("Campo no valido.")
        row = self.db.q("SELECT id FROM users WHERE username=?", (name,))
        if not row:
            raise UserError(f"@{name} no esta en la lista.", 404)
        was_recording = False
        if self.manager is not None:
            u = self.manager.users.get(name)
            was_recording = bool(u and u.recording)
        self.db.x(f"UPDATE users SET {field}=? WHERE id=?", (1 if value else 0, row[0]["id"]))
        if self.manager is not None:
            self.manager.load_users()
            if field == "enabled" and not value:
                try:
                    self.call(self.manager.stop_user(name))
                except Exception:
                    log.warning("No se pudo detener la grabacion de @%s al pausarlo", name)
        return was_recording

    def record_user(self, name: str) -> dict:
        if self.manager is None:
            raise UserError("El monitor no esta listo; espera unos segundos.", 503)
        if self.db.q("SELECT 1 FROM users WHERE username=?", (name,)) == []:
            raise UserError(f"@{name} no esta en la lista.", 404)
        return self.call(self.manager.record_now(name), 120)

    def stop_user_recording(self, name: str) -> bool:
        """Detiene la grabacion (el directo sigue). Devuelve si estaba grabando."""
        if self.manager is None:
            raise UserError("El monitor no esta listo; espera unos segundos.", 503)
        u = self.manager.users.get(name)
        if u is None:
            raise UserError(f"@{name} no esta en la lista.", 404)
        was = bool(u.recording)
        self.call(self.manager.stop_user(name), 60)
        return was

    def set_favorite(self, rec_id: int, value: bool) -> bool:
        if not self.db.q("SELECT 1 FROM recordings WHERE id=?", (rec_id,)):
            raise UserError("Grabacion no encontrada.", 404)
        self.db.x("UPDATE recordings SET favorite=? WHERE id=?", (1 if value else 0, rec_id))
        return bool(value)

    def delete_recordings(self, name: str, keep_favorites: bool = True) -> tuple[int, int]:
        """Borra todas las grabaciones terminadas de un usuario; las favoritas se conservan."""
        row = self.db.q("SELECT id FROM users WHERE username=?", (name,))
        if not row:
            raise UserError(f"@{name} no esta en la lista.", 404)
        user_id = int(row[0]["id"])
        rows = self.db.q("SELECT id, path, favorite, status FROM recordings WHERE user_id=?", (user_id,))
        removed, kept = 0, 0
        for r in rows:
            if r["status"] in ("recording", "processing"):
                continue
            if keep_favorites and int(r["favorite"] or 0):
                kept += 1
                continue
            try:
                Path(r["path"]).unlink(missing_ok=True)
            except OSError as e:
                log.warning("No se pudo borrar %s: %s", r["path"], e)
            (THUMB_DIR / f"{r['id']}.jpg").unlink(missing_ok=True)
            self.db.x("DELETE FROM recordings WHERE id=?", (r["id"],))
            removed += 1
        if removed and self.manager is not None:
            self.manager._totals_at = 0.0
        log.info("Borradas %d grabaciones de @%s (favoritas conservadas: %d)", removed, name, kept)
        return removed, kept

    # -- trabajos en segundo plano
    def start_job(self, kind: str, target) -> Job:
        job = Job(kind)
        with self._jobs_lock:
            now = time.time()
            for jid in [j for j, old in self.jobs.items() if now - old.created > 3600 and old.state != "running"]:
                self.jobs.pop(jid, None)
            self.jobs[job.id] = job

        def run() -> None:
            try:
                target(job)
            except Exception as e:
                job.state = "error"
                job.error = redact(f"{type(e).__name__}: {e}")
                log.error("Trabajo %s fallo\n%s", kind, traceback.format_exc())
            else:
                if job.state == "running":
                    job.state = "done"
            finally:
                job.message = job.message or ("Listo" if job.state == "done" else "Error")
                job.progress = 1.0 if job.state == "done" else job.progress

        threading.Thread(target=run, daemon=True, name=f"job-{kind}").start()
        return job

    def job(self, jid: str) -> Optional[Job]:
        with self._jobs_lock:
            return self.jobs.get(jid)

    def active_jobs(self) -> int:
        with self._jobs_lock:
            return sum(1 for j in self.jobs.values() if j.state == "running")

    def _loop_thread(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.manager = Manager(self.db, self.settings, self.engine)
        self.manager.notify = self.notify
        self.manager.load_users()
        self.loop.create_task(self.manager.run())
        self.loop.run_forever()

    # -- avisos / ventanas
    def notify(self, title: str, msg: str, kind: str = "info", username: Optional[str] = None,
               image: Optional[Path] = None) -> None:
        log.info("[aviso] %s - %s", title, msg)
        if self.settings.get("notifications") == "1" and self.tray:
            self.tray.notify(title, msg)
        self.pusher.send(kind, title, msg, image)

    def open_panel(self) -> None:
        if not focus_panel_window():
            open_app_window(self.panel_url)

    def open_recordings(self) -> None:
        d = Path(self.settings.get("output_dir"))
        d.mkdir(parents=True, exist_ok=True)
        open_folder(d)

    # -- arranque
    def start(self) -> None:
        self._bind()                                   # termina el proceso si ya hay otra instancia
        threading.Thread(target=self._loop_thread, daemon=True).start()
        while self.manager is None:
            time.sleep(0.05)
        assert self.httpd is not None
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="http").start()
        self.restart_bot()

        if WIN:                                        # si el .exe cambio de carpeta, actualizar el inicio automatico
            try:
                cur = autostart_get()
                if cur and cur != autostart_command():
                    autostart_set(True)
            except Exception:
                log.warning("No se pudo revisar el inicio con Windows\n%s", traceback.format_exc())

        def boot():
            try:
                if self.minimized and not self.settings.get("proxy"):
                    wait_for_network(90)               # al encender el PC la red puede tardar
                if self.settings.get("auto_update_on_start") == "1":
                    log.info("Actualizando motor al iniciar...")
                    self.updater.run_blocking()
            except Exception:
                log.error("Error en el arranque\n%s", traceback.format_exc())
            finally:
                self.loop.call_soon_threadsafe(self.manager.ready.set)
        threading.Thread(target=boot, daemon=True).start()

        log.info("Interfaz lista en %s%s", self.url, "  (iniciado en segundo plano)" if self.minimized else "")
        if self.open_browser and not self.minimized:
            threading.Timer(1.0, self.open_panel).start()

        try:
            self.tray = Tray(self) if tray_available() else None
        except Exception:                              # sin bandeja el panel sigue funcionando
            log.error("No se pudo crear el icono de la bandeja\n%s", traceback.format_exc())
            self.tray = None
        try:
            if self.tray:
                try:
                    self.tray.run()                    # bloquea hasta Salir
                except Exception:
                    log.error("La bandeja fallo\n%s", traceback.format_exc())
                if not self.quitting:
                    log.warning("La bandeja se cerro; el programa sigue en segundo plano.")
                    self.tray = None
                    self.quit_event.wait()
            else:
                self.quit_event.wait()
        except KeyboardInterrupt:
            pass
        self.shutdown()
        self._cleanup_instance()

    def _bind(self) -> None:
        handler = make_handler(self)
        if not self.restarted:                         # ¿ya hay otro programa abierto? (puerto guardado)
            try:
                info = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
                if probe_instance(int(info["port"])):
                    return self._already_running(int(info["port"]))
            except Exception:
                pass
        httpd = None
        for _ in range(40 if self.restarted else 2):   # al reiniciar, esperar a que el anterior suelte el puerto
            try:
                httpd = Server(("127.0.0.1", self.port), handler)
                break
            except OSError:
                time.sleep(0.5)
        if httpd is None:
            if probe_instance(self.port):
                return self._already_running(self.port)
            for p in range(self.port + 1, self.port + 30):
                try:
                    httpd = Server(("127.0.0.1", p), handler)
                    self.port = p
                    break
                except OSError:
                    continue
        if httpd is None:
            log.error("No se pudo abrir ningun puerto local (probados del %s al %s).",
                      self.port, self.port + 29)
            _fatal(f"No se pudo abrir el panel: los puertos {self.port}-{self.port + 29} estan ocupados.\n\n"
                   "Cierra otros programas que usen esos puertos o abre LiveVault con --port 9000.")
            sys.exit(1)
        self.httpd = httpd
        self.url = f"http://127.0.0.1:{self.port}"
        self.panel_url = f"{self.url}/?t={PANEL_TOKEN}"     # el panel solo abre con la clave
        try:
            INSTANCE_FILE.write_text(json.dumps({"port": self.port, "pid": os.getpid()}), encoding="utf-8")
        except OSError:
            pass

    def _already_running(self, port: int) -> None:
        url = f"http://127.0.0.1:{port}"
        log.info("El programa ya esta en ejecucion (%s).", url)
        if not self.minimized and not self.restarted:
            if not focus_panel_window():
                open_app_window(f"{url}/?t={PANEL_TOKEN}")
        sys.exit(0)

    def _cleanup_instance(self) -> None:
        try:
            if json.loads(INSTANCE_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
                INSTANCE_FILE.unlink()
        except Exception:
            pass

    # -- cierre
    def shutdown(self) -> None:
        if self._down:
            return
        self._down = True
        if self.bot is not None:
            self.bot.stop()
        log.info("Cerrando: finalizando grabaciones en curso...")
        try:
            self.call(self.manager.shutdown(), timeout=120)
        except Exception:
            log.error("Error al cerrar\n%s", traceback.format_exc())
        try:
            if self.httpd:
                self.httpd.shutdown()
                self.httpd.server_close()
        except Exception:
            pass

    def request_quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        threading.Thread(target=self._quit_worker, daemon=True, name="quit").start()

    def _quit_worker(self) -> None:
        try:
            if any(u["status"] == "recording" for u in self.manager_snapshot()):
                self.notify("Cerrando", "Guardando las grabaciones en curso antes de salir…")
            self.shutdown()
        finally:
            if self.tray:
                self.tray.stop()
            self.quit_event.set()

    def restart(self) -> None:
        def go():
            time.sleep(0.5)
            self.quitting = True
            self.shutdown()
            self._cleanup_instance()
            args = [sys.executable] + ([] if FROZEN else [str(Path(__file__).resolve())])
            args += ["--port", str(self.port), "--no-browser", "--restarted"]
            env = dict(os.environ)
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"      # el .exe nuevo debe descomprimirse por su cuenta
            subprocess.Popen(args, cwd=str(APP_DIR), env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=(0x00000008 | 0x00000200) if WIN else 0)   # DETACHED | NEW_PROCESS_GROUP
            if self.tray:
                self.tray.stop()
            time.sleep(0.4)
            os._exit(0)
        threading.Thread(target=go, daemon=True).start()


class TelegramError(Exception):
    def __init__(self, msg: str, code: int = 0, retry_after: float = 0.0):
        super().__init__(msg)
        self.code = code
        self.retry_after = retry_after


_COMMAND_ALIASES = {"estado": "status", "anadir": "add", "agregar": "add",
                    "ayuda": "help", "inicio": "start", "comandos": "help",
                    "lista": "list", "usuario": "user", "eliminar": "del", "quitar": "del",
                    "pausar": "pause", "reanudar": "resume", "grabar": "record",
                    "detener": "stop", "ultimas": "last"}


def _parse_command(text: str) -> tuple[str, str]:
    """'/add@MiBot @user' -> ('add', '@user'). Texto normal -> ('', texto)."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return "", text
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return "", text
    command = parts[0].split("@", 1)[0].strip().lower()
    return _COMMAND_ALIASES.get(command, command), (parts[1].strip() if len(parts) > 1 else "")


def _chat_name(chat: dict) -> str:
    return str(chat.get("title") or chat.get("username") or " ".join(
        x for x in (chat.get("first_name"), chat.get("last_name")) if x) or "").strip()


def _fmt_size(n: Any) -> str:
    try:
        value = float(n or 0)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "?"


class TelegramBot:
    """Escucha ordenes por long polling (fase 1).

    Solo atiende al chat configurado en Ajustes; /start, /help e /id se responden a cualquiera
    para poder configurar el chat. El offset se guarda en la BD para no repetir ordenes viejas
    despues de un reinicio.
    """

    POLL_TIMEOUT = 20
    HELP_TEXT = ("Usuarios:\n"
                 "/add @usuario — añadir y comprobar\n"
                 "/del @usuario — quitar (con confirmación)\n"
                 "/list [página] — lista con estados\n"
                 "/user @usuario — detalle y botones\n"
                 "/pause @usuario · /resume @usuario — vigilancia\n"
                 "/auto on|off @usuario — grabación automática\n\n"
                 "Grabación:\n"
                 "/record @usuario — grabar ahora\n"
                 "/stop @usuario — detener la grabación\n"
                 "/last @usuario — últimas grabaciones\n\n"
                 "General:\n"
                 "/status — resumen\n"
                 "/id — id de este chat\n"
                 "/help — esta ayuda\n\n"
                 "También puedes enviarme @usuario o el enlace de su perfil.")

    def __init__(self, app: "App", transport=None):
        self.app = app
        self._transport = transport          # inyectable en las pruebas
        self.enabled = bool(app.settings.get("push_tg_token").strip())
        self.error = ""
        self.running = False
        self.last_chat: Optional[dict] = None
        self.last_chat_at = 0.0
        self._chat_seen = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._denied_at: dict[str, float] = {}
        self._pending: dict[str, dict] = {}
        self._offset = 0

    # -- ciclo de vida ---------------------------------------------------
    def start(self) -> None:
        if not self.enabled or (self._thread is not None and self._thread.is_alive()):
            return
        self.running = True                   # antes de lanzar el hilo: evita una carrera con «Detectar chat»
        try:
            self._thread = threading.Thread(target=self._run, daemon=True, name="telegram-bot")
            self._thread.start()
        except Exception:
            self.running = False
            raise

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {"enabled": self.enabled,
                "running": bool(self._thread is not None and self._thread.is_alive()),
                "error": self.error,
                "chat": self.app.settings.get("push_tg_chat").strip()}

    # -- API de Telegram -------------------------------------------------
    def _api(self, method: str, payload: dict, timeout: float = 30) -> dict:
        if self._transport is not None:
            return self._transport(method, payload)
        token = self.app.settings.get("push_tg_token").strip()
        if not token:
            raise TelegramError("Token del bot no configurado", 401)
        url = f"{TELEGRAM_API}/bot{token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "LiveVault"})
        opener = _http_opener(self.app.settings.get("proxy").strip())
        try:
            with opener.open(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            info: dict = {}
            try:
                info = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:
                pass
            desc = str(info.get("description") or f"HTTP {e.code}")
            retry = float((info.get("parameters") or {}).get("retry_after") or 0)
            raise TelegramError(desc[:200], e.code, retry)
        if not isinstance(data, dict) or not data.get("ok"):
            raise TelegramError(str((data or {}).get("description") or "respuesta no valida")[:200])
        return data

    def _send(self, chat_id: Any, text: str, reply_markup=None) -> None:
        text = (text or "").strip()
        if not text:
            return
        for i in range(0, len(text), 3900):
            payload = {"chat_id": str(chat_id), "text": text[i:i + 3900],
                       "disable_web_page_preview": True}
            if reply_markup and i == 0:
                payload["reply_markup"] = reply_markup
            try:
                self._api("sendMessage", payload, timeout=20)
            except TelegramError as e:
                if e.code == 429 and e.retry_after:
                    time.sleep(min(10.0, e.retry_after))
                    try:
                        self._api("sendMessage", payload, timeout=20)
                    except Exception:
                        pass
                else:
                    log.warning("Bot: no se pudo responder al chat %s (%s)", chat_id, e)
                return

    def _typing(self, chat_id: Any) -> None:
        try:
            self._api("sendChatAction", {"chat_id": str(chat_id), "action": "typing"}, timeout=10)
        except Exception:
            pass

    def _set_commands(self) -> None:
        commands = [
            {"command": "list", "description": "Lista de usuarios"},
            {"command": "user", "description": "Detalle de un usuario"},
            {"command": "add", "description": "Anadir un usuario"},
            {"command": "del", "description": "Quitar un usuario"},
            {"command": "record", "description": "Grabar ahora"},
            {"command": "stop", "description": "Detener la grabacion"},
            {"command": "pause", "description": "Pausar la vigilancia"},
            {"command": "resume", "description": "Reanudar la vigilancia"},
            {"command": "auto", "description": "Grabacion automatica on/off"},
            {"command": "last", "description": "Ultimas grabaciones"},
            {"command": "status", "description": "Estado del programa"},
            {"command": "id", "description": "Id de este chat"},
            {"command": "help", "description": "Comandos disponibles"},
            {"command": "start", "description": "Iniciar el bot"},
        ]
        try:
            self._api("setMyCommands", {"commands": commands}, timeout=20)
        except Exception:
            log.debug("Bot: no se pudo publicar la lista de comandos\n%s", traceback.format_exc())

    # -- offset ----------------------------------------------------------
    def _load_offset(self) -> int:
        try:
            rows = self.app.db.q("SELECT value FROM bot_state WHERE key=?", ("offset",))
            return int(rows[0]["value"]) if rows else 0
        except Exception:
            return 0

    def _save_offset(self, value: int) -> None:
        try:
            self.app.db.x("INSERT INTO bot_state(key,value) VALUES('offset',?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(value),))
        except Exception:
            log.debug("Bot: no se pudo guardar el offset\n%s", traceback.format_exc())

    def _bootstrap_offset(self) -> int:
        """Primera ejecucion: no ejecutar ordenes viejas de las ultimas 24 h."""
        try:
            data = self._api("getUpdates", {"offset": -1, "timeout": 0,
                                            "allowed_updates": ["message", "callback_query"]}, timeout=20)
            result = data.get("result") or []
            if result:
                return int(result[-1].get("update_id", 0)) + 1
        except Exception:
            log.debug("Bot: no se pudo inicializar el offset\n%s", traceback.format_exc())
        return 0

    # -- bucle -----------------------------------------------------------
    def _run(self) -> None:
        if not self.enabled:
            return
        self.running = True
        try:
            self._set_commands()
            self._offset = self._load_offset()
            if self._offset == 0:
                self._offset = self._bootstrap_offset()
            backoff = 1.0
            while not self._stop.is_set():
                try:
                    data = self._api("getUpdates", {"offset": self._offset,
                                                    "timeout": self.POLL_TIMEOUT,
                                                    "allowed_updates": ["message", "callback_query"]},
                                     timeout=self.POLL_TIMEOUT + 15)
                    self.error = ""
                    backoff = 1.0
                    for update in data.get("result") or []:
                        if self._stop.is_set():
                            break
                        try:
                            self.handle_update(update)
                        except Exception:
                            log.warning("Bot: error procesando una actualizacion\n%s",
                                        traceback.format_exc())
                        try:
                            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                            self._save_offset(self._offset)
                        except Exception:
                            pass
                    if not data.get("result"):
                        self._stop.wait(0.2)   # un proxy que responde al instante no debe girar en vacio
                except TelegramError as e:
                    self.error = redact(str(e))
                    if e.code == 401:
                        log.error("Bot: el token de Telegram no es valido; revisa Ajustes")
                        self._stop.wait(300)
                    elif e.code == 409:
                        log.warning("Bot: otra instancia esta usando getUpdates; reintento en 15 s")
                        self._stop.wait(15)
                    else:
                        self._stop.wait(min(60.0, backoff))
                        backoff = min(60.0, backoff * 2)
                except Exception as e:
                    self.error = redact(f"{type(e).__name__}: {e}")
                    log.warning("Bot: fallo de red (%s); reintento en %.0f s",
                                type(e).__name__, backoff)
                    self._stop.wait(backoff)
                    backoff = min(60.0, backoff * 2)
        except Exception:
            log.error("Bot: el bucle de escucha se detuvo\n%s", traceback.format_exc())
        finally:
            self.running = False

    # -- mensajes --------------------------------------------------------
    def _allowed(self, chat: dict) -> bool:
        configured = self.app.settings.get("push_tg_chat").strip().lower()
        if not configured:
            return False
        if configured.startswith("@"):
            return str(chat.get("username") or "").lower() == configured.lstrip("@")
        return str(chat.get("id") or "") == configured

    def handle_update(self, update: dict) -> None:
        if update.get("callback_query"):
            self._handle_callback(update["callback_query"])
            return
        msg = update.get("message") or {}
        if not msg:
            return
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        if sender.get("is_bot"):
            return
        chat_id = chat.get("id")
        if chat_id is None:
            return
        text = str(msg.get("text") or "").strip()
        command, args = _parse_command(text)
        # «Detectar chat» solo tiene en cuenta /start (o el chat ya autorizado): asi no se
        # autoriza por error a un desconocido que escriba al bot justo antes de pulsar el boton.
        if command == "start" or self._allowed(chat):
            self.last_chat = {"id": str(chat_id), "name": _chat_name(chat)}
            self.last_chat_at = time.time()
            self._chat_seen.set()
        if not text:
            return
        allowed = self._allowed(chat)
        if command == "start":
            self._reply_start(chat_id, allowed)
            return
        if command == "id":
            self._reply_id(chat_id, chat, allowed)
            return
        if command == "help":
            self._reply_help(chat_id, allowed)
            return
        if not allowed:
            self._deny(chat_id)
            return
        handlers = {
            "status": lambda: self._reply_status(chat_id),
            "add": lambda: self._cmd_add(chat_id, args),
            "list": lambda: self._cmd_list(chat_id, args),
            "user": lambda: self._cmd_user(chat_id, args),
            "del": lambda: self._cmd_del(chat_id, args),
            "pause": lambda: self._cmd_pause(chat_id, args),
            "resume": lambda: self._cmd_resume(chat_id, args),
            "auto": lambda: self._cmd_auto(chat_id, args),
            "record": lambda: self._cmd_record(chat_id, args),
            "stop": lambda: self._cmd_stop(chat_id, args),
            "last": lambda: self._cmd_last(chat_id, args),
        }
        handler = handlers.get(command)
        if handler is not None:
            handler()
        elif command:
            self._send(chat_id, "No conozco ese comando. Escribe /help.")
        elif "@" in text or "tiktok.com/" in text.lower():
            self._cmd_add(chat_id, text)
        elif parse_username(text):
            hint = parse_username(text)
            self._send(chat_id, f"Para vigilar a @{hint}, escríbeme:\n/add {hint}")
        else:
            self._send(chat_id, "No te he entendido. Escribe /help para ver lo que puedo hacer.")

    def _deny(self, chat_id: Any) -> None:
        key = str(chat_id)
        now = time.time()
        if now - self._denied_at.get(key, 0.0) < 60:
            return
        self._denied_at[key] = now
        self._send(chat_id, "⛔ Este chat no está autorizado para dar órdenes.\n\n"
                            "Si eres el dueño, configura este chat en Ajustes > Telegram "
                            "(o pulsa «Detectar chat») y vuelve a intentarlo. /id te dice el número.")

    def _reply_start(self, chat_id: Any, allowed: bool) -> None:
        if not allowed:
            self._send(chat_id, "👋 Soy el bot de LiveVault.\n\n"
                                "Este chat no está autorizado todavía. Escribe /id, copia el número "
                                "y pégalo en Ajustes > Telegram > chat (o pulsa «Detectar chat»).")
            return
        self._send(chat_id, "👋 Bot de LiveVault.\n\n" + self.HELP_TEXT)

    def _reply_help(self, chat_id: Any, allowed: bool) -> None:
        text = self.HELP_TEXT
        if not allowed:
            text += "\n\n⛔ Este chat no está autorizado para ejecutar los comandos."
        self._send(chat_id, text)

    def _reply_id(self, chat_id: Any, chat: dict, allowed: bool) -> None:
        lines = [f"🆔 Chat: {chat_id}"]
        if chat.get("type"):
            lines.append(f"Tipo: {chat['type']}")
        name = _chat_name(chat)
        if name:
            lines.append(f"Nombre: {name}")
        lines.append("✅ Autorizado para dar órdenes." if allowed
                     else "⛔ No autorizado: pégalo en Ajustes > Telegram > chat.")
        self._send(chat_id, "\n".join(lines))

    def _reply_status(self, chat_id: Any) -> None:
        self._send(chat_id, self._status_text())

    def _status_text(self) -> str:
        mgr = self.app.manager
        snap = self.app.manager_snapshot()
        lines: list[str] = []
        if not snap:
            lines.append("Todavía no hay usuarios en la lista.")
        else:
            live = [u for u in snap if u.get("live") or u.get("status") == "recording"]
            rec = [u for u in snap if u.get("status") == "recording"]
            paused = [u for u in snap if not u.get("enabled")]
            lines.append(f"👥 {len(snap)} usuarios · 🔴 {len(live)} en vivo · ⏺ {len(rec)} grabando"
                         + (f" · ⏸ {len(paused)} pausados" if paused else ""))
            for u in rec[:4]:
                lines.append(f"⏺ {self._who(u)} · {fmt_secs(u.get('rec_seconds'))} · "
                             f"{_fmt_size(u.get('rec_size'))}")
            others = [u for u in live if u.get("status") != "recording"]
            for u in others[:4]:
                viewers = f" · 👁 {u['viewers']:,}".replace(",", ".") if u.get("viewers") else ""
                lines.append(f"🔴 {self._who(u)}{viewers}")
            if len(others) > 4:
                lines.append(f"… y {len(others) - 4} más en vivo")
        versions = engine_versions()
        ff = ffmpeg_version(find_ffmpeg(self.app.settings))
        lines.append(f"🎥 TikTokLive {versions.get('TikTokLive') or '?'} · ffmpeg {ff}")
        if mgr is not None:
            state = {"none": "no configurada", "ok": "correcta", "expired": "VENCIDA",
                     "unknown": "sin comprobar"}.get(mgr.session_state, mgr.session_state)
            disk = f" · 💾 {mgr.free_gb:.0f} GB libres" if mgr.free_gb is not None else ""
            lines.append(f"🔑 Sesión: {state}{disk}")
            if mgr.low_space:
                lines.append("💾 Grabaciones automáticas en pausa por poco espacio.")
            blocked = int(mgr.global_block_until - time.time())
            if blocked > 0:
                lines.append(f"⛔ TikTok está limitando las consultas ({blocked} s).")
        if self.error:
            lines.append(f"🤖 Bot: {self.error}")
        return "\n".join(lines)

    @staticmethod
    def _who(u: dict) -> str:
        if u.get("nickname"):
            return f"{u['nickname']} (@{u['username']})"
        return f"@{u['username']}"

    def _cmd_add(self, chat_id: Any, raw: str) -> None:
        raw = (raw or "").strip()
        if not raw:
            self._send(chat_id, "Envíame el @usuario o el enlace de su perfil. Por ejemplo:\n"
                                "/add @manolito64")
            return
        try:
            name = self.app.add_user(raw)
        except AddUserError as e:
            if e.code == 409:
                self._send(chat_id, f"ℹ️ @{parse_username(raw) or raw} ya está en la lista.")
            else:
                self._send(chat_id, f"❌ {e}")
            return
        except Exception:
            log.warning("Bot: no se pudo añadir %r\n%s", raw, traceback.format_exc())
            self._send(chat_id, "❌ No se pudo añadir; revisa el panel.")
            return
        self._typing(chat_id)
        user = None
        try:
            if self.app.manager is not None:
                user = self.app.call(self.app.manager.check_now(name), 60)
        except Exception:
            log.info("Bot: la comprobación inmediata de @%s no terminó", name)
        lines = [f"✅ Añadido @{name}" + (f" ({user.nickname})" if user is not None and user.nickname else "")]
        if user is None:
            lines.append("Se comprobará en la próxima revisión.")
        elif user.error_kind == "not_found":
            lines.append("⚠️ TikTok dice que no existe o nunca ha emitido. Si te has equivocado, "
                         "quítalo desde el panel.")
        elif user.error_kind == "age":
            lines.append("⚠️ Es un directo +18 y falta la sessionid en Ajustes.")
        elif user.error_kind in ("network", "blocked", "unknown", "flaky", "missing"):
            lines.append(f"⚠️ Añadido, pero no se pudo comprobar ahora ({user.error_kind}).")
        elif user.recording:
            lines.append("🔴 En vivo y grabando.")
        elif user.live:
            viewers = f" · 👁 {user.viewers:,}".replace(",", ".") if user.viewers else ""
            title = f" · «{user.title[:80]}»" if user.title else ""
            lines.append(f"🔴 En vivo ahora{viewers}{title}.")
        else:
            lines.append("⚪ Sin directo ahora mismo.")
        self._send(chat_id, "\n".join(lines))

    # -- utilidades de usuarios ------------------------------------------
    @staticmethod
    def _require_name(raw: str) -> str:
        name = parse_username(raw or "")
        if not name:
            raise UserError("Indica un @usuario válido.")
        return name

    def _find_user(self, name: str) -> Optional[dict]:
        for u in self.app.manager_snapshot():
            if u.get("username") == name:
                return u
        return None

    @staticmethod
    def _ago(seconds: Any) -> str:
        if seconds is None:
            return "—"
        s = int(seconds)
        return "ahora" if s < 5 else (f"hace {s} s" if s < 90 else f"hace {s // 60} min")

    @staticmethod
    def _short_date(iso: Any) -> str:
        try:
            return datetime.fromisoformat(str(iso)).strftime("%d/%m %H:%M")
        except (TypeError, ValueError):
            return str(iso or "?")

    def _user_line(self, u: dict) -> str:
        name = u.get("nickname") or ("@" + u["username"])
        handle = f" (@{u['username']})" if u.get("nickname") else ""
        if not u.get("enabled"):
            return f"⏸ {name}{handle} · pausado"
        if u.get("status") == "recording":
            return (f"⏺ {name}{handle} · {fmt_secs(u.get('rec_seconds'))} · "
                    f"{_fmt_size(u.get('rec_size'))}")
        if u.get("live"):
            viewers = f" · 👁 {u['viewers']:,}".replace(",", ".") if u.get("viewers") else ""
            return f"🔴 {name}{handle}{viewers}"
        if u.get("status") == "error":
            return f"⚠️ {name}{handle} · {str(u.get('error') or 'problema')[:60]}"
        return f"⚪ {name}{handle} · {self._ago(u.get('last_check_ago'))}"

    # -- /list -----------------------------------------------------------
    def _cmd_list(self, chat_id: Any, args: str) -> None:
        try:
            page = max(1, int((args or "").strip() or "1"))
        except ValueError:
            page = 1
        text, markup = self._list_page(page)
        self._send(chat_id, text, markup)

    def _list_page(self, page: int) -> tuple[str, Optional[dict]]:
        snap = sorted(self.app.manager_snapshot(), key=lambda u: u.get("username", ""))
        if not snap:
            return "Todavía no hay usuarios. Añade uno con /add @usuario.", None
        size = 8
        pages = max(1, (len(snap) + size - 1) // size)
        page = max(1, min(pages, page))
        chunk = snap[(page - 1) * size: page * size]
        live = sum(1 for u in snap if u.get("live") or u.get("status") == "recording")
        rec = sum(1 for u in snap if u.get("status") == "recording")
        lines = [f"👥 {len(snap)} usuarios · 🔴 {live} en vivo · ⏺ {rec} grabando"]
        for i, u in enumerate(chunk, start=(page - 1) * size + 1):
            lines.append(f"{i}. {self._user_line(u)}")
        if pages > 1:
            lines.append(f"\nPágina {page}/{pages}")
        nav = []
        if page > 1:
            nav.append({"text": "◀", "callback_data": f"list|{page - 1}"})
        if page < pages:
            nav.append({"text": "▶", "callback_data": f"list|{page + 1}"})
        markup = {"inline_keyboard": [nav]} if nav else None
        return "\n".join(lines), markup

    # -- /user -----------------------------------------------------------
    def _cmd_user(self, chat_id: Any, args: str) -> None:
        try:
            name = self._require_name(args)
        except UserError as e:
            self._send(chat_id, f"❌ {e}\nUso: /user @usuario")
            return
        u = self._find_user(name)
        if u is None:
            self._send(chat_id, f"@{name} no está en la lista. Añádelo con /add @{name}.")
            return
        self._send(chat_id, self._user_text(u), self._user_keyboard(u))

    def _user_text(self, u: dict) -> str:
        name = u.get("nickname") or ("@" + u["username"])
        lines = [f"👤 {name}" + (f" · @{u['username']}" if u.get("nickname") else "")]
        if not u.get("enabled"):
            lines.append("⏸ Vigilancia pausada.")
        if u.get("status") == "recording":
            extra = f" · {u['rec_quality']}" if u.get("rec_quality") else ""
            lines.append(f"⏺ Grabando: {fmt_secs(u.get('rec_seconds'))} · "
                         f"{_fmt_size(u.get('rec_size'))}{extra}")
        elif u.get("live"):
            viewers = f" · 👁 {u['viewers']:,}".replace(",", ".") if u.get("viewers") else ""
            title = f" · «{str(u.get('title'))[:80]}»" if u.get("title") else ""
            lines.append(f"🔴 En vivo{viewers}{title}")
        elif u.get("status") == "error":
            lines.append(f"⚠️ {u.get('error') or 'Con problema'}")
        else:
            lines.append("⚪ Sin directo ahora mismo.")
        if u.get("error") and u.get("status") != "error":
            lines.append(f"⚠️ {u['error']}")
        if u.get("last_check_ago") is not None:
            next_in = int(u.get("next_check_in") or 0)
            lines.append(f"🕒 Última revisión: {self._ago(u['last_check_ago'])}"
                         + (f" · próxima en {next_in} s" if next_in > 1 else ""))
        lines.append(f"📼 {u.get('total_recordings', 0)} grabaciones · {_fmt_size(u.get('total_size'))}")
        lines.append(f"⚙️ Automática: {'sí' if u.get('auto_record') else 'no'} · "
                     f"Vigilancia: {'activa' if u.get('enabled') else 'pausada'}")
        return "\n".join(lines)

    def _user_keyboard(self, u: dict) -> dict:
        name = u["username"]
        first = [{"text": "⏹ Detener", "callback_data": f"u|stop|{name}"}] if u.get("status") == "recording" \
            else [{"text": "⏺ Grabar", "callback_data": f"u|record|{name}"}]
        first.append({"text": "📼 Últimas", "callback_data": f"u|last|{name}"})
        second = [{"text": "⏸ Pausar" if u.get("enabled") else "▶ Reanudar",
                   "callback_data": f"u|{'pause' if u.get('enabled') else 'resume'}|{name}"},
                  {"text": "🗑 Quitar", "callback_data": f"u|del|{name}"}]
        return {"inline_keyboard": [first, second]}

    # -- /pause, /resume, /auto ------------------------------------------
    def _cmd_pause(self, chat_id: Any, args: str) -> None:
        self._flag(chat_id, args, "enabled", False, "pause", "pausado")

    def _cmd_resume(self, chat_id: Any, args: str) -> None:
        self._flag(chat_id, args, "enabled", True, "resume", "reanudado")

    def _flag(self, chat_id: Any, args: str, field: str, value: bool,
              verb: str, done: str) -> None:
        try:
            name = self._require_name(args)
        except UserError as e:
            self._send(chat_id, f"❌ {e}\nUso: /{verb} @usuario")
            return
        u = self._find_user(name)
        if field == "enabled" and u is not None and bool(u.get("enabled")) == value:
            self._send(chat_id, f"@{name} ya estaba {'activo' if value else 'pausado'}.")
            return
        try:
            stopped = self.app.set_user_flag(name, field, value)
        except UserError as e:
            self._send(chat_id, f"❌ {e}")
            return
        except Exception:
            log.warning("Bot: /%s @%s fallo\n%s", verb, name, traceback.format_exc())
            self._send(chat_id, "❌ No se pudo cambiar; revisa el panel.")
            return
        extra = " y se detuvo la grabación en curso" if stopped else ""
        self._send(chat_id, f"{'▶️' if value else '⏸'} @{name} {done}{extra}.")

    def _cmd_auto(self, chat_id: Any, args: str) -> None:
        parts = (args or "").split()
        usage = "Uso: /auto on @usuario  o  /auto off @usuario"
        if len(parts) < 2 or parts[0].lower() not in ("on", "off", "si", "sí", "no"):
            self._send(chat_id, usage)
            return
        value = parts[0].lower() in ("on", "si", "sí")
        try:
            name = self._require_name(" ".join(parts[1:]))
        except UserError:
            self._send(chat_id, usage)
            return
        u = self._find_user(name)
        if u is not None and bool(u.get("auto_record")) == value:
            self._send(chat_id, f"@{name} ya tenía la grabación automática "
                                f"{'activada' if value else 'desactivada'}.")
            return
        try:
            self.app.set_user_flag(name, "auto_record", value)
        except UserError as e:
            self._send(chat_id, f"❌ {e}")
            return
        self._send(chat_id, f"⚙️ Grabación automática {'activada' if value else 'desactivada'} "
                            f"para @{name}.")

    # -- /record, /stop --------------------------------------------------
    def _cmd_record(self, chat_id: Any, args: str) -> None:
        try:
            name = self._require_name(args)
        except UserError as e:
            self._send(chat_id, f"❌ {e}\nUso: /record @usuario")
            return
        self._typing(chat_id)
        try:
            res = self.app.record_user(name)
        except UserError as e:
            self._send(chat_id, f"❌ {e}")
            return
        except Exception:
            log.warning("Bot: /record @%s fallo\n%s", name, traceback.format_exc())
            self._send(chat_id, "❌ No se pudo comprobar; mira /status o el panel.")
            return
        self._send(chat_id, ("⏺ " if res.get("ok") else "⚠️ ") + str(res.get("msg") or "Hecho"))

    def _cmd_stop(self, chat_id: Any, args: str) -> None:
        try:
            name = self._require_name(args)
        except UserError as e:
            self._send(chat_id, f"❌ {e}\nUso: /stop @usuario")
            return
        try:
            was = self.app.stop_user_recording(name)
        except UserError as e:
            self._send(chat_id, f"❌ {e}")
            return
        except Exception:
            log.warning("Bot: /stop @%s fallo\n%s", name, traceback.format_exc())
            self._send(chat_id, "❌ No se pudo detener; mira el panel.")
            return
        if was:
            self._send(chat_id, f"⏹ Grabación de @{name} detenida. El directo sigue; "
                                "no se reanudará hasta que termine.")
        else:
            self._send(chat_id, f"@{name} no estaba grabando.")

    # -- /last -----------------------------------------------------------
    def _cmd_last(self, chat_id: Any, args: str) -> None:
        try:
            name = self._require_name(args)
        except UserError as e:
            self._send(chat_id, f"❌ {e}\nUso: /last @usuario")
            return
        rows = self.app.db.q(
            "SELECT r.id, r.started_at, r.duration_sec, r.size_bytes, r.quality "
            "FROM recordings r JOIN users u ON u.id=r.user_id "
            "WHERE u.username=? AND r.status='done' ORDER BY r.id DESC LIMIT 3", (name,))
        if not rows:
            self._send(chat_id, f"No hay grabaciones guardadas de @{name}.")
            return
        lines = [f"📼 Últimas de @{name}:"]
        for r in rows:
            quality = f" · {r['quality']}" if r.get("quality") else ""
            lines.append(f"· {self._short_date(r.get('started_at'))} · "
                         f"{fmt_secs(r.get('duration_sec'))} · {_fmt_size(r.get('size_bytes'))}{quality}")
        self._send(chat_id, "\n".join(lines))
        thumb = THUMB_DIR / f"{rows[0]['id']}.jpg"
        if thumb.exists():
            self._send_photo(chat_id, thumb, f"Última grabación · "
                                             f"{self._short_date(rows[0].get('started_at'))}")

    def _send_photo(self, chat_id: Any, path: Path, caption: str = "") -> bool:
        if self._transport is not None:
            self._transport("sendPhoto", {"chat_id": str(chat_id), "caption": caption,
                                          "_file": str(path)})
            return True
        token = self.app.settings.get("push_tg_token").strip()
        if not token:
            return False
        try:
            data = path.read_bytes()
        except OSError:
            return False
        boundary = "----tkrec" + uuid.uuid4().hex
        parts = []
        for key, value in (("chat_id", str(chat_id)), ("caption", caption[:1000])):
            if value:
                parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                             f"{value}\r\n".encode("utf-8"))
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                     f"filename=\"miniatura.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode()
                     + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(
            f"{TELEGRAM_API}/bot{token}/sendPhoto", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with _http_opener(self.app.settings.get("proxy").strip()).open(req, timeout=30) as r:
                return bool(json.loads(r.read().decode("utf-8", "replace")).get("ok"))
        except Exception as e:
            log.warning("Bot: no se pudo enviar la miniatura (%s)", type(e).__name__)
            return False

    # -- /del y confirmaciones -------------------------------------------
    def _cmd_del(self, chat_id: Any, args: str) -> None:
        try:
            name = self._require_name(args)
        except UserError as e:
            self._send(chat_id, f"❌ {e}\nUso: /del @usuario")
            return
        if not self.app.db.q("SELECT 1 FROM users WHERE username=?", (name,)):
            self._send(chat_id, f"@{name} no está en la lista.")
            return
        self._prune_pending()
        nonce = uuid.uuid4().hex[:8]
        self._pending[nonce] = {"action": "del", "name": name, "chat": str(chat_id),
                                "expires": time.time() + 120}
        keyboard = {"inline_keyboard": [
            [{"text": "Quitar de la lista", "callback_data": f"del|remove|{name}|{nonce}"}],
            [{"text": "Quitar y borrar archivos", "callback_data": f"del|files|{name}|{nonce}"}],
            [{"text": "Cancelar", "callback_data": f"del|cancel|{name}|{nonce}"}],
        ]}
        self._send(chat_id, f"¿Quitar a @{name} de la lista?\n\n"
                            "· «Quitar de la lista» deja sus grabaciones en el disco.\n"
                            "· «Quitar y borrar archivos» elimina también sus videos "
                            "(no se puede deshacer).", keyboard)

    def _prune_pending(self) -> None:
        now = time.time()
        for key in [k for k, v in self._pending.items() if v.get("expires", 0) < now]:
            self._pending.pop(key, None)

    def _handle_callback(self, cq: dict) -> None:
        if (cq.get("from") or {}).get("is_bot"):
            return
        cq_id = cq.get("id")
        msg = cq.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        if not self._allowed(chat):
            self._answer_cb(cq_id, "No autorizado")
            return
        parts = str(cq.get("data") or "").split("|")
        kind = parts[0] if parts else ""
        if kind == "list" and len(parts) >= 2:
            try:
                page = max(1, int(parts[1]))
            except ValueError:
                page = 1
            text, markup = self._list_page(page)
            self._answer_cb(cq_id)
            self._edit(chat_id, msg.get("message_id"), text, markup)
            return
        if kind == "del" and len(parts) >= 4:
            self._answer_cb(cq_id, "Procesando…")
            self._do_delete(chat_id, msg.get("message_id"), parts[1], parts[2], parts[3])
            return
        if kind == "u" and len(parts) >= 3:
            self._answer_cb(cq_id)
            self._user_action(chat_id, parts[1], parts[2])
            return
        self._answer_cb(cq_id, "Acción no reconocida")

    def _do_delete(self, chat_id: Any, message_id: Any, mode: str, name: str,
                   nonce: str) -> None:
        self._prune_pending()
        entry = self._pending.pop(nonce, None)
        if (entry is None or entry.get("chat") != str(chat_id) or entry.get("name") != name
                or parse_username(name) != name):
            self._edit(chat_id, message_id, "La confirmación caducó o no es válida. "
                                            "Vuelve a usar /del.", {"inline_keyboard": []})
            return
        if mode == "cancel":
            self._edit(chat_id, message_id, "Cancelado. No se ha quitado a nadie.",
                       {"inline_keyboard": []})
            return
        delete = mode == "files"
        try:
            self.app.remove_user(name, delete_files=delete)
        except UserError as e:
            self._edit(chat_id, message_id, f"❌ {e}", {"inline_keyboard": []})
            return
        except Exception:
            log.warning("Bot: fallo al quitar @%s\n%s", name, traceback.format_exc())
            self._edit(chat_id, message_id, "❌ No se pudo quitar; revisa el panel.",
                       {"inline_keyboard": []})
            return
        if delete:
            text = f"✅ @{name} y sus archivos eliminados."
        else:
            text = f"✅ @{name} quitado de la lista. Sus grabaciones siguen en la carpeta."
        self._edit(chat_id, message_id, text, {"inline_keyboard": []})

    def _user_action(self, chat_id: Any, action: str, name: str) -> None:
        if parse_username(name) != name:
            self._send(chat_id, "Acción no válida.")
            return
        if action == "del":
            self._cmd_del(chat_id, name)
        elif action == "last":
            self._cmd_last(chat_id, name)
        elif action == "record":
            self._cmd_record(chat_id, name)
        elif action == "stop":
            self._cmd_stop(chat_id, name)
        elif action == "pause":
            self._cmd_pause(chat_id, name)
        elif action == "resume":
            self._cmd_resume(chat_id, name)
        else:
            self._send(chat_id, "Acción no reconocida.")

    def _answer_cb(self, cq_id: Any, text: str = "") -> None:
        if not cq_id:
            return
        payload: dict = {"callback_query_id": cq_id}
        if text:
            payload["text"] = text[:200]
        try:
            self._api("answerCallbackQuery", payload, timeout=10)
        except Exception:
            pass

    def _edit(self, chat_id: Any, message_id: Any, text: str,
              reply_markup: Optional[dict] = None) -> None:
        if message_id is None:
            self._send(chat_id, text, reply_markup)
            return
        payload: dict = {"chat_id": str(chat_id), "message_id": message_id, "text": text[:3900]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            self._api("editMessageText", payload, timeout=20)
        except TelegramError as e:
            if "not modified" in str(e).lower():
                return
            self._send(chat_id, text, reply_markup)
        except Exception:
            self._send(chat_id, text, reply_markup)

    def detect_chat(self, timeout: float = 35) -> tuple[str, str]:
        """Para el boton «Detectar chat»: usa el mensaje que ya ha visto el bucle."""
        if not self.enabled:
            raise RuntimeError("El bot no está activo: configura el token en Ajustes.")
        if self.last_chat and time.time() - self.last_chat_at < 300:
            return self.last_chat["id"], self.last_chat["name"]
        self._chat_seen.clear()
        if self._chat_seen.wait(timeout):
            return self.last_chat["id"], self.last_chat["name"]
        raise RuntimeError("No hay mensajes nuevos. Abre tu bot en Telegram, escríbele /start "
                           "y vuelve a pulsar el botón.")


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "LiveVault/2.0"
        protocol_version = "HTTP/1.1"
        MAX_BODY = 2_000_000

        def log_message(self, fmt, *args):  # silenciar log por peticion
            pass

        # -- helpers
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _common_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            # el panel es autocontenido: nada de recursos ni conexiones externas
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; media-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                             "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")

        def _json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._common_headers()
            self.end_headers()
            self.wfile.write(body)

        def _text(self, text, ctype="text/plain; charset=utf-8", code=200, extra=None):
            body = text.encode("utf-8") if isinstance(text, str) else text
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._common_headers()
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if not n or n > self.MAX_BODY:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        def _guard(self, method: str, path: str, qs: dict) -> bool:
            if not self._host_ok():
                self._json({"error": "host no permitido"}, 403)
                return False
            # defensa extra contra paginas de otros sitios y DNS rebinding (el Host ya lo impide,
            # esto cubre navegadores/proxies que reescriben cabeceras)
            origin = (self.headers.get("Origin") or "").rstrip("/")
            if origin and origin not in (f"http://127.0.0.1:{app.port}", f"http://localhost:{app.port}"):
                self._json({"error": "origen no permitido"}, 403)
                return False
            site = self.headers.get("Sec-Fetch-Site")
            if site and site not in ("same-origin", "none"):
                self._json({"error": "origen no permitido"}, 403)
                return False
            if path == "/api/ping":
                return True                    # solo lo usa el propio programa para detectar la instancia
            # Clave aleatoria por instalacion: 127.0.0.1 lo alcanza cualquier proceso del PC (y otros
            # usuarios de Windows), asi que sin esto podrian leer tus grabaciones o cambiar ajustes.
            got = self.headers.get("X-TK-Token") or (qs.get("t") or [""])[0]
            if not got or not hmac.compare_digest(got, PANEL_TOKEN):
                if path in ("/", "/index.html"):
                    return self._panel_hint()
                self._json({"error": "clave del panel no valida; recarga la pagina"}, 403)
                return False
            return True

        def _panel_hint(self) -> bool:
            """GET a la raiz sin la clave: se explica como abrir el panel (no se revela nada)."""
            self._text(
                "<!doctype html><meta charset='utf-8'><title>LiveVault</title>"
                "<body style='font:16px system-ui;background:#0f1115;color:#e8eaf0;padding:40px'>"
                "<h1>LiveVault está abierto</h1><p>Por seguridad, el panel solo se abre con su clave.</p>"
                "<p>Abre el panel desde el <b>icono de LiveVault junto al reloj</b> "
                "(doble clic) o desde el menú <b>Salir</b> no lo cierres.</p></body>",
                "text/html; charset=utf-8", code=403)
            return False

        # -- metodos
        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _dispatch(self, method: str):
            try:
                url = urlparse(self.path)
                path, qs = unquote(url.path), parse_qs(url.query)
                if not self._guard(method, path, qs):
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if self.headers.get("Transfer-Encoding"):      # no leer cuerpos por trozos
                    self.close_connection = True
                    return self._json({"error": "cuerpo no permitido"}, 411)
                if length > self.MAX_BODY:
                    self.close_connection = True
                    return self._json({"error": "cuerpo demasiado grande"}, 413)
                if method == "GET" and path in ("/", "/index.html"):
                    html = (RES_DIR / "ui" / "index.html").read_text(encoding="utf-8")
                    html = html.replace("__TK_TOKEN__", PANEL_TOKEN)
                    return self._text(html, "text/html; charset=utf-8", extra={"Cache-Control": "no-cache"})
                self.route(method, path, qs)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.error("Error en la API %s %s\n%s", method, self.path, traceback.format_exc())
                try:
                    self._json({"error": "Error interno (ver log)"}, 500)
                except Exception:
                    pass

        def route(self, method: str, path: str, qs: dict):
            s, mgr = app.settings, app.manager
            if method == "GET" and path == "/api/ping":
                return self._json({"app": "tkrec", "version": APP_VERSION, "pid": os.getpid()})
            if method == "POST" and path == "/api/quit":
                app.request_quit()
                return self._json({"ok": True})
            if method == "POST" and path == "/api/session/check":
                try:
                    info = app.call(mgr.check_session_now(), 50)
                except Exception:
                    log.warning("Comprobacion manual de sessionid fallo\n%s", traceback.format_exc())
                    return self._json({"error": "No se pudo comprobar; intenta de nuevo."}, 500)
                return self._json(info)
            if method == "GET" and path == "/api/state":
                ok, why = Engine.available()
                ex = find_ffmpeg(s)
                return self._json({
                    "users": mgr.snapshot(),
                    "engine": {"ok": ok, "error": why, "versions": engine_versions(),
                               "ffmpeg": ffmpeg_version(ex), "updating": app.updater.running,
                               "restart_needed": app.updater.restart_needed, "origin": engine_origin()},
                    "session": {"state": mgr.session_state,
                                "checked_ago": int(time.time() - mgr.session_checked) if mgr.session_checked else None,
                                "account": mgr.session_account, "nickname": mgr.session_nickname},
                    "push": app.pusher.configured(),
                    "blocked_for": max(0, int(mgr.global_block_until - time.time())),
                    "disk": {"free_gb": round(mgr.free_gb, 1) if mgr.free_gb is not None else None,
                             "total_gb": round(mgr.total_gb, 1) if mgr.total_gb is not None else None,
                             "low": mgr.low_space},
                    "jobs": app.active_jobs(),
                    "bot": app.bot_status(),
                    "tray": app.tray is not None,
                    "app_version": APP_VERSION,
                })
            if method == "POST" and path == "/api/users":
                try:
                    name = app.add_user(self._body().get("username", ""))
                except AddUserError as e:
                    return self._json({"error": str(e)}, e.code)
                return self._json({"ok": True, "username": name})
            m = re.fullmatch(r"/api/users/([A-Za-z0-9._]+)", path)
            if m and method == "DELETE":
                try:
                    app.remove_user(m[1], delete_files=qs.get("delete_files", ["0"])[0] == "1")
                except UserError as e:
                    return self._json({"error": str(e)}, e.code)
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/users/([A-Za-z0-9._]+)/(enabled|auto_record|record|stop|recordings|open)", path)
            if m:
                name, action = m[1], m[2]
                row = app.db.q("SELECT * FROM users WHERE username=?", (name,))
                if not row:
                    return self._json({"error": "no existe"}, 404)
                if action == "recordings" and method == "GET":
                    recs = app.db.q("SELECT id, path, started_at, ended_at, duration_sec, size_bytes, status, "
                                    "quality, note, session_id, favorite, codec, width, height, fps, frames, "
                                    "frozen_secs, ts_bad, analyzed_at "
                                    "FROM recordings WHERE user_id=? ORDER BY id DESC",
                                    (row[0]["id"],))
                    u = mgr.users.get(name)
                    for r in recs:
                        r["file"] = Path(r["path"]).name
                        r["exists"] = Path(r["path"]).exists()
                        r["thumb"] = (THUMB_DIR / f"{r['id']}.jpg").exists()
                        if r["status"] == "recording":
                            r["size_bytes"] = u.rec_size if u and u.recording else r["size_bytes"]
                            r["duration_sec"] = (time.time() - u.rec_started) if u and u.recording else 0
                        r.pop("path")
                    # un directo puede ser varios archivos (cortes/reconexiones): agrupar por sesion
                    groups: dict[str, dict] = {}
                    for r in recs:
                        key = r["session_id"] or f"s{r['id']}"
                        g = groups.get(key)
                        if g is None:
                            g = {"id": r["session_id"] or "", "files": [], "count": 0,
                                 "started_at": r["started_at"], "duration_sec": 0.0, "size_bytes": 0,
                                 "status": r["status"], "quality": r["quality"] or ""}
                            groups[key] = g
                        g["files"].append(r)
                        g["count"] += 1
                        g["duration_sec"] += float(r["duration_sec"] or 0)
                        g["size_bytes"] += int(r["size_bytes"] or 0)
                        if r["status"] in ("recording", "processing"):
                            g["status"] = r["status"]
                        if r["started_at"] < g["started_at"]:
                            g["started_at"] = r["started_at"]
                        g["_last"] = max(g.get("_last", 0), int(r["id"]))
                    sessions = sorted(groups.values(), key=lambda g: g["_last"], reverse=True)
                    for g in sessions:
                        g.pop("_last", None)
                        g["can_merge"] = (g["count"] > 1 and bool(g["id"]) and g["status"] == "done"
                                          and all(f["status"] == "done" and f["exists"] for f in g["files"]))
                    return self._json({"recordings": recs, "sessions": sessions})
                if action == "recordings" and method == "DELETE":
                    keep = qs.get("keep_favorites", ["1"])[0] != "0"
                    try:
                        removed, kept = app.delete_recordings(name, keep_favorites=keep)
                    except UserError as e:
                        return self._json({"error": str(e)}, e.code)
                    return self._json({"ok": True, "removed": removed, "kept": kept})
                if method != "POST":
                    return self._json({"error": "metodo"}, 405)
                if action in ("enabled", "auto_record"):
                    value = bool(self._body().get("value"))
                    try:
                        stopped = app.set_user_flag(name, action, value)
                    except UserError as e:
                        return self._json({"error": str(e)}, e.code)
                    return self._json({"ok": True, "stopped": stopped})
                if action == "record":
                    try:
                        return self._json(app.record_user(name))
                    except UserError as e:
                        return self._json({"error": str(e)}, e.code)
                if action == "stop":
                    try:
                        app.stop_user_recording(name)
                    except UserError as e:
                        return self._json({"error": str(e)}, e.code)
                    return self._json({"ok": True})
                if action == "open":
                    d = Path(s.get("output_dir")) / name
                    d.mkdir(parents=True, exist_ok=True)
                    return self._json(open_folder(d))
            m = re.fullmatch(r"/api/recordings/(\d+)", path)
            if m and method == "DELETE":
                row = app.db.q("SELECT r.*, u.username FROM recordings r JOIN users u ON u.id=r.user_id WHERE r.id=?", (int(m[1]),))
                if not row:
                    return self._json({"error": "no existe"}, 404)
                if row[0]["status"] == "recording":
                    return self._json({"error": "Detiene la grabacion antes de borrarla."}, 409)
                try:
                    Path(row[0]["path"]).unlink(missing_ok=True)
                except OSError as e:
                    return self._json({"error": f"No se pudo borrar el archivo: {e}"}, 500)
                (THUMB_DIR / f"{int(m[1])}.jpg").unlink(missing_ok=True)
                app.db.x("DELETE FROM recordings WHERE id=?", (int(m[1]),))
                return self._json({"ok": True})
            m = re.fullmatch(r"/api/recordings/(\d+)/favorite", path)
            if m and method == "POST":
                try:
                    value = app.set_favorite(int(m[1]), bool(self._body().get("value")))
                except UserError as e:
                    return self._json({"error": str(e)}, e.code)
                return self._json({"ok": True, "favorite": value})
            m = re.fullmatch(r"/api/recordings/(\d+)/analyze", path)
            if m and method == "POST":
                rec_id = int(m[1])
                row = app.db.q("SELECT * FROM recordings WHERE id=?", (rec_id,))
                if not row or not Path(row[0]["path"]).is_file():
                    return self._json({"error": "Archivo no encontrado"}, 404)
                exe = find_ffmpeg(s)
                if not exe:
                    return self._json({"error": "No se encuentra ffmpeg."}, 500)
                dur = row[0]["duration_sec"] or 0
                h = analyze_video(exe, Path(row[0]["path"]), dur)
                app.db.x("UPDATE recordings SET codec=?, width=?, height=?, fps=?, frames=?, "
                         "frozen_secs=?, ts_bad=?, analyzed_at=? WHERE id=?",
                         (*analysis_fields(h), rec_id))
                fz = ", ".join(f"{fmt_secs(a)}-{fmt_secs(b)}" for a, b in h["freezes"][:12])
                log.info("Analisis manual de %s: codec=%s %s fps=%s fotogramas=%s congelado=%s (%.0f s en %d tramos: %s) "
                         "tiempos: retrocesos=%d hueco_max=%.1fs desfase_av=%.2f->%.2fs (calidad %s)",
                         Path(row[0]["path"]).name, h["codec"], h["size"], h["fps"], h["frames"], h["frozen"],
                         h["frozen_secs"], len(h["freezes"]), fz or "-", h["ts_backwards"], h["ts_maxgap"],
                         h["av_start"], h["av_end"], row[0]["quality"])
                return self._json({"codec": h["codec"], "size": h["size"], "fps": h["fps"], "frames": h["frames"],
                                   "ts_bad": h["ts_bad"], "ts_backwards": h["ts_backwards"], "ts_maxgap": h["ts_maxgap"],
                                   "av_start": h["av_start"], "av_end": h["av_end"],
                                   "frozen": h["frozen"], "frozen_secs": h["frozen_secs"], "duration": dur,
                                   "freezes": [[a, b] for a, b in h["freezes"][:12]]})
            m = re.fullmatch(r"/api/recordings/(\d+)/repair", path)
            if m and method == "POST":
                rec_id = int(m[1])
                row = app.db.q("SELECT * FROM recordings WHERE id=?", (rec_id,))
                if not row or not Path(row[0]["path"]).is_file():
                    return self._json({"error": "Archivo no encontrado"}, 404)
                exe = find_ffmpeg(s)
                if not exe:
                    return self._json({"error": "No se encuentra ffmpeg."}, 500)
                if not VIDEO_JOB_LOCK.acquire(blocking=False):
                    return self._json({"error": "Ya hay un trabajo de video en curso. Espera a que termine."}, 409)
                rec = dict(row[0])
                src = Path(rec["path"])
                dur = rec["duration_sec"] or 0

                def work(job: Job) -> None:
                    try:
                        job.message = "Analizando el video..."
                        h = analyze_video(exe, src, dur)
                        dst = src.with_name(src.stem + "_reparado.mp4")
                        job.message = "Reparando (recodificando)..."
                        if not repair_video(exe, src, dst, h["frames"] or 0, dur,
                                            progress=lambda f, secs: setattr(job, "progress", f)):
                            raise RuntimeError("No se pudo reparar (ver Diagnostico).")
                        new_dur = probe_duration(exe, dst) or dur
                        new_id = app.db.x(
                            "INSERT INTO recordings(user_id,path,started_at,ended_at,duration_sec,size_bytes,status,quality,note) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (rec["user_id"], str(dst), rec["started_at"],
                             datetime.now().isoformat(timespec="seconds"), new_dur, dst.stat().st_size,
                             "done", rec["quality"], f"Reparado a partir de #{rec_id}"))
                        make_thumb(exe, dst, new_dur, THUMB_DIR / f"{new_id}.jpg")
                        mgr._totals_at = 0.0
                        job.result = {"id": new_id}
                        job.message, job.progress = "Copia reparada lista", 1.0
                    finally:
                        VIDEO_JOB_LOCK.release()

                job = app.start_job("repair", work)
                return self._json({"job": job.id})
            m = re.fullmatch(r"/api/sessions/([0-9a-f]{4,32})/merge", path)
            if m and method == "POST":
                sid = m[1]
                rows = app.db.q("SELECT r.*, u.username FROM recordings r JOIN users u ON u.id=r.user_id "
                                "WHERE r.session_id=? ORDER BY r.id", (sid,))
                if len(rows) < 2:
                    return self._json({"error": "Esa sesion no tiene varios archivos que unir."}, 400)
                if any(r["status"] != "done" for r in rows):
                    return self._json({"error": "Espera a que terminen de procesarse los archivos."}, 409)
                recs = [dict(r) for r in rows]
                paths = [Path(r["path"]) for r in recs]
                if any(not p.is_file() for p in paths):
                    return self._json({"error": "Falta alguno de los archivos de la sesion."}, 409)
                exe = find_ffmpeg(s)
                if not exe:
                    return self._json({"error": "No se encuentra ffmpeg."}, 500)
                if not VIDEO_JOB_LOCK.acquire(blocking=False):
                    return self._json({"error": "Ya hay un trabajo de video en curso. Espera a que termine."}, 409)
                body = self._body()
                delete_orig = bool(body.get("delete_originals"))
                total = sum(float(r["duration_sec"] or 0) for r in recs)
                try:
                    stamp = datetime.fromisoformat(recs[0]["started_at"]).strftime("%Y-%m-%d_%H-%M-%S")
                except ValueError:
                    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                out_dir = paths[0].parent

                def work_merge(job: Job) -> None:
                    try:
                        job.message = f"Uniendo {len(recs)} archivos (sin recodificar)..."
                        out = unique_path(out_dir, f"{recs[0]['username']}_{stamp}_unido", ".mp4")
                        merged = merge_segments(exe, paths, out, total,
                                                progress=lambda f: setattr(job, "progress", f))
                        if merged is None:
                            raise RuntimeError("No se pudieron unir: los archivos pueden tener formato distinto.")
                        dur = probe_duration(exe, merged) or total
                        new_id = app.db.x(
                            "INSERT INTO recordings(user_id,path,started_at,ended_at,duration_sec,size_bytes,status,quality,note) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            (recs[0]["user_id"], str(merged), recs[0]["started_at"],
                             datetime.now().isoformat(timespec="seconds"), dur, merged.stat().st_size,
                             "done", "unido", f"Unido de {len(recs)} archivos (sesion {sid})"))
                        make_thumb(exe, merged, dur, THUMB_DIR / f"{new_id}.jpg")
                        mgr._totals_at = 0.0
                        if delete_orig:
                            for r in recs:
                                if not int(r.get("favorite") or 0):
                                    mgr._purge_recording(int(r["id"]), Path(r["path"]))
                        job.result = {"id": new_id, "file": merged.name, "deleted": delete_orig}
                        job.message, job.progress = "Video unido listo", 1.0
                    finally:
                        VIDEO_JOB_LOCK.release()

                job = app.start_job("merge", work_merge)
                return self._json({"job": job.id})
            m = re.fullmatch(r"/api/jobs/([0-9a-f]+)", path)
            if m and method == "GET":
                job = app.job(m[1])
                if not job:
                    return self._json({"error": "trabajo no encontrado"}, 404)
                return self._json(job.public())
            if method == "GET" and path == "/api/logs":
                name = (qs.get("name") or ["actividad"])[0]
                if name not in ("actividad", "errores", "motor-pip", "motor-prueba"):
                    return self._json({"error": "log no valido"}, 400)
                try:
                    lines = max(10, min(1000, int((qs.get("lines") or ["200"])[0])))
                except ValueError:
                    lines = 200
                return self._json({"name": name,
                                   "text": scrub_paths(redact(tail_file(LOG_DIR / f"{name}.log", lines)))})
            m = re.fullmatch(r"/thumbs/(\d+)", path)
            if m and method == "GET":
                return self.serve_image(THUMB_DIR / f"{int(m[1])}.jpg", 86400)
            if method == "POST" and path == "/api/push/test":
                if not any(app.pusher.configured().values()):
                    return self._json({"error": "Primero configura ntfy, Telegram o un webhook y guarda los ajustes."}, 400)
                res = app.pusher.send_sync("saved", "✅ Prueba de LiveVault",
                                           "Si ves este aviso en tu celular, todo está bien configurado.")
                return self._json({"results": res})
            if method == "POST" and path == "/api/push/tg-detect":
                if not s.get("push_tg_token").strip():
                    return self._json({"error": "Pega primero el token del bot y guarda los ajustes."}, 400)
                try:
                    if app.bot is not None and app.bot.running:
                        chat, name = app.bot.detect_chat(35)
                    else:
                        chat, name = app.pusher.telegram_detect_chat()
                except RuntimeError as e:
                    return self._json({"error": str(e)}, 400)
                s.set_many({"push_tg_chat": chat})
                return self._json({"chat_id": chat, "name": name})
            m = re.fullmatch(r"/avatars/([A-Za-z0-9._]+)", path)
            if m and method == "GET":
                return self.serve_image(avatar_path(m[1]), 86400)
            m = re.fullmatch(r"/files/(\d+)", path)
            if m and method == "GET":
                return self.serve_file(int(m[1]), download=qs.get("dl", ["0"])[0] == "1")
            if path == "/api/settings":
                if method == "GET":
                    d = s.public()
                    d["autostart_supported"] = WIN
                    try:
                        d["autostart"] = bool(autostart_get())
                    except Exception:
                        d["autostart"] = False
                    return self._json(d)
                if method == "POST":
                    return self._save_settings(self._body())
            if method == "POST" and path == "/api/open_folder":
                d = Path(s.get("output_dir"))
                d.mkdir(parents=True, exist_ok=True)
                return self._json(open_folder(d))
            if method == "POST" and path == "/api/open_logs":
                return self._json(open_folder(LOG_DIR))
            if method == "GET" and path == "/api/engine":
                return self._json({"running": app.updater.running, "lines": app.updater.lines,
                                   "result": app.updater.result, "restart_needed": app.updater.restart_needed,
                                   "versions": engine_versions()})
            if method == "POST" and path == "/api/engine/update":
                started = app.updater.start()
                return self._json({"started": started})
            if method == "POST" and path == "/api/restart":
                app.restart()
                return self._json({"ok": True})
            if method == "GET" and path == "/api/report":
                text = build_report(app)
                if qs.get("download", ["0"])[0] == "1":
                    return self._text(text, extra={"Content-Disposition":
                                      f'attachment; filename="livevault_reporte_{datetime.now():%Y%m%d_%H%M%S}.txt"'})
                return self._json({"report": text})
            if method == "GET" and path == "/api/support":
                data = build_support(app)
                return self._text(data, "application/zip",
                                  extra={"Content-Disposition":
                                         f'attachment; filename="livevault_soporte_{datetime.now():%Y%m%d_%H%M%S}.zip"'})
            if method == "POST" and path == "/api/backup":
                dest = app.db.backup(BACKUP_DIR, 7)
                if dest is None:
                    return self._json({"error": "No se pudo crear la copia (ver Diagnostico)."}, 500)
                log.info("Copia de seguridad manual: %s", dest.name)
                return self._json({"ok": True, "file": dest.name, "size": dest.stat().st_size})
            if method == "POST" and path == "/api/log/clear":
                # no truncar el archivo abierto por el handler (dejaba un hueco de bytes NUL):
                # rotarlo es lo correcto y de paso se borran las copias
                for h in list(log.handlers):
                    if (isinstance(h, logging.handlers.RotatingFileHandler)
                            and Path(h.baseFilename).name == "errores.log"):
                        h.acquire()
                        try:
                            h.flush()
                            h.doRollover()
                        except Exception:
                            log.debug("No se pudo rotar el log\n%s", traceback.format_exc())
                        finally:
                            h.release()
                for f in LOG_DIR.glob("errores.log.*"):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                return self._json({"ok": True})
            return self._json({"error": "no encontrado"}, 404)

        def _save_settings(self, body: dict):
            s = app.settings
            clean: dict[str, str] = {}
            if "output_dir" in body:
                d = str(body["output_dir"]).strip()
                if not d:
                    return self._json({"error": "La carpeta de grabaciones no puede estar vacia."}, 400)
                try:
                    Path(d).mkdir(parents=True, exist_ok=True)
                    test = Path(d) / ".tkrec_test"
                    test.write_text("ok")
                    test.unlink()
                except Exception as e:
                    return self._json({"error": f"No se puede escribir en esa carpeta: {e}"}, 400)
                clean["output_dir"] = d
            if "check_interval" in body:
                try:
                    clean["check_interval"] = str(max(15, min(3600, int(float(body["check_interval"])))))
                except ValueError:
                    return self._json({"error": "Intervalo no valido."}, 400)
            if body.get("quality") in ("max", "hd", "sd"):
                clean["quality"] = body["quality"]
            if body.get("final_format") in ("mp4", "mkv"):
                clean["final_format"] = body["final_format"]
            for k in ("auto_update_on_start", "ytdlp_fallback", "notifications",
                      "push_live", "push_recording", "push_saved", "push_problem", "push_session",
                      "push_ended"):
                if k in body:
                    clean[k] = "1" if body[k] else "0"
            for key, lo, hi, msg in (("min_keep_kb", 0, 10_000_000, "El tamano minimo no es valido."),
                                     ("retention_days", 0, 3650, "Los dias de retencion no son validos."),
                                     ("max_total_gb", 0, 1_000_000, "El limite de espacio no es valido."),
                                     ("min_free_gb", 0, 10_000, "El espacio libre minimo no es valido.")):
                if key in body:
                    try:
                        clean[key] = str(max(lo, min(hi, int(float(body[key])))))
                    except (TypeError, ValueError):
                        return self._json({"error": msg}, 400)
            # opcionales que no son secretos: vacio significa "quitarlo"
            if "ffmpeg_path" in body:
                clean["ffmpeg_path"] = str(body.get("ffmpeg_path") or "").strip()
                if clean["ffmpeg_path"] and not Path(clean["ffmpeg_path"]).is_file():
                    return self._json({"error": "La ruta de ffmpeg indicada no existe."}, 400)
            if "push_tg_chat" in body:
                clean["push_tg_chat"] = str(body.get("push_tg_chat") or "").strip()
            if "push_ntfy_server" in body:
                clean["push_ntfy_server"] = (str(body.get("push_ntfy_server") or "").strip()
                                             or DEFAULTS["push_ntfy_server"])
            for k in sorted(SECRET_KEYS):
                v = body.get(k)
                if v is not None and (str(v).strip() or body.get(k + "_clear")):
                    val = str(v).strip()
                    if k == "session_id":
                        val = extract_sessionid(val)      # acepta la cookie entera pegada
                    clean[k] = val
                if body.get(k + "_clear"):
                    clean[k] = ""
            checks = {
                "push_ntfy_topic": (r"[A-Za-z0-9_-]{1,64}", "El tema de ntfy solo admite letras, números, - y _ (máx. 64)."),
                "push_tg_token": (r"\d{5,}:[A-Za-z0-9_-]{20,}", "El token del bot de Telegram no tiene el formato esperado (123456:ABC...)."),
                "push_tg_chat": (r"-?\d{1,20}|@[A-Za-z0-9_]{3,64}", "El chat de Telegram debe ser un número (o @canal)."),
                "push_webhook": (r"https?://[^\s]+", "La URL del webhook debe empezar por http:// o https://."),
            }
            for k, (rx, msg) in checks.items():
                if clean.get(k) and not re.fullmatch(rx, clean[k]):
                    return self._json({"error": msg}, 400)
            if clean.get("push_ntfy_server") is not None:
                if clean["push_ntfy_server"] == "":
                    clean["push_ntfy_server"] = DEFAULTS["push_ntfy_server"]
                elif not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", clean["push_ntfy_server"]):
                    return self._json({"error": "La dirección del servidor ntfy no es válida."}, 400)
            old_tg = (s.get("push_tg_token"), s.get("push_tg_chat"))
            s.set_many(clean)
            if {"session_id", "proxy"} & set(clean):
                app.call(app.engine.drop())       # clientes nuevos con la nueva configuracion
            if "session_id" in clean and app.manager:
                app.manager.session_reset()
            if (s.get("push_tg_token"), s.get("push_tg_chat")) != old_tg:
                app.restart_bot()
            if {"retention_days", "max_total_gb", "min_free_gb"} & set(clean) and app.manager:
                try:                              # aplicar la retencion sin esperar a la siguiente vuelta
                    asyncio.run_coroutine_threadsafe(app.manager._storage_tick(), app.loop)
                except Exception:
                    pass
            if WIN and "autostart" in body:
                try:
                    autostart_set(bool(body["autostart"]))
                except Exception as e:
                    log.error("No se pudo cambiar el inicio con Windows\n%s", traceback.format_exc())
                    return self._json({"error": f"No se pudo configurar el inicio con Windows: {e}"}, 500)
            return self._json({"ok": True})

        def serve_image(self, p: Path, max_age: int):
            try:
                data = p.read_bytes()
            except OSError:
                return self._json({"error": "sin imagen"}, 404)
            ctype = sniff_image(data)
            if not ctype:
                return self._json({"error": "sin imagen"}, 404)
            return self._text(data, ctype, extra={"Cache-Control": f"public, max-age={max_age}"})

        def serve_file(self, rec_id: int, download: bool):
            row = app.db.q("SELECT path FROM recordings WHERE id=?", (rec_id,))
            if not row or not Path(row[0]["path"]).is_file():
                return self._json({"error": "Archivo no encontrado"}, 404)
            p = Path(row[0]["path"])
            try:
                size = p.stat().st_size
            except OSError:
                return self._json({"error": "El archivo aún se está escribiendo; espera un momento."}, 503)
            if size <= 0:
                return self._json({"error": "Todavía no hay datos grabados; espera unos segundos."}, 503)
            ctype = {".mp4": "video/mp4", ".mkv": "video/x-matroska"}.get(p.suffix.lower()) or \
                mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            try:
                f = open(p, "rb")
            except OSError:
                return self._json({"error": "El archivo aún se está escribiendo; espera un momento."}, 503)
            with f:
                start, end, code = 0, size - 1, 200
                rng = self.headers.get("Range")
                if rng:
                    m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                    if m:
                        if m[1]:
                            start = int(m[1])
                            end = int(m[2]) if m[2] else size - 1
                        elif m[2]:
                            start = max(0, size - int(m[2]))
                        end = min(end, size - 1)
                        if start > end:
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{size}")
                            self.send_header("Content-Length", "0")
                            self._common_headers()
                            self.end_headers()
                            return
                        code = 206
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self._common_headers()
                if code == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                if download:
                    self.send_header("Content-Disposition", f'attachment; filename="{p.name}"')
                self.end_headers()
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1024 * 256, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (ConnectionError, OSError):
                        return                      # el reproductor cerro la conexion (saltar en el video): normal
                    left -= len(chunk)

    return H


def open_folder(path: Path) -> dict:
    try:
        if WIN:
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdin=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "msg": f"No se pudo abrir la carpeta: {e}"}


# --------------------------------------------------------------------------- main
def _fatal(msg: str, title: str = "LiveVault") -> None:
    """Aviso visible cuando el .exe no tiene consola: si no, el usuario no ve nada al fallar."""
    try:
        log.error("%s", msg)
    except Exception:
        pass
    try:
        if WIN:
            ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10)   # MB_ICONERROR
        else:
            print(msg, file=sys.stderr)
    except Exception:
        pass


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--pip-worker":          # modos auxiliares (actualizar / probar el motor)
        return pip_worker(argv[1:])
    if argv and argv[0] == "--selftest":
        if len(argv) < 3:
            _fatal("Uso interno: --selftest <carpeta-del-motor> <archivo-de-salida>")
            return 2
        return selftest(argv[1], argv[2])
    if argv and argv[0] in ("--version", "-V"):
        print(f"LiveVault {APP_VERSION}")
        return 0
    ap = argparse.ArgumentParser(description="LiveVault")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="no abrir el panel al iniciar")
    ap.add_argument("--minimized", action="store_true", help="iniciar en segundo plano (solo bandeja)")
    ap.add_argument("--restarted", action="store_true", help="uso interno tras reiniciar")
    args = ap.parse_args()
    setup_logging()
    log.info("LiveVault %s | Python %s | %s | datos en %s", APP_VERSION, platform.python_version(),
             "exe" if FROZEN else "script", DATA_DIR)
    if DATA_DIR_FALLBACK:
        log.warning("La carpeta del programa no admite escritura: "
                    "la base de datos y los ajustes se guardan en %s", DATA_DIR)
    if _on_onedrive(DATA_DIR):
        log.warning("La carpeta de datos esta dentro de OneDrive (%s). La sincronizacion puede "
                    "bloquear la base de datos mientras graba. Se recomienda mover el programa a una "
                    "carpeta local, por ejemplo C:\\LiveVault.", DATA_DIR)
    sys.excepthook = lambda t, v, tb: log.error("Excepcion no controlada\n%s", "".join(traceback.format_exception(t, v, tb)))
    threading.excepthook = lambda a: log.error("Excepcion en hilo %s\n%s", getattr(a.thread, "name", "?"),
                                               "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    App(args.port, open_browser=not args.no_browser, minimized=args.minimized, restarted=args.restarted).start()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:                       # el .exe no tiene consola: hay que avisar en pantalla
        import traceback as _tb
        _fatal("LiveVault no pudo arrancar.\n\n" + _tb.format_exc()[-1800:] +
               "\n\nPuedes enviar este texto junto con tu reporte de Diagnóstico.")
        sys.exit(1)
