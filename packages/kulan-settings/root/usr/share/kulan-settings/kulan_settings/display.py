"""Displays: resolution, refresh rate, scale and rotation of each screen,
Night Light (warm colours at night) and text size."""
from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from . import screens
from .common import Page, choice_row, has_key, open_gnome_panel, reset_keys, settings, switch_row

COLOR_SCHEMA = "org.gnome.settings-daemon.plugins.color"
TEXT_SIZES = [("1.0", "Default"), ("1.1", "Larger"), ("1.25", "Large"), ("1.5", "Largest")]


class DisplayPage(Page):
    title = "Displays"
    icon = "video-display-symbolic"
    keywords = ("display", "screen", "monitor", "resolution", "refresh rate", "hz", "scale", "zoom", "rotate",
                "portrait", "night light", "warm", "blue light", "text size", "font size")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.color = settings(COLOR_SCHEMA)
        self.interface = settings("org.gnome.desktop.interface")
        self.state: screens.State | None = None
        self.screen_groups: list = []
        self.applying = False
        self.screens_anchor = self.add_group()  # screens are inserted here, above Night Light
        self.screens_anchor.set_visible(False)
        self.config = screens.DisplayConfig(self.reload_screens)

        night = self.add_group("Night Light", "Warmer colours in the evening are easier on the eyes.")
        enabled = switch_row(self.color, "night-light-enabled", "Night Light")
        self.add_rows(night, enabled)
        if self.color is not None:
            schedule = choice_row("Schedule", None, [("sunset", "Sunset to sunrise"), ("manual", "Set times")],
                                  lambda: "sunset" if self.color.get_boolean("night-light-schedule-automatic")
                                  else "manual",
                                  lambda v: self.color.set_boolean("night-light-schedule-automatic", v == "sunset"))
            night.add(schedule)
            self.times = Adw.ActionRow(title="Times")
            self.from_spin = self.hour_spin("night-light-schedule-from", "Night Light starts")
            self.to_spin = self.hour_spin("night-light-schedule-to", "Night Light ends")
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
            box.append(Gtk.Label(label="From"))
            box.append(self.from_spin)
            box.append(Gtk.Label(label="to"))
            box.append(self.to_spin)
            self.times.add_suffix(box)
            night.add(self.times)
            if has_key(self.color, "night-light-temperature"):
                warmth = Adw.ActionRow(title="Warmth")
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1700, 4700, 100)
                scale.set_inverted(True)  # right = warmer
                scale.set_size_request(220, -1)
                scale.set_value(self.color.get_uint("night-light-temperature"))
                scale.update_property([Gtk.AccessibleProperty.LABEL], ["Night Light warmth"])
                scale.connect("value-changed",
                              lambda s: self.color.set_uint("night-light-temperature", int(s.get_value())))
                warmth.add_suffix(scale)
                night.add(warmth)
            self.watch(self.color, "changed", lambda *_a: self.sync())
            self.sync()

        display = self.add_group("Display")
        if has_key(self.interface, "text-scaling-factor"):
            display.add(choice_row("Text size", None, TEXT_SIZES, self.get_text_size,
                                   lambda v: self.interface.set_double("text-scaling-factor", float(v))))
        more = Adw.ActionRow(title="Arrange screens, mirroring", activatable=True)
        more.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        more.connect("activated", lambda _r: open_gnome_panel("display"))
        display.add(more)
        self.add_reset(self.reset)

    # --- screens ---------------------------------------------------------------------

    def teardown(self) -> None:
        self.config.close()
        super().teardown()

    def reload_screens(self) -> None:
        if not self.torn_down:
            self.config.get_state(self.show_screens)

    def show_screens(self, state) -> None:
        if self.applying or self.torn_down:
            return
        self.state = state
        for group in self.screen_groups:
            self.page.remove(group)
        self.screen_groups = []
        if state is None:
            group = Adw.PreferencesGroup(title="Screens",
                                         description="Screen settings need GNOME’s display service.")
            self.insert_group(group)
            return
        active = state.active_connectors()
        many = len(active) > 1
        for connector in active:
            monitor = state.monitor(connector)
            if monitor is not None:
                self.insert_group(self.screen_group(state, monitor, many))

    def insert_group(self, group: Adw.PreferencesGroup) -> None:
        """Add a screen's group right below the earlier ones, above Night Light.
        Adw.PreferencesPage only appends, so move it inside the page's box."""
        self.page.add(group)
        previous = self.screen_groups[-1] if self.screen_groups else self.screens_anchor
        self.screen_groups.append(group)
        box = group.get_parent()
        if isinstance(box, Gtk.Box) and previous.get_parent() is box:
            box.reorder_child_after(group, previous)

    def screen_group(self, state, monitor: dict, many: bool) -> Adw.PreferencesGroup:
        connector = monitor["connector"]
        title = monitor["name"]
        if monitor["builtin"]:
            title = "Built-in display" if not many else f"{title} (built-in)"
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title if many or monitor["builtin"] else "Screen"),
                                     description="" if many else GLib.markup_escape_text(monitor["name"]))
        mode = state.current_mode(connector)
        logical = state.logical_for(connector)
        if mode is None or logical is None:
            return group

        sizes = screens.resolutions(monitor)
        preferred = next(((m["width"], m["height"]) for m in monitor["modes"] if m["preferred"]), None)
        size_labels = [f"{w} × {h}" + (" (recommended)" if (w, h) == preferred else "") for w, h in sizes]
        size_row = Adw.ComboRow(title="Resolution", model=Gtk.StringList.new(size_labels))
        current_size = (mode["width"], mode["height"])
        if current_size in sizes:
            size_row.set_selected(sizes.index(current_size))
        group.add(size_row)

        rates = screens.modes_for(monitor, current_size)
        rate_row = Adw.ComboRow(title="Refresh rate",
                                model=Gtk.StringList.new([f"{m['refresh']:.2f} Hz" for m in rates]))
        rate_ids = [m["id"] for m in rates]
        if mode["id"] in rate_ids:
            rate_row.set_selected(rate_ids.index(mode["id"]))
        rate_row.set_visible(len(rates) > 1)
        group.add(rate_row)

        scales = mode["scales"] or [1.0]
        scale_row = Adw.ComboRow(title="Scale", subtitle="Makes everything bigger",
                                 model=Gtk.StringList.new([screens.scale_label(v) for v in scales]))
        scale_row.set_selected(scales.index(screens.best_scale(scales, logical["scale"])))
        scale_row.set_visible(len(scales) > 1)
        group.add(scale_row)

        rotations = [t for t, _l in screens.ROTATIONS]
        rotate_row = Adw.ComboRow(title="Orientation",
                                  model=Gtk.StringList.new([label for _t, label in screens.ROTATIONS]))
        if logical["transform"] in rotations:
            rotate_row.set_selected(rotations.index(logical["transform"]))
        group.add(rotate_row)

        def apply(_row=None, _pspec=None, changed: str = "") -> None:
            if self.applying or self.state is None:
                return
            size = sizes[size_row.get_selected()] if sizes else current_size
            options = screens.modes_for(monitor, size)
            if not options:
                return
            if changed == "size":
                # New size: its fastest rate, and the closest scale it allows.
                new_mode = options[0]
            else:
                new_mode = options[min(rate_row.get_selected(), len(options) - 1)]
            new_scales = new_mode["scales"] or [1.0]
            wanted = scales[scale_row.get_selected()] if changed == "scale" else logical["scale"]
            new_scale = screens.best_scale(new_scales, wanted)
            transform = rotations[rotate_row.get_selected()]
            try:
                config = screens.build_config(self.state, connector, new_mode, new_scale, transform)
            except ValueError as exc:
                self.toast(f"Couldn’t change the screen: {exc}")
                self.reload_screens()
                return
            self.applying = True

            def done(error) -> None:
                self.applying = False
                if error:
                    self.toast(f"Couldn’t change the screen: {error}")
                self.reload_screens()

            self.config.apply(self.state, config, done)

        size_row.connect("notify::selected", lambda r, p: apply(r, p, "size"))
        rate_row.connect("notify::selected", lambda r, p: apply(r, p, "rate"))
        scale_row.connect("notify::selected", lambda r, p: apply(r, p, "scale"))
        rotate_row.connect("notify::selected", lambda r, p: apply(r, p, "rotate"))
        return group

    def hour_spin(self, key: str, label: str) -> Gtk.SpinButton:
        spin = Gtk.SpinButton.new_with_range(0, 23.5, 0.5)
        spin.set_digits(1)
        spin.set_value(self.color.get_double(key))
        spin.set_valign(Gtk.Align.CENTER)
        spin.update_property([Gtk.AccessibleProperty.LABEL], [f"{label} (hour)"])
        spin.connect("value-changed", lambda s: self.color.set_double(key, s.get_value()))
        return spin

    def sync(self) -> None:
        on = self.color.get_boolean("night-light-enabled")
        self.times.set_visible(not self.color.get_boolean("night-light-schedule-automatic"))
        self.times.set_sensitive(on)

    def get_text_size(self) -> str:
        value = self.interface.get_double("text-scaling-factor")
        return min((v for v, _l in TEXT_SIZES), key=lambda v: abs(float(v) - value))

    def reset(self) -> None:
        reset_keys(self.color, ["night-light-enabled", "night-light-schedule-automatic",
                                "night-light-schedule-from", "night-light-schedule-to", "night-light-temperature"])
        reset_keys(self.interface, ["text-scaling-factor"])
        self.window.reload_page(self)
        self.toast("Night Light and text size reset to Kulan defaults")
