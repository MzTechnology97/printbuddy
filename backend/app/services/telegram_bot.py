"""Telegram bot commands: the notification bot also takes commands, by long polling (no public webhook).

A Telegram notification provider with ``commands_enabled`` gets one poller per bot token
(``getUpdates``). Only the chats in ``allowed_chat_ids`` (default: the provider's ``chat_id``) are
answered; every other update is ignored. When Telegram answers 409 (another poller, or a webhook,
on the same bot) that poller stops until the next restart.

Each action calls the route function the UI calls, with its checks, and is logged with the chat id.
Printer actions, starting the next queue item and queueing a component ask for an inline
confirmation; the "Piatto libero" button of the plate-to-clear notification acts at once.
Messages are plain text. The bot token never reaches a log: bots are named by their providers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.core import database

logger = logging.getLogger(__name__)

POLL_SECONDS = 30  # getUpdates long-poll timeout
RELOAD_SECONDS = 60  # how often the providers' settings are read again
CONFIRM_SECONDS = 10 * 60  # a confirmation button older than this is refused
MAX_TEXT = 4000  # Telegram's limit is 4096 characters

COMMANDS = [
    ("stato", "Stampanti: avanzamento e tempo che manca"),
    ("coda", "Coda di stampa"),
    ("cosastampare", "Cosa stampare, per urgenza"),
    ("pausa", "Metti in pausa una stampante"),
    ("riprendi", "Riprendi una stampante"),
    ("annulla", "Annulla la stampa di una stampante"),
    ("avvia", "Avvia la prossima voce della coda"),
    ("accoda", "Accoda un componente"),
    ("aiuto", "Elenco dei comandi"),
]
# command -> (action, question, states a printer must be in to be offered)
PRINTER_COMMANDS = {
    "pausa": ("pause", "Mettere in pausa", ("RUNNING", "PREPARE")),
    "riprendi": ("resume", "Riprendere", ("PAUSE",)),
    "annulla": ("stop", "Annullare la stampa di", ("RUNNING", "PAUSE", "PREPARE")),
}
STATES = {
    "RUNNING": "in stampa",
    "PAUSE": "in pausa",
    "PREPARE": "in preparazione",
    "SLICING": "in preparazione",
    "FINISH": "finita",
    "FAILED": "fallita",
    "IDLE": "libera",
}


class TelegramError(Exception):
    """Telegram refused a call (the message never contains the token)."""

    def __init__(self, method: str, status: int, description: str = ""):
        super().__init__(f"{method}: HTTP {status} {description}".strip())
        self.status = status


class TelegramConflict(TelegramError):
    """409: another getUpdates poller, or a webhook, is active on this bot."""


class TelegramClient:
    """The few Bot API calls the bot needs."""

    def __init__(self, token: str):
        self._token = token
        self._http = httpx.AsyncClient(timeout=POLL_SECONDS + 15)

    async def call(self, method: str, files: dict | None = None, **params: Any) -> Any:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        if files:
            data = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in params.items()}
            response = await self._http.post(url, data=data, files=files)
        else:
            response = await self._http.post(url, json=params)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code == 409:
            raise TelegramConflict(method, 409, body.get("description", ""))
        if response.status_code != 200 or not body.get("ok"):
            raise TelegramError(method, response.status_code, body.get("description", ""))
        return body.get("result")

    async def close(self) -> None:
        await self._http.aclose()


@dataclass
class Reply:
    text: str
    buttons: list[list[tuple[str, str]]] = field(default_factory=list)  # rows of (label, callback data)
    photo: bytes | None = None


@dataclass
class _Poller:
    client: Any
    label: str  # the providers' names, for logs
    allowed: set[str]
    task: asyncio.Task | None = None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "on", "yes")


def allowed_chats(config: dict) -> set[str]:
    """``allowed_chat_ids`` (comma or space separated), or the provider's own ``chat_id``."""
    raw = str(config.get("allowed_chat_ids") or "").strip() or str(config.get("chat_id") or "").strip()
    return {chat for chat in re.split(r"[,\s]+", raw) if chat}


def commands_enabled(config: dict) -> bool:
    return _truthy(config.get("commands_enabled"))


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


