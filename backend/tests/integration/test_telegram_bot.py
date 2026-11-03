"""Telegram bot commands (backend/app/services/telegram_bot.py).

A fake Telegram client stands in for the Bot API and the printers are stubbed: nothing is sent
to Telegram and no printer is contacted. Data is made up.
"""

import asyncio
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import update

from backend.app.models.notification import NotificationProvider
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import telegram_bot as tb
from backend.app.services.notification_service import NotificationService

TOKEN = "123456:SECRET-test-token"
CHAT = "4242"


class FakeTelegram:
    """Records Bot API calls; ``getUpdates`` hands out the queued batches, then waits."""

    def __init__(self, batches=()):
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.batches = list(batches)
        self.closed = False

    async def call(self, method, files=None, **params):
        self.calls.append((method, params, files))
        if method == "getUpdates":
            if not self.batches:
                await asyncio.sleep(3600)
            batch = self.batches.pop(0)
            if isinstance(batch, Exception):
                raise batch
            return batch
        return True

    async def close(self):
        self.closed = True

    def sent(self):
        return [(m, p) for m, p, _ in self.calls if m in ("sendMessage", "sendPhoto")]


def _state(state="RUNNING", progress=42.0, remaining=75):
    return MagicMock(state=state, progress=progress, remaining_time=remaining, subtask_name="box", gcode_file=None)


def _ok_data(reply: tb.Reply) -> str:
    return reply.buttons[0][0][1]


async def _until(condition, tries=200):
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.01)


@pytest.fixture
def bot(async_client):  # async_client: the bot's sessions use the test database
    fakes: list[FakeTelegram] = []

    def factory(token):
        fakes.append(FakeTelegram())
        return fakes[-1]

    bot = tb.TelegramBot(client_factory=factory)
    bot.fakes = fakes
    yield bot


async def _provider(db, name="Telegram", **config):
    db.add(NotificationProvider(name=name, provider_type="telegram", config=json.dumps(config), enabled=True))
    await db.commit()


def test_allowed_chats_default_to_the_provider_chat():
    assert tb.allowed_chats({"chat_id": " 42 "}) == {"42"}
    assert tb.allowed_chats({"chat_id": "42", "allowed_chat_ids": "1, 2 -100"}) == {"1", "2", "-100"}
    assert tb.commands_enabled({"commands_enabled": "true"}) and not tb.commands_enabled({})


async def test_one_poller_per_bot_only_for_providers_taking_commands(bot, db_session):
    await _provider(db_session, "A", bot_token=TOKEN, chat_id="1", commands_enabled="true")
    await _provider(db_session, "B", bot_token=TOKEN, chat_id="2", commands_enabled=True)
    await _provider(db_session, "C", bot_token="other:token", chat_id="3")  # notifications only
    await bot.reconcile()
    assert list(bot._pollers) == [TOKEN] and bot._pollers[TOKEN].allowed == {"1", "2"}
    assert bot._pollers[TOKEN].label == "A, B"

    await db_session.execute(update(NotificationProvider).values(enabled=False))
    await db_session.commit()
    await bot.reconcile()
    assert bot._pollers == {} and bot.fakes[0].closed


async def test_poller_stops_on_409_and_is_not_started_again(bot, db_session, caplog):
    await _provider(db_session, bot_token=TOKEN, chat_id=CHAT, commands_enabled="true")
    fakes = []

    def factory(token):
        fakes.append(FakeTelegram([tb.TelegramConflict("getUpdates", 409, "Conflict")]))
        return fakes[-1]

    bot._factory = factory
    with caplog.at_level(logging.INFO, logger=tb.__name__):
        await bot.reconcile()
        await _until(lambda: TOKEN in bot._stopped)
        await bot.reconcile()
    assert TOKEN in bot._stopped and bot._pollers == {} and len(fakes) == 1 and fakes[0].closed
    assert "409" in caplog.text and TOKEN not in caplog.text


