"""Telegram bot commands (backend/app/services/telegram_bot.py).

A fake Telegram client stands in for the Bot API and the printers are stubbed: nothing is sent
to Telegram and no printer is contacted. Data is made up.
"""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import update

from backend.app.models.notification import NotificationProvider
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import telegram_bot as tb
from backend.app.services.notification_service import NotificationService
from backend.app.services.printer_manager import printer_manager

TOKEN = "123456:SECRET-test-token"
CHAT = "4242"  # a private chat: its id is the user's
BOT = "farm_bot"


class FakeTelegram:
    """Records Bot API calls; ``getUpdates`` hands out the queued batches, then waits."""

    def __init__(self, batches=()):
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.batches = list(batches)
        self.closed = False

    async def call(self, method, files=None, **params):
        self.calls.append((method, params, files))
        if method == "getMe":
            return {"username": BOT}
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

    def polls(self):
        return [p for m, p, _ in self.calls if m == "getUpdates"]


def _state(state="RUNNING", progress=42.0, remaining=75, job="box"):
    return MagicMock(state=state, progress=progress, remaining_time=remaining, subtask_name=job, gcode_file=None)


def _ok_data(reply: tb.Reply) -> str:
    return reply.buttons[0][0][1]


def _message(text, chat=CHAT, user=CHAT, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "from": {"id": int(user)}, "text": text}}


async def _until(condition, tries=2000):
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


@pytest.fixture
def fast_sleep(monkeypatch):
    """The poller's waits (5 s, 5 min) take no time; its long poll still blocks."""
    real_sleep = asyncio.sleep
    monkeypatch.setattr(tb.asyncio, "sleep", lambda s: real_sleep(0 if s < 3600 else s))


async def _provider(db, name="Telegram", **config):
    db.add(NotificationProvider(name=name, provider_type="telegram", config=json.dumps(config), enabled=True))
    await db.commit()


async def _run_poller(bot, db, fake):
    await _provider(db, bot_token=TOKEN, chat_id=CHAT, commands_enabled="true")
    bot._factory = lambda token: fake
    await bot.reconcile()


def test_allowed_chats_and_users():
    assert tb.allowed_chats({"chat_id": " 42 "}) == {"42"}
    assert tb.allowed_chats({"chat_id": "42", "allowed_chat_ids": "1, 2 -100"}) == {"1", "2", "-100"}
    # A group never authorises its members by itself; a private chat's id is its user's.
    assert tb.allowed_users({"chat_id": "42", "allowed_chat_ids": "7, -100"}) == {"7"}
    assert tb.allowed_users({"chat_id": "-100", "allowed_user_ids": "7 8"}) == {"7", "8"}
    assert tb.commands_enabled({"commands_enabled": "true"}) and not tb.commands_enabled({})


async def test_one_poller_per_bot_only_for_providers_taking_commands(bot, db_session):
    await _provider(db_session, "A", bot_token=TOKEN, chat_id="1", commands_enabled="true")
    await _provider(db_session, "B", bot_token=TOKEN, chat_id="-2", allowed_user_ids="5", commands_enabled=True)
    await _provider(db_session, "C", bot_token="other:token", chat_id="3")  # notifications only
    await bot.reconcile()
    poller = bot._pollers[TOKEN]
    assert list(bot._pollers) == [TOKEN] and (poller.allowed, poller.users) == ({"1", "-2"}, {"1", "5"})
    assert poller.label == "A, B"

    await db_session.execute(update(NotificationProvider).values(enabled=False))
    await db_session.commit()
    await bot.reconcile()
    assert bot._pollers == {} and bot.fakes[0].closed


async def test_backlog_is_skipped_at_start_then_new_updates_are_answered(bot, db_session, fast_sleep):
    fake = FakeTelegram([[_message("/annulla 3", update_id=40)], [_message("/aiuto", update_id=41)]])
    await _run_poller(bot, db_session, fake)
    await _until(lambda: fake.sent() and len(fake.polls()) >= 3)
    await bot.stop()
    first, second = fake.polls()[:2]
    assert (first["offset"], first["timeout"]) == (-1, 0)  # only confirms what piled up while off
    assert second["offset"] == 41
    assert len(fake.sent()) == 1 and "Comandi:" in fake.sent()[0][1]["text"]  # /annulla never ran
    assert fake.polls()[-1]["offset"] == 42


async def test_409_waits_and_polls_again(bot, db_session, fast_sleep, caplog):
    conflict = tb.TelegramConflict("getUpdates", 409, "Conflict")
    fake = FakeTelegram([[], conflict, [_message("/aiuto", update_id=5)]])
    with caplog.at_level(logging.INFO, logger=tb.__name__):
        await _run_poller(bot, db_session, fake)
        await _until(lambda: fake.sent())
    await bot.stop()
    assert TOKEN not in bot._stopped and fake.sent()
    assert "(409); retrying in 300s" in caplog.text and TOKEN not in caplog.text


