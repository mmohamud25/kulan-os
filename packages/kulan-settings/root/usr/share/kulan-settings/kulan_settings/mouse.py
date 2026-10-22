"""Mouse and touchpad: pointer speed, scrolling direction, tap to click."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, Gtk

from .common import Page, choice_row, has_key, reset_keys, settings, switch_row

MOUSE = "org.gnome.desktop.peripherals.mouse"
TOUCHPAD = "org.gnome.desktop.peripherals.touchpad"
INTERFACE = "org.gnome.desktop.interface"

MOUSE_KEYS = ["speed", "natural-scroll", "left-handed", "accel-profile"]
TOUCHPAD_KEYS = ["speed", "natural-scroll", "tap-to-click", "two-finger-scrolling-enabled", "edge-scrolling-enabled",
                 "disable-while-typing", "send-events", "click-method", "tap-and-drag"]


def has_touchpad() -> bool:
    """True when the kernel lists a touchpad (laptops); desktops skip that group."""
    try:
        text = Path("/proc/bus/input/devices").read_text(errors="replace").lower()
    except OSError:
        return True  # can't tell: show it rather than hide a real touchpad
    return "touchpad" in text or "trackpad" in text or "synaptics" in text or "elan" in text


def speed_row(gsettings: Gio.Settings | None, title: str) -> Adw.ActionRow | None:
    """A slider from slow to fast for a speed key (-1 to 1)."""
    if not has_key(gsettings, "speed"):
        return None
    row = Adw.ActionRow(title=title)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, 0.05)
    scale.set_hexpand(True)
    scale.set_size_request(220, -1)
    scale.set_draw_value(False)
    scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
    scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
    gsettings.bind("speed", scale.get_adjustment(), "value", Gio.SettingsBindFlags.DEFAULT)
    slow = Gtk.Label(label="Slow", css_classes=["dim-label", "caption"])
    fast = Gtk.Label(label="Fast", css_classes=["dim-label", "caption"])
    box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
    box.append(slow)
    box.append(scale)
    box.append(fast)
    row.add_suffix(box)
    return row


class MousePage(Page):
    title = "Mouse and touchpad"
    icon = "input-mouse-symbolic"
    keywords = ("mouse", "touchpad", "trackpad", "pointer", "cursor", "scroll", "natural scrolling", "tap to click",
                "left-handed", "speed", "acceleration", "click")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.mouse = settings(MOUSE)
        self.touchpad = settings(TOUCHPAD)
        self.interface = settings(INTERFACE)

        general = self.add_group("General")
        if has_key(self.mouse, "left-handed"):
            general.add(choice_row("Primary button", "The button you click with",
                                   [("left", "Left"), ("right", "Right")],
                                   lambda: "right" if self.mouse.get_boolean("left-handed") else "left",
                                   lambda v: self.mouse.set_boolean("left-handed", v == "right")))
        self.add_rows(general, switch_row(self.interface, "locate-pointer", "Find the pointer",
                                          "Press Ctrl to show where the pointer is"))

        mouse = self.add_group("Mouse")
        self.add_rows(
            mouse,
            speed_row(self.mouse, "Pointer speed"),
            self.accel_row(),
            switch_row(self.mouse, "natural-scroll", "Natural scrolling",
                       "Content moves with your fingers, as on a phone"),
        )

        if self.touchpad is not None and has_touchpad():
            pad = self.add_group("Touchpad")
            if has_key(self.touchpad, "send-events"):
                pad.add(choice_row("Touchpad", None,
                                   [("enabled", "On"), ("disabled-on-external-mouse", "Off while a mouse is plugged in"),
                                    ("disabled", "Off")],
                                   lambda: self.touchpad.get_string("send-events"),
                                   lambda v: self.touchpad.set_string("send-events", v)))
            self.add_rows(
                pad,
                speed_row(self.touchpad, "Pointer speed"),
                switch_row(self.touchpad, "tap-to-click", "Tap to click", "Tap the touchpad instead of pressing it"),
                switch_row(self.touchpad, "tap-and-drag", "Tap and drag",
                           "Tap twice and keep the finger down to drag"),
                switch_row(self.touchpad, "natural-scroll", "Natural scrolling",
                           "Content moves with your fingers, as on a phone"),
                switch_row(self.touchpad, "disable-while-typing", "Ignore the touchpad while typing"),
            )
            if has_key(self.touchpad, "two-finger-scrolling-enabled") and has_key(self.touchpad, "edge-scrolling-enabled"):
                pad.add(choice_row("Scroll with", None,
                                   [("two", "Two fingers"), ("edge", "The edge of the touchpad")],
                                   lambda: "edge" if self.touchpad.get_boolean("edge-scrolling-enabled")
                                   and not self.touchpad.get_boolean("two-finger-scrolling-enabled") else "two",
                                   self.set_scroll))
            if has_key(self.touchpad, "click-method"):
                pad.add(choice_row("Right-click with", "Pressing the touchpad",
                                   [("fingers", "Two fingers"), ("areas", "The bottom-right corner"),
                                    ("default", "The touchpad’s own setting")],
                                   lambda: self.touchpad.get_string("click-method"),
                                   lambda v: self.touchpad.set_string("click-method", v)))
        self.add_reset(self.reset)

    def accel_row(self) -> Adw.SwitchRow | None:
        """On: the pointer goes further when the mouse moves quickly (the default)."""
        if not has_key(self.mouse, "accel-profile"):
            return None
        row = Adw.SwitchRow(title="Mouse acceleration",
                            subtitle="Moving the mouse quickly takes the pointer further",
                            active=self.mouse.get_string("accel-profile") != "flat")
        row.connect("notify::active",
                    lambda r, _p: self.mouse.set_string("accel-profile", "default" if r.get_active() else "flat"))
        return row

    def set_scroll(self, value: str) -> None:
        self.touchpad.set_boolean("two-finger-scrolling-enabled", value == "two")
        self.touchpad.set_boolean("edge-scrolling-enabled", value == "edge")

    def reset(self) -> None:
        reset_keys(self.mouse, MOUSE_KEYS)
        reset_keys(self.touchpad, TOUCHPAD_KEYS)
        reset_keys(self.interface, ["locate-pointer"])
        self.window.reload_page(self)
        self.toast("Mouse and touchpad reset to Kulan defaults")