def _detail(detail: Any) -> str:
    if isinstance(detail, dict) and detail.get("code") == "insufficient_filament":
        return "filamento insufficiente sulla bobina assegnata: avviala dall'interfaccia"
    return str(detail)


def _duration(minutes: int | None) -> str:
    if not minutes:
        return "?"
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _confirm(action: str, arg: Any) -> list[list[tuple[str, str]]]:
    return [[("✅ Conferma", f"ok:{action}:{arg}:{int(time.time())}"), ("✖ No", "no")]]


def _item_name(item) -> str:
    if item.library_file is not None:
        return item.library_file.filename
    if item.archive is not None:
        return item.archive.filename
    return f"voce {item.id}"


class TelegramBot:
    def __init__(self, client_factory=TelegramClient):
        self._factory = client_factory
        self._pollers: dict[str, _Poller] = {}  # bot token -> its poller
        self._stopped: set[str] = set()  # tokens that got a 409 or were refused: not polled again
        self._task: asyncio.Task | None = None

    # ---------------------------------------------------------------- pollers

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._supervise(), name="telegram-bot")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        for token in list(self._pollers):
            await self._drop(token)

    async def _supervise(self) -> None:
        while True:
            try:
                await self.reconcile()
            except Exception:
                logger.exception("Telegram bot: reading the providers failed")
            await asyncio.sleep(RELOAD_SECONDS)

    async def reconcile(self) -> None:
        """One poller per bot token of the enabled Telegram providers that take commands."""
        from backend.app.models.notification import NotificationProvider

        bots: dict[str, tuple[set[str], list[str]]] = {}
        async with database.async_session() as db:
            rows = await db.execute(
                select(NotificationProvider).where(
                    NotificationProvider.enabled.is_(True), NotificationProvider.provider_type == "telegram"
                )
            )
            for provider in rows.scalars():
                config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
                token = str(config.get("bot_token") or "").strip()
                if token and commands_enabled(config):
                    chats, names = bots.setdefault(token, (set(), []))
                    chats.update(allowed_chats(config))
                    names.append(provider.name)
        for token in [t for t in self._pollers if t not in bots]:
            await self._drop(token)
        for token, (chats, names) in bots.items():
            if token in self._stopped:
                continue
            poller = self._pollers.get(token)
            if poller is None:
                poller = self._pollers[token] = _Poller(self._factory(token), ", ".join(names), chats)
                poller.task = asyncio.create_task(self._poll(token, poller), name="telegram-bot-poller")
                logger.info("Telegram bot %s: taking commands from %d chat(s)", poller.label, len(chats))
            else:
                poller.allowed = chats

    async def _drop(self, token: str) -> None:
        poller = self._pollers.pop(token, None)
        if poller is None:
            return
        if poller.task is not None and poller.task is not asyncio.current_task():
            poller.task.cancel()
        await poller.client.close()

    async def _poll(self, token: str, poller: _Poller) -> None:
        try:
            await poller.client.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
        except (httpx.HTTPError, TelegramError) as exc:
            logger.warning("Telegram bot %s: setMyCommands failed: %s", poller.label, _describe(exc))
        offset, delay = None, 5
        while True:
            params: dict[str, Any] = {"timeout": POLL_SECONDS, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            try:
                updates = await poller.client.call("getUpdates", **params)
                delay = 5
            except TelegramConflict:
                logger.warning(
                    "Telegram bot %s: another poller or a webhook is active on this bot (409): commands stop",
                    poller.label,
                )
                self._stopped.add(token)
                await self._drop(token)
                return
            except TelegramError as exc:
                if exc.status in (401, 403, 404):  # the token is not valid: polling again won't help
                    logger.warning("Telegram bot %s: %s; commands stop", poller.label, exc)
                    self._stopped.add(token)
                    await self._drop(token)
                    return
                logger.warning("Telegram bot %s: %s; retrying in %ss", poller.label, exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            except httpx.TransportError as exc:
                logger.warning("Telegram bot %s: getUpdates %s; retrying in %ss", poller.label, _describe(exc), delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            for update in updates or []:
                offset = update["update_id"] + 1
                try:
                    await self.handle(poller, update)
                except Exception:
                    logger.exception("Telegram bot %s: update %s failed", poller.label, update.get("update_id"))

    # ---------------------------------------------------------------- updates

    async def handle(self, poller: _Poller, update: dict) -> None:
        query = update.get("callback_query")
        message = (query or {}).get("message") or update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        if chat not in poller.allowed:
            logger.info("Telegram bot %s: ignored an update from chat %s (not allowed)", poller.label, chat)
            return
        if query is not None:
            await poller.client.call("answerCallbackQuery", callback_query_id=query["id"])
            reply = await self.on_button(chat, query.get("data") or "")
        else:
            text = (message.get("text") or "").strip()
            if not text.startswith("/"):
                return
            reply = await self.on_command(chat, text)
        await self._send(poller.client, chat, reply, message.get("message_thread_id"))

    async def _send(self, client, chat: str, reply: Reply, thread_id: int | None) -> None:
        params: dict[str, Any] = {"chat_id": chat}
        if thread_id:
            params["message_thread_id"] = thread_id
        if reply.buttons:
            params["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row] for row in reply.buttons
                ]
            }
        if reply.photo:
            files = {"photo": ("photo.jpg", reply.photo, "image/jpeg")}
            await client.call("sendPhoto", files=files, caption=reply.text, **params)
        else:
            await client.call("sendMessage", text=reply.text[:MAX_TEXT], **params)

    async def on_command(self, chat: str, text: str) -> Reply:
        word, _, arg = text[1:].partition(" ")
        command, arg = word.split("@", 1)[0].lower(), arg.strip()
        logger.info("Telegram command /%s %s from chat %s", command, arg, chat)
        if command == "stato":
            return await self._status()
        if command == "coda":
            return await self._queue()
        if command == "cosastampare":
            return await self._plan()
        if command in PRINTER_COMMANDS:
            return await self._ask_printer(command, arg)
        if command == "avvia":
            return await self._ask_start(arg)
        if command == "accoda":
            return await self._ask_enqueue(arg)
        if command in ("start", "aiuto", "help"):
            return Reply("Comandi:\n" + "\n".join(f"/{c} – {d}" for c, d in COMMANDS))
        return Reply("Comando sconosciuto: /aiuto per l'elenco.")

    async def on_button(self, chat: str, data: str) -> Reply:
        kind, *parts = data.split(":")
        if kind == "no":
            return Reply("Annullato.")
        if kind == "photo" and len(parts) == 1 and parts[0].isdigit():
            return await self._photo(int(parts[0]))
        if kind == "clear" and len(parts) == 1:  # the plate-to-clear notification's button: no second confirmation
            return await self.execute(chat, "clear", parts[0])
        if kind == "ask" and len(parts) == 2:
            command, arg = parts
            if command in PRINTER_COMMANDS:
                return await self._ask_printer(command, arg)
            if command == "avvia":
                return await self._ask_start(arg)
            if command == "accoda":
                return await self._ask_enqueue(arg)
        if kind == "ok" and len(parts) == 3:
            action, arg, asked = parts
            if not asked.isdigit() or time.time() - int(asked) > CONFIRM_SECONDS:
                return Reply("Conferma scaduta: ripeti il comando.")
            return await self.execute(chat, action, arg)
        return Reply("Pulsante non valido.")

    # ---------------------------------------------------------------- actions

    async def execute(self, chat: str, action: str, arg: str) -> Reply:
        """Run an action through the UI's route function; logged with the chat id."""
        from backend.app.api.routes import printers as printer_routes
        from backend.app.api.routes.print_queue import start_queue_item

        printer_actions = {
            "pause": (printer_routes.pause_print, "Pausa inviata."),
            "resume": (printer_routes.resume_print, "Ripresa inviata."),
            "stop": (printer_routes.stop_print, "Annullamento inviato."),
            "clear": (printer_routes.clear_plate, "Piatto segnato come libero: la prossima stampa parte a breve."),
        }
        if action not in (*printer_actions, "start", "enqueue"):
            return Reply("Azione sconosciuta.")
        if action != "enqueue" and not arg.isdigit():
            return Reply("Pulsante non valido.")
        async with database.async_session() as db:
            try:
                if action in printer_actions:
                    route, text = printer_actions[action]
                    await route(int(arg), _=None, db=db)
                elif action == "start":
                    await start_queue_item(int(arg), skip_filament_check=False, db=db, _=None)
                    text = "Voce sbloccata: parte appena la stampante è libera."
                else:
                    text = await self._enqueue(db, arg)
            except HTTPException as exc:
                logger.info("Telegram action %s %s from chat %s refused: %s", action, arg, chat, exc.detail)
                return Reply(f"Non fatto: {_detail(exc.detail)}")
        logger.info("Telegram action %s %s from chat %s: %s", action, arg, chat, text)
        return Reply(text)

    async def _printers(self, db) -> list:
        from backend.app.models.printer import Printer

        rows = await db.execute(select(Printer).where(Printer.is_active.is_(True)).order_by(Printer.name))
        return list(rows.scalars())

    @staticmethod
    def _find(printers: list, arg: str):
        """By id or name (whole, or the only one containing it); None when not found or ambiguous."""
        if not arg:
            return None
        if arg.isdigit():
            return next((p for p in printers if p.id == int(arg)), None)
        exact = [p for p in printers if p.name.lower() == arg.lower()]
        found = exact or [p for p in printers if arg.lower() in p.name.lower()]
        return found[0] if len(found) == 1 else None

    async def _ask_printer(self, command: str, arg: str) -> Reply:
        from backend.app.services.printer_manager import printer_manager

        action, question, states = PRINTER_COMMANDS[command]
        async with database.async_session() as db:
            printers = await self._printers(db)
        printer = self._find(printers, arg)
        if printer is None:
            fit = [p for p in printers if getattr(printer_manager.get_status(p.id), "state", None) in states]
            if not fit:
                return Reply("Nessuna stampante a cui serve.")
            return Reply("Quale stampante?", [[(p.name, f"ask:{command}:{p.id}")] for p in fit])
        return Reply(f"{question} {printer.name}?", _confirm(action, printer.id))

    async def _ask_start(self, arg: str) -> Reply:
        from backend.app.models.print_queue import PrintQueueItem

        async with database.async_session() as db:
            printers = await self._printers(db)
            printer = self._find(printers, arg)
            if arg and printer is None:
                return Reply(f"Stampante '{arg}' non trovata.")
            query = (
                select(PrintQueueItem)
                .options(selectinload(PrintQueueItem.library_file), selectinload(PrintQueueItem.archive))
                .where(PrintQueueItem.status == "pending")
                .order_by(PrintQueueItem.position, PrintQueueItem.id)
            )
            if printer is not None:
                query = query.where(PrintQueueItem.printer_id == printer.id)
            item = (await db.execute(query.limit(1))).scalars().first()
            if item is None:
                return Reply("Nessuna voce in attesa in coda.")
            where = {p.id: p.name for p in printers}.get(item.printer_id, "la prima stampante libera")
            return Reply(f"Avviare {_item_name(item)} su {where}?", _confirm("start", item.id))

    async def _ask_enqueue(self, arg: str) -> Reply:
        from backend.app.farm.products.models import FarmComponent

        code = arg.split()[0].upper() if arg else ""
        if not code:
            rows = [r for r in (await self._plan_rows())["components"] if r["known"] and r["plates"]][:5]
            if not rows:
                return Reply("Niente da accodare dal piano: scrivi /accoda CODICE.")
            return Reply("Quale componente?", [[(f"{r['code']} {r['name']}", f"ask:accoda:{r['code']}")] for r in rows])
        async with database.async_session() as db:
            comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == code))
        if comp is None or comp.retired_at is not None:
            return Reply(f"Componente {code} non trovato.")
        colour = f" ({comp.color})" if comp.color else ""
        return Reply(
            f"Accodare una stampa di {comp.code} {comp.name}{colour} sulla prima stampante libera adatta?",
            _confirm("enqueue", comp.code),
        )

    async def _enqueue(self, db, code: str) -> str:
        """Accoda's route, with the component's plates in the plan's order (first free printer that fits)."""
        from backend.app.farm.jobs.routes import JobIn, JobsIn, create_jobs
        from backend.app.farm.plan import service as plan

        row = next(iter((await plan.build_plan(db, codes=[code]))["components"]), None)
        plate_ids = [o["plate_id"] for o in (row or {}).get("plates", [])][:50]
        if not plate_ids:
            return f"Nessun piatto confermato per {code}: non accodato."
        jobs = await create_jobs(JobsIn(jobs=[JobIn(component_code=code, plate_ids=plate_ids)]), db=db)
        return f"Accodato {code} ({jobs[0]['pieces']} pezzi): parte sulla prima stampante libera adatta."

    # ---------------------------------------------------------------- reads

    async def _status(self) -> Reply:
        from backend.app.services.printer_manager import printer_manager

        async with database.async_session() as db:
            printers = await self._printers(db)
        lines, buttons = ["Stampanti:"], []
        for p in printers:
            state = printer_manager.get_status(p.id)
            if state is None or not printer_manager.is_connected(p.id):
                lines.append(f"• {p.name}: non connessa")
                continue
            what = STATES.get(state.state, str(state.state).lower())
            if state.state in ("RUNNING", "PAUSE", "PREPARE"):
                name = state.subtask_name or state.gcode_file or ""
                what += f" {state.progress:.0f}% · mancano {_duration(state.remaining_time)}"
                what += f" · {name}" if name else ""
            if printer_manager.is_awaiting_plate_clear(p.id):
                what += " · piatto da liberare"
            lines.append(f"• {p.name}: {what}")
            buttons.append((f"📷 {p.name}", f"photo:{p.id}"))
        return Reply("\n".join(lines), [buttons[i : i + 2] for i in range(0, len(buttons), 2)])

    async def _photo(self, printer_id: int) -> Reply:
        from backend.app.main import _capture_snapshot_for_notification
        from backend.app.models.printer import Printer

        async with database.async_session() as db:
            printer = await db.get(Printer, printer_id)
        if printer is None:
            return Reply("Stampante non trovata.")
        image = await _capture_snapshot_for_notification(printer_id, printer, logger)
        if not image:
            return Reply(f"Nessuna foto da {printer.name} (camera non disponibile o foto disattivate).")
        return Reply(printer.name, photo=image)

    async def _queue(self) -> Reply:
        from backend.app.models.print_queue import PrintQueueItem

        async with database.async_session() as db:
            printers = {p.id: p.name for p in await self._printers(db)}
            rows = await db.execute(
                select(PrintQueueItem)
                .options(selectinload(PrintQueueItem.library_file), selectinload(PrintQueueItem.archive))
                .where(PrintQueueItem.status.in_(("pending", "printing")))
                .order_by(PrintQueueItem.status != "printing", PrintQueueItem.position, PrintQueueItem.id)
            )
            items = list(rows.scalars())
        if not items:
            return Reply("La coda è vuota.")
        lines = [f"Coda ({len(items)}):"]
        for item in items[:20]:
            where = printers.get(item.printer_id, "prima libera")
            flag = "in stampa" if item.status == "printing" else ("avvio manuale" if item.manual_start else "in attesa")
            lines.append(f"• {_item_name(item)} – {where} – {flag}")
        if len(items) > 20:
            lines.append(f"… e altre {len(items) - 20}")
        return Reply("\n".join(lines))

    async def _plan_rows(self) -> dict:
        from backend.app.farm.plan.routes import get_plan

        async with database.async_session() as db:
            return await get_plan(db=db)

    async def _plan(self) -> Reply:
        plan = await self._plan_rows()
        rows = plan["components"][:8]
        if not rows:
            return Reply("Niente da stampare" + (f" ({plan['error']})." if plan.get("error") else "."))
        lines = ["Cosa stampare:"]
        for i, r in enumerate(rows, 1):
            colour = f" ({r['color']})" if r.get("color") else ""
            lines.append(
                f"{i}. {r['code']} {r['name']}{colour} – {r['status']} · da stampare {r['to_print']}"
                f" · in coda {r['in_queue']}"
            )
        if plan.get("error"):
            lines.append(f"(dati dell'ultima lettura: {plan['error']})")
        buttons = [[(f"➕ Accoda {r['code']}", f"ask:accoda:{r['code']}")] for r in rows if r["known"] and r["plates"]]
        return Reply("\n".join(lines), buttons[:5])


telegram_bot = TelegramBot()
