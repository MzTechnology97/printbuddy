"""MQTT Relay Service for publishing Printbuddy events to external MQTT brokers.

This service enables integration with external automation systems like
Node-RED, Home Assistant, and other MQTT-based platforms.
"""

import asyncio
import json
import logging
import ssl
import threading
import time
from datetime import datetime, timezone
from typing import Any

import paho.mqtt.client as mqtt

from backend.app.utils.paho_teardown import retire_paho_client

logger = logging.getLogger(__name__)

# How often assigned spools are checked for low filament while the printer events are on.
FILAMENT_LOW_CHECK_SECONDS = 60.0
# A slot that sent filament/low is re-armed only this many points above its threshold, so a spool whose
# remaining weight wobbles around the threshold (AMS remain sync, rounding) publishes once, not on every wobble.
FILAMENT_LOW_REARM_MARGIN = 5.0


class MQTTRelayService:
    """Publishes Printbuddy events to an external MQTT broker."""

    # Minimum interval between status updates per printer (seconds)
    STATUS_THROTTLE_SECONDS = 1.0

    def __init__(self):
        self.client: mqtt.Client | None = None
        self.enabled = False
        self.connected = False
        self.topic_prefix = "printbuddy"
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker = ""
        self._port = 1883
        self._last_printer_status: dict[int, float] = {}  # printer_id -> last publish timestamp
        self._smart_plug_service = None  # Lazy import to avoid circular dependency
        self._settings: dict = {}  # Store settings for smart plug service
        self._disconnection_event: threading.Event | None = None
        # Printer offline/online and low filament events (upstream 1.3.0): off unless switched on.
        self.printer_events = False
        # (printer_id, ams_id, tray_id, spool_id) of the slots whose filament/low went out, until re-armed.
        self._relayed_filament_low: set[tuple[int, int, int, int]] = set()

    async def configure(self, settings: dict) -> bool:
        """Configure MQTT connection from settings.

        Returns True if connection was successful or MQTT is disabled.
        """
        self.enabled = settings.get("mqtt_enabled", False)
        self.printer_events = bool(settings.get("mqtt_printer_events", False))
        self._settings = settings  # Store for smart plug service

        if not self.enabled:
            await self.disconnect()
            # Also configure smart plug service (will disable it)
            await self._configure_smart_plug_service(settings)
            logger.info("MQTT relay disabled")
            return True

        broker = settings.get("mqtt_broker", "")
        port = settings.get("mqtt_port", 1883)
        username = settings.get("mqtt_username", "")
        password = settings.get("mqtt_password", "")
        self.topic_prefix = settings.get("mqtt_topic_prefix", "printbuddy")
        use_tls = settings.get("mqtt_use_tls", False)

        if not broker:
            logger.warning("MQTT enabled but no broker configured")
            return False

        # Store for status endpoint
        self._broker = broker
        self._port = port

        # Disconnect existing connection if settings changed
        if self.client:
            await self.disconnect()

        # Create and connect client
        result = await self._connect(broker, port, username, password, use_tls)

        # Configure smart plug service with same settings
        await self._configure_smart_plug_service(settings)

        return result

    async def _configure_smart_plug_service(self, settings: dict):
        """Configure the MQTT smart plug service with the same broker settings."""
        try:
            if self._smart_plug_service is None:
                from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service

                self._smart_plug_service = mqtt_smart_plug_service

            await self._smart_plug_service.configure(settings)
        except Exception as e:
            logger.error("Failed to configure MQTT smart plug service: %s", e)

    @property
    def smart_plug_service(self):
        """Get the MQTT smart plug service instance."""
        if self._smart_plug_service is None:
            from backend.app.services.mqtt_smart_plug import mqtt_smart_plug_service

            self._smart_plug_service = mqtt_smart_plug_service
        return self._smart_plug_service

    async def _connect(self, broker: str, port: int, username: str, password: str, use_tls: bool) -> bool:
        """Establish MQTT connection."""
        try:
            # Create client with callback API version 2 (use MQTTv311 for broader compatibility)
            self.client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=f"printbuddy-{id(self)}",
                protocol=mqtt.MQTTv311,
            )

            # Set up callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect

            # Configure authentication
            if username:
                self.client.username_pw_set(username, password)

            # Configure TLS (allow self-signed certs for testing)
            if use_tls:
                self.client.tls_set(cert_reqs=ssl.CERT_NONE)
                self.client.tls_insecure_set(True)  # Allow self-signed certs

            # Run connect_async in thread pool with timeout to avoid blocking
            # on unreachable brokers (connect_async does synchronous socket creation)
            try:
                await asyncio.wait_for(asyncio.to_thread(self.client.connect_async, broker, port, 60), timeout=3.0)
            except TimeoutError:
                logger.warning("MQTT relay connection to %s:%s timed out", broker, port)
                return False

            self.client.loop_start()

            # Wait briefly for connection callback
            await asyncio.sleep(1.0)

            if self.connected:
                logger.info("MQTT relay connected to %s:%s", broker, port)
                # Publish online status
                self._publish_status("online")
                return True
            else:
                logger.warning("MQTT relay connection pending to %s:%s", broker, port)
                return True  # Connection is async, may succeed later

        except Exception as e:
            logger.error("MQTT relay connection failed: %s", e)
            self.connected = False
            return False

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: dict,
        reason_code: int | mqtt.ReasonCode,
        properties: mqtt.Properties | None = None,
    ):
        """Callback when connected to broker."""
        # Handle both MQTTv311 (int) and MQTTv5 (ReasonCode) return codes
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
        if rc == 0:
            self.connected = True
            logger.info("MQTT relay connected successfully")
            # Publish online status
            self._publish_status("online")
        else:
            self.connected = False
            logger.error("MQTT relay connection failed: %s", reason_code)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags_or_rc: dict | int | mqtt.ReasonCode,
        reason_code: int | mqtt.ReasonCode | None = None,
        properties: mqtt.Properties | None = None,
    ):
        """Callback when disconnected from broker."""
        self.connected = False
        # Handle both MQTTv311 (rc as 3rd param) and MQTTv5 (flags, rc, props)
        rc = reason_code if reason_code is not None else flags_or_rc
        rc_val = rc if isinstance(rc, int) else getattr(rc, "value", 0)
        if rc_val != 0:
            logger.warning("MQTT relay disconnected: %s", rc)
        else:
            logger.info("MQTT relay disconnected cleanly")
        if self._disconnection_event:
            self._disconnection_event.set()

    async def disconnect(self, timeout: float = 0):
        """Disconnect from MQTT broker."""
        if self.client:
            try:
                # Publish offline status before disconnecting
                self._publish_status("offline")
                self._disconnection_event = threading.Event()
                self.client.disconnect()
                await asyncio.to_thread(self._disconnection_event.wait, timeout=timeout)
                retire_paho_client(self.client, "relay")
            except Exception as e:
                logger.debug("MQTT disconnect error (ignored): %s", e)
            finally:
                self.client = None
                self.connected = False

    def _publish_status(self, status: str):
        """Publish Printbuddy status (online/offline)."""
        self._publish(
            f"{self.topic_prefix}/status",
            {"status": status, "timestamp": datetime.now(timezone.utc).isoformat()},
            retain=True,
        )

    def _publish(self, topic: str, payload: dict, retain: bool = False):
        """Publish message to MQTT broker."""
        if not self.client or not self.connected:
            return

        try:
            with self._lock:
                self.client.publish(topic, json.dumps(payload, default=str), qos=1, retain=retain)
        except Exception as e:
            logger.debug("MQTT publish error: %s", e)

    @property
    def printer_events_ready(self) -> bool:
        """The printer offline/online and low filament events are switched on and can be sent now."""
        return bool(self.enabled and self.connected and self.printer_events)

    def get_status(self) -> dict:
        """Get current MQTT relay status for API."""
        return {
            "enabled": self.enabled,
            "connected": self.connected,
            "broker": self._broker if self.enabled else "",
            "port": self._port if self.enabled else 0,
            "topic_prefix": self.topic_prefix,
        }

    # =========================================================================
    # Printer Events
    # =========================================================================

    async def on_printer_status(self, printer_id: int, state: Any, printer_name: str, printer_serial: str):
        """Publish printer status change (throttled to 1 update/sec per printer)."""
        if not self.enabled or not self.connected:
            return

        # Throttle status updates to avoid flooding MQTT broker
        now = time.time()
        last_publish = self._last_printer_status.get(printer_id, 0)
        if now - last_publish < self.STATUS_THROTTLE_SECONDS:
            return  # Skip this update, too soon since last publish
        self._last_printer_status[printer_id] = now

        # Build status payload from PrinterState
        payload = {
            "printer_id": printer_id,
            "printer_name": printer_name,
            "printer_serial": printer_serial,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "connected": state.connected,
            "state": state.state,
            "progress": state.progress,
            "remaining_time": state.remaining_time,
            "layer_num": state.layer_num,
            "total_layers": state.total_layers,
            "current_print": state.current_print,
            "subtask_name": state.subtask_name,
            "gcode_file": state.gcode_file,
            "temperatures": state.temperatures,
            "wifi_signal": state.wifi_signal,
            "chamber_light": state.chamber_light,
            "speed_level": state.speed_level,
            "cooling_fan_speed": state.cooling_fan_speed,
            "big_fan1_speed": state.big_fan1_speed,
            "big_fan2_speed": state.big_fan2_speed,
            "heatbreak_fan_speed": state.heatbreak_fan_speed,
        }

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/status",
            payload,
            retain=True,
        )

    async def on_printer_online(self, printer_id: int, printer_name: str, printer_serial: str):
        """Publish that a printer reported offline is back (only after a published offline, see main.py)."""
        if not self.printer_events_ready:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/online",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_printer_offline(self, printer_id: int, printer_name: str, printer_serial: str):
        """Publish that a printer has been offline past the grace; never for our own power-off (see main.py)."""
        if not self.printer_events_ready:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/offline",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_print_start(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        filename: str,
        subtask_name: str,
    ):
        """Publish print started event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/print/started",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "filename": filename,
                "subtask_name": subtask_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_print_complete(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        filename: str,
        subtask_name: str,
        status: str,
    ):
        """Publish print completed event."""
        if not self.enabled or not self.connected:
            return

        # Determine topic based on status
        if status == "completed":
            topic = f"{self.topic_prefix}/printers/{printer_serial}/print/completed"
        else:
            topic = f"{self.topic_prefix}/printers/{printer_serial}/print/failed"

        self._publish(
            topic,
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "filename": filename,
                "subtask_name": subtask_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_ams_change(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        ams_data: list,
    ):
        """Publish AMS filament change event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/ams/changed",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "ams_units": ams_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_printer_error(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        errors: list,
    ):
        """Publish printer HMS error event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/error",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "errors": errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Print Queue Events
    # =========================================================================

    async def on_queue_job_added(
        self,
        job_id: int,
        filename: str,
        printer_id: int | None,
        printer_name: str | None,
    ):
        """Publish job added to queue event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/queue/job_added",
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_queue_job_started(
        self,
        job_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
    ):
        """Publish queued job started printing event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/queue/job_started",
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_queue_job_completed(
        self,
        job_id: int,
        filename: str,
        printer_id: int,
        printer_name: str,
        status: str,
    ):
        """Publish queued job finished event."""
        if not self.enabled or not self.connected:
            return

        topic = (
            f"{self.topic_prefix}/queue/job_completed"
            if status == "completed"
            else f"{self.topic_prefix}/queue/job_failed"
        )

        self._publish(
            topic,
            {
                "job_id": job_id,
                "filename": filename,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Maintenance Events
    # =========================================================================

    async def on_maintenance_alert(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
        current_value: float,
        threshold: float,
    ):
        """Publish maintenance alert triggered event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/alert",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "current_value": current_value,
                "threshold": threshold,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_maintenance_acknowledged(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
    ):
        """Publish maintenance alert acknowledged event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/acknowledged",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_maintenance_reset(
        self,
        printer_id: int,
        printer_name: str,
        maintenance_type: str,
    ):
        """Publish maintenance counter reset event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/maintenance/reset",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "maintenance_type": maintenance_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Archive Events
    # =========================================================================

    async def on_archive_created(
        self,
        archive_id: int,
        print_name: str,
        printer_name: str,
        status: str,
    ):
        """Publish print archived event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/archive/created",
            {
                "archive_id": archive_id,
                "print_name": print_name,
                "printer_name": printer_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_archive_updated(
        self,
        archive_id: int,
        print_name: str,
        status: str,
    ):
        """Publish archive record updated event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/archive/updated",
            {
                "archive_id": archive_id,
                "print_name": print_name,
                "status": status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    # =========================================================================
    # Filament/Spoolman Events
    # =========================================================================

    async def on_filament_low(
        self,
        printer_id: int,
        printer_name: str,
        printer_serial: str,
        slot: str,
        remaining_percent: int,
        color: str | None,
        spool_id: int,
    ):
        """Publish that the spool in a slot crossed its low-stock threshold (once per crossing)."""
        if not self.printer_events_ready:
            return

        self._publish(
            f"{self.topic_prefix}/printers/{printer_serial}/filament/low",
            {
                "printer_id": printer_id,
                "printer_name": printer_name,
                "printer_serial": printer_serial,
                "slot": slot,
                "remaining_percent": remaining_percent,
                "color": color,
                "spool_id": spool_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def check_filament_low(self) -> None:
        """Publish filament/low for each assigned spool newly below its low-stock threshold (upstream #2913's rule).

        Remaining = label weight - used weight of the spool assigned to the slot (inventory or Spoolman), against the
        spool's own threshold or the global ``low_stock_threshold``, as on the Inventory page; the AMS remain % is
        not used. The record survives a broker drop (a reconnect re-sends nothing) and is forgotten when the events
        are switched off, so switching them on reports what is low now.
        """
        if not (self.enabled and self.printer_events):
            self._relayed_filament_low.clear()
            return
        if not self.connected:
            return
        from backend.app.core.database import async_session
        from backend.app.models.printer import Printer
        from backend.app.services.spool_assignment_notifications import (
            _global_tray_from_assignment,
            _slot_label_from_global_tray,
        )

        async with async_session() as db:
            slots = await _filament_low_slots(db)
            printers: dict[int, Printer | None] = {}
            for key, (pct, threshold, color) in slots.items():
                printer_id, ams_id, tray_id, spool_id = key
                if pct >= threshold + FILAMENT_LOW_REARM_MARGIN:
                    self._relayed_filament_low.discard(key)
                    continue
                if pct >= threshold or key in self._relayed_filament_low:
                    continue
                if printer_id not in printers:
                    printers[printer_id] = await db.get(Printer, printer_id)
                printer = printers[printer_id]
                if printer is None:
                    continue
                self._relayed_filament_low.add(key)
                slot = _slot_label_from_global_tray(_global_tray_from_assignment(ams_id, tray_id))
                await self.on_filament_low(
                    printer_id, printer.name, printer.serial_number, slot, int(pct), color, spool_id
                )

    async def run_filament_low_checks(self) -> None:
        """Background loop for check_filament_low (started in main's lifespan)."""
        while True:
            try:
                await self.check_filament_low()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("MQTT relay low-filament check failed: %s", e)
            await asyncio.sleep(FILAMENT_LOW_CHECK_SECONDS)

    # =========================================================================
    # Smart Plug Events
    # =========================================================================

    async def on_smart_plug_state(
        self,
        plug_id: int,
        plug_name: str,
        state: str,
        printer_id: int | None = None,
        printer_name: str | None = None,
    ):
        """Publish smart plug state change event."""
        if not self.enabled or not self.connected:
            return

        topic = f"{self.topic_prefix}/smart_plugs/on" if state == "on" else f"{self.topic_prefix}/smart_plugs/off"

        self._publish(
            topic,
            {
                "plug_id": plug_id,
                "plug_name": plug_name,
                "state": state,
                "printer_id": printer_id,
                "printer_name": printer_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_smart_plug_energy(
        self,
        plug_id: int,
        plug_name: str,
        power: float,
        energy_today: float,
        energy_total: float,
    ):
        """Publish smart plug energy update event."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/smart_plugs/energy",
            {
                "plug_id": plug_id,
                "plug_name": plug_name,
                "power_watts": power,
                "energy_today_kwh": energy_today,
                "energy_total_kwh": energy_total,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_slot_assignment_completed(
        self,
        assignment_id: int,
        tray_uuid: str | None,
        tag_uid: str | None,
        spool_id: int,
        printer_id: int,
        ams_id: int,
        tray_id: int,
        time_to_placement: float,
    ):
        """Publish event when a pending slot assignment is completed."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/inventory/slot_assignment/completed",
            {
                "assignment_id": assignment_id,
                "tray_uuid": tray_uuid,
                "tag_uid": tag_uid,
                "spool_id": spool_id,
                "printer_id": printer_id,
                "ams_id": ams_id,
                "tray_id": tray_id,
                "time_to_placement": time_to_placement,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def on_slot_assignment_timed_out(
        self,
        assignment_id: int,
        tray_uuid: str | None,
        tag_uid: str | None,
    ):
        """Publish event when a pending slot assignment times out."""
        if not self.enabled or not self.connected:
            return

        self._publish(
            f"{self.topic_prefix}/inventory/slot_assignment/timed_out",
            {
                "assignment_id": assignment_id,
                "tray_uuid": tray_uuid,
                "tag_uid": tag_uid,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )


def _remaining_percent(label_weight: float | None, weight_used: float | None) -> float | None:
    """Remaining filament in % of the label weight, as the Inventory page counts it; None without a label weight."""
    if not label_weight or label_weight <= 0:
        return None
    return max(0.0, float(label_weight) - float(weight_used or 0.0)) / float(label_weight) * 100.0


async def _filament_low_slots(db) -> dict[tuple[int, int, int, int], tuple[float, float, str | None]]:
    """(printer_id, ams_id, tray_id, spool_id) -> (remaining %, threshold %, colour name) of every assigned spool."""
    from sqlalchemy import select

    from backend.app.api.routes.settings import get_setting
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment

    try:
        global_threshold = float(await get_setting(db, "low_stock_threshold") or 20.0)
    except ValueError:
        global_threshold = 20.0
    if not 0 < global_threshold <= 100:
        global_threshold = 20.0

    slots: dict[tuple[int, int, int, int], tuple[float, float, str | None]] = {}
    if (await get_setting(db, "spoolman_enabled") or "").lower() == "true":
        from backend.app.api.routes._spoolman_helpers import _map_spoolman_spool
        from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
        from backend.app.services.spoolman import SpoolmanUnavailableError, get_spoolman_client

        assignments = (await db.execute(select(SpoolmanSlotAssignment))).scalars().all()
        client = await get_spoolman_client() if assignments else None
        if client is None:
            return slots
        try:
            by_id = {raw.get("id"): raw for raw in await client.get_all_spools()}  # archived spools excluded
        except SpoolmanUnavailableError as e:
            logger.debug("Low-filament check skipped, Spoolman unreachable: %s", e)
            return slots
        for a in assignments:
            raw = by_id.get(a.spoolman_spool_id)
            if raw is None:
                continue
            try:
                mapped = _map_spoolman_spool(raw)
            except ValueError:
                continue
            pct = _remaining_percent(mapped.get("label_weight"), mapped.get("weight_used"))
            if pct is not None:  # Spoolman spools have no per-spool threshold
                slots[(a.printer_id, a.ams_id, a.tray_id, a.spoolman_spool_id)] = (
                    pct,
                    global_threshold,
                    mapped.get("color_name"),
                )
        return slots

    rows = await db.execute(
        select(SpoolAssignment, Spool)
        .join(Spool, SpoolAssignment.spool_id == Spool.id)
        .where(Spool.archived_at.is_(None))  # an archived spool is not stock
    )
    for a, spool in rows.all():
        pct = _remaining_percent(spool.label_weight, spool.weight_used)
        if pct is not None:
            slots[(a.printer_id, a.ams_id, a.tray_id, spool.id)] = (
                pct,
                float(spool.low_stock_threshold_pct or global_threshold),
                spool.color_name,
            )
    return slots


# Global instance
mqtt_relay = MQTTRelayService()
