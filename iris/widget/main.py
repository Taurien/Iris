#!/usr/bin/env python3
"""Iris's always-visible desktop presence. Standalone — runs under the
system Python (needs system GTK4/PyGObject, not the project's uv venv),
and only talks to the rest of Iris over the local HTTP API (never imports
the `iris` package directly), so it has no dependency on the venv at all.

GNOME on Wayland doesn't implement wlr-layer-shell and GTK4 dropped
set_keep_above()/set_skip_taskbar_hint() entirely — so, like Coucou does on
the same desktop environment, this runs under XWayland (GDK_BACKEND=x11,
set by the launcher script) and uses `wmctrl` after the window is shown to
get real always-on-top/sticky/skip-taskbar behavior and to pin its position.

Shaped and animated after Coucou's actual notch geometry (read from its
source, windows/src/core/layout.ts: NOTCH_H=32, ROUNDED_CORNER=14, solid
#000 background) rather than guessed proportions — a flat wide bar flush
against the top edge with a FIXED height, not a tall narrow pill. Height
staying constant is why content is a single row (eye + one state label)
instead of two stacked lines: stacking them made the whole bar as tall as
its text needed, which is what made the first version look like a long
vertical capsule instead of a notch. Width is animated by hand each frame
(set_size_request + reposition) rather than with Gtk.Revealer, both to get
a slight spring-like overshoot (ease-out-back, same shape as Coucou's
spring-driven width/height) and because Revealer's height footprint always
matches its un-collapsed content for horizontal slide types, which is
exactly the bug above.

The colored dot is a Cairo-drawn eye (iris + pupil + eyelid) instead of a
flat CSS circle — "Iris" is literally the colored part of the eye, and it
gives the idle state somewhere to put small personality touches (blinking,
glancing) the way Coucou's Mochi does, without needing actual character
art. Expression parameters (color/pupil offset/eyelid openness) are plain
instance attributes that `_draw_eye` renders as-is; blink/glance/pulse are
small self-rescheduling GLib timeouts that only run in "idle" (each checks
`self._state != "idle": return False` as its first line and gets restarted
whenever the widget re-enters idle), so they never fight with an active
state's fixed expression.
"""
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import cairo  # noqa: F401  (registers the cairo.Context foreign struct for PyGObject)
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

STATUS_URL = os.environ.get("IRIS_WIDGET_STATUS_URL", "http://127.0.0.1:8787/status")
POLL_INTERVAL_MS = 1000
WINDOW_TITLE = os.environ.get("IRIS_WIDGET_TITLE", "Iris")

BAR_HEIGHT = 34  # Coucou's NOTCH_H (32) plus 2px for our slightly larger font
ANIM_DURATION_MS = 260
ANIM_TICK_MS = 16

STATE_LABEL = {
    "idle": "en espera",
    "listening": "escuchando…",
    "thinking": "pensando…",
    "speaking": "hablando…",
}
# "error" is deliberately NOT in here or in EXPANDED_STATES: the user chose
# "just the face, no text" for it — the bar stays at idle width, only the
# eye's expression changes.
EXPANDED_STATES = {"listening", "thinking", "speaking"}

# ── Eye / expression ──────────────────────────────────────────────────────

EYE_DIAMETER = 20
NOTCH_BG = (0.0, 0.0, 0.0)  # must match .notch-shell's background-color below

EXPRESSION = {
    "idle": {"color": "#6b7079", "pupil": (0.0, 0.0), "openness": 1.0},
    "listening": {"color": "#3B9EFF", "pupil": (0.0, 0.0), "openness": 1.0},
    "thinking": {"color": "#A78BFA", "pupil": (0.2, -0.35), "openness": 0.85},
    "speaking": {"color": "#34D399", "pupil": (0.0, 0.0), "openness": 1.0},
    "error": {"color": "#F4505E", "pupil": (0.0, 0.25), "openness": 0.5},
}
# Subtle cosmetic tint of the base "thinking"/"speaking" color depending on
# which backend answered (iris/brain/__init__.py's `detail=backend_name`) —
# purely decorative, falls back to the plain state color if unrecognized.
BACKEND_TINT = {
    "claude-code": "#A78BFA",
    "antigravity": "#8E8CFB",
    "opencode": "#A79EC8",
}
TINT_STATES = {"thinking", "speaking"}

