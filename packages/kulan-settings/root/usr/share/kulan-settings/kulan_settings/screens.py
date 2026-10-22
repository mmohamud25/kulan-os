"""Screens through Mutter's DisplayConfig D-Bus API: read the current monitors
and apply a new resolution, refresh rate, scale or rotation. Changes are
applied as "persistent", so GNOME Shell asks "Keep these display settings?"
and puts the old ones back by itself if nobody answers."""
from __future__ import annotations

from gi.repository import Gio, GLib

BUS_NAME = "org.gnome.Mutter.DisplayConfig"
PATH = "/org/gnome/Mutter/DisplayConfig"
METHOD_PERSISTENT = 2
LAYOUT_LOGICAL = 1

ROTATIONS = [(0, "Landscape"), (1, "Portrait (right)"), (3, "Portrait (left)"), (2, "Landscape (flipped)")]


class State:
    """GetCurrentState, unpacked into plain Python."""

    def __init__(self, raw) -> None:
        serial, monitors, logical, props = raw
        self.serial = serial
        self.layout_mode = int(props.get("layout-mode", LAYOUT_LOGICAL))
        self.global_scale = bool(props.get("global-scale-required", False))
        self.monitors = []
        for (connector, vendor, product, mserial), modes, mprops in monitors:
            mode_list = []
            for mode_id, width, height, refresh, preferred_scale, scales, flags in modes:
                mode_list.append({
                    "id": mode_id, "width": width, "height": height, "refresh": refresh,
                    "preferred_scale": preferred_scale, "scales": list(scales),
                    "current": bool(flags.get("is-current")), "preferred": bool(flags.get("is-preferred")),
                    "interlaced": bool(flags.get("is-interlaced")),
                })
            self.monitors.append({
                "connector": connector, "spec": (connector, vendor, product, mserial),
                "name": mprops.get("display-name") or connector, "builtin": bool(mprops.get("is-builtin")),
                "modes": mode_list,
            })
        self.logical = []
        for x, y, scale, transform, primary, specs, _lprops in logical:
            self.logical.append({"x": x, "y": y, "scale": scale, "transform": transform, "primary": primary,
                                 "connectors": [spec[0] for spec in specs]})

    def monitor(self, connector: str) -> dict | None:
        return next((m for m in self.monitors if m["connector"] == connector), None)

    def current_mode(self, connector: str) -> dict | None:
        monitor = self.monitor(connector)
        if monitor is None:
            return None
        return next((m for m in monitor["modes"] if m["current"]), None)

    def logical_for(self, connector: str) -> dict | None:
        return next((lm for lm in self.logical if connector in lm["connectors"]), None)

    def active_connectors(self) -> list[str]:
        return [c for lm in self.logical for c in lm["connectors"]]


def resolutions(monitor: dict) -> list[tuple[int, int]]:
    """Distinct sizes, largest first (interlaced modes left out)."""
    sizes = {(m["width"], m["height"]) for m in monitor["modes"] if not m["interlaced"]}
    return sorted(sizes, key=lambda s: (s[0] * s[1], s[0]), reverse=True)


def modes_for(monitor: dict, size: tuple[int, int]) -> list[dict]:
    """Modes of one size, fastest refresh first."""
    found = [m for m in monitor["modes"] if (m["width"], m["height"]) == size and not m["interlaced"]]
    return sorted(found, key=lambda m: -m["refresh"])


def scale_label(scale: float) -> str:
    return f"{round(scale * 100)}%"


def best_scale(scales: list[float], wanted: float) -> float:
    return min(scales, key=lambda s: abs(s - wanted)) if scales else 1.0


def logical_size(width: int, height: int, scale: float, transform: int, layout_mode: int) -> tuple[float, float]:
    if transform % 2 == 1:  # 90 or 270 degrees (and their flipped versions)
        width, height = height, width
    if layout_mode == LAYOUT_LOGICAL:
        return width / scale, height / scale
    return float(width), float(height)


