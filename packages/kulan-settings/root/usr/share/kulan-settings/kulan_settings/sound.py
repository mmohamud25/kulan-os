"""Sound: choose the speakers and microphone, set their volume, and pick the
alert sound. Uses GNOME's own volume library (Gvc, shipped with GNOME
Shell), the same one GNOME Settings and the top bar use."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, has_key, settings

ALERTS_DIR = Path("/usr/share/sounds/gnome/default/alerts")
ALERTS = [("click", "Click"), ("string", "String"), ("swing", "Swing"), ("hum", "Hum")]
SOUND_SCHEMA = "org.gnome.desktop.sound"
CUSTOM_THEME = "__custom"

Gvc = None


def load_gvc():
    """GNOME Shell's private volume library. Its folder must come first: a
    different libgvc (Graphviz) can sit in the normal library path."""
    global Gvc
    if Gvc is not None:
        return Gvc
    try:
        import gi

        folder = next((f for f in ("/usr/lib/gnome-shell", "/usr/lib64/gnome-shell")
                       if os.path.exists(os.path.join(f, "Gvc-1.0.typelib"))), None)
        if folder is not None:
            try:  # PyGObject up to 3.50 (Debian 13)
                gi.require_version("GIRepository", "2.0")
                from gi.repository import GIRepository
                GIRepository.Repository.prepend_search_path(folder)
                GIRepository.Repository.prepend_library_path(folder)
            except ValueError:  # PyGObject 3.52 and later
                gi.require_version("GIRepository", "3.0")
                from gi.repository import GIRepository
                repo = GIRepository.Repository.dup_default()
                repo.prepend_search_path(folder)
                repo.prepend_library_path(folder)
        gi.require_version("Gvc", "1.0")
        from gi.repository import Gvc as module
    except (ImportError, ValueError):
        return None
    Gvc = module
    return Gvc


def custom_dir() -> Path:
    return Path(GLib.get_user_data_dir()) / "sounds" / CUSTOM_THEME


def current_alert() -> str | None:
    """The alert in the "__custom" theme GNOME's Sound page uses, if set."""
    try:
        target = os.readlink(custom_dir() / "bell-terminal.ogg")
    except OSError:
        return None
    name = Path(target).stem
    return name if name in dict(ALERTS) else None


def set_alert(name: str, sound: "Gio.Settings | None") -> None:
    """Point the "__custom" theme at one of GNOME's alerts (as GNOME's Sound
    page does), inheriting the current theme for every other sound."""
    folder = custom_dir()
    folder.mkdir(parents=True, exist_ok=True)
    inherits = "kulan"
    if sound is not None:
        theme = sound.get_string("theme-name")
        if theme and theme != CUSTOM_THEME:
            inherits = theme
    index = folder / "index.theme"
    if not index.exists():
        index.write_text(f"[Sound Theme]\nName=Custom\nInherits={inherits}\nDirectories=.\n", encoding="utf-8")
    source = ALERTS_DIR / f"{name}.ogg"
    for link in ("bell-terminal.ogg", "bell-window-system.ogg"):
        path = folder / link
        tmp = folder / f".{link}.new"
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(source)
        tmp.replace(path)
    if sound is not None and sound.get_string("theme-name") != CUSTOM_THEME:
        sound.set_string("theme-name", CUSTOM_THEME)


