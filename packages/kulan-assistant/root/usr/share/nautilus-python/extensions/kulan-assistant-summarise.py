"""Kulan Assistant: "Summarise" in Files, for one PDF, Word, LibreOffice or text document.

The summary is written on this computer by Kulan Assistant and shown in its
window; the document isn't changed. Turned off in Kulan Settings > Assistant.
"""
from pathlib import Path

from gi.repository import Gio, GLib, GObject, Nautilus

SUFFIXES = {".pdf", ".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".docx", ".odt", ".rtf", ".log"}
SCHEMA = "org.kulan.assistant"


def enabled() -> bool:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA, True) is None:
        return False
    return Gio.Settings.new(SCHEMA).get_boolean("file-summaries")


class KulanAssistantSummarise(GObject.GObject, Nautilus.MenuProvider):
    def get_file_items(self, files):
        if len(files) != 1 or files[0].is_directory() or files[0].get_uri_scheme() != "file":
            return []
        location = files[0].get_location()
        path = location.get_path() if location else None
        if not path or Path(path).suffix.lower() not in SUFFIXES or not enabled():
            return []
        item = Nautilus.MenuItem(name="KulanAssistant::summarise", label="Summarise")
        item.connect("activate", self.summarise, path)
        return [item]

    @staticmethod
    def summarise(_item, path: str) -> None:
        try:
            Gio.Subprocess.new(["kulan-assistant", "summarise", path], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            pass
