"""Ask Kulan's service: what the Ask Kulan panel in the desktop (Kulan Shell)
talks to, over the session bus. Started by D-Bus when the panel first asks;
it stops by itself after a quiet while.

  Ask(text)  -> the assistant's answer, and its plan if something is to be done.
                Opening, finding and checking happen straight away; anything
                that changes the computer waits for Run.
  Run(id)    -> does the plan's steps; says how each went.
  Undo(id)   -> puts back what Run changed.
  Status()   -> whether the assistant can answer at all (and why not).

Every reply is one JSON string. Plans are kept in memory only, for
PLAN_TTL_S; nothing is written anywhere.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time

from gi.repository import Gio, GLib

from . import agent, engine, instant

BUS_NAME = "org.kulan.Assistant.Ask"
OBJECT_PATH = "/org/kulan/Assistant/Ask"
PLAN_TTL_S = 30 * 60
IDLE_EXIT_S = 15 * 60
MAX_TEXT = 500
# Changes run only when the Ask Kulan panel asks: the desktop itself, never
# another program on the session bus (it could skip the person's "Do it").
SHELL = "/usr/bin/gnome-shell"


def from_shell(connection: Gio.DBusConnection, sender: str) -> bool:
    try:
        pid = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "GetConnectionUnixProcessID", GLib.Variant("(s)", (sender,)),
                                   GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        return os.readlink(f"/proc/{pid}/exe") == SHELL
    except (GLib.Error, OSError):
        return False

INTERFACE = """
<node>
  <interface name="org.kulan.Assistant.Ask">
    <method name="Ask"><arg type="s" name="text" direction="in"/><arg type="s" name="reply" direction="out"/></method>
    <method name="Run"><arg type="s" name="id" direction="in"/><arg type="s" name="reply" direction="out"/></method>
    <method name="Undo"><arg type="s" name="id" direction="in"/><arg type="s" name="reply" direction="out"/></method>
    <method name="Status"><arg type="s" name="reply" direction="out"/></method>
  </interface>
</node>
"""


class Plans:
    """The plans made so far, by id, with what Run did (for Undo)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: dict[str, dict] = {}

    def add(self, steps: list[agent.Step]) -> str:
        pid = secrets.token_hex(8)
        with self.lock:
            self.expire()
            self.items[pid] = {"steps": steps, "undos": [], "ran": False, "made": time.monotonic()}
        return pid

    def get(self, pid: str) -> dict | None:
        with self.lock:
            self.expire()
            return self.items.get(pid)

    def expire(self) -> None:
        now = time.monotonic()
        for pid in [p for p, item in self.items.items() if now - item["made"] > PLAN_TTL_S]:
            del self.items[pid]


def _result(step: agent.Step) -> dict:
    r = step.result
    return {"text": step.text, "ok": bool(r and r.ok), "message": r.message if r else "", "found": r.found if r else []}


INSTANT_NOTE = "You can still ask about the time, date, battery or free space."


def status() -> dict:
    problem = engine.readiness()
    if problem:
        return {"ready": False, "message": problem[0], "settings": problem[1], "note": INSTANT_NOTE}
    return {"ready": True, "message": ""}


def ask(plans: Plans, text: str) -> dict:
    text = " ".join(text.split())[:MAX_TEXT]
    if not text:
        return {"error": "Type a question or what you'd like done."}
    quick = instant.answer(text)  # the time, the date, battery, free space: no model needed
    if quick:
        return {"id": "", "reply": quick, "steps": [], "confirm": False, "instant": True}
    state = status()
    if not state["ready"]:
        return {"error": state["message"], "settings": state.get("settings")}
    try:
        reply, steps = agent.plan(text)
    except engine.EngineError as exc:
        return {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the panel must always get an answer
        return {"error": f"The assistant stopped unexpectedly: {exc}"}
    pid = plans.add(steps)
    out = {"id": pid, "reply": reply, "steps": [{"text": s.text, "changes": s.action.changes} for s in steps],
           "confirm": any(s.action.changes for s in steps)}
    if steps and not out["confirm"]:
        out.update(run(plans, pid))  # only opening, finding or checking: nothing to confirm
    if not steps and not reply:
        out["reply"] = "Sorry, I couldn't work that out. Try saying it another way."
    return out


def run(plans: Plans, pid: str) -> dict:
    item = plans.get(pid)
    if item is None:
        return {"error": "That plan has expired. Ask again."}
    with plans.lock:
        if item["ran"]:
            return {"error": "That's already done."}
        item["ran"] = True
    results = []
    for step in item["steps"]:
        result = agent.run_step(step)
        if result.undo:
            item["undos"].append(result.undo)
        results.append(_result(step))
    failed = sum(not r["ok"] for r in results)
    return {"results": results, "undo": bool(item["undos"]),
            "summary": "Done." if not failed else "Done, but not everything worked."}


def undo(plans: Plans, pid: str) -> dict:
    item = plans.get(pid)
    if item is None or not item["undos"]:
        return {"ok": False, "message": "There's nothing to undo."}
    failed = 0
    for fn in reversed(item["undos"]):
        try:
            fn()
        except Exception:  # noqa: BLE001 - try every undo
            failed += 1
    item["undos"] = []
    return {"ok": not failed, "message": "Undone." if not failed else "Some changes couldn’t be undone."}


class Service:
    def __init__(self, loop: GLib.MainLoop) -> None:
        self.loop = loop
        self.plans = Plans()
        self.busy = 0
        self.idle_timer = GLib.timeout_add_seconds(IDLE_EXIT_S, self.idle_exit)

    def idle_exit(self) -> bool:
        if self.busy:
            return GLib.SOURCE_CONTINUE
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def on_call(self, conn, sender, _path, _iface, method, params, invocation) -> None:
        GLib.source_remove(self.idle_timer)
        self.idle_timer = GLib.timeout_add_seconds(IDLE_EXIT_S, self.idle_exit)
        if method in ("Run", "Undo") and not from_shell(conn, sender):
            invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "only the Ask Kulan panel can do that")
            return
        args = params.unpack()
        work = {"Ask": lambda: ask(self.plans, args[0]), "Run": lambda: run(self.plans, args[0]),
                "Undo": lambda: undo(self.plans, args[0]), "Status": status}.get(method)
        if work is None:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        self.busy += 1

        def worker() -> None:
            try:
                reply = work()
            except Exception as exc:  # noqa: BLE001
                reply = {"error": f"The assistant stopped unexpectedly: {exc}"}
            text = json.dumps(reply)

            def done() -> bool:
                self.busy -= 1
                invocation.return_value(GLib.Variant("(s)", (text,)))
                return GLib.SOURCE_REMOVE
            GLib.idle_add(done)

        # Planning takes seconds and actions may wait (Wi-Fi): never block the bus.
        threading.Thread(target=worker, daemon=True).start()


