"""Pruebas del bot de Telegram sin red (transporte falso): python -m unittest discover -s tests -v"""
import asyncio
import shutil
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


class FakeSettings:
    def __init__(self, values=None):
        self.values = values or {"push_tg_token": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                                 "push_tg_chat": "999"}

    def get(self, key):
        return self.values.get(key, "")


class FakeDB:
    def __init__(self, users=None, recordings=None):
        self.state = {}
        self.users = set(users or [])
        self.recordings = list(recordings or [])

    def q(self, sql, args=()):
        if "bot_state" in sql and args and args[0] == "offset" and "offset" in self.state:
            return [{"value": self.state["offset"]}]
        if "FROM users WHERE username" in sql and args:
            return [{"1": 1}] if args[0] in self.users else []
        if "FROM recordings" in sql:
            return list(self.recordings)
        return []

    def x(self, sql, args=()):
        if "bot_state" in sql and args:
            self.state["offset"] = args[0]
        return 0


class FakeUser:
    def __init__(self, nickname="", live=None, viewers=None, title="", recording=False,
                 error=None, error_kind=None):
        self.nickname, self.live, self.viewers = nickname, live, viewers
        self.title, self.recording = title, recording
        self.error, self.error_kind = error, error_kind


class FakeManager:
    def __init__(self, user=None):
        self.user = user
        self.session_state = "ok"
        self.free_gb = 123.4
        self.low_space = False
        self.global_block_until = 0.0
        self.users = {}

    async def check_now(self, name):
        return self.user


class FakeApp:
    def __init__(self, user=None, snapshot=None, add_error=None, recordings=None, users=None):
        self.settings = FakeSettings()
        self._snapshot = snapshot or []
        self.db = FakeDB(users=users if users is not None
                         else {u["username"] for u in self._snapshot},
                         recordings=recordings)
        self.manager = FakeManager(user)
        self.add_error = add_error
        self.added = []
        self.removed = []
        self.flags = []
        self.record_result = {"ok": True, "msg": "Grabacion iniciada"}
        self.was_recording = False
        self.remove_error = None

    def manager_snapshot(self):
        return self._snapshot

    def add_user(self, raw):
        if self.add_error is not None:
            raise self.add_error
        self.added.append(raw)
        return "nuevo"

    def remove_user(self, name, delete_files=False):
        if self.remove_error is not None:
            raise self.remove_error
        self.removed.append((name, delete_files))

    def set_user_flag(self, name, field, value):
        self.flags.append((name, field, value))
        for u in self._snapshot:
            if u.get("username") == name:
                u[field] = value
        return self.was_recording

    def record_user(self, name):
        return self.record_result

    def stop_user_recording(self, name):
        return self.was_recording

    def call(self, coro, timeout=60):
        return asyncio.run(coro)


class FakeTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": []}

    def text(self):
        return "\n".join(str(p.get("text", "")) for m, p in self.calls if m == "sendMessage")

    def edited(self):
        return "\n".join(str(p.get("text", "")) for m, p in self.calls if m == "editMessageText")

    def answered(self):
        return [str(p.get("text", "")) for m, p in self.calls if m == "answerCallbackQuery"]

    def markup(self):
        for m, p in self.calls:
            if m == "sendMessage" and p.get("reply_markup"):
                return p["reply_markup"]
        return None


def update(text, chat_id=999, is_bot=False):
    return {"update_id": 1,
            "message": {"text": text,
                        "chat": {"id": chat_id, "type": "private"},
                        "from": {"id": chat_id, "is_bot": is_bot}}}


def callback(data, chat_id=999):
    return {"update_id": 2,
            "callback_query": {"id": "cb1", "data": data,
                               "from": {"id": chat_id, "is_bot": False},
                               "message": {"message_id": 5,
                                           "chat": {"id": chat_id, "type": "private"}}}}


