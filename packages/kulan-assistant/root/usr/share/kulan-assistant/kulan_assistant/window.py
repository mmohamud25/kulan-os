"""The Kulan Assistant window: shows the answer as it is written.

Opened by the writing tools (Shift+Super+W), "Summarise" in Files and the
Live Text notification. With --replace (writing tools), "Replace" and
"Copy" hand the result back to Kulan Shell on stdout, which pastes it into
the app the text came from (exit code 3) or copies it (exit code 4).
"""
from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import engine, tasks  # noqa: E402

APP_ID = "org.kulan.Assistant"
EXIT_REPLACE, EXIT_COPY = 3, 4


class AssistantWindow(Adw.ApplicationWindow):
    def __init__(self, app: AssistantApp, task_id: str, text: str, source: str, replace: bool) -> None:
        super().__init__(application=app, title="Kulan Assistant", default_width=560, default_height=460)
        self.app = app
        self.text = text
        self.replace = replace
        self.cancel = threading.Event()
        self.result = ""
        self.running = False

        header = Adw.HeaderBar()
        choices = [t for t in tasks.TASKS if t.id in tasks.WRITING_TASKS or t.id == task_id]
        if not replace:
            choices = [t for t in choices if not t.replaces or t.id in ("translate", task_id)] or choices
        self.choices = choices
        self.task_list = Gtk.DropDown.new_from_strings([t.title for t in choices])
        self.task_list.set_selected(next((i for i, t in enumerate(choices) if t.id == task_id), 0))
        self.task_list.update_property([Gtk.AccessibleProperty.LABEL], ["What to do with the text"])
        self.task_list.connect("notify::selected", lambda *_a: self.start())
        header.set_title_widget(self.task_list)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                      margin_start=14, margin_end=14)
        self.source_label = Gtk.Label(label=source, xalign=0, wrap=True, lines=2,
                                      ellipsize=3, css_classes=["dim-label", "caption"])
        box.append(self.source_label)
        self.status = Gtk.Box(spacing=8)
        self.spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        self.status.append(self.spinner)
        self.status.append(self.status_label)
        box.append(self.status)
        self.view = Gtk.TextView(editable=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, vexpand=True,
                                 left_margin=10, right_margin=10, top_margin=10, bottom_margin=10)
        self.view.update_property([Gtk.AccessibleProperty.LABEL], ["Kulan Assistant's answer"])
        scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True, css_classes=["card"])
        box.append(scroller)

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        # When the assistant can't answer yet (no model, not installed): the
        # page of Kulan Settings that fixes it.
        self.fix_page = ""
        self.fix_button = Gtk.Button(label="Open Kulan Settings", css_classes=["suggested-action"], visible=False)
        self.fix_button.connect("clicked", lambda _b: (engine.open_settings(self.fix_page), self.close()))
        buttons.append(self.fix_button)
        self.stop_button = Gtk.Button(label="Stop")
        self.stop_button.connect("clicked", lambda _b: self.cancel.set())
        self.copy_button = Gtk.Button(label="Copy")
        self.copy_button.connect("clicked", lambda _b: self.finish(EXIT_COPY))
        buttons.append(self.stop_button)
        buttons.append(self.copy_button)
        if replace:
            self.replace_button = Gtk.Button(label="Replace", css_classes=["suggested-action"])
            self.replace_button.connect("clicked", lambda _b: self.finish(EXIT_REPLACE))
            buttons.append(self.replace_button)
        box.append(buttons)
        box.append(Gtk.Label(label="Kulan Assistant can make mistakes. Check important details.", wrap=True,
                             css_classes=["dim-label", "caption"]))

        self.toasts = Adw.ToastOverlay(child=box)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.connect("close-request", lambda _w: self.cancel.set() or False)
        self.start()

    # --- running a task -------------------------------------------------------------
    def current_task(self) -> tasks.Task:
        return self.choices[self.task_list.get_selected()]

    def start(self) -> None:
        self.cancel.set()  # stop a previous run
        self.cancel = threading.Event()
        self.view.get_buffer().set_text("")
        self.result = ""
        if self.not_ready():
            return
        self.set_busy(True, "Starting the assistant…"
                      if not engine.healthy() else "Writing…")
        threading.Thread(target=self.worker, args=(self.current_task().id, self.cancel), daemon=True).start()

    def worker(self, task_id: str, cancel: threading.Event) -> None:
        try:
            engine.ensure_running()
            GLib.idle_add(self.set_busy, True, "Writing…")
            for piece in tasks.run(task_id, self.text):
                if cancel.is_set():
                    break
                GLib.idle_add(self.append, piece, cancel)
            GLib.idle_add(self.done, None, cancel)
        except engine.EngineError as exc:
            GLib.idle_add(self.done, str(exc), cancel)
        except Exception as exc:  # noqa: BLE001 - show it rather than hang
            print(f"kulan-assistant: {exc!r}", file=sys.stderr)
            GLib.idle_add(self.done, engine.UNEXPECTED, cancel)

    def not_ready(self) -> bool:
        """Says so (with a button to fix it) when the assistant can't answer yet."""
        problem = engine.readiness()
        self.fix_button.set_visible(problem is not None)
        if problem is None:
            return False
        self.fix_page = problem[1]
        self.set_busy(False, problem[0])
        self.copy_button.set_visible(False)
        if self.replace:
            self.replace_button.set_visible(False)
        return True

    def append(self, piece: str, cancel: threading.Event) -> bool:
        if cancel is self.cancel and not cancel.is_set():
            buffer = self.view.get_buffer()
            buffer.insert(buffer.get_end_iter(), piece)
        return False

    def done(self, error: str | None, cancel: threading.Event) -> bool:
        if cancel is not self.cancel:
            return False  # an older run, replaced by a newer one
        if error:
            self.set_busy(False, error)
        else:
            self.set_busy(False, "Stopped." if cancel.is_set() else "")
        return False

    def set_busy(self, busy: bool, message: str) -> bool:
        self.running = busy
        self.spinner.set_spinning(busy)
        self.spinner.set_visible(busy)
        self.status_label.set_label(message)
        self.status.set_visible(bool(message) or busy)
        self.stop_button.set_visible(busy)
        self.copy_button.set_sensitive(not busy)
        if self.replace:
            self.replace_button.set_sensitive(not busy)
        return False

    def answer(self) -> str:
        buffer = self.view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()

    def finish(self, code: int) -> None:
        text = self.answer()
        if not text:
            return
        if self.replace:
            # Kulan Shell pastes or copies it once this window has closed.
            sys.stdout.write(text)
            sys.stdout.flush()
            self.app.exit_code = code
            self.close()
            return
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toasts.add_toast(Adw.Toast(title="Copied", timeout=2))

    def on_key(self, _controller, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval == Gdk.KEY_Return and state & Gdk.ModifierType.CONTROL_MASK and not self.running:
            self.finish(EXIT_REPLACE if self.replace else EXIT_COPY)
            return True
        return False


class AssistantApp(Adw.Application):
    def __init__(self, task_id: str, text: str, source: str, replace: bool) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.task_id, self.text, self.source, self.replace = task_id, text, source, replace
        self.exit_code = 0

    def do_activate(self) -> None:
        AssistantWindow(self, self.task_id, self.text, self.source, self.replace).present()


def show(task_id: str, text: str, source: str, replace: bool = False) -> int:
    app = AssistantApp(task_id, text, source, replace)
    app.run([sys.argv[0]])
    return app.exit_code
