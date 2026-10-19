"""Shared live-state channel. The desktop widget, the Telegram mini app, and
(eventually) the Android app all read this via the HTTP server in web.py —
none of them talk to the daemon's internals directly."""
import time

# How long callers hold "error" before reverting to "idle", so the widget's
# 1s poll has at least one real chance to observe it on the wire.
ERROR_MIN_VISIBLE_SEC = 1.5

_state = {
    "state": "idle",  # idle | listening | thinking | speaking | error
    "channel": None,  # "Telegram" | "Voz local" | "Voz (Telegram)" | None
    "since": time.time(),
    "detail": None,
}


def set_state(state: str, channel: str | None = None, detail: str | None = None) -> None:
    _state["state"] = state
    _state["channel"] = channel
    _state["since"] = time.time()
    _state["detail"] = detail


def get_state() -> dict:
    return dict(_state)