async def test_unexpected_errors_never_kill_the_poller(bot, db_session, fast_sleep, caplog):
    timeout = httpx.ReadTimeout("", request=httpx.Request("POST", f"https://api.telegram.org/bot{TOKEN}/getUpdates"))
    fake = FakeTelegram([[], timeout, ValueError("bad json"), [_message("/aiuto", update_id=9)]])
    with caplog.at_level(logging.INFO, logger=tb.__name__):
        await _run_poller(bot, db_session, fake)
        await _until(lambda: fake.sent())
        assert TOKEN in bot._pollers  # still the same poller
    await bot.stop()
    assert "ReadTimeout" in caplog.text and "ValueError: bad json" in caplog.text and TOKEN not in caplog.text


async def test_a_poller_that_ends_is_restarted(bot, db_session):
    await _provider(db_session, bot_token=TOKEN, chat_id=CHAT, commands_enabled="true")
    await bot.reconcile()
    first = bot._pollers[TOKEN]
    first.task.cancel()  # as if it had died
    await asyncio.gather(first.task, return_exceptions=True)
    await asyncio.sleep(0)
    assert TOKEN not in bot._pollers
    await bot.reconcile()
    assert bot._pollers[TOKEN] is not first
    await bot.stop()


async def test_other_chats_users_and_bots_are_ignored(bot):
    poller = tb._Poller(FakeTelegram(), "Telegram", {CHAT, "-100"}, {CHAT}, username=BOT)
    await bot.handle(poller, _message("/annulla 1", chat="999", user="999"))
    await bot.handle(poller, _message("/annulla 1", chat="-100", user="777"))  # allowed group, unknown member
    query = {"id": "q", "data": "clear:1:0", "from": {"id": 777}, "message": {"chat": {"id": int(CHAT)}}}
    await bot.handle(poller, {"update_id": 2, "callback_query": query})
    await bot.handle(poller, _message("/aiuto@OtherBot"))  # a group's command for another bot
    assert poller.client.calls == []

    await bot.handle(poller, _message(f"/aiuto@{BOT}", chat="-100", user=CHAT))  # allowed user in the group
    assert "Comandi:" in poller.client.sent()[0][1]["text"]


async def test_pause_asks_then_runs_the_ui_route_once(bot, printer_factory, caplog):
    printer = await printer_factory(name="X1C uno", model="X1C")
    client = MagicMock(pause_print=MagicMock(return_value=True))
    running = _state()
    with (
        patch("backend.app.services.printer_manager.printer_manager.get_status", side_effect=lambda pid: running),
        patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client),
        caplog.at_level(logging.INFO, logger=tb.__name__),
    ):
        pick = await bot.on_command(CHAT, "/pausa", CHAT)
        assert pick.buttons == [[("X1C uno", f"ask:pausa:{printer.id}")]]
        ask = await bot.on_button(CHAT, pick.buttons[0][0][1], CHAT)
        assert ask.text == "Mettere in pausa X1C uno?"
        client.pause_print.assert_not_called()  # nothing happens before the confirmation

        assert (await bot.on_button(CHAT, _ok_data(ask), "999")).text == "Questa conferma è di un altro utente."
        ask = await bot.on_command(CHAT, "/pausa x1c", CHAT)
        assert (await bot.on_button(CHAT, _ok_data(ask), CHAT)).text == "Pausa inviata."
        assert (await bot.on_button(CHAT, _ok_data(ask), CHAT)).text.startswith("Conferma scaduta o già usata")
        client.pause_print.assert_called_once()  # a second tap does nothing

        stale = await bot.on_command(CHAT, "/pausa x1c", CHAT)
        bot._pending[_ok_data(stale).split(":")[1]].created -= tb.CONFIRM_SECONDS + 5
        assert (await bot.on_button(CHAT, _ok_data(stale), CHAT)).text.startswith("Conferma scaduta")
        assert client.pause_print.call_count == 1
    assert f"Telegram action pause {printer.id} from chat {CHAT}, user {CHAT}" in caplog.text


async def test_a_confirmation_for_an_earlier_print_is_refused(bot, printer_factory):
    await printer_factory(name="K2", model="K2")
    client = MagicMock(stop_print=MagicMock(return_value=True))
    running = {"now": _state(job="box")}
    with (
        patch(
            "backend.app.services.printer_manager.printer_manager.get_status", side_effect=lambda pid: running["now"]
        ),
        patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=client),
    ):
        ask = await bot.on_command(CHAT, "/annulla k2", CHAT)
        running["now"] = _state(job="lid")  # 9 minutes later another print runs
        reply = await bot.on_button(CHAT, _ok_data(ask), CHAT)
    assert reply.text.startswith("La stampa è cambiata")
    client.stop_print.assert_not_called()