async def test_network_errors_are_retried_and_updates_answered(bot, db_session, monkeypatch, caplog):
    await _provider(db_session, bot_token=TOKEN, chat_id=CHAT, commands_enabled="true")
    timeout = httpx.ReadTimeout("", request=httpx.Request("POST", f"https://api.telegram.org/bot{TOKEN}/getUpdates"))
    update_ = {"update_id": 7, "message": {"chat": {"id": int(CHAT)}, "text": "/aiuto"}}
    fake = FakeTelegram([timeout, [update_]])
    bot._factory = lambda token: fake
    real_sleep = asyncio.sleep
    monkeypatch.setattr(tb.asyncio, "sleep", lambda s: real_sleep(0 if s < 60 else s))
    with caplog.at_level(logging.INFO, logger=tb.__name__):
        await bot.reconcile()
        await _until(lambda: fake.sent() and len([c for c in fake.calls if c[0] == "getUpdates"]) >= 3)
    await bot.stop()
    assert "Comandi:" in fake.sent()[0][1]["text"]
    offsets = [p.get("offset") for m, p, _ in fake.calls if m == "getUpdates"]
    assert offsets[-1] == 8  # the answered update is not fetched again
    assert "ReadTimeout" in caplog.text and TOKEN not in caplog.text


async def test_other_chats_are_ignored(bot):
    poller = tb._Poller(FakeTelegram(), "Telegram", {CHAT})
    await bot.handle(poller, {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/annulla 1"}})
    query = {"id": "q", "data": "clear:1", "message": {"chat": {"id": 999}}}
    await bot.handle(poller, {"update_id": 2, "callback_query": query})
    assert poller.client.calls == []


async def test_pause_asks_for_confirmation_then_uses_the_ui_route(bot, printer_factory, caplog):
    printer = await printer_factory(name="X1C uno", model="X1C")
    client = MagicMock(pause_print=MagicMock(return_value=True))
    with (
        patch("backend.app.services.printer_manager.printer_manager.get_status", return_value=_state()),
        patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client),
        caplog.at_level(logging.INFO, logger=tb.__name__),
    ):
        pick = await bot.on_command(CHAT, "/pausa")
        assert pick.buttons == [[("X1C uno", f"ask:pausa:{printer.id}")]]
        ask = await bot.on_button(CHAT, pick.buttons[0][0][1])
        assert ask.text == "Mettere in pausa X1C uno?"
        client.pause_print.assert_not_called()  # nothing happens before the confirmation

        done = await bot.on_button(CHAT, _ok_data(ask))
        assert done.text == "Pausa inviata."
        client.pause_print.assert_called_once()

        stale = f"ok:pause:{printer.id}:{int(time.time()) - tb.CONFIRM_SECONDS - 5}"
        assert (await bot.on_button(CHAT, stale)).text.startswith("Conferma scaduta")
        assert client.pause_print.call_count == 1
    assert f"Telegram action pause {printer.id} from chat {CHAT}" in caplog.text


async def test_route_refusal_is_reported(bot, printer_factory):
    printer = await printer_factory(name="A1", model="A1")
    with patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=None):
        reply = await bot.on_button(CHAT, f"ok:stop:{printer.id}:{int(time.time())}")
    assert reply.text == "Non fatto: Printer not connected"


async def test_plate_clear_button_acts_at_once_through_the_route(bot, printer_factory):
    printer = await printer_factory(name="P1S", model="P1S")
    manager = "backend.app.api.routes.printers.printer_manager"
    with (
        patch(f"{manager}.is_connected", return_value=True),
        patch(f"{manager}.get_status", return_value=_state("FINISH")),
        patch(f"{manager}.is_awaiting_plate_clear", return_value=True),
        patch(f"{manager}.set_awaiting_plate_clear") as set_flag,
    ):
        reply = await bot.on_button(CHAT, f"clear:{printer.id}")
    assert reply.text.startswith("Piatto segnato come libero")
    set_flag.assert_called_once_with(printer.id, False)


async def test_queue_and_start_next_item(bot, db_session, printer_factory):
    from backend.app.models.library import LibraryFile

    printer = await printer_factory(name="A1 due", model="A1")
    out = LibraryFile(filename="box_plate1.gcode.3mf", file_path="/x/b.gcode.3mf", file_type="3mf", file_size=1)
    db_session.add(out)
    await db_session.flush()
    item = PrintQueueItem(printer_id=printer.id, library_file_id=out.id, status="pending", manual_start=True)
    db_session.add(item)
    await db_session.commit()

    queue = await bot.on_command(CHAT, "/coda")
    assert "box_plate1.gcode.3mf – A1 due – avvio manuale" in queue.text

    ask = await bot.on_command(CHAT, "/avvia a1 due")
    assert ask.text == "Avviare box_plate1.gcode.3mf su A1 due?"
    with patch("backend.app.api.routes.print_queue.compute_deficit_for_queue_item", AsyncMock(return_value=[])):
        done = await bot.on_button(CHAT, _ok_data(ask))
    assert done.text.startswith("Voce sbloccata")
    await db_session.refresh(item)
    assert item.manual_start is False