def snap_user(**kw):
    base = {"username": "uno", "nickname": "Uno", "status": "offline", "live": False,
            "enabled": True, "auto_record": True, "viewers": None, "title": "",
            "rec_seconds": 0, "rec_size": 0, "rec_quality": "", "last_check_ago": 10,
            "next_check_in": 30, "total_recordings": 2, "total_size": 2048, "error": None}
    base.update(kw)
    return base


class BotTests(unittest.TestCase):
    def make(self, **kw):
        fake = FakeApp(**kw)
        transport = FakeTransport()
        return app.TelegramBot(fake, transport=transport), transport, fake

    def test_parse_command(self):
        self.assertEqual(app._parse_command("/add @Pepe"), ("add", "@Pepe"))
        self.assertEqual(app._parse_command("/add@MiBot @Pepe"), ("add", "@Pepe"))
        self.assertEqual(app._parse_command("/estado"), ("status", ""))
        self.assertEqual(app._parse_command("/anadir\n@pepe"), ("add", "@pepe"))
        self.assertEqual(app._parse_command("/lista 2"), ("list", "2"))
        self.assertEqual(app._parse_command("/quitar @x"), ("del", "@x"))
        self.assertEqual(app._parse_command("@pepe"), ("", "@pepe"))
        self.assertEqual(app._parse_command("hola"), ("", "hola"))

    def test_allowed(self):
        bot, _, _ = self.make()
        self.assertTrue(bot._allowed({"id": 999}))
        self.assertFalse(bot._allowed({"id": 1}))
        bot.app.settings.values["push_tg_chat"] = "@micanal"
        self.assertTrue(bot._allowed({"id": -100, "username": "MiCanal"}))
        self.assertFalse(bot._allowed({"id": -100, "username": "otro"}))

    def test_status_needs_authorization(self):
        bot, transport, _ = self.make()
        bot.handle_update(update("/status", chat_id=1))
        self.assertIn("no está autorizado", transport.text())

    def test_id_works_from_any_chat(self):
        bot, transport, _ = self.make()
        bot.handle_update(update("/id", chat_id=42))
        self.assertIn("42", transport.text())
        self.assertIn("No autorizado", transport.text())

    def test_start_and_help(self):
        bot, transport, _ = self.make()
        bot.handle_update(update("/start"))
        self.assertIn("Bot de LiveVault", transport.text())
        self.assertIn("/add", transport.text())
        transport.calls.clear()
        bot.handle_update(update("/ayuda"))
        self.assertIn("/status", transport.text())

    def test_status_allowed_with_recording(self):
        snap = [snap_user(status="recording", live=True, viewers=10, rec_seconds=61,
                          rec_size=2 * 1024 * 1024)]
        bot, transport, _ = self.make(snapshot=snap)
        bot.handle_update(update("/status"))
        text = transport.text()
        self.assertIn("1 grabando", text)
        self.assertIn("0:01:01", text)
        self.assertIn("2.0 MB", text)
        self.assertIn("TikTokLive", text)
        self.assertIn("Sesión: correcta", text)

    def test_add_invalid(self):
        bot, transport, _ = self.make(add_error=app.AddUserError("Usuario no valido.", 400))
        bot.handle_update(update("/add @x"))
        self.assertIn("Usuario no valido", transport.text())

    def test_add_duplicate(self):
        bot, transport, _ = self.make(add_error=app.AddUserError("@x ya esta en la lista.", 409))
        bot.handle_update(update("/add @x"))
        self.assertIn("ya está en la lista", transport.text())

    def test_add_live(self):
        user = FakeUser(nickname="Nuevo", live=True, viewers=1234, title="Cumple")
        bot, transport, fake = self.make(user=user)
        bot.handle_update(update("/add @nuevo"))
        text = transport.text()
        self.assertIn("✅ Añadido @nuevo (Nuevo)", text)
        self.assertIn("En vivo ahora", text)
        self.assertIn("1.234", text)
        self.assertIn("Cumple", text)
        self.assertEqual(fake.added, ["@nuevo"])

    def test_add_offline(self):
        bot, transport, _ = self.make(user=FakeUser(nickname="", live=False))
        bot.handle_update(update("@nuevo"))
        self.assertIn("Sin directo", transport.text())

    def test_add_not_found_warning(self):
        bot, transport, _ = self.make(user=FakeUser(error="x", error_kind="not_found"))
        bot.handle_update(update("/add @nuevo"))
        self.assertIn("no existe", transport.text())

    def test_list_paginates(self):
        snap = [snap_user(username=f"u{i}", nickname="") for i in range(10)]
        bot, transport, _ = self.make(snapshot=snap)
        bot.handle_update(update("/list"))
        self.assertIn("Página 1/2", transport.text())
        markup = transport.markup()
        self.assertIsNotNone(markup)
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "list|2")
        transport.calls.clear()
        bot.handle_update(callback("list|2"))
        self.assertIn("Página 2/2", transport.edited())
        self.assertIn("", transport.answered())

    def test_user_detail_and_buttons(self):
        snap = [snap_user(status="recording", live=True, viewers=10, rec_seconds=61,
                          rec_size=1024, rec_quality="hd/flv", total_recordings=3)]
        bot, transport, _ = self.make(snapshot=snap)
        bot.handle_update(update("/user @uno"))
        text = transport.text()
        self.assertIn("Grabando", text)
        self.assertIn("hd/flv", text)
        markup = transport.markup()
        data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
        self.assertIn("u|stop|uno", data)
        self.assertIn("u|last|uno", data)
        self.assertIn("u|del|uno", data)

    def test_pause_resume(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(update("/pause @uno"))
        self.assertIn("pausado", transport.text())
        self.assertEqual(fake.flags, [("uno", "enabled", False)])
        transport.calls.clear()
        bot.handle_update(update("/resume @uno"))
        self.assertIn("reanudado", transport.text())
        self.assertEqual(fake.flags[-1], ("uno", "enabled", True))

    def test_pause_already(self):
        bot, transport, fake = self.make(snapshot=[snap_user(enabled=False)])
        bot.handle_update(update("/pause @uno"))
        self.assertIn("ya estaba pausado", transport.text())
        self.assertEqual(fake.flags, [])

    def test_auto(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(update("/auto off @uno"))
        self.assertIn("desactivada", transport.text())
        self.assertEqual(fake.flags, [("uno", "auto_record", False)])

    def test_record(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(update("/record @uno"))
        self.assertIn("Grabacion iniciada", transport.text())

    def test_stop(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        fake.was_recording = True
        bot.handle_update(update("/stop @uno"))
        self.assertIn("detenida", transport.text())
        transport.calls.clear()
        fake.was_recording = False
        bot.handle_update(update("/stop @uno"))
        self.assertIn("no estaba grabando", transport.text())

    def test_last_with_thumb(self):
        rows = [{"id": 77, "started_at": "2026-01-01T10:00:00", "duration_sec": 65,
                 "size_bytes": 2 * 1024 * 1024, "quality": "hd/flv"}]
        bot, transport, _ = self.make(snapshot=[snap_user()], recordings=rows)
        thumb = app.THUMB_DIR / "77.jpg"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"\xff\xd8\xff\xe0datos")
        try:
            bot.handle_update(update("/last @uno"))
        finally:
            thumb.unlink(missing_ok=True)
        text = transport.text()
        self.assertIn("Últimas de @uno", text)
        self.assertIn("0:01:05", text)
        self.assertIn("2.0 MB", text)
        photos = [p for m, p in transport.calls if m == "sendPhoto"]
        self.assertTrue(photos and photos[0]["_file"].endswith("77.jpg"))

    def test_del_confirmation_remove(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(update("/del @uno"))
        self.assertIn("¿Quitar a @uno", transport.text())
        keyboard = transport.markup()
        buttons = [b["callback_data"] for row in keyboard["inline_keyboard"] for b in row]
        self.assertEqual(len(buttons), 3)
        nonce = buttons[0].split("|")[3]
        transport.calls.clear()
        bot.handle_update(callback(f"del|remove|uno|{nonce}"))
        self.assertEqual(fake.removed, [("uno", False)])
        self.assertIn("quitado de la lista", transport.edited())
        self.assertEqual(transport.answered()[0], "Procesando…")

    def test_del_confirmation_files_and_cancel(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(update("/del @uno"))
        nonce = list(bot._pending)[0]
        bot.handle_update(callback(f"del|files|uno|{nonce}"))
        self.assertEqual(fake.removed, [("uno", True)])
        self.assertIn("archivos eliminados", transport.edited())
        transport.calls.clear()
        bot.handle_update(update("/del @uno"))
        nonce = list(bot._pending)[0]
        bot.handle_update(callback(f"del|cancel|uno|{nonce}"))
        self.assertIn("Cancelado", transport.edited())
        self.assertEqual(fake.removed, [("uno", True)])

    def test_del_expired_nonce(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(callback("del|remove|uno|deadbeef"))
        self.assertIn("caducó", transport.edited())
        self.assertEqual(fake.removed, [])

    def test_callback_requires_authorization(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(callback("u|pause|uno", chat_id=7))
        self.assertIn("No autorizado", transport.answered())
        self.assertEqual(fake.flags, [])

    def test_user_button_record(self):
        bot, transport, fake = self.make(snapshot=[snap_user()])
        bot.handle_update(callback("u|record|uno"))
        self.assertIn("Grabacion iniciada", transport.text())

    def test_unknown_command(self):
        bot, transport, _ = self.make()
        bot.handle_update(update("/foo"))
        self.assertIn("No conozco ese comando", transport.text())

    def test_plain_text(self):
        bot, transport, _ = self.make()
        bot.handle_update(update("hola"))
        self.assertIn("Para vigilar a @hola", transport.text())
        transport.calls.clear()
        bot.handle_update(update("esto es una frase normal"))
        self.assertIn("No te he entendido", transport.text())

    def test_bot_ignores_other_bots(self):
        bot, transport, _ = self.make()
        bot.handle_update(update("/status", is_bot=True))
        self.assertFalse(transport.calls)

    def test_offset_persists(self):
        bot, _, fake = self.make()
        self.assertEqual(bot._load_offset(), 0)
        bot._save_offset(123)
        self.assertEqual(bot._load_offset(), 123)
        self.assertEqual(fake.db.state["offset"], "123")

    def test_detect_chat_uses_last_message(self):
        bot, _, _ = self.make()
        bot.handle_update(update("/start", chat_id=777))
        self.assertEqual(bot.detect_chat(timeout=0.1), ("777", ""))

    def test_detect_chat_ignores_strangers(self):
        bot, _, _ = self.make()
        bot.handle_update(update("/id", chat_id=777))       # no es /start ni el chat autorizado
        with self.assertRaises(RuntimeError):
            bot.detect_chat(timeout=0.1)
        self.assertEqual(bot.status()["running"], False)
        self.assertTrue(bot.status()["enabled"])

    def test_send_chunks_long_text(self):
        bot, transport, _ = self.make()
        bot._send(999, "x" * 9000)
        sends = [p for m, p in transport.calls if m == "sendMessage"]
        self.assertEqual(len(sends), 3)
        self.assertEqual(sum(len(p["text"]) for p in sends), 9000)

    def test_run_processes_update_and_stops(self):
        fake = FakeApp()
        queue = [update("/status")]
        sent = []

        def transport(method, payload):
            if method == "getUpdates":
                if payload.get("offset") == -1:
                    return {"ok": True, "result": []}
                if queue:
                    return {"ok": True, "result": [queue.pop(0)]}
                return {"ok": True, "result": []}
            if method == "sendMessage":
                sent.append(str(payload.get("text", "")))
            return {"ok": True, "result": {}}

        bot = app.TelegramBot(fake, transport=transport)
        bot.start()
        for _ in range(100):
            if any("usuarios" in text for text in sent):
                break
            time.sleep(0.02)
        bot.stop()
        bot._thread.join(2)
        self.assertTrue(any("usuarios" in text for text in sent), sent)
        self.assertFalse(bot._thread.is_alive())
        self.assertGreaterEqual(bot._load_offset(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
