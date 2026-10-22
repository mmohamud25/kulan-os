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
        # Two screens of the same model share a name: add where they're plugged in.
        names = [m["name"] for m in self.monitors]
        for m in self.monitors:
            if names.count(m["name"]) > 1:
                m["name"] = f"{m['name']} ({m['connector']})"
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
        modes = {}
        for c in lm["connectors"]:
            if c == connector:
                modes[c] = mode
            elif connector in lm["connectors"]:
                # Mirrored screens must all show the same size.
                same = [m for m in modes_for(state.monitor(c) or {"modes": []}, (mode["width"], mode["height"]))]
                if not same:
                    raise ValueError("the mirrored screens don’t share that resolution")
                modes[c] = min(same, key=lambda m: abs(m["refresh"] - mode["refresh"]))
            else:
                modes[c] = state.current_mode(c) or old_mode
        boxes.append({"lm": lm, "x": lm["x"], "y": lm["y"], "old_w": old_w, "old_h": old_h, "w": w, "h": h,
                      "scale": new_scale, "transform": new_transform, "modes": modes})
    config = []
    for box in relayout(boxes):
        lm = box["lm"]
        monitors = [(c, box["modes"][c]["id"], {}) for c in lm["connectors"] if box["modes"][c].get("id")]
        config.append((box["x"], box["y"], float(box["scale"]), box["transform"], lm["primary"], monitors))
    return config


# --- several screens -----------------------------------------------------------------

SIDES = [("right", "To the right"), ("left", "To the left"), ("above", "Above"), ("below", "Below")]


def connected(state: State) -> list[str]:
    return [m["connector"] for m in state.monitors]


def usual_mode(monitor: dict, current: dict | None) -> dict | None:
    """The mode a screen gets when it's switched on: its current one, else
    its preferred one, else the largest."""
    if current is not None:
        return current
    preferred = [m for m in monitor["modes"] if m["preferred"] and not m["interlaced"]]
    if preferred:
        return preferred[0]
    sizes = resolutions(monitor)
    return modes_for(monitor, sizes[0])[0] if sizes else None


def sides_around(state: State, center: str) -> dict[str, str]:
    """Where each active screen sits now, seen from the center screen."""
    middle = state.logical_for(center)
    if middle is None:
        return {}
    mode = state.current_mode(center) or {"width": 1920, "height": 1080}
    cw, ch = logical_size(mode["width"], mode["height"], middle["scale"], middle["transform"], state.layout_mode)
    sides = {}
    for lm in state.logical:
        if lm is middle:
            continue
        dx, dy = lm["x"] - middle["x"], lm["y"] - middle["y"]
        if dx >= cw:
            side = "right"
        elif dx < 0 and abs(dx) >= abs(dy):
            side = "left"
        elif dy < 0:
            side = "above"
        elif dy >= ch:
            side = "below"
        else:
            side = "right"
        for c in lm["connectors"]:
            sides[c] = side
    return sides


def arrangement(state: State) -> tuple[str, str, dict[str, str]]:
    """How the screens are used now: ("join"|"mirror"|"single", primary
    connector, {connector: side of the primary})."""
    primary_lm = next((lm for lm in state.logical if lm["primary"]), state.logical[0] if state.logical else None)
    if primary_lm is None:
        return "join", "", {}
    primary = primary_lm["connectors"][0]
    if len(state.logical) == 1:
        return ("mirror" if len(primary_lm["connectors"]) > 1 else "single"), primary, {}
    sides = sides_around(state, primary)
    return "join", primary, sides


def layout_config(state: State, how: str, primary: str, sides: dict[str, str]) -> list:
    """ApplyMonitorsConfig's logical monitors for joined, mirrored or single
    use. Joined screens sit around the primary one, in order, each on its side."""
    monitors = {m["connector"]: m for m in state.monitors}
    if primary not in monitors:
        raise ValueError("that screen isn’t connected")

    def lm_of(c):
        return state.logical_for(c)

    if how == "single":
        mode = usual_mode(monitors[primary], state.current_mode(primary))
        lm = lm_of(primary)
        scale = lm["scale"] if lm else (mode["preferred_scale"] or 1.0)
        return [(0, 0, float(best_scale(mode["scales"] or [1.0], scale)), lm["transform"] if lm else 0, True,
                 [(primary, mode["id"], {})])]
    if how == "mirror":
        common = set(resolutions(monitors[primary]))
        for c in monitors:
            common &= set(resolutions(monitors[c]))
        if not common:
            raise ValueError("these screens don’t share a resolution to show the same picture")
        size = sorted(common, key=lambda s: s[0] * s[1], reverse=True)[0]
        chosen = {c: modes_for(monitors[c], size)[0] for c in monitors}
        scales = set(chosen[primary]["scales"] or [1.0])
        for mode in chosen.values():
            scales &= set(mode["scales"] or [1.0])
        scale = 1.0 if 1.0 in scales or not scales else min(scales)
        return [(0, 0, float(scale), 0, True, [(c, chosen[c]["id"], {}) for c in monitors])]
    # Joined: the primary at the centre, others around it on their side.
    boxes = {}
    for c, monitor in monitors.items():
        mode = usual_mode(monitor, state.current_mode(c) if lm_of(c) and len(lm_of(c)["connectors"]) == 1
                          else None)
        if mode is None:
            continue
        lm = lm_of(c)
        scale = lm["scale"] if lm else (mode["preferred_scale"] or 1.0)
        scale = best_scale(mode["scales"] or [1.0], scale)
        transform = lm["transform"] if lm else 0
        w, h = logical_size(mode["width"], mode["height"], scale, transform, state.layout_mode)
        boxes[c] = {"mode": mode, "scale": scale, "transform": transform, "w": int(round(w)), "h": int(round(h))}
    if state.global_scale and primary in boxes:
        for c, box in boxes.items():
            box["scale"] = best_scale(box["mode"]["scales"] or [1.0], boxes[primary]["scale"])
            w, h = logical_size(box["mode"]["width"], box["mode"]["height"], box["scale"], box["transform"],
                                state.layout_mode)
            box["w"], box["h"] = int(round(w)), int(round(h))
    p = boxes[primary]
    p["x"], p["y"] = 0, 0
    edge = {"right": p["w"], "left": 0, "above": 0, "below": p["h"]}
    for c in [c for c in monitors if c != primary and c in boxes]:
        box, side = boxes[c], sides.get(c, "right")
        if side == "right":
            box["x"], box["y"] = edge["right"], 0
            edge["right"] += box["w"]
        elif side == "left":
            edge["left"] -= box["w"]
            box["x"], box["y"] = edge["left"], 0
        elif side == "above":
            edge["above"] -= box["h"]
            box["x"], box["y"] = 0, edge["above"]
        else:
            box["x"], box["y"] = 0, edge["below"]
            edge["below"] += box["h"]
    min_x = min(b["x"] for b in boxes.values())
    min_y = min(b["y"] for b in boxes.values())
    return [(b["x"] - min_x, b["y"] - min_y, float(b["scale"]), b["transform"], c == primary,
             [(c, b["mode"]["id"], {})]) for c, b in boxes.items()]


class DisplayConfig:
    """Async access to org.gnome.Mutter.DisplayConfig."""

    def __init__(self, on_change) -> None:
        self.proxy = None
        self.on_change = on_change
        self.handler = 0
        self.closed = False
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                                  BUS_NAME, PATH, BUS_NAME, None, self.got_proxy)

    def got_proxy(self, _source, result) -> None:
        if self.closed:
            return
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
        self.closed = True
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
