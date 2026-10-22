"""Kulan Assistant, "do it for me": turn a request into a plan of Kulan actions.

The model never runs anything itself. It writes a plan in a fixed JSON shape
(the engine is held to a JSON schema, so even a small model can't invent
actions or arguments), choosing only from the actions below. Kulan shows the
plan in plain words; anything that changes the computer waits for "Do it",
and settings changes can be undone. Things the actions can't do (deleting
files, typing passwords, anything as administrator) aren't possible at all.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from . import engine

MAX_STEPS = 6
SETTINGS_PAGES = ["wifi", "bluetooth", "sound", "focus", "notifications", "users", "parental-controls", "appearance",
                  "desktop", "dock", "top-bar", "display", "battery", "storage", "login-items", "apps", "security",
                  "privacy", "keyboard", "mouse", "accessibility", "assistant", "prayer-times", "date-time", "region",
                  "updates", "add-ons", "about"]
ACCENTS = ["teal", "blue", "green", "yellow", "orange", "red", "pink", "purple", "slate"]
# where -> (GLib's special folder, its usual name when the system has none set)
PLACES = {"home": ("", ""), "desktop": ("DESKTOP", "Desktop"), "documents": ("DOCUMENTS", "Documents"),
          "downloads": ("DOWNLOAD", "Downloads"), "pictures": ("PICTURES", "Pictures"),
          "music": ("MUSIC", "Music"), "videos": ("VIDEOS", "Videos")}
DOCK_SIZES = {"small": 28, "medium": 34, "large": 44}
TEXT_SIZES = {"normal": 1.0, "large": 1.2, "larger": 1.4}
LOCK_MINUTES = [1, 2, 5, 10, 15, 30, 0]


def _settings(schema: str) -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(schema, True) is None:
        return None
    return Gio.Settings(schema_id=schema)


@dataclass
class Result:
    ok: bool
    message: str
    undo: Callable[[], None] | None = None
    found: list[str] = field(default_factory=list)  # paths, for find_files


@dataclass
class Action:
    id: str
    help: str                     # for the model
    args: dict                    # JSON schema "properties"
    changes: bool                 # needs "Do it"
    describe: Callable[[dict], str]
    run: Callable[[dict], Result]


# --- settings helpers ----------------------------------------------------------------
def _set(schema: str, key: str, value: GLib.Variant, done: str) -> Result:
    s = _settings(schema)
    if s is None:
        return Result(False, "That setting isn't available here")
    if not s.is_writable(key):
        return Result(False, "That setting is locked (compliance mode or your organisation)")
    old = s.get_value(key)
    s.set_value(key, value)
    Gio.Settings.sync()
    return Result(True, done, undo=lambda: (s.set_value(key, old), Gio.Settings.sync()))


def set_style(a: dict) -> Result:
    mode = a["mode"]
    kulan = _settings("org.kulan.desktop")
    undo_auto = None
    if kulan is not None and kulan.props.settings_schema.has_key("automatic-style"):
        was = kulan.get_boolean("automatic-style")
        kulan.set_boolean("automatic-style", mode == "automatic")
        undo_auto = lambda: kulan.set_boolean("automatic-style", was)  # noqa: E731
    if mode == "automatic":
        Gio.Settings.sync()
        return Result(True, "Light by day, dark after sunset", undo=undo_auto)
    r = _set("org.gnome.desktop.interface", "color-scheme",
             GLib.Variant("s", "prefer-dark" if mode == "dark" else "default"), f"{mode.capitalize()} style on")
    if r.ok and undo_auto:
        first = r.undo
        r.undo = lambda: (first(), undo_auto())
    return r


def open_app(a: dict) -> Result:
    wanted = a["name"].strip().lower()
    best, score = None, 0
    for info in Gio.AppInfo.get_all():
        if not info.should_show():
            continue
        names = [info.get_display_name() or "", info.get_name() or "", info.get_id() or ""]
        for n in names:
            n = n.lower()
            s = 3 if n == wanted else 2 if n.startswith(wanted) else 1 if wanted in n else 0
            if s > score:
                best, score = info, s
    if best is None:
        return Result(False, f"No app called “{a['name']}”")
    try:
        best.launch([], None)
    except GLib.Error as e:
        return Result(False, f"{best.get_display_name()} didn't open: {e.message}")
    return Result(True, f"Opened {best.get_display_name()}")


def open_settings(a: dict) -> Result:
    try:
        subprocess.Popen(["kulan-settings", a["page"]], start_new_session=True)
    except OSError as e:
        return Result(False, str(e))
    return Result(True, "Opened Kulan Settings")


def _place(where: str) -> Path:
    special, usual = PLACES.get(where, ("", ""))
    if special:
        path = GLib.get_user_special_dir(getattr(GLib.UserDirectory, f"DIRECTORY_{special}"))
        if path and Path(path) != Path.home():
            return Path(path)
        return Path.home() / usual
    return Path.home()


def create_folder(a: dict) -> Result:
    name = a["name"].strip().strip("/")
    if not name or "/" in name or name in (".", "..") or len(name) > 120:
        return Result(False, "That isn't a folder name")
    path = _place(a["where"]) / name
    if path.exists():
        return Result(False, f"“{name}” is already there")
    path.mkdir(parents=True)
    return Result(True, f"Made the folder {path}",
                  undo=lambda: path.rmdir() if path.exists() and not any(path.iterdir()) else None)


def find_files(a: dict) -> Result:
    words = [w for w in re.split(r"\s+", a["query"].lower()) if w]
    if not words:
        return Result(False, "Nothing to look for")
    hits: list[str] = []
    for root, dirs, files in os.walk(Path.home()):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("snap", "node_modules")]
        for f in files:
            if all(w in f.lower() for w in words):
                hits.append(os.path.join(root, f))
                if len(hits) >= 20:
                    break
        if len(hits) >= 20:
            break
    if not hits:
        return Result(True, f"No files with “{a['query']}” in their name")
    return Result(True, f"Found {len(hits)} file{'s' if len(hits) != 1 else ''}", found=hits)


def set_volume(a: dict) -> Result:
    percent = max(0, min(100, int(a["percent"])))
    tool = shutil.which("wpctl")
    if tool is None:
        return Result(False, "Sound settings aren't available")
    old = subprocess.run([tool, "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True).stdout
    match = re.search(r"([\d.]+)", old)
    r = subprocess.run([tool, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent / 100:.2f}"])
    if r.returncode != 0:
        return Result(False, "The volume couldn't be changed")
    undo = (lambda: subprocess.run([tool, "set-volume", "@DEFAULT_AUDIO_SINK@", match.group(1)])) if match else None
    return Result(True, f"Volume {percent}%", undo=undo)


def set_wifi(a: dict) -> Result:
    if shutil.which("nmcli") is None:
        return Result(False, "Wi-Fi settings aren't available")
    state = "on" if a["on"] else "off"
    r = subprocess.run(["nmcli", "radio", "wifi", state], capture_output=True, text=True)
    if r.returncode != 0:
        return Result(False, r.stderr.strip() or "Wi-Fi couldn't be changed")
    back = "off" if a["on"] else "on"
    return Result(True, f"Wi-Fi {state}", undo=lambda: subprocess.run(["nmcli", "radio", "wifi", back]))


ACTIONS = [
    Action("set_style", "Light or dark appearance, or automatic (light by day, dark at night).",
           {"mode": {"enum": ["light", "dark", "automatic"]}}, True,
           lambda a: f"Switch to the {a['mode']} style", set_style),
    Action("set_accent", "The accent colour of buttons, switches and selections.",
           {"color": {"enum": ACCENTS}}, True, lambda a: f"Make the accent colour {a['color']}",
           lambda a: _set("org.gnome.desktop.interface", "accent-color", GLib.Variant("s", a["color"]),
                          f"Accent colour {a['color']}")),
    Action("set_dock_size", "How big the icons in the dock are.",
           {"size": {"enum": list(DOCK_SIZES)}}, True, lambda a: f"Make the dock icons {a['size']}",
           lambda a: _set("org.gnome.shell.extensions.dash-to-dock", "dash-max-icon-size",
                          GLib.Variant("i", DOCK_SIZES[a["size"]]), f"Dock icons {a['size']}")),
    Action("set_text_size", "How big text is everywhere.",
           {"size": {"enum": list(TEXT_SIZES)}}, True, lambda a: f"Make text {a['size']}",
           lambda a: _set("org.gnome.desktop.interface", "text-scaling-factor",
                          GLib.Variant("d", TEXT_SIZES[a["size"]]), f"Text {a['size']}")),
    Action("set_night_light", "Warmer screen colours in the evening.",
           {"on": {"type": "boolean"}}, True, lambda a: f"Turn Night Light {'on' if a['on'] else 'off'}",
           lambda a: _set("org.gnome.settings-daemon.plugins.color", "night-light-enabled",
                          GLib.Variant("b", a["on"]), f"Night Light {'on' if a['on'] else 'off'}")),
    Action("set_do_not_disturb", "Hide notification pop-ups (Do Not Disturb).",
           {"on": {"type": "boolean"}}, True, lambda a: f"Turn Do Not Disturb {'on' if a['on'] else 'off'}",
           lambda a: _set("org.gnome.desktop.notifications", "show-banners", GLib.Variant("b", not a["on"]),
                          f"Do Not Disturb {'on' if a['on'] else 'off'}")),
    Action("set_screen_lock", "Minutes away before the screen locks (0 means never).",
           {"minutes": {"enum": LOCK_MINUTES}}, True,
           lambda a: "Never lock the screen by itself" if not a["minutes"]
           else f"Lock the screen after {a['minutes']} minute{'s' if a['minutes'] != 1 else ''} away",
           lambda a: _set("org.gnome.desktop.session", "idle-delay", GLib.Variant("u", a["minutes"] * 60),
                          "Screen lock changed")),
    Action("set_volume", "Sound volume, 0 to 100.",
           {"percent": {"type": "integer", "minimum": 0, "maximum": 100}}, True,
           lambda a: f"Set the volume to {a['percent']}%", set_volume),
    Action("set_wifi", "Turn Wi-Fi on or off.",
           {"on": {"type": "boolean"}}, True, lambda a: f"Turn Wi-Fi {'on' if a['on'] else 'off'}", set_wifi),
    Action("create_folder", "Make a new folder.",
           {"name": {"type": "string", "maxLength": 120}, "where": {"enum": list(PLACES)}}, True,
           lambda a: f"Make a folder “{a['name']}” in {a['where'].capitalize()}", create_folder),
    Action("open_app", "Open an app by its name.",
           {"name": {"type": "string", "maxLength": 60}}, False, lambda a: f"Open {a['name']}", open_app),
    Action("open_settings", "Open a page of Kulan Settings.",
           {"page": {"enum": SETTINGS_PAGES}}, False,
           lambda a: f"Open Kulan Settings: {a['page'].replace('-', ' ')}", open_settings),
    Action("find_files", "Find files in the home folder whose names contain these words.",
           {"query": {"type": "string", "maxLength": 80}}, False,
           lambda a: f"Look for files named like “{a['query']}”", find_files),
]
BY_ID = {a.id: a for a in ACTIONS}


def plan_schema() -> dict:
    steps = [{"type": "object", "additionalProperties": False, "required": ["action", "args"],
              "properties": {"action": {"const": a.id},
                             "args": {"type": "object", "additionalProperties": False,
                                      "required": list(a.args), "properties": a.args}}}
             for a in ACTIONS]
    return {"type": "object", "additionalProperties": False, "required": ["reply", "steps"],
            "properties": {"reply": {"type": "string", "maxLength": 300},
                           "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {"anyOf": steps}}}}


SYSTEM = """You are Kulan Assistant on a Kulan OS computer. You turn the person's request into steps,
using only these actions:
{actions}
Rules:
- Use only the actions above, with exactly the listed arguments. Never invent actions.
- If the request can't be done with these actions (deleting files, passwords, installing,
  anything else), return no steps and say briefly in "reply" what they can do instead.
