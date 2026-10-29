"""Instant answers: the time, the date, the battery and free space, read
straight from this computer, without a model.

They work before a model is downloaded and answer in well under a second
when one is. Only short, plain questions are answered here ("what time is
it?", "how much battery do I have"); anything longer or less certain ("set
a timer", "free up space") goes to the assistant as before, so a wrong guess
here can't stop a real request.
"""
from __future__ import annotations

import datetime
import re
import shutil
from pathlib import Path

from . import facts

# Said before or after the question; dropped before matching.
_LEAD = re.compile(r"^(?:(?:hey|hi|ok|okay)\s+)?(?:kulan\s*,?\s+)?(?:please\s+)?"
                   r"(?:(?:can|could|would)\s+you\s+)?(?:(?:please\s+)?(?:tell|show|give)\s+me\s+)?(?:please\s+)?")
_TAIL = re.compile(r"\s+(?:please|now|right now|thanks|thank you)$")

_TIME = re.compile(r"(?:what(?:'?s| is)?\s+)?(?:the\s+)?(?:current\s+)?time(?:\s+(?:is it|now|right now))?"
                   r"|what time(?:\s+is it)?(?:\s+(?:now|right now|here))?"
                   r"|time\s+now")
_DATE = re.compile(r"(?:what(?:'?s| is)?\s+)?(?:the\s+)?(?:today'?s\s+|current\s+)?date(?:\s+today)?"
                   r"|what(?:'?s| is)?\s+(?:the\s+)?date(?:\s+(?:is it\s+)?today)?"
                   r"|what day(?:\s+of the week)?(?:\s+is it)?(?:\s+today)?"
                   r"|what(?:'?s| is)\s+today|today'?s date|which day is (?:it|today)")
_BATTERY = re.compile(r"(?:what(?:'?s| is)?\s+)?(?:my\s+|the\s+)?battery(?:\s+(?:level|percentage|percent|status|charge|life))?"
                      r"(?:\s+(?:left|now|at))?"
                      r"|how much (?:battery|charge)(?:\s+(?:do i have|have i got|is left|left|remains))?"
                      r"|how(?:'?s| is)\s+(?:my\s+|the\s+)?battery"
                      r"|is (?:my|the) (?:battery|laptop|computer) charging"
                      r"|am i (?:charging|plugged in)")
_STORAGE = re.compile(r"how much (?:free\s+|disk\s+)?(?:space|storage|room)(?:\s+(?:do i have|have i got|is left|"
                      r"is free|left|remains|is there))?(?:\s+(?:left|on (?:my|this|the) (?:computer|disk|drive|laptop)))?"
                      r"|(?:what(?:'?s| is)?\s+)?(?:my\s+|the\s+)?(?:free\s+|disk\s+|available\s+)?(?:space|storage)"
                      r"(?:\s+(?:left|free|available|used))?"
                      r"|(?:free|disk) space"
                      r"|how full is (?:my|the) (?:disk|drive|computer|storage)"
                      r"|is (?:my|the) (?:disk|drive|storage) full")
# "Space" or "storage" alone: too vague to be sure; the assistant decides.
_TOO_VAGUE = {"space", "storage", "room"}

KINDS = ("time", "date", "battery", "storage")


def _clean(text: str) -> str:
    text = text.lower().replace("’", "'").strip()
    text = re.sub(r"[?!.,]+", " ", text)
    text = " ".join(text.split())
    for _ in range(2):
        text = _LEAD.sub("", text, count=1).strip()
        text = _TAIL.sub("", text).strip()
    return text


def kind(text: str) -> str | None:
    """Which instant answer a question asks for, or None for anything else."""
    words = _clean(text)
    if not words or len(words) > 60:
        return None
    for name, pattern in (("time", _TIME), ("date", _DATE), ("battery", _BATTERY), ("storage", _STORAGE)):
        if pattern.fullmatch(words):
            if name == "storage" and words in _TOO_VAGUE:
                return None
            return name
    return None


def _uses_12_hour() -> bool:
    return facts._out(["gsettings", "get", "org.gnome.desktop.interface", "clock-format"]).strip("'\"") == "12h"


def time_answer(now: datetime.datetime | None = None, twelve: bool | None = None) -> str:
    now = now or datetime.datetime.now()
    twelve = _uses_12_hour() if twelve is None else twelve
    clock = now.strftime("%-I:%M %p") if twelve else now.strftime("%H:%M")
    return f"It's {clock}."


