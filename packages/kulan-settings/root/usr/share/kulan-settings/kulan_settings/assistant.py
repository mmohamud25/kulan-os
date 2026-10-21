"""Assistant: Kulan Assistant's model and features, all on this computer."""
from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .common import Page, choice_row, read_json, settings, switch_row

REGISTRY = Path("/usr/share/kulan-assistant/models.json")
MODELS_DIR = Path("/var/lib/kulan-assistant/models")
ENGINE = Path("/usr/lib/kulan-assistant/engine/llama-server")
SCHEMA = "org.kulan.assistant"
LANGUAGES = ["English", "Arabic", "French", "Spanish", "German", "Italian", "Portuguese", "Dutch", "Swedish",
             "Turkish", "Russian", "Chinese", "Japanese", "Korean", "Hindi", "Urdu", "Swahili", "Amharic",
             "Persian", "Indonesian", "Polish", "Ukrainian"]


def memory_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (ValueError, OSError):
        return 0.0


def models() -> list[dict]:
    return [m for m in read_json(REGISTRY, []) if isinstance(m, dict) and m.get("id") and m.get("file")]


def downloaded(model: dict) -> bool:
    path = MODELS_DIR / model["file"]
    return path.is_file() and path.stat().st_size == model.get("size")


def recommended(all_models: list[dict], memory: float) -> str | None:
    """The largest model that leaves room for everything else (half the memory, at least 4 GB free)."""
    fits = [m for m in all_models if m.get("memory_gb", 99) <= max(memory - 4, memory / 2)]
    return (fits[-1] if fits else (all_models[0] if all_models else {})).get("id")


class AssistantPage(Page):
    title = "Assistant"
    icon = "kulan-assistant-symbolic"
    keywords = ("assistant", "ai", "writing", "proofread", "rewrite", "summarise", "summarize", "translate",
                "model", "private", "search")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.settings = settings(SCHEMA)
        # Rounded: an "8 GB" computer reports a little less than 8.
        self.memory = round(memory_gb())
        if self.settings is None or not ENGINE.exists():
            status = Adw.StatusPage(icon_name="kulan-assistant-symbolic", title="Kulan Assistant isn’t installed",
                                    description="Private writing help, summaries in Files and search with "
                                                "questions, all on this computer. Install it from Add-ons.")
            button = Gtk.Button(label="Open Add-ons", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
            button.connect("clicked", lambda _b: self.window.show_page("add-ons"))
            status.set_child(button)
            self.set_child(status)
            self.model_group = None
            return

        self.add_group(
            None,
            "Writing help, summaries and search with questions. The assistant runs entirely on this computer: "
            "what you select, summarise or search for never leaves it, and it works without the internet "
            "once a model is downloaded.")
        self.model_group = self.add_group("Model", self.model_description())
        self.model_rows: list[Gtk.Widget] = []
        self.progress = Gtk.ProgressBar(show_text=True, margin_top=6, visible=False)
        self.progress.update_property([Gtk.AccessibleProperty.LABEL], ["Model download"])

        features = self.add_group("Features")
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
        # Rebuilt after the click that changed it has finished.
        self.watch(self.settings, "changed::model", lambda *_a: GLib.idle_add(lambda: self.shown() and False))
        self.listen_busy(self.on_busy)
        self.shown()

    def model_description(self) -> str:
        memory = f"This computer has {self.memory:.0f} GB of memory. " if self.memory else ""
        return (memory + "Bigger models write better but need more memory and are slower. Models are "
                "downloaded once, for everyone on this computer, from Hugging Face.")

    def shown(self) -> None:
        if self.model_group is None:
            return
        for row in self.model_rows:
            self.model_group.remove(row)
        self.model_rows = []
        self.buttons: list[Gtk.Button] = []
        all_models = models()
        best = recommended(all_models, self.memory)
        # Until someone picks one, the choice follows what suits this computer.
        if (best and self.settings.get_user_value("model") is None and not any(downloaded(m) for m in all_models)
                and self.settings.get_string("model") != best):
            self.settings.set_string("model", best)
        chosen = self.settings.get_string("model")
        first_radio = None
        for model in all_models:
            have = downloaded(model)
            size = f"{model.get('size', 0) / 1e9:.1f} GB"
            title = model.get("title", model["id"])
            if model["id"] == best:
                title += " (recommended for this computer)"
            row = Adw.ActionRow(title=GLib.markup_escape_text(title),
                                subtitle=GLib.markup_escape_text(f"{model.get('description', '')} · {size} · "
                                                                 f"{model.get('source', '')}, "
                                                                 f"{model.get('license', '')}"))
            radio = Gtk.CheckButton(valign=Gtk.Align.CENTER, active=model["id"] == chosen)
            radio.update_property([Gtk.AccessibleProperty.LABEL], [f"Use {model.get('title', model['id'])}"])
            if first_radio is None:
                first_radio = radio
            else:
                radio.set_group(first_radio)
            radio.connect("toggled", self.on_choose, model["id"])
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            if have:
                row.add_suffix(Gtk.Label(label="Downloaded", css_classes=["dim-label", "caption"]))
                button = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Delete this model", css_classes=["flat"])
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"Delete the {model.get('title')} model"])
                button.connect("clicked", self.on_remove, model)
            else:
                button = Gtk.Button(label="Download", valign=Gtk.Align.CENTER,
                                    css_classes=["suggested-action"] if model["id"] == chosen else [])
                button.update_property([Gtk.AccessibleProperty.LABEL], [f"Download the {model.get('title')} model"])
                button.connect("clicked", self.on_download, model)
            button.set_sensitive(not self.window.helper.busy)
            row.add_suffix(button)
            self.buttons.append(button)
            self.model_group.add(row)
            self.model_rows.append(row)
        self.model_group.add(self.progress)
        self.model_rows.append(self.progress)

    def on_choose(self, radio: Gtk.CheckButton, model_id: str) -> None:
        if radio.get_active() and self.settings.get_string("model") != model_id:
            self.settings.set_string("model", model_id)

    def on_download(self, _button: Gtk.Button, model: dict) -> None:
        self.settings.set_string("model", model["id"])
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
                                 body=f"This frees {model.get('size', 0) / 1e9:.1f} GB for everyone on this "
                                      "computer. You can download it again later.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: r == "delete" and self.window.helper.run(
            ["assistant-model-remove", model["id"]], f"The {title} model was deleted", on_done=lambda _ok: self.shown()))
        dialog.present(self.window)

    def on_busy(self, busy: bool) -> None:
        for button in getattr(self, "buttons", []):
            button.set_sensitive(not busy)