- "reply" is one or two short, friendly sentences about what will happen.
- At most {max} steps."""


def system_prompt() -> str:
    lines = []
    for a in ACTIONS:
        args = ", ".join(f"{k}: {('one of ' + '/'.join(map(str, v['enum']))) if 'enum' in v else v.get('type')}"
                         for k, v in a.args.items())
        lines.append(f"- {a.id}({args}): {a.help}")
    return SYSTEM.format(actions="\n".join(lines), max=MAX_STEPS)


@dataclass
class Step:
    action: Action
    args: dict
    text: str
    result: Result | None = None


def validate(plan: dict) -> tuple[str, list[Step]]:
    """Only well-formed steps of known actions survive (the schema should
    already guarantee it; this is the second lock)."""
    reply = str(plan.get("reply", "")).strip()[:300] if isinstance(plan, dict) else ""
    steps = []
    for raw in (plan.get("steps") or [])[:MAX_STEPS] if isinstance(plan, dict) else []:
        if not isinstance(raw, dict):
            continue
        action = BY_ID.get(str(raw.get("action")))
        args = raw.get("args")
        if action is None or not isinstance(args, dict) or set(args) != set(action.args):
            continue
        ok = True
        for key, spec in action.args.items():
            value = args[key]
            if "enum" in spec and value not in spec["enum"]:
                ok = False
            elif spec.get("type") == "boolean" and not isinstance(value, bool):
                ok = False
            elif spec.get("type") == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                ok = False
            elif spec.get("type") == "string" and (not isinstance(value, str) or not value.strip()):
                ok = False
        if ok:
            steps.append(Step(action, args, action.describe(args)))
    return reply, steps


def plan(request: str) -> tuple[str, list[Step]]:
    """Ask the model for a plan. Raises engine.EngineError if it can't run."""
    engine.ensure_running()
    body = {
        "messages": [{"role": "system", "content": system_prompt()},
                     {"role": "user", "content": request.strip()[:500]}],
        "temperature": 0.1,
        "max_tokens": 400,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": plan_schema()}},
    }
    with engine.request("/v1/chat/completions", body, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    engine.touch_last_use()
    content = data["choices"][0]["message"].get("content") or "{}"
    content = engine.THINK.sub("", content).strip()
    try:
        return validate(json.loads(content))
    except ValueError:
        return "Sorry, I couldn't work out what to do. Try saying it another way.", []


def run_step(step: Step) -> Result:
    try:
        step.result = step.action.run(step.args)
    except Exception as e:  # an action never takes the window down
        step.result = Result(False, f"That didn't work: {e}")
    return step.result
