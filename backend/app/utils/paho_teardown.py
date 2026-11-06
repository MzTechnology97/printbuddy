"""Let go of a paho MQTT client without waiting for its network thread.

``Client.loop_stop()`` joins the network thread with no timeout. That thread
only checks the stop flag between loop iterations, so while it sits in a TLS
handshake with a printer that answers TCP and then trickles bytes, the join
waits for as long as the printer keeps it going. The caller is the asyncio
thread, so the whole app (UI, API, health check) stops answering
(upstream #3068).
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# A healthy network thread exits in well under a second.
_RETIRE_SLOW_SECONDS = 5.0

_CALLBACKS = ("on_connect", "on_disconnect", "on_subscribe", "on_message")


def retire_paho_client(client, label: str) -> None:
    """Shut *client* down on a thread of its own and return immediately.

    Callbacks are cleared inline first: a zombie that finishes its handshake
    later would otherwise auto-reconnect and report itself connected behind
    its replacement's back.
    """
    for attr in _CALLBACKS:
        try:
            setattr(client, attr, None)
        except Exception:
            pass

    def _teardown() -> None:
        started = time.monotonic()
        try:
            # DISCONNECT stops paho's auto-reconnect, so an unacked QoS 1
            # project_file cannot replay onto a revived session.
            client.disconnect()
        except Exception:
            pass
        try:
            client.loop_stop()
        except Exception:
            pass
        waited = time.monotonic() - started
        if waited >= _RETIRE_SLOW_SECONDS:
            logger.warning(
                "[%s] Retiring the old MQTT client took %.0fs (network thread would not stop); "
                "it had already been replaced",
                label,
                waited,
            )

    try:
        threading.Thread(target=_teardown, name=f"mqtt-retire-{label}", daemon=True).start()
    except RuntimeError as e:
        logger.error("[%s] Could not start the MQTT teardown thread: %s", label, e)
        try:
            client.disconnect()
        except Exception:
            pass
