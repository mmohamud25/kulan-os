"""Accessibility: seeing, hearing, typing and pointing more easily."""
from __future__ import annotations

from gi.repository import Gio

from .common import Page, choice_row, has_key, reset_keys, settings, switch_row

INTERFACE = "org.gnome.desktop.interface"
A11Y = "org.gnome.desktop.a11y"
A11Y_INTERFACE = "org.gnome.desktop.a11y.interface"
APPLICATIONS = "org.gnome.desktop.a11y.applications"
KEYBOARD = "org.gnome.desktop.a11y.keyboard"
MAGNIFIER = "org.gnome.desktop.a11y.magnifier"
WM = "org.gnome.desktop.wm.preferences"

TEXT_SIZES = [("1.0", "Standard"), ("1.15", "Larger"), ("1.25", "Large"), ("1.5", "Largest")]
CURSOR_SIZES = [("24", "Standard"), ("32", "Medium"), ("48", "Large"), ("64", "Larger"), ("96", "Largest")]


class AccessibilityPage(Page):
    title = "Accessibility"
    icon = "org.gnome.Settings-accessibility-symbolic|preferences-desktop-accessibility-symbolic"
    keywords = ("accessibility", "a11y", "screen reader", "orca", "zoom", "magnifier", "large text", "contrast",
                "cursor size", "sticky keys", "slow keys", "bounce keys", "visual alert", "animations",
                "scroll bars", "on-screen keyboard")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.interface = settings(INTERFACE)
        self.a11y = settings(A11Y)
        self.a11y_interface = settings(A11Y_INTERFACE)
        self.apps = settings(APPLICATIONS)
        self.keyboard = settings(KEYBOARD)
        self.magnifier = settings(MAGNIFIER)
        self.wm = settings(WM)

        seeing = self.add_group("Seeing")
        self.add_rows(
            seeing,
            switch_row(self.apps, "screen-reader-enabled", "Screen reader", "Reads what’s on the screen aloud"),
            switch_row(self.apps, "screen-magnifier-enabled", "Zoom", "Magnify the area around the pointer"),
            switch_row(self.a11y_interface, "high-contrast", "High contrast"),
        )
        if has_key(self.magnifier, "mag-factor"):
            zoom = choice_row("Zoom level", None, [("1.5", "1.5×"), ("2.0", "2×"), ("3.0", "3×"), ("4.0", "4×")],
                              lambda: f"{self.magnifier.get_double('mag-factor'):.1f}",
                              lambda v: self.magnifier.set_double("mag-factor", float(v)))
            if has_key(self.apps, "screen-magnifier-enabled"):
                self.apps.bind("screen-magnifier-enabled", zoom, "sensitive", Gio.SettingsBindFlags.GET)
            seeing.add(zoom)
        if has_key(self.interface, "text-scaling-factor"):
            seeing.add(choice_row("Text size", "For apps and the desktop", TEXT_SIZES,
                                  lambda: self.nearest(self.interface.get_double("text-scaling-factor"), TEXT_SIZES),
                                  lambda v: self.interface.set_double("text-scaling-factor", float(v))))
        if has_key(self.interface, "cursor-size"):
            seeing.add(choice_row("Pointer size", None, CURSOR_SIZES,
                                  lambda: self.nearest(self.interface.get_int("cursor-size"), CURSOR_SIZES),
                                  lambda v: self.interface.set_int("cursor-size", int(v))))
        self.add_rows(
            seeing,
            switch_row(self.interface, "enable-animations", "Reduce motion",
                       "Fewer animations when windows and menus open", invert=True),
            switch_row(self.interface, "overlay-scrolling", "Always show scroll bars", invert=True),
        )

        hearing = self.add_group("Hearing")
        self.add_rows(hearing, switch_row(self.wm, "visual-bell", "Flash the screen for alert sounds"))

        typing = self.add_group("Typing")
        self.add_rows(
            typing,
            switch_row(self.apps, "screen-keyboard-enabled", "On-screen keyboard", "Type with the pointer or a touch screen"),
            switch_row(self.keyboard, "stickykeys-enable", "Sticky keys",
                       "Press Shift, Ctrl or Alt, then the next key, instead of holding both"),
            switch_row(self.keyboard, "slowkeys-enable", "Slow keys", "A key counts only when held a moment"),
            switch_row(self.keyboard, "bouncekeys-enable", "Bounce keys", "Ignore a key pressed twice quickly by mistake"),
        )

        menu = self.add_group()
        self.add_rows(menu, switch_row(self.a11y, "always-show-universal-access-status",
                                       "Accessibility menu in the top bar"))
        self.add_reset(self.reset)

    @staticmethod
    def nearest(value: float, choices: list[tuple[str, str]]) -> str:
        return min(choices, key=lambda c: abs(float(c[0]) - value))[0]

    def reset(self) -> None:
        reset_keys(self.apps, ["screen-reader-enabled", "screen-magnifier-enabled", "screen-keyboard-enabled"])
        reset_keys(self.a11y_interface, ["high-contrast"])
        reset_keys(self.magnifier, ["mag-factor"])
        reset_keys(self.interface, ["text-scaling-factor", "cursor-size", "enable-animations", "overlay-scrolling"])
        reset_keys(self.wm, ["visual-bell"])
        reset_keys(self.keyboard, ["stickykeys-enable", "slowkeys-enable", "bouncekeys-enable"])
        reset_keys(self.a11y, ["always-show-universal-access-status"])
        self.window.reload_page(self)
        self.toast("Accessibility reset to Kulan defaults")

