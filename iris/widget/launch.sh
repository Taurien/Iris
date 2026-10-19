#!/usr/bin/env bash
# Forces XWayland so GTK4's window-manager hints (always-on-top, skip-taskbar)
# actually work via wmctrl — GNOME on native Wayland doesn't support them.
export GDK_BACKEND=x11
exec python3 "$(dirname "$0")/main.py"
