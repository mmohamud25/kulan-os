"""Ask Kulan: the app. A conversation with Kulan Assistant, on this computer.

Ask a question, or say what you'd like done. Answers and plans appear in
the conversation; anything that changes the computer waits for "Do it" and
can be undone while the app is open. Earlier conversations are listed at the
side (kept on this computer only; see conversation.py). The time, the date,
battery and free space are answered straight away, even with no model.

Opened from the app grid, "Continue in the app" in the Ask Kulan panel,
and Activities search. `kulan-assistant do [TEXT]` opens it and asks TEXT in
a new conversation.
"""
from __future__ import annotations

import datetime
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import agent, conversation, engine  # noqa: E402

# The launcher's ID, so the dock shows the app's name and icon for this window.
APP_ID = "org.kulan.Assistant"
TITLE = "Ask Kulan"
EXAMPLES = ["Why is my computer slow?", "Dark mode and a smaller dock", "Tidy up my Downloads",
            "Remind me in 20 minutes to call home", "Find photos from last week", "How do I take a screenshot?"]
MAX_INPUT = 500

CSS = """
.kulan-ask-user { background-color: alpha(@accent_bg_color, 0.14); border-radius: 16px; padding: 8px 14px; }
.kulan-ask-answer { padding: 2px 4px; }
.kulan-ask-input { border-radius: 14px; padding: 6px 8px; }
.kulan-ask-input textview, .kulan-ask-input text { background: none; }
"""


