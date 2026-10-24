"""Kulan Assistant, "Do it for me": the window.

Type what you'd like done ("dark mode and a smaller dock", "open Firefox",
"find my CV"). The assistant writes a plan from Kulan's fixed list of
actions (agent.py) and shows it in plain words. Opening and finding happen
straight away; anything that changes a setting waits for "Do it", and can
be undone afterwards.
"""
from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import agent, engine, instant  # noqa: E402

# The launcher's ID, so the dock shows "Kulan Assistant" and its icon for this window.
APP_ID = "org.kulan.Assistant"
EXAMPLES = ["Dark mode and a smaller dock", "Turn on Do Not Disturb", "Make text bigger",
            "Open the Files app", "Find my CV", "Lock the screen after 10 minutes"]


class AgentWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, request: str) -> None:
        super().__init__(application=app, title="Kulan Assistant", default_width=600, default_height=560)
        self.steps: list[agent.Step] = []
        self.undos: list = []
        self.cancel: threading.Event | None = None

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Kulan Assistant", subtitle="Ask a question, or say what to do")))
        page = Adw.PreferencesPage()

        ask = Adw.PreferencesGroup(description="Tell the assistant what to do. It runs on this computer and "
                                               "can only use Kulan’s own actions; you see the plan first.")
        self.entry = Adw.EntryRow(title="What would you like done?", show_apply_button=True)
        self.entry.connect("apply", lambda _e: self.start())
        self.entry.connect("entry-activated", lambda _e: self.start())
        ask.add(self.entry)
        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, margin_top=10,
                            column_spacing=6, row_spacing=6)
        for text in EXAMPLES:
            button = Gtk.Button(label=text, css_classes=["pill", "flat"])
            button.connect("clicked", lambda _b, t=text: (self.entry.set_text(t), self.start()))
            chips.append(button)
        self.chips = chips
        ask.add(chips)
        page.add(ask)

        # What the assistant says, above its plan.
        said = Adw.PreferencesGroup()
        line = Gtk.Box(spacing=10)
        self.spinner = Gtk.Spinner(spinning=True, visible=False)
        self.status = Gtk.Label(wrap=True, xalign=0, hexpand=True, css_classes=["title-4"], visible=False)
        line.append(self.spinner)
        line.append(self.status)
        said.add(line)
        page.add(said)
        self.plan_group = Adw.PreferencesGroup()
        self.step_rows: list[Gtk.Widget] = []
        page.add(self.plan_group)

        actions = Gtk.Box(spacing=10, halign=Gtk.Align.END, margin_top=12, margin_end=12, margin_bottom=12)
        self.fix_page = ""
        self.fix_button = Gtk.Button(label="Open Kulan Settings", css_classes=["suggested-action", "pill"],
                                     visible=False)
        self.fix_button.connect("clicked", lambda _b: (engine.open_settings(self.fix_page), self.close()))
        actions.append(self.fix_button)
        self.undo_button = Gtk.Button(label="Undo", visible=False)
        self.undo_button.connect("clicked", lambda _b: self.undo())
        self.do_button = Gtk.Button(label="Do it", css_classes=["suggested-action", "pill"], visible=False)
        self.do_button.connect("clicked", lambda _b: self.execute())
        actions.append(self.undo_button)
        actions.append(self.do_button)

        view.set_content(page)
        view.add_bottom_bar(actions)
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        if request.strip():
            self.entry.set_text(request.strip())
            GLib.idle_add(self.start)

    # --- planning ------------------------------------------------------------------------
    def start(self) -> bool:
        request = self.entry.get_text().strip()
        if not request:
            return False
        if self.cancel:
            self.cancel.set()
        self.cancel = threading.Event()
        self.clear_steps()
        self.do_button.set_visible(False)
        self.undo_button.set_visible(False)
        self.undos = []
        quick = instant.answer(request)  # the time, the date, battery, free space: no model needed
        if quick:
            self.fix_button.set_visible(False)
            self.say(quick)
            return False
        problem = engine.readiness()
        self.fix_button.set_visible(problem is not None)
        if problem:
            self.fix_page = problem[1]
            self.say(problem[0])
            return False
        self.chips.set_visible(False)
        self.say("Working out what to do…", busy=True)
        threading.Thread(target=self.worker, args=(request, self.cancel), daemon=True).start()
        return False

    def worker(self, request: str, cancel: threading.Event) -> None:
        try:
            reply, steps = agent.plan(request)
            error = None
        except engine.EngineError as exc:
            reply, steps, error = "", [], str(exc)
        except Exception as exc:  # noqa: BLE001 - never leave the window spinning
            reply, steps, error = "", [], f"The assistant stopped unexpectedly: {exc}"
        GLib.idle_add(self.planned, reply, steps, error, cancel)

    def planned(self, reply: str, steps: list[agent.Step], error: str | None, cancel: threading.Event) -> bool:
        if cancel.is_set():
            return False
        if error:
            self.say(error)
            return False
        self.steps = steps
        if not steps:
            self.say(reply or "That isn’t something I can do yet. Try one of the examples.")
            self.chips.set_visible(True)
            return False
        self.say(reply or "Here’s the plan:")
        for step in steps:
            row = Adw.ActionRow(title=GLib.markup_escape_text(step.text))
            icon = Gtk.Image(icon_name="preferences-system-symbolic" if step.action.changes else "go-next-symbolic")
            row.add_prefix(icon)
            row.step_icon = icon
            self.plan_group.add(row)
            self.step_rows.append(row)
        if any(s.action.changes for s in steps):
            self.do_button.set_visible(True)
            self.do_button.grab_focus()
        else:
            self.execute()  # only opening or finding: nothing to confirm
        return False

    # --- doing ---------------------------------------------------------------------------
    def execute(self) -> None:
        """Does the steps in the background (fixing Wi-Fi or searching files can
        take a while), showing each one's result as it finishes."""
        self.do_button.set_visible(False)
        self.say("Doing it…", busy=True)
        steps, rows = list(self.steps), list(self.step_rows)
        cancel = self.cancel

        def work() -> None:
            failed = 0
            for step, row in zip(steps, rows):
                result = agent.run_step(step)
                failed += not result.ok
                GLib.idle_add(self.step_done, step, row, result, cancel)
            GLib.idle_add(self.all_done, failed, cancel)

        threading.Thread(target=work, daemon=True).start()

    def step_done(self, _step: agent.Step, row: Adw.ActionRow, result: agent.Result,
                  cancel: threading.Event | None) -> bool:
        if cancel is not self.cancel:
            return False
        row.set_subtitle(GLib.markup_escape_text(result.message))
        row.step_icon.set_from_icon_name("object-select-symbolic" if result.ok else "dialog-warning-symbolic")
        row.step_icon.set_css_classes(["success"] if result.ok else ["warning"])
        if result.undo:
            self.undos.append(result.undo)
        for path in result.found:
            found = Adw.ActionRow(title=GLib.markup_escape_text(GLib.path_get_basename(path)),
                                  subtitle=GLib.markup_escape_text(path), activatable=True)
            found.add_suffix(Gtk.Image(icon_name="document-open-symbolic"))
            found.connect("activated", lambda _r, p=path: Gtk.FileLauncher.new(
                Gio.File.new_for_path(p)).launch(self, None, None))
            self.plan_group.add(found)
            self.step_rows.append(found)
        return False

    def all_done(self, failed: int, cancel: threading.Event | None) -> bool:
        if cancel is not self.cancel:
            return False
        self.say("Done." if not failed else "Done, but not everything worked.")
        self.undo_button.set_visible(bool(self.undos))
        return False

    def undo(self) -> None:
        failed = 0
        for fn in reversed(self.undos):
            try:
                fn()
            except Exception:  # noqa: BLE001 - try every undo, then say how it went
                failed += 1
        self.undos = []
        self.undo_button.set_visible(False)
        self.toasts.add_toast(Adw.Toast(title="Put back as it was" if not failed else
                                        "Mostly put back; some changes couldn’t be undone", timeout=4))

    # --- helpers ---------------------------------------------------------------------------
    def say(self, text: str, busy: bool = False) -> None:
        self.status.set_label(text)
        self.status.set_visible(True)
        self.spinner.set_visible(busy)

    def clear_steps(self) -> None:
        for row in self.step_rows:
            self.plan_group.remove(row)
        self.step_rows = []
        self.steps = []


def show(request: str = "") -> int:
    app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", lambda a: AgentWindow(a, request).present())
    return app.run([sys.argv[0]])
