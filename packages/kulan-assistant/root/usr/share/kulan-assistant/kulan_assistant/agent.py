"""Kulan Assistant, "do it for me": turn a request into a plan of Kulan actions.

The model never runs anything itself. It writes a plan in a fixed JSON shape
(the engine is held to a JSON schema, so even a small model can't invent
actions or arguments), choosing only from the actions below. Kulan shows the
plan in plain words; anything that changes the computer waits for "Do it",
and settings changes can be undone. Things the actions can't do (deleting
files, typing passwords, anything as administrator) aren't possible at all.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from . import engine, instant

MAX_STEPS = 6
MAX_REPLY = 600
MAX_REPLY_APP = 1500  # the Ask Kulan app has room for longer answers
SETTINGS_PAGES = ["wifi", "bluetooth", "phone", "printers", "sound", "focus", "notifications", "users", "parental-controls",
                  "appearance", "desktop", "dock", "top-bar", "display", "battery", "storage", "login-items", "apps",
                  "security", "privacy", "backups", "keyboard", "mouse", "accessibility", "assistant", "prayer-times",
                  "date-time", "region", "updates", "add-ons", "about"]
PHOTO_WHEN = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month", "this_year", "last_year"]
PHOTO_TYPES = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif", ".tif", ".tiff", ".raw", ".cr2", ".nef", ".dng")
LARGE_MB = [100, 500, 1000]
DUPLICATE_PLACES = ["downloads", "documents", "pictures", "desktop", "home"]
TODO_FILE = Path(GLib.get_user_data_dir()) / "kulan" / "todo.json"  # Kulan Shell's To-do widget
MAX_FOUND = 40
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
        return Result(False, "This setting is locked by compliance mode or your organisation")
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


# --- tidying a folder ---------------------------------------------------------------------
# Loose files in Downloads or on the Desktop go into folders by kind, inside
# the same folder. Folders, hidden files, files still downloading and files
# of unknown kinds stay where they are; nothing is overwritten or deleted,
# and Undo puts every file back.
TIDY_PLACES = ["downloads", "desktop"]
KINDS = {
    "Documents": {"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "xls", "xlsx", "ods", "csv", "ppt", "pptx",
                  "odp", "epub", "pages", "numbers", "key"},
    "Pictures": {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "svg", "bmp", "tif", "tiff", "avif"},
    "Videos": {"mp4", "mkv", "mov", "avi", "webm", "m4v", "wmv"},
    "Music": {"mp3", "m4a", "flac", "wav", "ogg", "opus", "aac", "wma"},
    "Archives": {"zip", "tar", "gz", "tgz", "xz", "bz2", "7z", "rar", "zst"},
    "Installers": {"deb", "flatpakref", "flatpak", "appimage", "iso", "exe", "msi", "dmg", "pkg"},
}
PARTIAL = {"part", "crdownload", "download", "partial", "tmp"}
RECENT_S = 120  # a file changed in the last two minutes may still be downloading


def kind_of(name: str) -> str | None:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name.strip(".") else ""
    if name.lower().endswith((".tar.gz", ".tar.xz", ".tar.zst", ".tar.bz2")):
        return "Archives"
    return next((folder for folder, exts in KINDS.items() if ext in exts), None)


def tidy_moves(folder: Path) -> list[tuple[Path, Path]]:
    """(from, to) for every loose file that would move. Nothing is touched."""
    moves: list[tuple[Path, Path]] = []
    taken: set[Path] = set()
    now = time.time()
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        if name.rsplit(".", 1)[-1].lower() in PARTIAL:
            continue
        try:
            if now - entry.stat().st_mtime < RECENT_S:
                continue
        except OSError:
            continue
        kind = kind_of(name)
        if kind is None:
            continue
        target = folder / kind / name
        stem, dot, ext = name.rpartition(".") if "." in name.strip(".") else (name, "", "")
        n = 2
        while target.exists() or target in taken:
            target = folder / kind / (f"{stem} ({n}){dot}{ext}" if dot else f"{name} ({n})")
            n += 1
        taken.add(target)
        moves.append((entry, target))
    return moves


def describe_tidy(a: dict) -> str:
    folder = _place(a["where"])
    moves = tidy_moves(folder)
    label = a["where"].capitalize()
    if not moves:
        return f"Sort the files in {label} into folders (it already looks tidy)"
    counts: dict[str, int] = {}
    for _src, dst in moves:
        counts[dst.parent.name] = counts.get(dst.parent.name, 0) + 1
    parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    return f"Move {len(moves)} file{'s' if len(moves) != 1 else ''} in {label} into folders: {parts}"


def tidy_folder(a: dict) -> Result:
    folder = _place(a["where"])
    if not folder.is_dir():
        return Result(False, f"There's no {a['where'].capitalize()} folder")
    moves = tidy_moves(folder)
    if not moves:
        return Result(True, "It's already tidy")
    made: list[Path] = []
    done: list[tuple[Path, Path]] = []
    for src, dst in moves:
        try:
            if not dst.parent.exists():
                dst.parent.mkdir()
                made.append(dst.parent)
            if dst.exists():  # appeared meanwhile: leave the file where it is
                continue
            os.rename(src, dst)  # same folder tree, so a rename: never a copy
            done.append((src, dst))
        except OSError:
            continue

    def undo() -> None:
        for src, dst in reversed(done):
            try:
                if dst.exists() and not src.exists():
                    os.rename(dst, src)
            except OSError:
                pass
        for path in made:
            try:
                path.rmdir()  # only if empty again
            except OSError:
                pass

    skipped = len(moves) - len(done)
    message = f"Moved {len(done)} file{'s' if len(done) != 1 else ''} into folders"
    return Result(bool(done) or not moves, message + (f"; {skipped} left where they were" if skipped else ""),
                  undo=undo if done else None)


# --- reminders -------------------------------------------------------------------------
# A systemd user timer per reminder, so it comes even after a restart (shown
# at the next sign-in if the computer was off). The words are kept in a file
# of their own, never in the unit files.
MAX_REMINDER_MINUTES = 60 * 24 * 7


def reminders_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "kulan-assistant" / "reminders"


def units_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user"


def reminder_time(minutes: int, now: datetime.datetime | None = None) -> datetime.datetime:
    now = now or datetime.datetime.now()
    return (now + datetime.timedelta(minutes=minutes)).replace(second=0, microsecond=0)


def reminder_at(a: dict) -> datetime.datetime:
    try:
        return datetime.datetime.fromisoformat(a["_at"])
    except (KeyError, TypeError, ValueError):
        return reminder_time(int(a["minutes"]))


def when_words(at: datetime.datetime, now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    clock = at.strftime("%-I:%M %p").replace("AM", "am").replace("PM", "pm")
    if at.date() == now.date():
        return f"at {clock}"
    if at.date() == now.date() + datetime.timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"on {at:%A %-d %B} at {clock}"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30, check=False)


def remove_reminder(rid: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{12}", rid):
        return
    unit = f"kulan-reminder-{rid}"
    _systemctl("disable", "--now", f"{unit}.timer")
    for path in (units_dir() / f"{unit}.timer", units_dir() / f"{unit}.service", reminders_dir() / f"{rid}.json"):
        try:
            path.unlink()
        except OSError:
            pass
    _systemctl("daemon-reload")


def set_reminder(a: dict) -> Result:
    minutes = int(a["minutes"])
    message = " ".join(str(a["message"]).split())[:120]
    if not 1 <= minutes <= MAX_REMINDER_MINUTES or not message:
        return Result(False, "That reminder time isn't possible")
    if shutil.which("systemctl") is None:
        return Result(False, "Reminders aren't available here")
    # The time shown in the plan, even if "Do it" is pressed a while later.
    at = reminder_at(a)
    if at <= datetime.datetime.now():
        at = reminder_time(1)
    rid = secrets.token_hex(6)
    unit = f"kulan-reminder-{rid}"
    reminders_dir().mkdir(parents=True, exist_ok=True)
    units_dir().mkdir(parents=True, exist_ok=True)
    (reminders_dir() / f"{rid}.json").write_text(json.dumps({"message": message, "at": at.isoformat(),
                                                             "title": "Timer" if a.get("_title") == "Timer" else "Reminder"}))
    (units_dir() / f"{unit}.service").write_text(
        "[Unit]\nDescription=Kulan Assistant reminder\n\n[Service]\nType=oneshot\n"
        f"ExecStart=/usr/bin/kulan-assistant reminder-fire {rid}\n")
    (units_dir() / f"{unit}.timer").write_text(
        "[Unit]\nDescription=Kulan Assistant reminder\n\n[Timer]\n"
        f"OnCalendar={at:%Y-%m-%d %H:%M:00}\nPersistent=true\nAccuracySec=1s\n\n"
        "[Install]\nWantedBy=timers.target\n")
    _systemctl("daemon-reload")
    started = _systemctl("enable", "--now", f"{unit}.timer")
    if started.returncode != 0:
        remove_reminder(rid)
        return Result(False, "The reminder couldn't be set: " + (started.stderr.strip()[:120] or "systemd said no"))
    return Result(True, f"I'll remind you {when_words(at)}", undo=lambda: remove_reminder(rid))


def list_reminders() -> list[dict]:
    """Reminders still to come: [{"id", "message", "at"}], soonest first."""
    out = []
    for path in sorted(reminders_dir().glob("*.json")):
        rid = path.stem
        if not re.fullmatch(r"[0-9a-f]{12}", rid):
            continue
        try:
            data = json.loads(path.read_text())
            datetime.datetime.fromisoformat(str(data["at"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out.append({"id": rid, "message": str(data.get("message", ""))[:120], "at": str(data["at"])})
    return sorted(out, key=lambda r: r["at"])


def fire_reminder(rid: str) -> int:
    """Run by the reminder's timer: show it, then clear it away."""
    if not re.fullmatch(r"[0-9a-f]{12}", rid):
        return 2
    try:
        data = json.loads((reminders_dir() / f"{rid}.json").read_text())
    except (OSError, ValueError):
        remove_reminder(rid)
        return 1
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        hints = {"urgency": GLib.Variant("y", 2), "desktop-entry": GLib.Variant("s", "org.kulan.Assistant")}
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Kulan Assistant", 0, "org.kulan.Assistant",
                                                       "Timer" if data.get("title") == "Timer" else "Reminder",
                                                       str(data.get("message", "")), [], hints, 0)),
                      None, Gio.DBusCallFlags.NONE, 10000, None)
    except GLib.Error:
        return 1  # not shown: keep it, so it comes again at the next sign-in
    remove_reminder(rid)
    return 0