def date_answer(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return f"Today is {now:%A %-d %B %Y}."


def _battery_reading() -> tuple[int, str] | None:
    """(percent, state) for all of the computer's batteries together (some laptops have
    two), or None when there's none."""
    levels: list[int] = []
    states: list[str] = []
    now = full = 0
    for supply in sorted(Path("/sys/class/power_supply").glob("*")):
        if facts._read(str(supply / "type")) != "Battery":
            continue
        if facts._read(str(supply / "scope")).lower() == "device":
            continue  # a wireless mouse or keyboard, not this computer
        capacity = facts._read(str(supply / "capacity"))
        if not capacity.isdigit():
            continue
        levels.append(min(100, int(capacity)))
        states.append(facts._read(str(supply / "status")).lower())
        unit = "energy" if facts._read(str(supply / "energy_full")) else "charge"
        n, f = facts._read(str(supply / f"{unit}_now")), facts._read(str(supply / f"{unit}_full"))
        if n.isdigit() and f.isdigit() and int(f) > 0:
            now, full = now + int(n), full + int(f)
    if not levels:
        return None
    percent = round(min(100, now / full * 100)) if len(levels) > 1 and full else levels[0]
    state = ("charging" if "charging" in states else "discharging" if "discharging" in states
             else "full" if all(s == "full" for s in states) else states[0])
    return percent, state


def battery_answer(reading: tuple[int, str] | None | bool = False) -> str:
    reading = _battery_reading() if reading is False else reading
    if reading is None:
        return ("This computer doesn't report a battery, so it's running on mains power. (Virtual machines "
                "don't see the host's battery.)")
    percent, state = reading
    how = {"charging": "and charging", "discharging": "on battery power", "full": "fully charged",
           "not charging": "plugged in but not charging"}.get(state, "")
    if state == "full" or percent >= 100 and state != "discharging":
        return "The battery is full (100%)."
    text = f"The battery is at {percent}%{', ' + how if how else ''}."
    if state == "discharging" and percent <= 15:
        text += " Plug in soon."
    return text


def storage_answer(usage: dict[str, shutil._ntuple_diskusage] | None = None) -> str:
    """Free space where your files are (home) and, if it's a different disk,
    where the system is."""
    if usage is None:
        usage = {}
        for label, path in (("home", Path.home()), ("system", Path("/"))):
            try:
                usage[label] = shutil.disk_usage(path)
            except OSError:
                pass
    if not usage:
        return "I couldn't read how much space is free."
    home = usage.get("home") or usage.get("system")
    text = (f"{facts.gb(home.free)} free of {facts.gb(home.total)} "
            f"({home.free * 100 // max(home.total, 1)}% free).")
    system = usage.get("system")
    if system and "home" in usage and system.total != home.total:
        text += f" The system disk has {facts.gb(system.free)} free of {facts.gb(system.total)}."
    if home.free * 100 // max(home.total, 1) < 10:
        text += " That's getting low: Kulan Settings › Storage can help free some up."
    return text


def answer(text: str) -> str | None:
    """The instant answer to a question, or None if it isn't one of these."""
    which = kind(text)
    if which is None:
        return None
    return {"time": time_answer, "date": date_answer, "battery": battery_answer,
            "storage": storage_answer}[which]()


def self_test() -> None:
    import collections

    asks = {
        "time": ["What time is it?", "what's the time", "Hey Kulan, what time is it now?", "time", "current time",
                 "Can you tell me the time please", "whats the time?", "What’s the time?"],
        "date": ["What's the date today?", "what day is it", "today's date", "What is today?", "date",
                 "what day of the week is it", "tell me the date"],
        "battery": ["How much battery do I have?", "battery", "battery level", "what's my battery at",
                    "Is my laptop charging?", "how's the battery"],
        "storage": ["How much space do I have left?", "free space", "how much storage is left",
                    "How full is my disk?", "disk space", "how much free space on my computer",
                    "what's my storage"],
    }
    for want, questions in asks.items():
        for q in questions:
            assert kind(q) == want, (q, kind(q), want)
    # Real requests and longer questions go to the assistant.
    for q in ["set a timer for 10 minutes", "remind me at the time of the meeting", "free up space",
              "what time zone am I in", "change the date format", "why is my battery draining so fast",
              "make space on my disk", "space", "storage settings", "open Files", "time to tidy my downloads",
              "what's the date of the invoice from last month", "battery saver on", "", "   ?  "]:
        assert kind(q) is None, (q, kind(q))
    noon = datetime.datetime(2026, 10, 23, 14, 5)
    assert time_answer(noon, twelve=False) == "It's 14:05.", time_answer(noon, twelve=False)
    assert time_answer(noon, twelve=True) == "It's 2:05 PM."
    assert date_answer(noon) == "Today is Friday 23 October 2026."
    assert battery_answer(None).startswith("This computer doesn't report a battery")
    assert battery_answer((57, "charging")) == "The battery is at 57%, and charging."
    assert battery_answer((9, "discharging")) == "The battery is at 9%, on battery power. Plug in soon."
    assert battery_answer((100, "full")) == "The battery is full (100%)."
    assert battery_answer((80, "not charging")) == "The battery is at 80%, plugged in but not charging."
    du = collections.namedtuple("usage", "total used free")
    g = 1024 ** 3
    assert storage_answer({"home": du(100 * g, 60 * g, 40 * g), "system": du(100 * g, 60 * g, 40 * g)}) == \
        "40.0 GB free of 100.0 GB (40% free)."
    two = storage_answer({"home": du(500 * g, 450 * g, 50 * g), "system": du(30 * g, 20 * g, 10 * g)})
    assert two == "50.0 GB free of 500.0 GB (10% free). The system disk has 10.0 GB free of 30.0 GB.", two
    assert "getting low" in storage_answer({"home": du(100 * g, 95 * g, 5 * g)})
    assert storage_answer({}) == "I couldn't read how much space is free."
    # And on this computer, for real: each kind answers.
    for q in ("what time is it", "what's the date", "battery", "how much space do I have"):
        assert answer(q), q
    assert answer("tidy my downloads") is None
    print("instant answers self-test passed")
