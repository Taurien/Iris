"""Tiny client the GNOME custom keyboard shortcut invokes:
    python -m iris.ptt_client toggle   # one hotkey: press to start, press again to stop
    python -m iris.ptt_client start
    python -m iris.ptt_client stop
"""
import json
import socket
import sys
import urllib.request

from .ptt_daemon import SOCKET_PATH

STATUS_URL = "http://127.0.0.1:8787/status"


def _send(cmd: str) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(SOCKET_PATH))
        s.sendall((cmd + "\n").encode())


def _resolve_toggle() -> str:
    # GNOME custom shortcuts fire once per press (no separate release event),
    # so a single hotkey needs to ask the daemon's current state to know
    # whether this press means "start" or "stop".
    with urllib.request.urlopen(STATUS_URL, timeout=2) as resp:
        state = json.load(resp)["state"]
    return "STOP" if state == "listening" else "START"


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("start", "stop", "toggle"):
        print("usage: python -m iris.ptt_client start|stop|toggle", file=sys.stderr)
        sys.exit(1)

    arg = sys.argv[1]
    cmd = _resolve_toggle() if arg == "toggle" else arg.upper()
    _send(cmd)


if __name__ == "__main__":
    main()