# --- photos, big files and duplicates -----------------------------------------------------
def _walk(top: Path, deadline: float):
    """Files under top, skipping hidden folders and caches; stops at the deadline."""
    for root, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("snap", "node_modules", "__pycache__")]
        for f in files:
            if not f.startswith("."):
                yield Path(root) / f
        if time.monotonic() > deadline:
            return


def photo_range(when: str, today: datetime.date | None = None) -> tuple[datetime.date, datetime.date]:
    """[start, end) dates for a request like "last_week"."""
    today = today or datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    first = today.replace(day=1)
    previous_first = (first - datetime.timedelta(days=1)).replace(day=1)
    return {
        "today": (today, today + datetime.timedelta(days=1)),
        "yesterday": (today - datetime.timedelta(days=1), today),
        "this_week": (monday, today + datetime.timedelta(days=1)),
        "last_week": (monday - datetime.timedelta(days=7), monday),
        "this_month": (first, today + datetime.timedelta(days=1)),
        "last_month": (previous_first, first),
        "this_year": (today.replace(month=1, day=1), today + datetime.timedelta(days=1)),
        "last_year": (today.replace(year=today.year - 1, month=1, day=1), today.replace(month=1, day=1)),
    }[when]


def find_photos(a: dict) -> Result:
    start, end = photo_range(a["when"])
    deadline = time.monotonic() + 15
    hits = []
    places = [_place("pictures"), _place("downloads"), _place("desktop"), _place("documents")]
    seen: set[Path] = set()
    for top in places:
        if not top.is_dir() or top in seen:
            continue
        seen.add(top)
        for path in _walk(top, deadline):
            if path.suffix.lower() not in PHOTO_TYPES:
                continue
            try:
                day = datetime.date.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
            if start <= day < end:
                hits.append((path.stat().st_mtime, str(path)))
    words = a["when"].replace("_", " ")
    if not hits:
        return Result(True, f"No photos from {words} in Pictures, Downloads, Desktop or Documents")
    hits.sort(reverse=True)
    return Result(True, f"{len(hits)} photo{'s' if len(hits) != 1 else ''} from {words}"
                  + (f" (the newest {MAX_FOUND} shown)" if len(hits) > MAX_FOUND else ""),
                  found=[p for _t, p in hits[:MAX_FOUND]])