ERROR_HOLD_MS = 4000  # minimum time the widget keeps showing "error" once seen

BLINK_MIN_MS, BLINK_MAX_MS = 2500, 7000
BLINK_DURATION_MS = 160
GLANCE_MIN_MS, GLANCE_MAX_MS = 4000, 12000
GLANCE_OFFSET = 0.25
GLANCE_EASE_MS = 200
GLANCE_HOLD_MS = 600
PULSE_TICK_MS = 50
PULSE_PERIOD_MS = 3500
PULSE_AMPLITUDE = 0.04

CSS = b"""
window.iris-notch {
    background-color: transparent;
}
.notch-shell {
    background-color: #000000;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.08);
    padding: 0 14px;
    border-top-left-radius: 0;
    border-top-right-radius: 0;
    border-bottom-left-radius: 14px;
    border-bottom-right-radius: 14px;
}
.iris-state {
    color: #e8e9ec;
    font-size: 12.5px;
    font-weight: 500;
}
"""


def _primary_monitor_geometry() -> tuple[int, int, int, int]:
    """(x, y, width, height) of the primary monitor, via xrandr."""
    try:
        out = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return 0, 0, 1920, 1080

    primary = re.search(r"connected primary (\d+)x(\d+)\+(\d+)\+(\d+)", out)
    match = primary or re.search(r"connected (\d+)x(\d+)\+(\d+)\+(\d+)", out)
    if not match:
        return 0, 0, 1920, 1080
    w, h, x, y = (int(v) for v in match.groups())
    return x, y, w, h


def _ease_out_back(t: float) -> float:
    """Same gentle-overshoot shape as Coucou's spring-driven width/height
    (Tracked.springTo in island.ts) without simulating real spring physics."""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