async def test_enqueue_uses_accoda_route_with_the_plan_plates(bot, async_client, monkeypatch):
    shape = (await async_client.post("/api/v1/farm/shapes", json={"name": "Sopra"})).json()
    comp = (await async_client.post("/api/v1/farm/components", json={"shape_id": shape["id"], "color": "Nero"})).json()
    code = comp["code"]

    async def fake_plan(db, queued=None, codes=None):
        return {"components": [{"code": code, "plates": [{"plate_id": 5}, {"plate_id": 9}]}]}

    created = AsyncMock(return_value=[{"pieces": 15}])
    monkeypatch.setattr("backend.app.farm.plan.service.build_plan", fake_plan)
    monkeypatch.setattr("backend.app.farm.jobs.routes.create_jobs", created)

    ask = await bot.on_command(CHAT, f"/accoda {code.lower()}")
    assert ask.text.startswith(f"Accodare una stampa di {code}")
    done = await bot.on_button(CHAT, _ok_data(ask))
    assert done.text.startswith(f"Accodato {code} (15 pezzi)")
    job = created.call_args.args[0].jobs[0]
    assert (job.component_code, job.plate_ids, job.printer_id) == (code, [5, 9], None)


async def test_status_and_photo_on_request(bot, printer_factory):
    printer = await printer_factory(name="H2D", model="H2D")
    manager = "backend.app.services.printer_manager.printer_manager"
    with (
        patch(f"{manager}.get_status", return_value=_state()),
        patch(f"{manager}.is_connected", return_value=True),
        patch(f"{manager}.is_awaiting_plate_clear", return_value=False),
    ):
        status = await bot.on_command(CHAT, "/stato")
    assert "• H2D: in stampa 42% · mancano 1h 15m · box" in status.text
    assert status.buttons == [[("📷 H2D", f"photo:{printer.id}")]]

    client = FakeTelegram()
    with patch("backend.app.main._capture_snapshot_for_notification", AsyncMock(return_value=b"jpeg")):
        photo = await bot.on_button(CHAT, f"photo:{printer.id}")
    await bot._send(client, CHAT, photo, None)
    method, params, files = client.calls[0]
    assert method == "sendPhoto" and files["photo"][1] == b"jpeg" and params["caption"] == "H2D"


async def test_plan_lists_the_most_urgent_with_enqueue_buttons(bot, monkeypatch):
    rows = [
        {"code": "C0001", "name": "Box", "color": "Nero", "status": "urgente", "to_print": 12, "in_queue": 4},
        {"code": "C0002", "name": "Lid", "color": None, "status": "da_stampare", "to_print": 3, "in_queue": 0},
    ]
    rows[0].update(known=True, plates=[{"plate_id": 1}])
    rows[1].update(known=False, plates=[])

    async def fake_get_plan(db):
        return {"components": rows, "error": None}

    monkeypatch.setattr("backend.app.farm.plan.routes.get_plan", fake_get_plan)
    reply = await bot.on_command(CHAT, "/cosastampare@farm_bot")
    assert reply.text.splitlines()[1] == "1. C0001 Box (Nero) – urgente · da stampare 12 · in coda 4"
    assert reply.buttons == [[("➕ Accoda C0001", "ask:accoda:C0001")]]


@pytest.mark.parametrize(("commands", "markup"), [("true", True), ("false", False)])
async def test_plate_to_clear_notification_carries_the_button(commands, markup):
    service = NotificationService()
    http = MagicMock()
    http.post = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {"ok": True}))
    config = {"bot_token": TOKEN, "chat_id": CHAT, "commands_enabled": commands}
    with patch.object(service, "_get_client", AsyncMock(return_value=http)):
        ok, _ = await service._send_telegram(config, "*Piatto*\nX1C", buttons=[[("✅ Piatto libero", "clear:3")]])
    sent = http.post.call_args.kwargs["json"]
    assert ok
    assert ("reply_markup" in sent) is markup
    if markup:
        keyboard = {"inline_keyboard": [[{"text": "✅ Piatto libero", "callback_data": "clear:3"}]]}
        assert json.loads(sent["reply_markup"]) == keyboard