def find_large_files(a: dict) -> Result:
    limit = int(a["min_mb"]) * 1024 * 1024
    deadline = time.monotonic() + 15
    hits = []
    for path in _walk(Path.home(), deadline):
        try:
            size = path.lstat().st_size
        except OSError:
            continue
        if size >= limit and not path.is_symlink():
            hits.append((size, str(path)))
    if not hits:
        return Result(True, f"No files of {a['min_mb']} MB or more in your home folder")
    hits.sort(reverse=True)
    total = sum(n for n, _p in hits)
    return Result(True, f"{len(hits)} file{'s' if len(hits) != 1 else ''} of {a['min_mb']} MB or more, "
                        f"{total / 1024 ** 3:.1f} GB together. Biggest first; delete the ones you don't need in Files",
                  found=[p for _n, p in hits[:MAX_FOUND]])


def duplicate_groups(top: Path, deadline: float) -> list[list[Path]]:
    """Files with the very same contents: same size first, then the same SHA-256."""
    import hashlib
    by_size: dict[int, list[Path]] = {}
    for path in _walk(top, deadline):
        try:
            if path.is_symlink():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        if size >= 1024:  # tiny files aren't worth it
            by_size.setdefault(size, []).append(path)
    groups = []
    for size, paths in sorted(by_size.items(), reverse=True):
        if len(paths) < 2 or time.monotonic() > deadline:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in paths:
            digest = hashlib.sha256()
            try:
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:
                continue
            by_hash.setdefault(digest.hexdigest(), []).append(path)
        groups.extend(sorted(g, key=lambda p: p.stat().st_mtime) for g in by_hash.values() if len(g) > 1)
    return groups


def find_duplicates(a: dict) -> Result:
    top = _place(a["where"])
    if not top.is_dir():
        return Result(False, f"There's no {a['where'].capitalize()} folder")
    groups = duplicate_groups(top, time.monotonic() + 20)
    if not groups:
        return Result(True, f"No duplicate files in {a['where'].capitalize()}")
    extra = [p for g in groups for p in g[1:]]  # the copies after the oldest one
    space = sum(p.stat().st_size for p in extra if p.exists())
    return Result(True, f"{len(groups)} file{'s have' if len(groups) != 1 else ' has'} copies: {len(extra)} "
                        f"extra cop{'ies' if len(extra) != 1 else 'y'}, {space / 1024 ** 2:.0f} MB. Listed: the "
                        "copies; the oldest of each stays. Nothing is deleted",
                  found=[str(p) for p in extra[:MAX_FOUND]])


# --- timers and to-dos ---------------------------------------------------------------------
def set_timer(a: dict) -> Result:
    label = " ".join(str(a["label"]).split())[:60] or "Timer"
    result = set_reminder({"message": f"{label}: {a['minutes']} minutes are up", "minutes": a["minutes"],
                           **({"_at": a["_at"]} if "_at" in a else {}), "_title": "Timer"})
    if result.ok:
        result.message = f"Timer set for {a['minutes']} minute{'s' if a['minutes'] != 1 else ''}"
    return result