def when_label(stamp: float, now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    then = datetime.datetime.fromtimestamp(stamp)
    if then.date() == now.date():
        return then.strftime("%H:%M")
    if then.date() == (now - datetime.timedelta(days=1)).date():
        return "Yesterday"
    if (now - then).days < 7:
        return then.strftime("%A")
    return then.strftime("%-d %b %Y" if then.year != now.year else "%-d %b")


def step_record(step: agent.Step) -> dict:
    return {"text": step.text, "changes": step.action.changes, "action": step.action.id,
            "args": {k: v for k, v in step.args.items() if not k.startswith("_")}}


class Turn:
    """One answer on screen: its text, its steps and their buttons."""

    def __init__(self, window: "AskKulanWindow", conv: conversation.Conversation, message: dict,
                 steps: list[agent.Step] | None) -> None:
        self.window, self.conv, self.message = window, conv, message
        self.steps = steps or []      # live steps (just planned); none for a conversation reopened later
        self.undos: list = []
        self.rows: list[Adw.ActionRow] = []
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, halign=Gtk.Align.FILL)
        if message.get("text"):
            label = Gtk.Label(label=message["text"], wrap=True, xalign=0, selectable=True,
                              wrap_mode=Pango.WrapMode.WORD_CHAR, css_classes=["kulan-ask-answer"])
            label.update_property([Gtk.AccessibleProperty.LABEL], [f"Ask Kulan: {message['text']}"])
            self.box.append(label)
        records = message.get("steps", [])
        if records:
            self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
            for record in records:
                row = Adw.ActionRow(title=GLib.markup_escape_text(record.get("text", "")))
                icon = Gtk.Image()
                row.add_prefix(icon)
                row.step_icon = icon
                self.list.append(row)
                self.rows.append(row)
                self.show_record(row, record)
            self.box.append(self.list)
        self.buttons = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        self.box.append(self.buttons)
        self.status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"], visible=False)
        self.box.append(self.status)
        self.update_buttons()

    def show_record(self, row: Adw.ActionRow, record: dict) -> None:
        ok = record.get("ok")
        if ok is None:
            row.step_icon.set_from_icon_name("preferences-system-symbolic" if record.get("changes")
                                             else "go-next-symbolic")
            row.step_icon.set_css_classes([])
            if not self.steps and record.get("changes"):
                row.set_subtitle("Not done")
        else:
            row.step_icon.set_from_icon_name("object-select-symbolic" if ok else "dialog-warning-symbolic")
            row.step_icon.set_css_classes(["success"] if ok else ["warning"])
            row.set_subtitle(GLib.markup_escape_text(record.get("message", "")))
        for path in record.get("found", [])[:agent.MAX_FOUND]:
            if getattr(row, "found_added", None) is None:
                row.found_added = set()
            if path in row.found_added:
                continue
            row.found_added.add(path)
            found = Adw.ActionRow(title=GLib.markup_escape_text(GLib.path_get_basename(path)),
                                  subtitle=GLib.markup_escape_text(path.replace(GLib.get_home_dir(), "~", 1)),
                                  activatable=True)
            found.add_prefix(Gtk.Image(icon_name="text-x-generic-symbolic"))
            found.add_suffix(Gtk.Image(icon_name="document-open-symbolic"))
            found.connect("activated", lambda _r, p=path: Gtk.FileLauncher.new(
                Gio.File.new_for_path(p)).launch(self.window, None, None))
            self.list.append(found)

    def pending(self) -> bool:
        return bool(self.steps) and all(r.get("ok") is None for r in self.message.get("steps", []))

    def update_buttons(self) -> None:
        child = self.buttons.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.buttons.remove(child)
            child = nxt
        if self.pending() and any(s.action.changes for s in self.steps):
            do = Gtk.Button(label="Do it", css_classes=["suggested-action", "pill"])
            do.connect("clicked", lambda _b: self.run())
            self.buttons.append(do)
            self.do_button = do
        if self.undos:
            undo = Gtk.Button(label="Undo", css_classes=["pill"])
            undo.connect("clicked", lambda _b: self.undo())
            self.buttons.append(undo)
        if self.message.get("text"):
            copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy the answer", css_classes=["flat"])
            copy.update_property([Gtk.AccessibleProperty.LABEL], ["Copy the answer"])
            copy.connect("clicked", lambda _b: self.window.copy(self.message["text"]))
            self.buttons.append(copy)

    def say(self, text: str) -> None:
        self.status.set_label(text)
        self.status.set_visible(bool(text))

    # Doing the steps, in the background (fixing Wi-Fi or searching can take a while).
    def run(self) -> None:
        if not self.pending() or self.window.running:
            return
        self.window.running = True
        steps, records = list(self.steps), self.message["steps"]
        for child in list(self._buttons()):
            child.set_sensitive(False)
        self.say("Doing it…")

        def work() -> None:
            failed = 0
            for i, step in enumerate(steps):
                result = agent.run_step(step)
                failed += not result.ok
                GLib.idle_add(self.step_done, i, records[i], result)
            GLib.idle_add(self.all_done, failed)

        threading.Thread(target=work, daemon=True).start()

    def _buttons(self):
        child = self.buttons.get_first_child()
        while child:
            yield child
            child = child.get_next_sibling()

    def step_done(self, i: int, record: dict, result: agent.Result) -> bool:
        record.update(ok=result.ok, message=result.message, found=list(result.found[:agent.MAX_FOUND]))
        if result.undo:
            self.undos.append(result.undo)
        self.show_record(self.rows[i], record)
        return False

    def all_done(self, failed: int) -> bool:
        self.window.running = False
        self.steps = []
        self.say("Done." if not failed else "Done, but not everything worked.")
        self.update_buttons()
        self.window.save(self.conv)
        return False

    def undo(self) -> None:
        failed = 0
        for fn in reversed(self.undos):
            try:
                fn()
            except Exception:  # noqa: BLE001 - try every undo, then say how it went
                failed += 1
        self.undos = []
        self.say("Undone." if not failed else "Some changes couldn’t be undone.")
        self.update_buttons()


class AskKulanWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: conversation.Store | None = None) -> None:
        super().__init__(application=app, title=TITLE, default_width=920, default_height=680)
        self.set_size_request(360, 400)
        self.keep = store is not None or conversation.keep_history()
        self.store = store or (conversation.Store() if self.keep else None)
        self.current = conversation.Conversation()
        self.thinking_for: conversation.Conversation | None = None
        self.cancel: threading.Event | None = None
        self.running = False  # steps being done
        self.turns: dict[int, Turn] = {}  # id(message) -> Turn, for answers on screen

        # --- the side: conversations ---
        new = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New conversation (Ctrl+N)")
        new.update_property([Gtk.AccessibleProperty.LABEL], ["New conversation"])
        new.connect("clicked", lambda _b: self.new_conversation())
        menu = Gio.Menu()
        menu.append("Delete all conversations…", "win.delete-all")
        menu.append("Assistant settings", "win.settings")
        more = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="More")
        side_header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Conversations"))
        side_header.pack_start(new)
        side_header.pack_end(more)
        self.conv_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.conv_list.connect("row-activated", self.on_pick)
        self.conv_empty = Gtk.Label(label="Your conversations appear here." if self.keep
                                    else "Conversations aren’t saved. You can change this in Kulan Settings.",
                                    wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"],
                                    margin_top=24, margin_start=12, margin_end=12)
        side_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        side_box.append(self.conv_empty)
        side_box.append(self.conv_list)
        side_view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=side_box, vexpand=True,
                                                              hscrollbar_policy=Gtk.PolicyType.NEVER))
        side_view.add_top_bar(side_header)
        self.side_page = Adw.NavigationPage(title="Conversations", child=side_view)

        # --- the conversation ---
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=18, margin_bottom=18,
                                margin_start=16, margin_end=16)
        self.welcome = self.build_welcome()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self.welcome)
        column.append(self.messages)
        self.scroller = Gtk.ScrolledWindow(child=Adw.Clamp(child=column, maximum_size=780), vexpand=True,
                                           hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=6,
                                  bottom_margin=6, left_margin=6, right_margin=6, hexpand=True)
        self.input.update_property([Gtk.AccessibleProperty.LABEL], ["Ask Kulan, or say what you’d like done"])
        self.input.get_buffer().connect("changed", lambda _b: self.update_send())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_input_key)
        self.input.add_controller(keys)
        self.placeholder = Gtk.Label(label="Ask Kulan, or say what you’d like done", xalign=0,
                                     css_classes=["dim-label"], can_target=False, margin_start=8)
        overlay = Gtk.Overlay(child=Gtk.ScrolledWindow(child=self.input, propagate_natural_height=True,
                                                       max_content_height=140,
                                                       hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True))
        overlay.add_overlay(self.placeholder)
        self.send = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Send (Enter)", valign=Gtk.Align.END,
                               css_classes=["suggested-action", "circular"])
        self.send.update_property([Gtk.AccessibleProperty.LABEL], ["Send"])
        self.send.connect("clicked", lambda _b: self.on_send())
        entry = Gtk.Box(spacing=6, css_classes=["card", "kulan-ask-input"])
        entry.append(overlay)
        entry.append(self.send)
        note = Gtk.Label(label="Runs on this computer. Nothing you type leaves it. It can make mistakes.",
                         css_classes=["dim-label", "caption"], wrap=True, justify=Gtk.Justification.CENTER)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12,
                         margin_bottom=10, margin_top=6)
        bottom.append(entry)
        bottom.append(note)

        self.banner = Adw.Banner(button_label="Open Kulan Settings", use_markup=False)
        self.banner.connect("button-clicked", lambda _b: engine.open_settings(self.fix_page))
        self.fix_page = "assistant"
        self.toasts = Adw.ToastOverlay(child=self.scroller)
        self.title_widget = Adw.WindowTitle(title=TITLE, subtitle="On this computer")
        content_header = Adw.HeaderBar(title_widget=self.title_widget)
        content_view = Adw.ToolbarView(content=self.toasts)
        content_view.add_top_bar(content_header)
        content_view.add_top_bar(self.banner)
        content_view.add_bottom_bar(Adw.Clamp(child=bottom, maximum_size=780))
        self.content_page = Adw.NavigationPage(title=TITLE, child=content_view)

        self.split = Adw.NavigationSplitView(sidebar=self.side_page, content=self.content_page,
                                             min_sidebar_width=220, max_sidebar_width=300)
        self.split.set_show_content(True)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        narrow.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(narrow)
        self.set_content(self.split)

        for name, fn in (("delete-all", lambda *_a: self.confirm_delete_all()),
                         ("settings", lambda *_a: engine.open_settings("assistant")),
                         ("new", lambda *_a: self.new_conversation()), ("focus", lambda *_a: self.input.grab_focus())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", fn)
            self.add_action(action)
        app.set_accels_for_action("win.new", ["<Control>n"])
        app.set_accels_for_action("win.focus", ["<Control>l"])
        app.set_accels_for_action("window.close", ["<Control>w"])

        self.check_ready()
        self.refresh_list()
        self.show_conversation(self.current)
        self.update_send()
        self.input.grab_focus()

    # --- pieces ------------------------------------------------------------------------------------
    def build_welcome(self) -> Gtk.Widget:
        """The empty conversation: what Ask Kulan does, and examples to try."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, vexpand=True,
                      margin_top=36, margin_bottom=24, margin_start=16, margin_end=16)
        box.append(Gtk.Image(icon_name="org.kulan.Assistant", pixel_size=96, margin_bottom=6))
        box.append(Gtk.Label(label=TITLE, css_classes=["title-1"]))
        box.append(Gtk.Label(label="Ask a question, or say what you’d like done on this computer.", wrap=True,
                             justify=Gtk.Justification.CENTER, css_classes=["dim-label"]))
        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, min_children_per_line=1, max_children_per_line=2,
                            column_spacing=8, row_spacing=8, homogeneous=True, halign=Gtk.Align.CENTER, margin_top=12)
        for example in EXAMPLES:
            chip = Gtk.Button(label=example, css_classes=["pill"])
            chip.connect("clicked", lambda _b, t=example: self.ask(t))
            chips.append(chip)
        box.append(chips)
        return box

    def check_ready(self) -> None:
        problem = engine.readiness()
        if problem:
            self.fix_page = problem[1]
            self.banner.set_title(problem[0] + " You can still ask about the time, date, battery "
                                  "or free space.")
        self.banner.set_revealed(problem is not None)

    def update_send(self) -> None:
        text = self.input_text()
        self.placeholder.set_visible(not self.input.get_buffer().get_char_count())
        busy = self.thinking_for is not None
        self.send.set_sensitive(bool(text) and not busy)

    def input_text(self) -> str:
        buffer = self.input.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()

    def copy(self, text: str) -> None:
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toasts.add_toast(Adw.Toast(title="Copied", timeout=2))

    # --- conversations -----------------------------------------------------------------------------
    def save(self, conv: conversation.Conversation) -> None:
        if not self.store:
            return
        try:
            self.store.save(conv)
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=f"Couldn’t save this conversation: {e.strerror or e}", timeout=5))
            return
        self.refresh_list()

    def refresh_list(self) -> None:
        self.conv_list.remove_all()
        convs = self.store.list() if self.store else []
        if self.current.messages and all(c.id != self.current.id for c in convs):
            convs.insert(0, self.current)  # not saved (history off) but on screen
        for conv in convs:
            row = Adw.ActionRow(title=GLib.markup_escape_text(conv.title or "New conversation"),
                                subtitle=when_label(conv.updated), title_lines=1, activatable=True)
            row.conv_id = conv.id
            if self.store:
                delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Delete this conversation", css_classes=["flat"])
                delete.update_property([Gtk.AccessibleProperty.LABEL], [f"Delete “{conv.title}”"])
                delete.connect("clicked", lambda _b, c=conv: self.delete(c))
                row.add_suffix(delete)
            self.conv_list.append(row)
            if conv.id == self.current.id:
                self.conv_list.select_row(row)
        self.conv_empty.set_visible(not convs)
        self.conv_list.set_visible(bool(convs))

    def on_pick(self, _list, row) -> None:
        if row.conv_id == self.current.id:
            self.split.set_show_content(True)
            return
        conv = next((c for c in (self.store.list() if self.store else []) if c.id == row.conv_id), None)
        if conv is not None:
            if self.thinking_for is not None and self.thinking_for.id == conv.id:
                conv = self.thinking_for  # the live copy, still waiting for its answer
            self.current = conv
            self.show_conversation(conv)
        self.split.set_show_content(True)

    def new_conversation(self) -> None:
        if not self.current.messages:
            self.input.grab_focus()
            self.split.set_show_content(True)
            return
        self.current = conversation.Conversation()
        self.show_conversation(self.current)
        self.refresh_list()
        self.split.set_show_content(True)
        self.input.grab_focus()

    def delete(self, conv: conversation.Conversation) -> None:
        if self.store:
            self.store.delete(conv.id)
        if conv.id == self.current.id:
            self.current = conversation.Conversation()
            self.show_conversation(self.current)
        self.refresh_list()
        self.toasts.add_toast(Adw.Toast(title="Conversation deleted", timeout=3))

    def confirm_delete_all(self) -> None:
        dialog = Adw.AlertDialog(heading="Delete all conversations?",
                                 body="They’ll be deleted from this computer. This can’t be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete all")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == "delete" and self.delete_all())
        dialog.present(self)

    def delete_all(self) -> None:
        if self.store:
            self.store.delete_all()
        self.current = conversation.Conversation()
        self.show_conversation(self.current)
        self.refresh_list()

    # --- showing a conversation --------------------------------------------------------------------
    def show_conversation(self, conv: conversation.Conversation) -> None:
        child = self.messages.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.messages.remove(child)
            child = nxt
        self.turns = {k: v for k, v in self.turns.items() if v.conv is conv}
        for message in conv.messages:
            if message["role"] == "user":
                self.add_question(message["text"])
            else:
                self.add_answer(conv, message, None)
        if self.thinking_for is conv:
            self.add_thinking()
        self.welcome.set_visible(not conv.messages)
        self.title_widget.set_title(conv.title or TITLE)
        self.scroll_to_end()

    def add_question(self, text: str) -> None:
        label = Gtk.Label(label=text, wrap=True, xalign=0, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        label.update_property([Gtk.AccessibleProperty.LABEL], [f"You: {text}"])
        bubble = Gtk.Box(halign=Gtk.Align.END, css_classes=["kulan-ask-user"], margin_start=48)
        bubble.append(label)
        self.messages.append(bubble)

    def add_answer(self, conv: conversation.Conversation, message: dict, steps: list[agent.Step] | None) -> Turn:
        turn = self.turns.get(id(message))
        if turn is None or steps is not None:
            turn = Turn(self, conv, message, steps)
            self.turns[id(message)] = turn
        if turn.box.get_parent() is not None:
            turn.box.get_parent().remove(turn.box)
        self.messages.append(turn.box)
        return turn

    def add_thinking(self) -> None:
        box = Gtk.Box(spacing=8)
        spinner = Gtk.Spinner(spinning=True)
        box.append(spinner)
        box.append(Gtk.Label(label="Thinking…", css_classes=["dim-label"]))
        self.thinking_box = box
        self.messages.append(box)

    def scroll_to_end(self) -> None:
        def go() -> bool:
            adj = self.scroller.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False
        GLib.timeout_add(50, go)

    # --- asking ------------------------------------------------------------------------------------
    def on_input_key(self, _c, keyval: int, _code: int, state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self.on_send()
            return True
        return False

    def on_send(self) -> None:
        text = self.input_text()
        if text and self.thinking_for is None:
            self.input.get_buffer().set_text("")
            self.ask(text)

    def ask(self, text: str) -> None:
        text = " ".join(text.split())[:MAX_INPUT] if "\n" not in text else text.strip()[:MAX_INPUT]
        if not text or self.thinking_for is not None:
            return
        conv = self.current
        history = conv.history()
        conv.add("user", text)
        self.welcome.set_visible(False)
        self.title_widget.set_title(conv.title)
        self.add_question(text)
        self.thinking_for = conv
        self.add_thinking()
        self.update_send()
        self.save(conv)
        self.scroll_to_end()
        cancel = self.cancel = threading.Event()
        threading.Thread(target=self.worker, args=(conv, text, history, cancel), daemon=True).start()

    def worker(self, conv: conversation.Conversation, text: str, history: list[dict], cancel: threading.Event) -> None:
        try:
            if not agent.instant.answer(text):
                problem = engine.readiness()
                if problem:
                    GLib.idle_add(self.answered, conv, problem[0], [], cancel)
                    return
            reply, steps = agent.plan(text, history=history, long=True)
            GLib.idle_add(self.answered, conv, reply, steps, cancel)
        except engine.EngineError as e:
            GLib.idle_add(self.answered, conv, str(e), [], cancel)
        except Exception as e:  # noqa: BLE001 - never leave it thinking
            GLib.idle_add(self.answered, conv, f"The assistant stopped unexpectedly: {e}", [], cancel)

    def answered(self, conv: conversation.Conversation, reply: str, steps: list[agent.Step],
                 cancel: threading.Event) -> bool:
        if cancel.is_set():
            return False
        self.thinking_for = None
        if not reply and not steps:
            reply = "Sorry, I couldn’t work that out. Try saying it another way."
        message = conv.add("assistant", reply, [step_record(s) for s in steps])
        self.save(conv)
        if conv is self.current:
            if getattr(self, "thinking_box", None) is not None and self.thinking_box.get_parent() is self.messages:
                self.messages.remove(self.thinking_box)
            self.thinking_box = None
            turn = self.add_answer(conv, message, steps)  # the banner above offers the fix, if one is needed
            if steps and not any(s.action.changes for s in steps):
                turn.run()  # only opening, finding or checking: nothing to confirm
            elif steps:
                getattr(turn, "do_button", self.send).grab_focus()
            self.scroll_to_end()
        else:
            self.toasts.add_toast(Adw.Toast(title=f"Answered in “{conv.title}”", timeout=4))
        self.check_ready()
        self.update_send()
        return False


class AskKulanApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window: AskKulanWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS) if hasattr(provider, "load_from_string") else provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = AskKulanWindow(self)
        self.window.present()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        """A second `kulan-assistant do TEXT` reaches the running app: TEXT is
        asked in a new conversation."""
        text = " ".join(command_line.get_arguments()[1:]).strip()
        self.activate()
        if text and self.window is not None:
            self.window.new_conversation()
            self.window.ask(text)
        return 0


def show(request: str = "") -> int:
    return AskKulanApp().run([sys.argv[0], *([request] if request.strip() else [])])


# --- checks without a model (CI runs these under Xvfb) ---------------------------------------------
def self_test() -> None:
    import shutil
    import tempfile
    from pathlib import Path
    from unittest import mock

    def spin(until, timeout: float = 10.0) -> None:
        ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + int(timeout * 1e6)
        while not until() and GLib.get_monotonic_time() < end:
            ctx.iteration(False)
        ctx.iteration(False)
        assert until(), "timed out"

    tmp = Path(tempfile.mkdtemp())
    calls: list[str] = []

    def act(name: str, changes: bool, ok: bool = True) -> agent.Action:
        def run_it(_a: dict) -> agent.Result:
            calls.append(name)
            return agent.Result(ok, f"{name} done", undo=lambda: calls.append(f"undo {name}"),
                                found=["/tmp/cv.pdf"] if name == "find" else [])
        return agent.Action(name, "", {}, changes, lambda _a: f"Do {name}", run_it)

    plans = {
        "why slow": ("Firefox is using most of the memory.", []),
        "find my cv": ("Looking for your CV.", [agent.Step(act("find", False), {}, "Look for files named like “cv”")]),
        "dark mode": ("Switching to dark mode.", [agent.Step(act("dark", True), {}, "Switch to the dark style")]),
    }
    seen_history: list = []

    def fake_plan(text, history=None, long=False):
        assert long, "the app asks for long answers"
        quick = agent.instant.answer(text)  # as the real planner does, before any model
        if quick:
            return quick, []
        seen_history.append(history)
        return plans[text]

    try:
        Adw.init()
        app = Adw.Application(application_id="org.kulan.Assistant.Test", flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        store = conversation.Store(tmp / "conv")
        with mock.patch.object(agent, "plan", side_effect=fake_plan), \
                mock.patch.object(engine, "readiness", return_value=None):
            w = AskKulanWindow(app, store)
            assert w.welcome.get_visible() and not w.banner.get_revealed()
            # A question: an answer, saved.
            w.ask("why slow")
            spin(lambda: w.thinking_for is None)
            assert [m["role"] for m in w.current.messages] == ["user", "assistant"]
            assert store.list()[0].messages[1]["text"] == "Firefox is using most of the memory."
            assert not w.welcome.get_visible() and w.title_widget.get_title() == "why slow"
            # Finding: done straight away, with the file listed.
            w.ask("find my cv")
            spin(lambda: w.thinking_for is None and not w.running)
            assert calls == ["find"] and w.current.messages[-1]["steps"][0]["ok"] is True
            assert seen_history[-1] and seen_history[-1][0]["user"] == "why slow", seen_history
            # A change waits for Do it; Undo puts it back.
            w.ask("dark mode")
            spin(lambda: w.thinking_for is None)
            turn = w.turns[id(w.current.messages[-1])]
            assert turn.pending() and calls == ["find"]
            turn.run()
            spin(lambda: not w.running)
            assert calls[-1] == "dark" and turn.undos and not turn.pending()
            assert store.list()[0].messages[-1]["steps"][0]["message"] == "dark done"
            turn.undo()
            assert calls[-1] == "undo dark"
            # A new conversation, then back to the first: it's all there, not doable again.
            first = w.current.id
            w.new_conversation()
            assert w.welcome.get_visible() and w.current.id != first
            w.ask("why slow")
            spin(lambda: w.thinking_for is None)
            assert seen_history[-1] == [], "a new conversation starts fresh"
            assert len(store.list()) == 2
            row = w.conv_list.get_first_child()
            while row is not None and getattr(row, "conv_id", None) != first:
                row = row.get_next_sibling()
            w.on_pick(w.conv_list, row)
            assert w.current.id == first and len(w.current.messages) == 6
            reopened = [t for t in w.turns.values() if t.message is w.current.messages[-1]][0]
            assert not reopened.pending(), "an old plan can't be done again"
            # No model: questions say so, with the fix; instant answers still work.
            with mock.patch.object(engine, "readiness", return_value=("Kulan Assistant needs a model first.", "assistant")):
                w.check_ready()
                assert w.banner.get_revealed()
                w.ask("what time is it")
                spin(lambda: w.thinking_for is None)
                assert w.current.messages[-1]["text"].startswith("It's "), w.current.messages[-1]
                w.ask("why slow")
                spin(lambda: w.thinking_for is None)
                assert w.current.messages[-1]["text"] == "Kulan Assistant needs a model first."
            # Deleting.
            w.delete(store.list()[-1])
            assert len(store.list()) == 1
            w.delete_all()
            assert store.list() == [] and w.welcome.get_visible()
            # History off: the conversation is on screen, nothing is written.
            with mock.patch.object(conversation, "keep_history", return_value=False), \
                    mock.patch.object(conversation, "folder", return_value=tmp / "never"):
                off = AskKulanWindow(app)
                assert off.store is None
                off.ask("why slow")
                spin(lambda: off.thinking_for is None)
                assert len(off.current.messages) == 2 and not (tmp / "never").exists()
                off.close()
            # Narrow window: still builds; long text wraps.
            w.set_default_size(360, 500)
            w.ask("x" * 2000)  # unknown to the fake planner: an error answer, never a crash
            spin(lambda: w.thinking_for is None)
            assert len(w.current.messages[-2]["text"]) == MAX_INPUT
            w.close()
        assert when_label(datetime.datetime(2026, 10, 24, 9, 5).timestamp(),
                          datetime.datetime(2026, 10, 24, 18, 0)) == "09:05"
        assert when_label(datetime.datetime(2026, 10, 23, 9, 5).timestamp(),
                          datetime.datetime(2026, 10, 24, 18, 0)) == "Yesterday"
        assert when_label(datetime.datetime(2025, 1, 2).timestamp(), datetime.datetime(2026, 10, 24)) == "2 Jan 2025"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("Ask Kulan app self-test passed")