def relayout(boxes: list[dict]) -> list[dict]:
    """Keep screens touching after one changes size: a screen moves right by
    how much wider every screen entirely to its left became, and down by how
    much taller every screen entirely above it became. boxes have x, y, old_w,
    old_h, w, h; returns them with new integer x and y."""
    out = []
    for box in boxes:
        dx = sum(o["w"] - o["old_w"] for o in boxes if o is not box and o["x"] + o["old_w"] <= box["x"])
        dy = sum(o["h"] - o["old_h"] for o in boxes if o is not box and o["y"] + o["old_h"] <= box["y"])
        out.append({**box, "x": int(round(box["x"] + dx)), "y": int(round(box["y"] + dy))})
    # Mutter wants the layout to start at 0,0.
    min_x = min(b["x"] for b in out)
    min_y = min(b["y"] for b in out)
    return [{**b, "x": b["x"] - min_x, "y": b["y"] - min_y} for b in out]


def build_config(state: State, connector: str, mode: dict, scale: float, transform: int) -> list:
    """ApplyMonitorsConfig's logical monitors with one screen changed."""
    boxes = []
    for lm in state.logical:
        first = lm["connectors"][0]
        old_mode = state.current_mode(first) or {"width": 1024, "height": 768}
        old_w, old_h = logical_size(old_mode["width"], old_mode["height"], lm["scale"], lm["transform"],
                                    state.layout_mode)
        new_scale, new_transform, new_mode = lm["scale"], lm["transform"], old_mode
        if connector in lm["connectors"]:
            new_scale, new_transform, new_mode = scale, transform, mode
        elif state.global_scale:
            new_scale = scale
        w, h = logical_size(new_mode["width"], new_mode["height"], new_scale, new_transform, state.layout_mode)
        boxes.append({"lm": lm, "x": lm["x"], "y": lm["y"], "old_w": old_w, "old_h": old_h, "w": w, "h": h,
                      "scale": new_scale, "transform": new_transform,
                      "modes": {c: (mode if c == connector else (state.current_mode(c) or old_mode))
                                for c in lm["connectors"]}})
    config = []
    for box in relayout(boxes):
        lm = box["lm"]
        monitors = [(c, box["modes"][c]["id"], {}) for c in lm["connectors"] if box["modes"][c].get("id")]
        config.append((box["x"], box["y"], float(box["scale"]), box["transform"], lm["primary"], monitors))
    return config


class DisplayConfig:
    """Async access to org.gnome.Mutter.DisplayConfig."""

    def __init__(self, on_change) -> None:
        self.proxy = None
        self.on_change = on_change
        self.handler = 0
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                                  BUS_NAME, PATH, BUS_NAME, None, self.got_proxy)

    def got_proxy(self, _source, result) -> None:
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error:
            self.proxy = None
            self.on_change()
            return
        self.handler = self.proxy.connect("g-signal", self.signal)
        self.on_change()

    def signal(self, _proxy, _sender, name: str, _params) -> None:
        if name == "MonitorsChanged":
            self.on_change()

    def close(self) -> None:
        if self.proxy is not None and self.handler:
            self.proxy.disconnect(self.handler)
            self.handler = 0

    def get_state(self, done) -> None:
        if self.proxy is None or self.proxy.get_name_owner() is None:
            done(None)
            return

        def finished(proxy, result) -> None:
            try:
                done(State(proxy.call_finish(result).unpack()))
            except (GLib.Error, ValueError, TypeError):
                done(None)

        self.proxy.call("GetCurrentState", None, Gio.DBusCallFlags.NONE, 10000, None, finished)

    def apply(self, state: State, config: list, done) -> None:
        props = {}
        args = GLib.Variant("(uua(iiduba(ssa{sv}))a{sv})", (state.serial, METHOD_PERSISTENT, config, props))

        def finished(proxy, result) -> None:
            try:
                proxy.call_finish(result)
                done(None)
            except GLib.Error as exc:
                done(Gio.DBusError.get_remote_error(exc) and exc.message.split(": ", 1)[-1] or exc.message)

        self.proxy.call("ApplyMonitorsConfig", args, Gio.DBusCallFlags.NONE, 20000, None, finished)