def read_todos() -> list[dict]:
    try:
        data = json.loads(TODO_FILE.read_text())
        items = data.get("items") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    except (OSError, ValueError):
        return []


def write_todos(items: list[dict]) -> None:
    TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TODO_FILE.with_name(TODO_FILE.name + ".kulan-new")
    tmp.write_text(json.dumps({"version": 1, "items": items[:200]}, indent=1))
    os.chmod(tmp, 0o600)
    os.replace(tmp, TODO_FILE)


def add_todo(a: dict) -> Result:
    text = " ".join(str(a["task"]).split())[:200]
    if not text:
        return Result(False, "There's no task to add")
    items = read_todos()
    if len(items) >= 200:
        return Result(False, "The to-do list is full. Clear completed tasks first")
    tid = GLib.uuid_string_random()
    write_todos([{"id": tid, "text": text, "done": False}, *items])

    def undo() -> None:
        write_todos([i for i in read_todos() if i.get("id") != tid])
    return Result(True, f"Added “{text}” to your to-do list", undo=undo)


# --- fixing common problems --------------------------------------------------------------
def fix_wifi(_a: dict) -> Result:
    if shutil.which("nmcli") is None:
        return Result(False, "Wi-Fi settings aren't available")
    subprocess.run(["nmcli", "radio", "wifi", "off"], capture_output=True, timeout=20)
    time.sleep(2)
    on = subprocess.run(["nmcli", "radio", "wifi", "on"], capture_output=True, text=True, timeout=20)
    if on.returncode != 0:
        return Result(False, on.stderr.strip() or "Wi-Fi couldn't be turned back on")
    for _ in range(25):
        time.sleep(1)
        state = subprocess.run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"], capture_output=True,
                               text=True, timeout=10).stdout.strip()
        if state == "full":
            return Result(True, "Wi-Fi restarted, and the internet works")
    return Result(False, "Wi-Fi restarted, but there's no internet yet. Open Wi-Fi settings to pick a network, "
                         "or restart the router")