async def test_route_refusal_is_reported(bot, printer_factory):
    printer = await printer_factory(name="A1", model="A1")
    data = bot._confirm("stop", printer.id, (CHAT, CHAT), tb._print_running(printer.id))[0][0][1]
    with patch("backend.app.api.routes.printers.printer_manager.get_client", return_value=None):
        reply = await bot.on_button(CHAT, data, CHAT)
    assert reply.text == "Non fatto: Printer not connected"


async def test_plate_clear_button_only_for_the_finish_it_was_sent_for(bot, printer_factory):
    printer = await printer_factory(name="P1S", model="P1S")
    printer_manager.set_awaiting_plate_clear(printer.id, True)
    raised = int(printer_manager.plate_clear_raised_at(printer.id))
    manager = "backend.app.api.routes.printers.printer_manager"
    try:
        with (
            patch(f"{manager}.is_connected", return_value=True),
            patch(f"{manager}.get_status", return_value=_state("FINISH")),
            patch(f"{manager}.set_awaiting_plate_clear") as set_flag,
        ):
            assert (await bot.on_button(CHAT, f"clear:{printer.id}", CHAT)).text.startswith("Pulsante superato")
            older = await bot.on_button(CHAT, f"clear:{printer.id}:{raised - 600}", CHAT)
            assert older.text.startswith("Questa notifica è di una stampa precedente")
            set_flag.assert_not_called()
            reply = await bot.on_button(CHAT, f"clear:{printer.id}:{raised}", CHAT)
        assert reply.text.startswith("Piatto segnato come libero")
        set_flag.assert_called_once_with(printer.id, False)
    finally:
        printer_manager.set_awaiting_plate_clear(printer.id, False)
    assert printer_manager.plate_clear_raised_at(printer.id) is None


async def test_plate_to_clear_notification_carries_when_the_check_was_raised(printer_factory):
    printer = await printer_factory(name="A1 mini", model="A1")
    service = NotificationService()
    printer_manager.set_awaiting_plate_clear(printer.id, True)
    raised = int(printer_manager.plate_clear_raised_at(printer.id))
    try:
        with (
            patch.object(service, "_get_providers_for_event", AsyncMock(return_value=[MagicMock()])),
            patch.object(service, "_build_message_from_template", AsyncMock(return_value=("T", "B"))),
            patch.object(service, "_send_to_providers", AsyncMock(return_value=1)) as send,
        ):
            await service.on_farm_event("farm_plate_to_clear", {}, AsyncMock(), printer_id=printer.id)
    finally:
        printer_manager.set_awaiting_plate_clear(printer.id, False)
    assert send.call_args.kwargs["buttons"] == [[("✅ Piatto libero", f"clear:{printer.id}:{raised}")]]


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


async def test_enqueue_uses_accoda_route_once_with_the_plan_plates(bot, async_client, monkeypatch):
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
    await bot.on_button(CHAT, _ok_data(ask))  # a double tap
    created.assert_called_once()
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
    reply = await bot.on_command(CHAT, f"/cosastampare@{BOT}", CHAT, BOT)
    assert reply.text.splitlines()[1] == "1. C0001 Box (Nero) – urgente · da stampare 12 · in coda 4"
    assert reply.buttons == [[("➕ Accoda C0001", "ask:accoda:C0001")]]


async def test_command_arguments_reach_the_log_on_one_line(bot, caplog):
    with caplog.at_level(logging.INFO, logger=tb.__name__):
        await bot.on_command(CHAT, "/accoda X\nTelegram action stop 1 from chat 1: done", CHAT)
    record = next(r for r in caplog.records if r.getMessage().startswith("Telegram command"))
    assert "\n" not in record.getMessage()


@pytest.mark.parametrize(("commands", "markup"), [("true", True), ("false", False)])
async def test_plate_to_clear_notification_carries_the_button(commands, markup):
    service = NotificationService()
    http = MagicMock()
    http.post = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {"ok": True}))
    config = {"bot_token": TOKEN, "chat_id": CHAT, "commands_enabled": commands}
    with patch.object(service, "_get_client", AsyncMock(return_value=http)):
        ok, _ = await service._send_telegram(config, "*Piatto*\nX1C", buttons=[[("✅ Piatto libero", "clear:3:9")]])
    sent = http.post.call_args.kwargs["json"]
    assert ok
    assert ("reply_markup" in sent) is markup
    if markup:
        keyboard = {"inline_keyboard": [[{"text": "✅ Piatto libero", "callback_data": "clear:3:9"}]]}
        assert json.loads(sent["reply_markup"]) == keyboard