def play(path: Path) -> None:
    for player in (["pw-play", str(path)], ["paplay", str(path)], ["canberra-gtk-play", "-f", str(path)]):
        if shutil.which(player[0]):
            try:
                Gio.Subprocess.new(player, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
            except GLib.Error:
                continue
            return


class SoundPage(Page):
    title = "Sound"
    icon = "audio-speakers-symbolic"
    keywords = ("sound", "audio", "volume", "speakers", "headphones", "microphone", "mic", "input", "output",
                "alert", "beep", "app volume", "apps")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.sound = settings(SOUND_SCHEMA)
        self.control = None
        self.devices: dict[str, dict[int, object]] = {"output": {}, "input": {}}
        self.stream_watch: dict[str, tuple] = {}
        self.syncing = False

        self.output = self.add_group("Output")
        self.output_device = Adw.ComboRow(title="Play sound through", model=Gtk.StringList())
        self.output_device.connect("notify::selected", lambda r, _p: self.on_device("output", r))
        self.output_volume, self.output_scale, self.output_mute = self.volume_row("Output volume", "output")
        self.add_rows(self.output, self.output_device, self.output_volume)
        if has_key(self.sound, "allow-volume-above-100-percent"):
            loud = Adw.SwitchRow(title="Louder than 100%", subtitle="Can distort the sound")
            self.sound.bind("allow-volume-above-100-percent", loud, "active", Gio.SettingsBindFlags.DEFAULT)
            self.watch(self.sound, "changed::allow-volume-above-100-percent", lambda *_a: self.sync_volume("output"))
            self.output.add(loud)

        self.input = self.add_group("Input")
        self.input_device = Adw.ComboRow(title="Microphone", model=Gtk.StringList())
        self.input_device.connect("notify::selected", lambda r, _p: self.on_device("input", r))
        self.input_volume, self.input_scale, self.input_mute = self.volume_row("Input volume", "input")
        self.add_rows(self.input, self.input_device, self.input_volume)

        alerts = self.add_group("Alerts")
        current = current_alert()
        labels = Gtk.StringList.new([label for _n, label in ALERTS])
        self.alert = Adw.ComboRow(title="Alert sound", model=labels)
        names = [n for n, _l in ALERTS]
        self.alert.set_selected(names.index(current) if current in names else 0)
        if current is None:
            self.alert.set_subtitle("Your sound theme’s own alert")
        self.alert.connect("notify::selected", self.on_alert)
        alerts.add(self.alert)
        if has_key(self.sound, "event-sounds"):
            events = Adw.SwitchRow(title="Sounds for events", subtitle="Alerts, screenshots and other system sounds")
            self.sound.bind("event-sounds", events, "active", Gio.SettingsBindFlags.DEFAULT)
            alerts.add(events)

        self.apps = self.add_group("Apps", "The volume of each app that’s playing sound.")
        self.app_rows: dict[int, tuple] = {}
        self.no_apps = Adw.ActionRow(title="No apps are playing sound")
        self.apps.add(self.no_apps)

        self.status = Adw.StatusPage(icon_name="audio-speakers-symbolic", title="Sound isn’t ready",
                                     description="The sound service isn’t running yet.")
        if load_gvc() is None:
            self.set_ready(False, "Sound settings need GNOME Shell.")
            return
        self.set_ready(False, None)
        self.control = Gvc.MixerControl(name="Kulan Settings")
        for signal, handler in (
                ("state-changed", self.on_state),
                ("output-added", lambda _c, i: self.device_added("output", i)),
                ("output-removed", lambda _c, i: self.device_removed("output", i)),
                ("input-added", lambda _c, i: self.device_added("input", i)),
                ("input-removed", lambda _c, i: self.device_removed("input", i)),
                ("active-output-update", lambda *_a: self.sync_devices("output")),
                ("active-input-update", lambda *_a: self.sync_devices("input")),
                ("default-sink-changed", lambda *_a: self.follow_stream("output")),
                ("default-source-changed", lambda *_a: self.follow_stream("input")),
                ("stream-added", lambda _c, i: self.app_added(i)),
                ("stream-removed", lambda _c, i: self.app_removed(i))):
            self.watch(self.control, signal, handler)
        self.control.open()

    def teardown(self) -> None:
        for kind in list(self.stream_watch):
            self.unfollow(kind)
        for ident in list(self.app_rows):
            self.app_removed(ident)
        super().teardown()
        if self.control is not None:
            self.control.close()
            self.control = None

    def set_ready(self, ready: bool, why: str | None) -> None:
        for group in (self.output, self.input):
            group.set_sensitive(ready)
        if not ready and why:
            self.output.set_description(why)
        elif ready:
            self.output.set_description("")

    def on_state(self, _control, state) -> None:
        if state == Gvc.MixerControlState.READY:
            self.set_ready(True, None)
            self.sync_devices("output")
            self.sync_devices("input")
            self.follow_stream("output")
            self.follow_stream("input")
            for stream in self.control.get_sink_inputs():
                self.app_added(stream.get_id())
        elif state == Gvc.MixerControlState.FAILED:
            self.set_ready(False, "Couldn’t reach the sound service.")
        elif state in (Gvc.MixerControlState.CLOSED, Gvc.MixerControlState.CONNECTING):
            self.set_ready(False, "Connecting to the sound service…")

    # --- devices ---------------------------------------------------------------------

    def device_added(self, kind: str, ident: int) -> None:
        lookup = self.control.lookup_output_id if kind == "output" else self.control.lookup_input_id
        device = lookup(ident)
        if device is not None:
            self.devices[kind][ident] = device
            self.sync_devices(kind)

    def device_removed(self, kind: str, ident: int) -> None:
        self.devices[kind].pop(ident, None)
        self.sync_devices(kind)

    @staticmethod
    def device_name(device) -> str:
        description = device.get_description() or "Device"
        origin = device.get_origin() or ""
        return f"{description} – {origin}" if origin and origin not in description else description

    def sync_devices(self, kind: str) -> None:
        if self.control is None:
            return
        row = self.output_device if kind == "output" else self.input_device
        devices = sorted(self.devices[kind].items(), key=lambda item: self.device_name(item[1]).lower())
        stream = self.control.get_default_sink() if kind == "output" else self.control.get_default_source()
        active = None
        if stream is not None:
            lookup = (self.control.lookup_device_from_stream)
            current = lookup(stream)
            active = current.get_id() if current is not None else None
        self.syncing = True
        model = Gtk.StringList.new([self.device_name(d) for _i, d in devices] or ["None found"])
        row.set_model(model)
        row.ids = [i for i, _d in devices]
        if active in row.ids:
            row.set_selected(row.ids.index(active))
        row.set_sensitive(bool(devices))
        self.syncing = False

    def on_device(self, kind: str, row: Adw.ComboRow) -> None:
        if self.syncing or self.control is None or not getattr(row, "ids", None):
            return
        index = row.get_selected()
        if index >= len(row.ids):
            return
        device = self.devices[kind].get(row.ids[index])
        if device is None:
            return
        if kind == "output":
            self.control.change_output(device)
        else:
            self.control.change_input(device)

    # --- volume ----------------------------------------------------------------------

    def volume_row(self, label: str, kind: str):
        row = Adw.ActionRow(title=label)
        mute = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic" if kind == "output"
                                else "microphone-sensitivity-high-symbolic",
                                valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Mute")
        mute.update_property([Gtk.AccessibleProperty.LABEL], [f"Mute {kind}"])
        mute.connect("toggled", lambda b: self.on_mute(kind, b))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_size_request(260, -1)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [label])
        scale.connect("value-changed", lambda s: self.on_volume(kind, s))
        row.add_prefix(mute)
        row.add_suffix(scale)
        return row, scale, mute

    def stream(self, kind: str):
        if self.control is None:
            return None
        return self.control.get_default_sink() if kind == "output" else self.control.get_default_source()

    def unfollow(self, kind: str) -> None:
        stream, handlers = self.stream_watch.pop(kind, (None, ()))
        for handler in handlers:
            if stream is not None and stream.handler_is_connected(handler):
                stream.disconnect(handler)

    def follow_stream(self, kind: str) -> None:
        self.unfollow(kind)
        stream = self.stream(kind)
        if stream is not None:
            self.stream_watch[kind] = (stream, (
                stream.connect("notify::volume", lambda *_a: self.sync_volume(kind)),
                stream.connect("notify::is-muted", lambda *_a: self.sync_volume(kind))))
        self.sync_devices(kind)
        self.sync_volume(kind)

    def max_volume(self, kind: str) -> float:
        normal = self.control.get_vol_max_norm()
        if kind == "output" and self.sound is not None and has_key(self.sound, "allow-volume-above-100-percent") \
                and self.sound.get_boolean("allow-volume-above-100-percent"):
            return self.control.get_vol_max_amplified()
        return normal

    def sync_volume(self, kind: str) -> None:
        stream = self.stream(kind)
        scale = self.output_scale if kind == "output" else self.input_scale
        mute = self.output_mute if kind == "output" else self.input_mute
        row = self.output_volume if kind == "output" else self.input_volume
        row.set_sensitive(stream is not None)
        if stream is None:
            return
        self.syncing = True
        top = self.max_volume(kind) / self.control.get_vol_max_norm() * 100
        scale.set_range(0, top)
        scale.clear_marks()
        if top > 100:
            scale.add_mark(100, Gtk.PositionType.BOTTOM, None)
        scale.set_value(stream.get_volume() / self.control.get_vol_max_norm() * 100)
        mute.set_active(stream.get_is_muted())
        muted = stream.get_is_muted()
        if kind == "output":
            mute.set_icon_name("audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic")
        else:
            mute.set_icon_name("microphone-sensitivity-muted-symbolic" if muted
                               else "microphone-sensitivity-high-symbolic")
        self.syncing = False

    def on_volume(self, kind: str, scale: Gtk.Scale) -> None:
        stream = self.stream(kind)
        if self.syncing or stream is None:
            return
        volume = int(scale.get_value() / 100 * self.control.get_vol_max_norm())
        if stream.set_volume(volume):
            stream.push_volume()
        if stream.get_is_muted() and volume > 0:
            stream.change_is_muted(False)

    def on_mute(self, kind: str, button: Gtk.ToggleButton) -> None:
        stream = self.stream(kind)
        if self.syncing or stream is None:
            return
        stream.change_is_muted(button.get_active())

    # --- apps ------------------------------------------------------------------------

    def app_added(self, ident: int) -> None:
        if self.control is None or ident in self.app_rows:
            return
        stream = self.control.lookup_stream_id(ident)
        if stream is None or not isinstance(stream, Gvc.MixerSinkInput) or stream.is_event_stream():
            return
        if (stream.get_application_id() or "") in ("org.gnome.VolumeControl", "org.PulseAudio.pavucontrol",
                                                    "org.kulan.Settings"):
            return
        row = Adw.ActionRow(title=GLib.markup_escape_text(stream.get_name() or "App"))
        icon = Gtk.Image(pixel_size=32)
        if stream.get_gicon() is not None:
            icon.set_from_gicon(stream.get_gicon())
        else:
            icon.set_from_icon_name(stream.get_icon_name() or "application-x-executable-symbolic")
        row.add_prefix(icon)
        mute = Gtk.ToggleButton(valign=Gtk.Align.CENTER, css_classes=["flat"], tooltip_text="Mute")
        mute.update_property([Gtk.AccessibleProperty.LABEL], [f"Mute {stream.get_name() or 'app'}"])
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        scale.set_size_request(220, -1)
        scale.update_property([Gtk.AccessibleProperty.LABEL], [f"{stream.get_name() or 'App'} volume"])
        row.add_suffix(mute)
        row.add_suffix(scale)
        state = {"syncing": False}

        def sync(*_a) -> None:
            state["syncing"] = True
            scale.set_value(stream.get_volume() / self.control.get_vol_max_norm() * 100)
            mute.set_active(stream.get_is_muted())
            mute.set_icon_name("audio-volume-muted-symbolic" if stream.get_is_muted() else "audio-volume-high-symbolic")
            state["syncing"] = False

        def changed(s: Gtk.Scale) -> None:
            if state["syncing"]:
                return
            if stream.set_volume(int(s.get_value() / 100 * self.control.get_vol_max_norm())):
                stream.push_volume()
            if stream.get_is_muted() and s.get_value() > 0:
                stream.change_is_muted(False)

        def muted(b: Gtk.ToggleButton) -> None:
            if not state["syncing"]:
                stream.change_is_muted(b.get_active())
        scale.connect("value-changed", changed)
        mute.connect("toggled", muted)
        handlers = (stream.connect("notify::volume", sync), stream.connect("notify::is-muted", sync))
        sync()
        self.apps.add(row)
        self.app_rows[ident] = (row, stream, handlers)
        self.no_apps.set_visible(False)

    def app_removed(self, ident: int) -> None:
        row, stream, handlers = self.app_rows.pop(ident, (None, None, ()))
        for handler in handlers:
            if stream.handler_is_connected(handler):
                stream.disconnect(handler)
        if row is not None:
            self.apps.remove(row)
        self.no_apps.set_visible(not self.app_rows)

    # --- alerts ----------------------------------------------------------------------

    def on_alert(self, row: Adw.ComboRow, _pspec) -> None:
        name = ALERTS[row.get_selected()][0]
        try:
            set_alert(name, self.sound)
        except OSError as exc:
            self.toast(f"Couldn’t change the alert sound: {exc.strerror}")
            return
        row.set_subtitle("")
        play(ALERTS_DIR / f"{name}.ogg")
