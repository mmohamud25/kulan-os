"""Assistant: Kulan Assistant's models and features, all on this computer."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, choice_row, read_json, settings, switch_row

REGISTRY = Path("/usr/share/kulan-assistant/models.json")
MODELS_DIR = Path("/var/lib/kulan-assistant/models")
ENGINE = Path("/usr/lib/kulan-assistant/engine/llama-server")
WHISPER = Path("/usr/lib/kulan-assistant/whisper/whisper-cli")
SCHEMA = "org.kulan.assistant"
LANGUAGES = ["English", "Arabic", "French", "Spanish", "German", "Italian", "Portuguese", "Dutch", "Swedish",
             "Turkish", "Russian", "Chinese", "Japanese", "Korean", "Hindi", "Urdu", "Swahili", "Amharic",
             "Persian", "Indonesian", "Polish", "Ukrainian"]
# Spoken languages for Dictation (Whisper's codes).
SPOKEN = [("en", "English"), ("ar", "Arabic"), ("fr", "French"), ("es", "Spanish"), ("de", "German"),
          ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("sv", "Swedish"), ("tr", "Turkish"),
          ("ru", "Russian"), ("zh", "Chinese"), ("ja", "Japanese"), ("ko", "Korean"), ("hi", "Hindi"),
          ("ur", "Urdu"), ("sw", "Swahili"), ("am", "Amharic"), ("fa", "Persian"), ("id", "Indonesian"),
          ("pl", "Polish"), ("uk", "Ukrainian")]
# kind: (settings key, what the models are for)
KINDS = {
    "writing": ("model", "Bigger models write better but need more memory and are slower."),
    "dictation": ("dictation-model", "Turns what you say into text. The Accurate model copes better with "
                                     "accents, background noise and other languages."),
}


def memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (ValueError, OSError):
        return 0.0


def models(kind: str = "writing") -> list[dict]:
    return [m for m in read_json(REGISTRY, []) if isinstance(m, dict) and m.get("id") and m.get("file")
            and m.get("kind", "writing") == kind]


def downloaded(model: dict) -> bool:
    path = MODELS_DIR / model["file"]
    return path.is_file() and path.stat().st_size == model.get("size")


def recommended(all_models: list[dict], memory: float) -> str | None:
    """The largest model that leaves room for everything else (half the memory, at least 4 GB free)."""
    fits = [m for m in all_models if m.get("memory_gb", 99) <= max(memory - 4, memory / 2)]
    return (fits[-1] if fits else (all_models[0] if all_models else {})).get("id")


def size_text(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"


class AssistantPage(Page):
    title = "Assistant"
    icon = "kulan-assistant-symbolic"
    keywords = ("assistant", "ai", "writing", "proofread", "rewrite", "summarise", "summarize", "translate",
                "model", "private", "search", "dictation", "speech", "voice", "microphone")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.settings = settings(SCHEMA)
        # Rounded: an "8 GB" computer reports a little less than 8.
        self.memory = round(memory_gb())
        self.groups: dict[str, Adw.PreferencesGroup] = {}
        self.rows: dict[str, list[Gtk.Widget]] = {}
        self.buttons: list[Gtk.Button] = []
        if self.settings is None or not ENGINE.exists():
            status = Adw.StatusPage(icon_name="kulan-assistant-symbolic", title="Kulan Assistant isn’t installed",
                                    description="Private writing help, dictation, summaries in Files and search "
                                                "with questions, all on this computer. Install it from Add-ons.")
            button = Gtk.Button(label="Open Add-ons", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
            button.connect("clicked", lambda _b: self.window.show_page("add-ons"))
            status.set_child(button)
            self.set_child(status)
            return

        self.add_group(
            None,
            "Writing help, dictation, summaries and search with questions. Kulan Assistant runs entirely on this "
            "computer: what you select, say, summarise or search for never leaves it, and it works without the "
            "internet once a model is downloaded.")
        memory = f"This computer has {self.memory:.0f} GB of memory. " if self.memory else ""
        self.groups["writing"] = self.add_group("Writing model", memory + KINDS["writing"][1] + " Models are "
                                                "downloaded once, for everyone on this computer, from Hugging Face.")
        self.progress = Gtk.ProgressBar(show_text=True, margin_top=6, visible=False)
        self.progress.update_property([Gtk.AccessibleProperty.LABEL], ["Model download"])

        features = self.add_group("Writing features")
        self.add_rows(
            features,
            switch_row(self.settings, "writing-tools", "Writing tools",
                       "Select text in any app and press Shift+Super+W to proofread, rewrite, shorten or "
                       "translate it"),
            switch_row(self.settings, "file-summaries", "Summaries in Files",
                       "“Summarise” when you right-click a PDF, Word, LibreOffice or text document"),
            switch_row(self.settings, "live-text-actions", "Translate and explain after Live Text",
                       "Buttons on the Live Text notification for the text it copied"),
            switch_row(self.settings, "question-search", "Search with questions",
                       "Type a question in Activities, such as “the invoice from last month”"),
        )
        current = self.settings.get_string("translate-to")
        choices = [("", "This computer’s language")] + [(name, name) for name in LANGUAGES]
        if current and current not in LANGUAGES:
            choices.append((current, current))
        features.add(choice_row("Translate to", None, choices, lambda: self.settings.get_string("translate-to"),
                                lambda v: self.settings.set_string("translate-to", v)))

        # Reminders set with Ask Kulan: see what's coming, cancel one.
        self.reminders_group = self.add_group(
            "Reminders", "Ask Kulan sets them (Shift+Super+Space: “remind me at 5 to call Amina”). They come even "
                         "after a restart.")
        self.reminder_rows: list[Gtk.Widget] = []

        if WHISPER.exists() and self.settings.props.settings_schema.has_key("dictation"):
            dictation = self.add_group("Dictation", "Press Shift+Super+D, speak, then press it again: the words "
                                                    "are typed where the cursor is. Nothing is recorded or kept.")
            self.add_rows(dictation, switch_row(self.settings, "dictation", "Dictation",
                                                "Shift+Super+D starts and stops it"))
            spoken = [("", "Detect it each time")] + SPOKEN
            dictation.add(choice_row("Language you speak", "Choosing one is faster and more accurate", spoken,
                                     lambda: self.settings.get_string("dictation-language"),
                                     lambda v: self.settings.set_string("dictation-language", v)))
            self.groups["dictation"] = self.add_group("Dictation model", KINDS["dictation"][1])

        # Rebuilt after the click that changed it has finished.
        for key, _desc in KINDS.values():
            if self.settings.props.settings_schema.has_key(key):
                self.watch(self.settings, f"changed::{key}", lambda *_a: GLib.idle_add(lambda: self.shown() and False))
        self.listen_busy(self.on_busy)
        self.shown()

    def show_reminders(self) -> None:
        for row in self.reminder_rows:
            self.reminders_group.remove(row)
        self.reminder_rows = []
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        found = []
        for path in sorted((base / "kulan-assistant" / "reminders").glob("*.json")):
            try:
                data = json.loads(path.read_text())
                at = datetime.fromisoformat(str(data["at"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if re.fullmatch(r"[0-9a-f]{12}", path.stem):
                found.append((at, path.stem, str(data.get("message", ""))[:120]))
        if not found:
            row = Adw.ActionRow(title="No reminders", subtitle="Ask Kulan to remind you about something")
            self.reminders_group.add(row)
            self.reminder_rows.append(row)
            return
        for at, rid, message in sorted(found):
            when = at.strftime("%A %-d %B, %H:%M") if at.date() != datetime.now().date() else at.strftime("Today, %H:%M")
            row = Adw.ActionRow(title=GLib.markup_escape_text(message), subtitle=when)
            cancel = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                tooltip_text="Cancel this reminder")
            cancel.update_property([Gtk.AccessibleProperty.LABEL], [f"Cancel the reminder: {message}"])
            cancel.connect("clicked", lambda _b, r=rid: self.cancel_reminder(r))
            row.add_suffix(cancel)
            self.reminders_group.add(row)
            self.reminder_rows.append(row)

    def cancel_reminder(self, rid: str) -> None:
        try:
            proc = Gio.Subprocess.new(["kulan-assistant", "reminder-cancel", rid], Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.toast("Couldn’t cancel it")
            return
        proc.wait_async(None, lambda p, r: (p.wait_finish(r), self.show_reminders(),
                                           self.toast("Reminder cancelled" if p.get_successful()
                                                      else "Couldn’t cancel it")))

    def shown(self) -> None:
        if not self.groups:
            return
        if hasattr(self, "reminders_group"):
            self.show_reminders()
        self.buttons = []
        for kind, group in self.groups.items():
            for row in self.rows.get(kind, []):
                group.remove(row)
            self.rows[kind] = self.model_rows(kind, group)
        # The progress bar sits under the list being downloaded to.
        group = self.groups.get(getattr(self, "downloading", "writing"), self.groups["writing"])
        group.add(self.progress)
        self.rows.setdefault(getattr(self, "downloading", "writing"), []).append(self.progress)

    def model_rows(self, kind: str, group: Adw.PreferencesGroup) -> list[Gtk.Widget]:
        key = KINDS[kind][0]
        all_models = models(kind)
        best = recommended(all_models, self.memory) if kind == "writing" else None
        # Until someone picks one (or downloads one), the choice shown follows
        # what suits this computer; nothing is saved until then.
        picked = self.settings.get_user_value(key)
        chosen = picked.get_string() if picked is not None else (best or self.settings.get_string(key))
        rows: list[Gtk.Widget] = []
        first_radio = None
        for model in all_models:
            title = model.get("title", model["id"])
            if model["id"] == best:
                title += " (recommended for this computer)"
            row = Adw.ActionRow(title=GLib.markup_escape_text(title),
                                subtitle=GLib.markup_escape_text(
                                    f"{model.get('description', '')} · {size_text(model.get('size', 0))} · "
                                    f"{model.get('source', '')}, {model.get('license', '')}"))
            radio = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=model["id"] == chosen)
            radio.update_property([Gtk.AccessibleProperty.LABEL], [f"Use {model.get('title', model['id'])}"])
            if first_radio is None:
                first_radio = radio
            else:
                radio.set_group(first_radio)
            radio.connect("toggled", self.on_choose, key, model["id"])
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            if downloaded(model):
                row.add_suffix(Gtk.Label(label="Downloaded", css_classes=["dim-label", "caption"]))
                button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Delete this model", css_classes=["flat"])
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"Delete the {model.get('title')} model"])
                button.connect("clicked", self.on_remove, model)
            else:
                button = Gtk.Button(label="Download", valign=Gtk.Align.CENTER,
                                    css_classes=["suggested-action"] if model["id"] == chosen else [])
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"Download the {model.get('title')} model"])
                button.connect("clicked", self.on_download, kind, model)
            button.set_sensitive(not self.window.helper.busy)
            row.add_suffix(button)
            self.buttons.append(button)
            group.add(row)
            rows.append(row)
        return rows

    def on_choose(self, radio: Gtk.CheckButton, key: str, model_id: str) -> None:
        if radio.get_active() and self.settings.get_user_value(key) != GLib.Variant("s", model_id):
            self.settings.set_string(key, model_id)

    def on_download(self, _button: Gtk.Button, kind: str, model: dict) -> None:
        self.downloading = kind
        self.settings.set_string(KINDS[kind][0], model["id"])
        self.shown()
        self.progress.set_fraction(0)
        self.progress.set_text("Starting the download…")
        self.progress.set_visible(True)

        def progress(fraction: float, text: str) -> None:
            # The helper maps install lines (pmstatus) to 30–100 %; show the download's own percentage.
            self.progress.set_fraction(max(0.0, (fraction - 0.3) / 0.7))
            self.progress.set_text(text)

        def done(ok: bool) -> None:
            self.progress.set_visible(False)
            self.shown()

        self.window.helper.run(["assistant-model", model["id"]],
                               f"The {model.get('title', model['id'])} model is ready",
                               on_progress=progress, on_done=done)

    def on_remove(self, _button: Gtk.Button, model: dict) -> None:
        title = model.get("title", model["id"])
        dialog = Adw.AlertDialog(heading=f"Delete the {title} model?",
                                 body=f"This frees {size_text(model.get('size', 0))} for everyone on this "
                                      "computer. You can download it again later.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: r == "delete" and self.window.helper.run(
            ["assistant-model-remove", model["id"]], f"The {title} model was deleted", on_done=lambda _ok: self.shown()))
        dialog.present(self.window)

    def on_busy(self, busy: bool) -> None:
        for button in self.buttons:
            button.set_sensitive(not busy)