class IrisWidget(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=WINDOW_TITLE)
        self.add_css_class("iris-notch")
        self.set_decorated(False)
        self.set_resizable(False)

        self._state = "idle"
        self._detail: str | None = None
        self._error_hold_until: float | None = None
        self._mon_x, self._mon_y, self._mon_w, self._mon_h = _primary_monitor_geometry()

        # Expression render params — _draw_eye is a pure function of these.
        self._color = (0.0, 0.0, 0.0)
        self._pupil_dx = 0.0
        self._pupil_dy = 0.0
        self._openness = 1.0
        self._pulse_phase = 0.0

        self.shell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.shell.add_css_class("notch-shell")
        self.shell.set_overflow(Gtk.Overflow.HIDDEN)

        self.eye = Gtk.DrawingArea(valign=Gtk.Align.CENTER)
        self.eye.set_content_width(EYE_DIAMETER)
        self.eye.set_content_height(EYE_DIAMETER)
        self.eye.set_draw_func(self._draw_eye, None)
        self.shell.append(self.eye)

        self.label = Gtk.Label(label=STATE_LABEL["idle"], xalign=0, valign=Gtk.Align.CENTER)
        self.label.add_css_class("iris-state")
        self.label.set_visible(False)
        self.shell.append(self.label)

        self.set_child(self.shell)
        self.connect("show", self._on_show)

        self._apply_expression("idle", None)
        self._start_idle_personality()

        # Start already sized for idle, instead of animating from whatever
        # natural size GTK would otherwise pick before the first poll.
        self.shell.set_size_request(self._measure_target_width(expanded=False), BAR_HEIGHT)

        GLib.timeout_add(POLL_INTERVAL_MS, self._poll_status)

    # ── Eye drawing ───────────────────────────────────────────────────────

    def _draw_eye(self, _area, cr, width, height, _data) -> None:
        cx, cy = width / 2, height / 2
        scale = 1.0 + PULSE_AMPLITUDE * math.sin(self._pulse_phase) if self._state == "idle" else 1.0
        iris_r = (EYE_DIAMETER / 2 - 1) * scale

        r, g, b = self._color
        cr.set_source_rgb(r, g, b)
        cr.arc(cx, cy, iris_r, 0, 2 * math.pi)
        cr.fill()

        pupil_r = iris_r * 0.4
        px = cx + self._pupil_dx * iris_r
        py = cy + self._pupil_dy * iris_r
        cr.set_source_rgb(r * 0.35, g * 0.35, b * 0.35)
        cr.arc(px, py, pupil_r, 0, 2 * math.pi)
        cr.fill()

        lid_h = (1.0 - self._openness) * (height / 2)
        if lid_h > 0:
            cr.set_source_rgb(*NOTCH_BG)
            cr.rectangle(0, 0, width, lid_h)
            cr.fill()
            cr.rectangle(0, height - lid_h, width, lid_h)
            cr.fill()

    def _apply_expression(self, state: str, detail: str | None) -> None:
        expr = EXPRESSION.get(state, EXPRESSION["idle"])
        color = expr["color"]
        if state in TINT_STATES and detail in BACKEND_TINT:
            color = BACKEND_TINT[detail]
        self._color = _hex_to_rgb(color)
        self._pupil_dx, self._pupil_dy = expr["pupil"]
        self._openness = expr["openness"]
        self.eye.queue_draw()

    # ── Small scalar animator, reused by blink (openness) and glance (pupil) ──

    def _animate_attr(self, attr_name: str, target: float, duration_ms: float, on_done=None) -> None:
        start = getattr(self, attr_name)
        start_time = GLib.get_monotonic_time() / 1000

        def tick() -> bool:
            elapsed = GLib.get_monotonic_time() / 1000 - start_time
            t = min(1.0, elapsed / duration_ms)
            setattr(self, attr_name, start + (target - start) * t)
            self.eye.queue_draw()
            if t >= 1.0:
                if on_done:
                    on_done()
                return False
            return True

        GLib.timeout_add(ANIM_TICK_MS, tick)

    # ── Idle-only personality: blink, glance, breathing pulse ──────────────
    # Each loop below is self-cancelling (returns False / stops rescheduling
    # the moment self._state != "idle") and must be explicitly restarted
    # whenever the widget re-enters idle — see _poll_status.

    def _start_idle_personality(self) -> None:
        self._schedule_blink()
        self._schedule_glance()
        GLib.timeout_add(PULSE_TICK_MS, self._pulse_tick)

    def _schedule_blink(self) -> None:
        if self._state != "idle":
            return
        GLib.timeout_add(int(random.uniform(BLINK_MIN_MS, BLINK_MAX_MS)), self._do_blink)

    def _do_blink(self) -> bool:
        if self._state != "idle":
            return False
        half = BLINK_DURATION_MS / 2
        self._animate_attr(
            "_openness", 0.0, half,
            on_done=lambda: self._animate_attr("_openness", 1.0, half, on_done=self._schedule_blink),
        )
        return False

    def _schedule_glance(self) -> None:
        if self._state != "idle":
            return
        GLib.timeout_add(int(random.uniform(GLANCE_MIN_MS, GLANCE_MAX_MS)), self._do_glance)

    def _do_glance(self) -> bool:
        if self._state != "idle":
            return False
        dx = random.uniform(-GLANCE_OFFSET, GLANCE_OFFSET)
        dy = random.uniform(-GLANCE_OFFSET, GLANCE_OFFSET)
        self._animate_attr("_pupil_dx", dx, GLANCE_EASE_MS)
        self._animate_attr("_pupil_dy", dy, GLANCE_EASE_MS, on_done=self._hold_then_return_gaze)
        return False

    def _hold_then_return_gaze(self) -> None:
        GLib.timeout_add(GLANCE_HOLD_MS, self._return_gaze)

    def _return_gaze(self) -> bool:
        if self._state != "idle":
            return False
        self._animate_attr("_pupil_dx", 0.0, GLANCE_EASE_MS)
        self._animate_attr("_pupil_dy", 0.0, GLANCE_EASE_MS, on_done=self._schedule_glance)
        return False

    def _pulse_tick(self) -> bool:
        if self._state != "idle":
            return False
        self._pulse_phase += 2 * math.pi * (PULSE_TICK_MS / PULSE_PERIOD_MS)
        self.eye.queue_draw()
        return True

    # ── Status polling / width animation ───────────────────────────────────

    def _measure_target_width(self, expanded: bool) -> int:
        self.label.set_visible(expanded)
        # set_size_request from a previous animation forces a minimum that
        # measure() can't report *below* — clear it first, or "natural" width
        # would just echo back whatever we last forced (collapse would never
        # shrink past the last expanded width).
        self.shell.set_size_request(-1, -1)
        _, natural, _, _ = self.shell.measure(Gtk.Orientation.HORIZONTAL, -1)
        return natural

    def _poll_status(self) -> bool:
        try:
            with urllib.request.urlopen(STATUS_URL, timeout=2) as resp:
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError):
            data = {"state": "idle"}

        state = data.get("state", "idle")
        detail = data.get("detail")

        now = time.monotonic()
        if state == "error":
            self._error_hold_until = now + ERROR_HOLD_MS / 1000
            effective_state = "error"
        elif state == "idle" and self._error_hold_until is not None and now < self._error_hold_until:
            effective_state = "error"  # backend already reverted, we keep holding the face
        else:
            self._error_hold_until = None
            effective_state = state

        state_changed = effective_state != self._state
        detail_changed = detail != self._detail

        if state_changed or detail_changed:
            self._apply_expression(effective_state, detail)
        self._detail = detail

        if state_changed:
            entering_idle = effective_state == "idle" and self._state != "idle"
            self._state = effective_state
            if entering_idle:
                self._start_idle_personality()

            expanded = effective_state in EXPANDED_STATES
            self.label.set_text(STATE_LABEL.get(effective_state, effective_state))
            target_w = self._measure_target_width(expanded)
            if expanded:
                self.label.set_visible(True)  # grow with the label already clip-revealed
            self._animate_width(target_w)
        return True  # keep polling

    def _animate_width(self, target_w: int) -> None:
        start_w = self.get_width() or target_w
        elapsed = 0

        def tick() -> bool:
            nonlocal elapsed
            elapsed += ANIM_TICK_MS
            t = min(1.0, elapsed / ANIM_DURATION_MS)
            w = round(start_w + (target_w - start_w) * _ease_out_back(t))
            self.shell.set_size_request(max(w, 1), BAR_HEIGHT)
            self._reposition()
            if t >= 1.0:
                self.label.set_visible(self._state in EXPANDED_STATES)
                return False
            return True

        GLib.timeout_add(ANIM_TICK_MS, tick)

    def _on_show(self, *_args) -> None:
        GLib.timeout_add(300, self._pin_window)

    def _pin_window(self) -> bool:
        if not shutil.which("wmctrl"):
            return False  # nothing we can do without it; window stays default-placed
        subprocess.run(
            ["wmctrl", "-r", WINDOW_TITLE, "-b", "add,above,sticky,skip_taskbar"],
            check=False,
        )
        self._reposition()
        return False  # one-shot

    def _reposition(self) -> None:
        width = self.get_width()
        if width <= 1:
            return
        x = self._mon_x + (self._mon_w - width) // 2
        y = self._mon_y
        subprocess.run(
            ["wmctrl", "-r", WINDOW_TITLE, "-e", f"0,{x},{y},-1,-1"], check=False
        )


def main() -> None:
    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    app = Gtk.Application(application_id="co.iris.widget")
    app.connect("activate", lambda a: IrisWidget(a).present())
    app.run(None)


if __name__ == "__main__":
    main()