def fix_sound(_a: dict) -> Result:
    if shutil.which("systemctl") is None:
        return Result(False, "Sound settings aren't available")
    r = subprocess.run(["systemctl", "--user", "restart", "wireplumber", "pipewire", "pipewire-pulse"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return Result(False, "The sound system couldn't be restarted: " + r.stderr.strip()[:120])
    time.sleep(2)
    if shutil.which("wpctl"):
        volume = subprocess.run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True,
                                timeout=10).stdout
        if not volume.strip():
            return Result(False, "Sound restarted, but no speakers or headphones were found. Check the cable or "
                                 "Bluetooth")
        if "MUTED" in volume:
            return Result(True, "Sound restarted. It's muted, so press the volume key or open Sound settings")
    return Result(True, "Sound restarted")


def _size(path: Path, deadline: float) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        if time.monotonic() > deadline:
            break
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def fix_bluetooth(_a: dict) -> Result:
    tool = shutil.which("bluetoothctl")
    if tool is None:
        return Result(False, "Bluetooth isn't available on this computer")
    if shutil.which("rfkill"):
        subprocess.run(["rfkill", "unblock", "bluetooth"], capture_output=True, timeout=10)
    subprocess.run([tool, "power", "off"], capture_output=True, timeout=20)
    time.sleep(2)
    on = subprocess.run([tool, "power", "on"], capture_output=True, text=True, timeout=20)
    if on.returncode != 0 or "Failed" in on.stdout:
        return Result(False, "Bluetooth couldn't be turned on. Is there a Bluetooth switch or key on the computer?")
    time.sleep(3)
    connected = subprocess.run([tool, "devices", "Connected"], capture_output=True, text=True, timeout=10).stdout
    names = [line.split(" ", 2)[2] for line in connected.splitlines() if line.startswith("Device ") and line.count(" ") >= 2]
    if names:
        return Result(True, "Bluetooth restarted. Connected: " + ", ".join(names[:4]))
    return Result(True, "Bluetooth restarted. Nothing is connected yet. Turn the device off and on, or pair it again "
                        "in Bluetooth settings")


def fix_printer(_a: dict) -> Result:
    if shutil.which("lpstat") is None:
        return Result(False, "Printing isn't set up on this computer")
    printers = subprocess.run(["lpstat", "-p"], capture_output=True, text=True, timeout=15).stdout
    names = [line.split()[1] for line in printers.splitlines() if line.startswith("printer ")]
    if not names:
        return Result(False, "No printers are set up. Add one in Kulan Settings › Printers")
    resumed = []
    for line in printers.splitlines():
        parts = line.split()
        if line.startswith("printer ") and "disabled" in line and len(parts) > 1 and shutil.which("cupsenable"):
            if subprocess.run(["cupsenable", parts[1]], capture_output=True, timeout=15).returncode == 0:
                resumed.append(parts[1])
    jobs = subprocess.run(["lpstat", "-o"], capture_output=True, text=True, timeout=15).stdout.strip().splitlines()
    message = (f"Turned {', '.join(resumed)} back on. " if resumed else "")
    if jobs:
        message += (f"{len(jobs)} print job{'s are' if len(jobs) != 1 else ' is'} waiting. Check the printer is on and "
                    "has paper")
    else:
        message += "Nothing is waiting to print. If the printer is on and still doesn't print, remove and add it again in Printers"
    return Result(bool(resumed) or not jobs, message)


def back_up_now(_a: dict) -> Result:
    if shutil.which("deja-dup") is None:
        return Result(False, "Backups aren't installed")
    deja = _settings("org.gnome.DejaDup")
    backend = deja.get_string("backend") if deja is not None and deja.props.settings_schema.has_key("backend") else ""
    if backend not in ("drive", "local", "remote", "google", "microsoft"):
        open_settings({"page": "backups"})
        return Result(False, "Backups aren't set up yet. Choose a drive in Kulan Settings › Backups")
    try:
        subprocess.Popen(["deja-dup", "--backup"], start_new_session=True, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        return Result(False, f"The backup didn't start: {e}")
    return Result(True, "Backing up now. Déjà Dup shows the progress")


def check_storage(_a: dict) -> Result:
    deadline = time.monotonic() + 10
    disk = shutil.disk_usage(Path.home())
    places = [("Bin", Path.home() / ".local/share/Trash"), ("Downloads", _place("downloads")),
              ("App caches", Path.home() / ".cache"), ("Videos", _place("videos"))]
    sizes = [(label, _size(path, deadline)) for label, path in places if path.exists()]
    gbs = lambda n: f"{n / 1024 ** 3:.1f} GB"  # noqa: E731
    parts = ", ".join(f"{label} {gbs(n)}" for label, n in sorted(sizes, key=lambda kv: -kv[1]) if n >= 50 * 1024 ** 2)
    return Result(True, f"{gbs(disk.free)} free of {gbs(disk.total)}." + (f" Biggest: {parts}." if parts else "")
                  + " Kulan Settings › Storage can clear the Bin and old caches.")


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
           lambda a: "Never lock the screen automatically" if not a["minutes"]
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
    Action("tidy_folder", "Tidy Downloads or the Desktop: put loose files into folders by kind (Documents, "
           "Pictures, Videos, Music, Archives, Installers). Nothing is deleted.",
           {"where": {"enum": TIDY_PLACES}}, True, describe_tidy, tidy_folder),
    Action("set_reminder", "Remind the person about something, a number of minutes from now (for a clock time, "
           "work out the minutes from the time now).",
           {"message": {"type": "string", "maxLength": 120},
            "minutes": {"type": "integer", "minimum": 1, "maximum": MAX_REMINDER_MINUTES}}, True,
           lambda a: f"Remind you {when_words(reminder_at(a))}: “{a['message']}”", set_reminder),
    Action("fix_wifi", "Wi-Fi or the internet isn't working: turn Wi-Fi off and on again and check the connection.",
           {}, True, lambda a: "Restart Wi-Fi and check the internet", fix_wifi),
    Action("fix_sound", "No sound or the wrong speakers: restart the sound system and check the output.",
           {}, True, lambda a: "Restart the sound system and check the speakers", fix_sound),
    Action("check_storage", "See how much disk space is free and what uses the most (the computer is full, or "
           "slow because the disk is nearly full).",
           {}, False, lambda a: "Check what's using disk space", check_storage),
    Action("find_photos", "Find photos from a time (today, last week, last month...) in Pictures, Downloads, "
           "Desktop and Documents.",
           {"when": {"enum": PHOTO_WHEN}}, False,
           lambda a: f"Find photos from {a['when'].replace('_', ' ')}", find_photos),
    Action("find_large_files", "List the biggest files in the home folder (to free up space).",
           {"min_mb": {"enum": LARGE_MB}}, False,
           lambda a: f"List files of {a['min_mb']} MB or more", find_large_files),
    Action("find_duplicates", "Find files that are exact copies of each other in a folder. Nothing is deleted.",
           {"where": {"enum": DUPLICATE_PLACES}}, False,
           lambda a: f"Look for duplicate files in {a['where'].capitalize()}", find_duplicates),
    Action("set_timer", "A countdown timer: a notice when the minutes are up.",
           {"minutes": {"type": "integer", "minimum": 1, "maximum": 180}, "label": {"type": "string", "maxLength": 60}},
           True, lambda a: f"Set a {a['minutes']}-minute timer ({' '.join(str(a['label']).split())[:60] or 'Timer'})",
           set_timer),
    Action("add_todo", "Add a task to the person's to-do list (the To-do widget).",
           {"task": {"type": "string", "maxLength": 200}}, True,
           lambda a: f"Add “{' '.join(str(a['task']).split())[:200]}” to your to-do list", add_todo),
    Action("fix_bluetooth", "Bluetooth headphones, mouse or keyboard won't connect: restart Bluetooth.",
           {}, True, lambda a: "Restart Bluetooth and check what's connected", fix_bluetooth),
    Action("fix_printer", "The printer won't print: turn paused printers back on and check the queue.",
           {}, True, lambda a: "Check the printers and their queue", fix_printer),
    Action("back_up_now", "Back up the person's files now (to the drive chosen in Backups).",
           {}, True, lambda a: "Back up your files now", back_up_now),
]
BY_ID = {a.id: a for a in ACTIONS}


def plan_schema(max_reply: int = MAX_REPLY) -> dict:
    steps = [{"type": "object", "additionalProperties": False, "required": ["action", "args"],
              "properties": {"action": {"const": a.id},
                             "args": {"type": "object", "additionalProperties": False,
                                      "required": list(a.args), "properties": a.args}}}
             for a in ACTIONS]
    return {"type": "object", "additionalProperties": False, "required": ["reply", "steps"],
            "properties": {"reply": {"type": "string", "maxLength": max_reply},
                           "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {"anyOf": steps}}}}


SYSTEM = """You are Kulan Assistant, built into Kulan OS on this person's computer. You run on the computer
itself: you can't see the screen or the internet. Someone typed a question or a request.
- A question (how, why, what, is…): answer it in "reply" in {length}, using the facts
  about this computer below when they matter. You can't look anything up: for news, prices, weather
  or anything that changes, say you can't check that here. Add steps only when one clearly helps (for example
  check_storage when the disk is nearly full, or open_settings for the right page).
- A request: turn it into steps, and say in one short sentence in "reply" what will happen.
Actions you can use:
{actions}
Rules:
- Use only the actions above, with exactly the listed arguments. Never invent actions.
- If a request can't be done with these actions (deleting files, passwords, installing,
  anything else), return NO steps at all (not even a related one) and say briefly in "reply"
  how they can do it themselves.
- Be friendly and plain; no lists, no markdown. At most {max} steps.{conversation}
Facts about this computer now:
{facts}"""


# Worked examples: small models follow these far better than the rules alone
# (especially "no steps" for what the actions can't do).
EXAMPLES = [
    {"role": "user", "content": "Delete everything in my Downloads folder"},
    {"role": "assistant", "content": json.dumps({
        "reply": "I can't delete files. In Files, open Downloads, press Ctrl+A to select everything, then press "
                 "Delete: it all goes to the Bin, so you can still get it back.", "steps": []})},
    {"role": "user", "content": "Dark mode please, and open Files"},
    {"role": "assistant", "content": json.dumps({
        "reply": "Switching to dark mode and opening Files.",
        "steps": [{"action": "set_style", "args": {"mode": "dark"}}, {"action": "open_app", "args": {"name": "Files"}}]})},
]


SHORT_ANSWER = "up to four short sentences"
LONG_ANSWER = "a clear answer of up to two short paragraphs (more only if they asked for detail)"
CONVERSATION_NOTE = ("\n- Earlier messages of this conversation come before the newest one. Use them to understand "
                     "follow-ups (\"and the Desktop too\", \"make it bigger\"), but plan steps only for the newest "
                     "message; never repeat steps that were already done.")


def system_prompt(long: bool = False, conversation: bool = False) -> str:
    lines = []
    for a in ACTIONS:
        args = ", ".join(f"{k}: {('one of ' + '/'.join(map(str, v['enum']))) if 'enum' in v else v.get('type')}"
                         for k, v in a.args.items())
        lines.append(f"- {a.id}({args}): {a.help}")
    try:
        from . import facts
        now = facts.snapshot()
    except Exception:  # noqa: BLE001 - facts are a help, never a reason to fail
        now = f"Now: {datetime.datetime.now():%A %-d %B %Y, %H:%M}"
    return SYSTEM.format(actions="\n".join(lines), max=MAX_STEPS, facts=now,
                         length=LONG_ANSWER if long else SHORT_ANSWER,
                         conversation=CONVERSATION_NOTE if conversation else "")


@dataclass
class Step:
    action: Action
    args: dict
    text: str
    result: Result | None = None


def validate(plan: dict, max_reply: int = MAX_REPLY) -> tuple[str, list[Step]]:
    """Only well-formed steps of known actions survive (the schema should
    already guarantee it; this is the second lock)."""
    reply = str(plan.get("reply", "")).strip()[:max_reply] if isinstance(plan, dict) else ""
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
            elif spec.get("type") == "integer" and (not isinstance(value, int) or isinstance(value, bool)
                                                     or not spec.get("minimum", value) <= value <= spec.get("maximum", value)):
                ok = False
            elif spec.get("type") == "string" and (not isinstance(value, str) or not value.strip()
                                                    or len(value) > spec.get("maxLength", 10_000)):
                ok = False
        if ok:
            if action.id in ("set_reminder", "set_timer"):
                # Fixed when the plan is made, so the time shown is the time set.
                args = {**args, "_at": reminder_time(int(args["minutes"])).isoformat()}
            steps.append(Step(action, args, action.describe(args)))
    return reply, steps


def history_messages(history: list[dict] | None) -> list[dict]:
    """Earlier exchanges of a conversation ({"user", "reply", "steps"}), as
    the chat the model sees: each answer in the same JSON it gives."""
    out = []
    for turn in history or []:
        steps = [{"action": s["action"], "args": {k: v for k, v in s.get("args", {}).items() if not k.startswith("_")}}
                 for s in turn.get("steps", []) if s.get("action") in BY_ID]
        out.append({"role": "user", "content": str(turn.get("user", "")).strip()[:500]})
        # Earlier answers shortened: the model's memory (CONTEXT) also holds the
        # actions, the facts and the new answer.
        out.append({"role": "assistant", "content": json.dumps(
            {"reply": str(turn.get("reply", "")).strip()[:MAX_REPLY], "steps": steps})})
    return out


def plan(request: str, history: list[dict] | None = None, long: bool = False) -> tuple[str, list[Step]]:
    """Ask the model for a plan. Raises engine.EngineError if it can't run.
    The time, the date, battery and free space are answered straight away.
    history: earlier exchanges of the conversation (the Ask Kulan app);
    long: room for a longer answer (the app has space for it)."""
    quick = instant.answer(request)
    if quick:
        return quick, []
    engine.ensure_running()
    max_reply = MAX_REPLY_APP if long else MAX_REPLY
    body = {
        "messages": [{"role": "system", "content": system_prompt(long=long, conversation=bool(history))}, *EXAMPLES,
                     *history_messages(history), {"role": "user", "content": request.strip()[:500]}],
        "temperature": 0.1,
        "max_tokens": 1100 if long else 700,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": plan_schema(max_reply)}},
    }
    with engine.request("/v1/chat/completions", body, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    engine.touch_last_use()
    content = data["choices"][0]["message"].get("content") or "{}"
    content = engine.THINK.sub("", content).strip()
    try:
        return validate(json.loads(content), max_reply)
    except ValueError:
        return "Sorry, I couldn't work out what to do. Try saying it another way.", []


def run_step(step: Step) -> Result:
    try:
        step.result = step.action.run(step.args)
    except Exception as e:  # an action never takes the window down
        step.result = Result(False, f"That didn't work: {e}")
    return step.result


def self_test() -> None:
    """Checks that need no model: plans are checked, tidying moves and puts
    back, reminders are written and cleared. Uses a throwaway home folder."""
    import tempfile
    from unittest import mock

    # Plans: only known actions with valid arguments survive.
    reply, steps = validate({"reply": "OK", "steps": [
        {"action": "set_style", "args": {"mode": "dark"}},
        {"action": "rm_rf", "args": {}},
        {"action": "set_volume", "args": {"percent": 300}},
        {"action": "set_reminder", "args": {"message": "Call Amina", "minutes": 30}},
        {"action": "set_reminder", "args": {"message": "x", "minutes": 0}},
        {"action": "fix_wifi", "args": {}},
        {"action": "tidy_folder", "args": {"where": "documents"}},
    ]})
    assert reply == "OK" and [s.action.id for s in steps] == ["set_style", "set_reminder", "fix_wifi"], steps
    assert "_at" in steps[1].args and steps[1].text.startswith("Remind you at") or "tomorrow" in steps[1].text, steps[1]
    assert validate("nonsense") == ("", [])
    assert len(validate({"reply": "x" * 5000, "steps": []})[0]) == MAX_REPLY
    prompt = system_prompt()
    assert "tidy_folder(where: one of downloads/desktop)" in prompt and "Now:" in prompt, prompt[:400]

    now = datetime.datetime(2026, 10, 23, 16, 5)
    assert when_words(datetime.datetime(2026, 10, 23, 17, 0), now) == "at 5:00 pm"
    assert when_words(datetime.datetime(2026, 10, 24, 9, 30), now) == "tomorrow at 9:30 am"
    assert when_words(datetime.datetime(2026, 10, 27, 9, 30), now) == "on Tuesday 27 October at 9:30 am"

    with tempfile.TemporaryDirectory() as home:
        env = {"HOME": home, "XDG_CONFIG_HOME": f"{home}/.config", "XDG_STATE_HOME": f"{home}/.local/state"}
        with mock.patch.dict(os.environ, env), mock.patch.object(Path, "home", return_value=Path(home)), \
                mock.patch.object(GLib, "get_user_special_dir", return_value=None):
            downloads = Path(home) / "Downloads"
            (downloads / "Pictures").mkdir(parents=True)
            old = time.time() - 3600
            for name in ("CV.pdf", "photo.JPG", "photo.jpg", "song.mp3", "backup.tar.gz", "kulan.iso",
                         "notes", "half.pdf.part", ".hidden.pdf", "weird.xyz"):
                (downloads / name).write_text(name)
                os.utime(downloads / name, (old, old))
            (downloads / "Pictures" / "photo.jpg").write_text("already there")
            (downloads / "new.pdf").write_text("just downloaded")
            (downloads / "folder.pdf").mkdir()
            moves = {src.name: str(dst.relative_to(downloads)) for src, dst in tidy_moves(downloads)}
            assert moves == {"CV.pdf": "Documents/CV.pdf", "photo.JPG": "Pictures/photo.JPG",
                             "photo.jpg": "Pictures/photo (2).jpg", "song.mp3": "Music/song.mp3",
                             "backup.tar.gz": "Archives/backup.tar.gz", "kulan.iso": "Installers/kulan.iso"}, moves
            assert describe_tidy({"where": "downloads"}).startswith("Move 6 files in Downloads into folders: Pictures 2")
            before = sorted(str(p.relative_to(downloads)) for p in downloads.rglob("*"))
            result = tidy_folder({"where": "downloads"})
            assert result.ok and result.undo and result.message == "Moved 6 files into folders", result
            assert (downloads / "Pictures" / "photo (2).jpg").read_text() == "photo.jpg"
            assert (downloads / "Pictures" / "photo.jpg").read_text() == "already there"
            assert (downloads / "notes").exists() and (downloads / "half.pdf.part").exists()
            result.undo()
            after = sorted(str(p.relative_to(downloads)) for p in downloads.rglob("*"))
            assert after == before, (before, after)
            assert tidy_folder({"where": "desktop"}).ok is False  # no Desktop folder

            # Reminders: written for systemd, cleared by Undo (systemctl faked).
            calls: list[tuple] = []
            fake = lambda *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", "")  # noqa: E731
            with mock.patch(f"{__name__}._systemctl", side_effect=fake), \
                    mock.patch("shutil.which", return_value="/usr/bin/systemctl"):
                result = set_reminder({"message": "Call   Amina", "minutes": 90})
                assert result.ok and result.message.startswith("I'll remind you "), result
                [timer] = list(units_dir().glob("kulan-reminder-*.timer"))
                rid = timer.stem.rsplit("-", 1)[1]
                assert "Persistent=true" in timer.read_text() and "OnCalendar=" in timer.read_text()
                service = (units_dir() / f"kulan-reminder-{rid}.service").read_text()
                assert f"ExecStart=/usr/bin/kulan-assistant reminder-fire {rid}" in service and "Amina" not in service
                assert json.loads((reminders_dir() / f"{rid}.json").read_text())["message"] == "Call Amina"
                assert ("enable", "--now", f"kulan-reminder-{rid}.timer") in calls
                assert [r["message"] for r in list_reminders()] == ["Call Amina"], list_reminders()
                result.undo()
                assert list_reminders() == []
                assert not list(units_dir().glob("kulan-reminder-*")) and not list(reminders_dir().glob("*"))
                remove_reminder("../../etc")  # refused quietly
                assert fire_reminder("../x") == 2
                assert not set_reminder({"message": "x", "minutes": 0}).ok

                # Timers: a reminder titled "Timer".
                result = set_timer({"minutes": 10, "label": "Pasta"})
                assert result.ok and result.message == "Timer set for 10 minutes", result
                [rid] = [r["id"] for r in list_reminders()]
                data = json.loads((reminders_dir() / f"{rid}.json").read_text())
                assert data["title"] == "Timer" and data["message"] == "Pasta: 10 minutes are up", data
                result.undo()
                assert list_reminders() == []

            # Photos by date (by when the file was saved).
            assert photo_range("last_week", datetime.date(2026, 10, 23)) == (datetime.date(2026, 10, 12), datetime.date(2026, 10, 19))
            assert photo_range("last_month", datetime.date(2026, 1, 15)) == (datetime.date(2025, 12, 1), datetime.date(2026, 1, 1))
            assert photo_range("yesterday", datetime.date(2026, 3, 1)) == (datetime.date(2026, 2, 28), datetime.date(2026, 3, 1))
            pictures = Path(home) / "Pictures"
            (pictures / "Trip").mkdir(parents=True)
            today_ts = time.time()
            old_ts = today_ts - 86400 * 400
            for name, ts in (("Trip/beach.JPG", today_ts), ("cat.png", today_ts), ("old.jpg", old_ts), ("notes.txt", today_ts)):
                (pictures / name).write_bytes(b"x" * 2048)
                os.utime(pictures / name, (ts, ts))
            found = find_photos({"when": "today"})
            names = {Path(p).name for p in found.found}
            assert found.ok and {"beach.JPG", "cat.png"} <= names and not names & {"old.jpg", "notes.txt"}, found
            assert any("/Downloads/" in p for p in found.found), found  # Downloads too (the tidy test's photos)
            assert "No photos" in find_photos({"when": "last_week"}).message

            # Big files and exact copies (nothing deleted).
            (Path(home) / "Videos").mkdir()
            big = Path(home) / "Videos" / "film.mkv"
            with open(big, "wb") as fh:
                fh.truncate(101 * 1024 * 1024)
            large = find_large_files({"min_mb": 100})
            assert large.ok and large.found == [str(big)], large
            assert find_large_files({"min_mb": 500}).found == []
            (downloads / "report.pdf").write_bytes(b"A" * 5000)
            (downloads / "report (1).pdf").write_bytes(b"A" * 5000)
            (downloads / "other.pdf").write_bytes(b"B" * 5000)
            os.utime(downloads / "report.pdf", (old, old))
            dup = find_duplicates({"where": "downloads"})
            assert dup.ok and [Path(p).name for p in dup.found] == ["report (1).pdf"], dup
            assert (downloads / "report (1).pdf").exists()

            # To-dos go to the To-do widget's file; Undo takes them out again.
            with mock.patch(f"{__name__}.TODO_FILE", Path(home) / ".local/share/kulan/todo.json"):
                result = add_todo({"task": "  Buy   milk "})
                assert result.ok and read_todos()[0]["text"] == "Buy milk" and read_todos()[0]["done"] is False
                add_todo({"task": "Call the bank"})
                result.undo()
                assert [i["text"] for i in read_todos()] == ["Call the bank"], read_todos()

    # The new actions' plans are checked like the others.
    _r, steps = validate({"reply": "", "steps": [
        {"action": "find_photos", "args": {"when": "last_week"}}, {"action": "find_photos", "args": {"when": "1999"}},
        {"action": "find_large_files", "args": {"min_mb": 7}}, {"action": "set_timer", "args": {"minutes": 500, "label": "x"}},
        {"action": "set_timer", "args": {"minutes": 5, "label": "Tea"}}, {"action": "add_todo", "args": {"task": ""}}]})
    assert [s.action.id for s in steps] == ["find_photos", "set_timer"], steps
    assert "_at" in steps[1].args
    assert [s.action.id for s in validate({"steps": [{"action": "open_settings", "args": {"page": "backups"}},
                                                    {"action": "open_settings", "args": {"page": "phone"}}]})[1]] == \
        ["open_settings", "open_settings"]
    print("agent self-test passed")
