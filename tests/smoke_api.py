"""Prueba de humo del panel (sin TikTok): python tests/smoke_api.py

Arranca la aplicacion real en un puerto libre, recorre la API (estado, ajustes, grabaciones,
Range, logs, borrado) y la cierra. Sirve para detectar roturas del servidor y de las rutas.
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / ".test-data-smoke"
if DATA.exists():
    shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir(parents=True, exist_ok=True)
os.environ["TKREC_DATA"] = str(DATA)
sys.path.insert(0, str(HERE.parent))

import app  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


app.wait_for_network = lambda timeout=90: True          # no salir a internet en la prueba
app.tray_available = lambda: False                      # sin bandeja en CI
a = app.App(port=free_port(), open_browser=False, minimized=True)
thread = threading.Thread(target=a.start, daemon=True)
thread.start()
for _ in range(150):
    if a.url:
        break
    time.sleep(0.1)
assert a.url, "la aplicacion no arranco"

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails: list[str] = []


def call(path: str, method: str = "GET", body=None, headers=None, token: bool = True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(a.url + path, data=data, method=method)
    if token:
        req.add_header("X-TK-Token", app.PANEL_TOKEN)
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with opener.open(req, timeout=15) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def check(name: str, cond: bool, extra="") -> None:
    if cond:
        print(f"OK    {name}")
    else:
        print(f"FALLO {name}  {extra}")
        fails.append(name)


def jget(path: str, **kw):
    code, body, _ = call(path, **kw)
    try:
        return code, json.loads(body)
    except ValueError:
        return code, {}


code, data = jget("/api/ping", token=False)
check("ping sin clave", code == 200 and data.get("app") == "tkrec", (code, data))

code, body, _ = call("/", token=False)
check("raiz sin clave no revela el panel", code == 403 and b"__TK_TOKEN__" not in body, code)

code, body, _ = call("/")
check("panel con clave", code == 200 and b"LiveVault" in body and b"__TK_TOKEN__" not in body, code)

code, data = jget("/api/state")
disk = data.get("disk") or {}
check("state con users/disk/jobs", code == 200 and "users" in data and "disk" in data and "jobs" in data)
check("state incluye actualizaciones", code == 200 and isinstance(data.get("update"), dict), data.get("update"))
check("state version", data.get("app_version") == app.APP_VERSION, data.get("app_version"))

code, _, _ = call("/api/settings", "POST", {"check_interval": 5}, token=False)
check("POST sin clave del panel prohibido", code == 403, code)

code, _, _ = call("/api/settings", "POST", {"check_interval": 5},
                  headers={"Origin": "http://evil.test"})
check("POST con Origin ajeno prohibido", code == 403, code)

code, data = jget("/api/settings")
check("ajustes nuevos presentes",
      {"min_free_gb", "retention_days", "max_total_gb", "push_webhook", "push_ended"} <= set(data))

code, _ = jget("/api/quit")
check("ruta GET no permitida", code == 404, code)

code, _ = jget("/api/users", method="POST", body={"username": "no vale"})
check("usuario invalido", code == 400, code)

code, _ = jget("/api/settings", method="POST", body={"check_interval": 5})
check("intervalo se guarda y se limita", code == 200)
code, data = jget("/api/settings")
check("intervalo limitado a 15", data.get("check_interval") == "15", data.get("check_interval"))

code, _ = jget("/api/settings", method="POST",
               body={"retention_days": 7, "max_total_gb": 10, "min_free_gb": 0})
check("retencion guardada", code == 200)

code, _ = jget("/api/settings", method="POST", body={"push_webhook": "ftp://malo"})
check("webhook invalido rechazado", code == 400, code)

code, data = jget("/api/settings", method="POST",
                  body={"session_id": "sessionid=COOKIE123; tt-target-idc=useast5"})
check("sessionid extraida de la cookie", code == 200)
check("sessionid guardada", a.settings.get("session_id") == "COOKIE123", a.settings.get("session_id"))
code, _ = jget("/api/settings", method="POST", body={"session_id_clear": True})
check("sessionid se puede borrar", a.settings.get("session_id") == "")

code, data = jget("/api/session/check", method="POST")
check("comprobar sessionid sin cookie", code == 200 and data.get("state") == "none", data)

uid = a.db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("smoketest", "2026-01-01T00:00:00"))
folder = Path(a.settings.get("output_dir")) / "smoketest"
folder.mkdir(parents=True, exist_ok=True)
video = folder / "smoke.mp4"
video.write_bytes(bytes(range(256)) * 8)
rid = a.db.x("INSERT INTO recordings(user_id,path,started_at,status,duration_sec,size_bytes) VALUES(?,?,?,?,?,?)",
             (uid, str(video), "2026-01-01T00:00:00", "done", 3, video.stat().st_size))

code, data = jget("/api/users/smoketest/recordings")
check("listado de grabaciones", code == 200 and len(data.get("recordings", [])) == 1, data)

code, body, headers = call(f"/files/{rid}", headers={"Range": "bytes=10-19"})
check("Range 206", code == 206 and len(body) == 10, (code, len(body)))
check("Content-Range", headers.get("Content-Range") == f"bytes 10-19/{video.stat().st_size}", headers.get("Content-Range"))
check("cabeceras de seguridad", headers.get("X-Content-Type-Options") == "nosniff", headers.get("X-Content-Type-Options"))

code, body, headers = call(f"/files/{rid}?dl=1")
check("descarga con attachment", code == 200 and "attachment" in (headers.get("Content-Disposition") or ""))

empty = folder / "empty.mp4"
empty.write_bytes(b"")
erid = a.db.x("INSERT INTO recordings(user_id,path,started_at,status) VALUES(?,?,?,?)",
              (uid, str(empty), "2026-01-01T00:00:00", "recording"))
code, data = jget(f"/files/{erid}")
check("archivo en curso sin datos 503", code == 503 and "espera" in str(data.get("error", "")).lower(), (code, data))
a.db.x("DELETE FROM recordings WHERE id=?", (erid,))
empty.unlink(missing_ok=True)

code, _ = jget(f"/thumbs/{rid}")
check("miniatura inexistente 404", code == 404, code)

code, data = jget("/api/report")
check("informe", code == 200 and "reporte de diagnostico" in data.get("report", ""))

code, data = jget("/api/logs?name=actividad&lines=20")
check("ver log", code == 200 and "text" in data, data)

code, _ = jget("/api/log/clear", method="POST")
check("vaciar errores", code == 200)

code, data = jget("/api/push/test", method="POST")
check("prueba de avisos sin configurar", code == 400, (code, data))

# trabajo en segundo plano: reparar un video real (si hay ffmpeg)
exe = app.find_ffmpeg()
if exe:
    real = folder / "real.mp4"
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30", "-t", "4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(real)],
                   check=True, timeout=180)
    rid2 = a.db.x("INSERT INTO recordings(user_id,path,started_at,status,duration_sec,size_bytes) VALUES(?,?,?,?,?,?)",
                  (uid, str(real), "2026-01-01T00:00:00", "done", 4, real.stat().st_size))
    code, data = jget(f"/api/recordings/{rid2}/repair", method="POST")
    check("reparacion en segundo plano", code == 200 and data.get("job"), (code, data))
    job_id = data.get("job") or ""
    job = {}
    end = time.time() + 90
    while time.time() < end:
        code, job = jget(f"/api/jobs/{job_id}")
        if job.get("state") != "running":
            break
        time.sleep(0.5)
    check("trabajo terminado", job.get("state") == "done", job)
    check("copia reparada creada", bool((job.get("result") or {}).get("id")), job)

    # sesiones: dos trozos del mismo directo -> agrupados y unibles
    m1, m2 = folder / "s1.mp4", folder / "s2.mp4"
    shutil.copy2(real, m1)
    shutil.copy2(real, m2)
    sid = "abcdef123456"
    for i, p in enumerate((m1, m2)):
        a.db.x("INSERT INTO recordings(user_id,path,started_at,status,duration_sec,size_bytes,quality,session_id) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (uid, str(p), f"2026-01-02T10:00:0{i * 4}", "done", 4, p.stat().st_size, "hd/flv", sid))
    code, data = jget("/api/users/smoketest/recordings")
    groups = [s for s in data.get("sessions", []) if s.get("id") == sid]
    check("sesion agrupada", code == 200 and len(groups) == 1 and groups[0]["count"] == 2, groups)
    check("sesion se puede unir", bool(groups) and groups[0].get("can_merge"), groups)
    code, data = jget(f"/api/sessions/{sid}/merge", method="POST", body={"delete_originals": True})
    check("unir en segundo plano", code == 200 and data.get("job"), (code, data))
    job_id = data.get("job") or ""
    job = {}
    end = time.time() + 90
    while time.time() < end:
        code, job = jget(f"/api/jobs/{job_id}")
        if job.get("state") != "running":
            break
        time.sleep(0.5)
    check("union terminada", job.get("state") == "done", job)
    merged_id = (job.get("result") or {}).get("id")
    check("video unido creado", bool(merged_id), job)
    check("originales borrados", not m1.exists() and not m2.exists())
    if merged_id:
        mrow = a.db.q("SELECT path, note FROM recordings WHERE id=?", (merged_id,))[0]
        check("union como entrada propia",
              "_unido" in Path(mrow["path"]).name and "Unido de 2" in (mrow["note"] or ""), mrow)

# soporte y copias de seguridad
code, body, _ = call("/api/support")
check("paquete de soporte", code == 200 and body[:2] == b"PK", (code, body[:20]))
if body[:2] == b"PK":
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        names = set(z.namelist())
    check("soporte con reporte y csv", {"reporte.txt", "ajustes.json", "grabaciones.csv"} <= names, names)
code, data = jget("/api/backup", method="POST")
check("copia manual", code == 200 and data.get("ok"), (code, data))
if data.get("file"):
    check("copia en disco", (app.BACKUP_DIR / data["file"]).exists())
check("base de datos integra", a.db.quick_check() == "ok")

# bot de Telegram: comandos y autorizacion con un transporte falso (sin red)
replies = []


def fake_tg(method, payload):
    replies.append((method, payload))
    return {"ok": True, "result": []}


a.settings.set_many({"push_tg_chat": "999"})
bot = app.TelegramBot(a, transport=fake_tg)
bot.enabled = True
a.bot = bot


def tg(text, chat=999):
    replies.clear()
    bot.handle_update({"update_id": 1, "message": {
        "text": text, "chat": {"id": chat, "type": "private"},
        "from": {"id": chat, "is_bot": False}}})
    return "\n".join(str(p.get("text", "")) for m, p in replies if m == "sendMessage")


def tgc(data, chat=999):
    replies.clear()
    bot.handle_update({"update_id": 2, "callback_query": {
        "id": "cb", "data": data, "from": {"id": chat, "is_bot": False},
        "message": {"message_id": 5, "chat": {"id": chat, "type": "private"}}}})
    return "\n".join(str(p.get("text", "")) for m, p in replies
                     if m in ("sendMessage", "editMessageText"))


status = tg("/status")
check("bot /status", "usuarios" in status and "TikTokLive" in status, status)
check("bot /help", "/add" in tg("/help"))
check("bot /id desde chat ajeno", "7" in tg("/id", chat=7))
check("bot rechaza chat ajeno", "no está autorizado" in tg("/status", chat=7))
check("bot /add invalido", "no valido" in tg("/add no vale"))
check("bot /add duplicado", "ya está en la lista" in tg("/add @smoketest"))
check("bot estado", a.bot_status().get("chat") == "999" and not a.bot_status().get("running"))
bot._save_offset(4242)
check("bot offset persistente", bot._load_offset() == 4242)
name = a.add_user("@botuser")
check("alta compartida con el panel", name == "botuser" and bool(
    a.db.q("SELECT 1 FROM users WHERE username='botuser'")))
if a.manager is not None and a.manager.users.get("botuser"):
    a.manager.users["botuser"].next_check = time.time() + 3600   # no salir a TikTok en la prueba
listing = tg("/list")
check("bot /list", "botuser" in listing and "usuarios" in listing, listing)
check("bot /user", "botuser" in tg("/user @botuser"))
check("bot /pause", "pausado" in tg("/pause @botuser"))
check("bot /resume", "reanudado" in tg("/resume @botuser"))
check("bot /auto", "desactivada" in tg("/auto off @botuser"))
check("bot /last sin grabaciones", "No hay grabaciones" in tg("/last @botuser"))
check("bot /del pide confirmación", "¿Quitar a @botuser" in tg("/del @botuser"))
nonce = next(iter(bot._pending))
deleted = tgc(f"del|remove|botuser|{nonce}")
check("bot /del ejecuta", "quitado de la lista" in deleted, deleted)
check("usuario borrado por el bot", not a.db.q("SELECT 1 FROM users WHERE username='botuser'"))

# favoritas y borrado masivo, con archivos reales en la carpeta de salida
fav_uid = a.db.x("INSERT INTO users(username, added_at) VALUES(?,?)", ("favuser", "2026-01-01T00:00:00"))
fav_dir = Path(a.settings.get("output_dir")) / "favuser"
fav_dir.mkdir(parents=True, exist_ok=True)
fa, fb = fav_dir / "a.mp4", fav_dir / "b.mp4"
fa.write_bytes(b"a" * 1024)
fb.write_bytes(b"b" * 1024)
rid_a = a.db.x("INSERT INTO recordings(user_id,path,started_at,status,duration_sec,size_bytes) "
               "VALUES(?,?,?,?,?,?)", (fav_uid, str(fa), "2026-01-01T00:00:00", "done", 10, 1024))
a.db.x("INSERT INTO recordings(user_id,path,started_at,status,duration_sec,size_bytes) "
       "VALUES(?,?,?,?,?,?)", (fav_uid, str(fb), "2026-01-01T00:00:00", "done", 10, 1024))
code, data = jget(f"/api/recordings/{rid_a}/favorite", method="POST", body={"value": True})
check("marcar favorita", code == 200 and data.get("favorite") is True, data)
code, data = jget("/api/users/favuser/recordings?keep_favorites=1", method="DELETE")
check("borrar todas conserva favoritas",
      code == 200 and data.get("removed") == 1 and data.get("kept") == 1 and fa.exists() and not fb.exists(),
      (code, data, fa.exists(), fb.exists()))
check("favorita sigue en BD", bool(a.db.q("SELECT 1 FROM recordings WHERE id=? AND favorite=1", (rid_a,))))
code, _ = jget("/api/users/favuser?delete_files=1", method="DELETE")
check("limpiar favuser", code == 200 and not fa.exists(), code)

code, _ = jget("/api/jobs/000000000000")
check("trabajo inexistente 404", code == 404, code)

code, data = jget("/api/update/install", method="POST")
check("actualizar sin version nueva 409", code == 409, (code, data))

code, data = jget("/api/users/smoketest/stop", method="POST")
check("detener", code == 200 and data.get("ok"), (code, data))

code, _ = jget("/api/users/smoketest?delete_files=1", method="DELETE")
check("borrar usuario y archivos", code == 200 and not video.exists(), (code, video.exists()))

code, body, _ = call("/")
check("panel HTML", code == 200 and b"LiveVault" in body)

a.request_quit()
thread.join(40)
check("cierre ordenado", not thread.is_alive())

shutil.rmtree(DATA, ignore_errors=True)
if fails:
    print("\nFALLOS:", ", ".join(fails))
    sys.exit(1)
print("\nTodo correcto.")