def serve() -> int:
    loop = GLib.MainLoop()
    service = Service(loop)
    node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)

    def on_bus(connection, _name) -> None:
        connection.register_object(OBJECT_PATH, node.interfaces[0], service.on_call, None, None)

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, on_bus, None,
                     lambda *_a: loop.quit())
    loop.run()
    return 0


def self_test() -> None:
    from unittest import mock

    plans = Plans()
    calls: list[str] = []

    def act(name: str, changes: bool, ok: bool = True, undoable: bool = False) -> agent.Action:
        def run_it(_a: dict) -> agent.Result:
            calls.append(name)
            return agent.Result(ok, f"{name} done", undo=(lambda: calls.append(f"undo {name}")) if undoable else None)
        return agent.Action(name, "", {}, changes, lambda _a: f"Do {name}", run_it)

    ready = {"ready": True, "message": ""}
    with mock.patch(f"{__name__}.status", return_value=ready):
        # A question: an answer and no steps.
        with mock.patch.object(agent, "plan", return_value=("Firefox uses most memory.", [])):
            out = ask(plans, "  why   is it slow? ")
        assert out["reply"] == "Firefox uses most memory." and out["steps"] == [] and not out["confirm"], out
        # Only opening: done straight away.
        step = agent.Step(act("open", False), {}, "Open Files")
        with mock.patch.object(agent, "plan", return_value=("Opening Files.", [step])):
            out = ask(plans, "open files")
        assert not out["confirm"] and out["results"][0]["ok"] and calls == ["open"], (out, calls)
        # A change waits for Run; Undo puts it back; Run twice is refused.
        steps = [agent.Step(act("tidy", True, undoable=True), {}, "Tidy"), agent.Step(act("wifi", True, ok=False), {}, "Wi-Fi")]
        with mock.patch.object(agent, "plan", return_value=("I'll tidy up.", steps)):
            out = ask(plans, "tidy downloads")
        assert out["confirm"] and "results" not in out and calls == ["open"], (out, calls)
        done = run(plans, out["id"])
        assert done["undo"] and done["summary"] == "Done, but not everything worked." and calls[-2:] == ["tidy", "wifi"]
        assert run(plans, out["id"])["error"] == "That's already done."
        assert undo(plans, out["id"])["ok"] and calls[-1] == "undo tidy"
        assert undo(plans, out["id"])["message"] == "There's nothing to undo."
        assert run(plans, "nope")["error"].startswith("That plan has expired")
        # Engine trouble comes back as a message.
        with mock.patch.object(agent, "plan", side_effect=engine.EngineError("No model yet.")):
            assert ask(plans, "hi there")["error"] == "No model yet."
        assert ask(plans, "   ")["error"].startswith("Type a question")
    with mock.patch.object(engine, "usable_model", return_value=None), \
            mock.patch.object(type(engine.ENGINE), "exists", return_value=True):
        assert ask(plans, "hello")["settings"] == "assistant"
        assert status()["note"] == INSTANT_NOTE
        # Instant answers need no model, and no plan is kept for them.
        before = len(plans.items)
        for question in ("What time is it?", "what's the date", "how much space do I have left"):
            out = ask(plans, question)
            assert out.get("instant") and out["reply"] and not out["steps"] and "error" not in out, out
        assert len(plans.items) == before
    with mock.patch.object(engine, "readiness", side_effect=AssertionError("no model check for instant answers")), \
            mock.patch.object(agent, "plan", side_effect=AssertionError("no model for instant answers")):
        assert ask(plans, "battery")["instant"]
    # Old plans are forgotten.
    pid = plans.add([])
    plans.items[pid]["made"] -= PLAN_TTL_S + 1
    assert plans.get(pid) is None
    json.dumps(status())
    print("ask service self-test passed")
