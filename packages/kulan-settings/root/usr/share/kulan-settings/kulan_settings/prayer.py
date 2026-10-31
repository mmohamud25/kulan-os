"""Prayer Times: off until turned on. The next prayer in the top bar and a
notification at each prayer time, calculated on this computer for the city
chosen here (from an offline list; nothing is looked up online)."""
from __future__ import annotations

import difflib
import gzip
import unicodedata
from pathlib import Path

from gi.repository import Adw, GLib, Gtk

from .common import KULAN_SCHEMA, Page, choice_row, has_key, settings, switch_row

CITIES = Path("/usr/share/kulan/cities.tsv.gz")
MAX_RESULTS = 8
METHODS = [
    ("", "The one used in the city’s country"),
    ("MWL", "Muslim World League"),
    ("ISNA", "Islamic Society of North America"),
    ("Egypt", "Egyptian General Authority of Survey"),
    ("Makkah", "Umm al-Qura University, Makkah"),
    ("Karachi", "University of Islamic Sciences, Karachi"),
    ("Tehran", "Institute of Geophysics, University of Tehran"),
    ("Jafari", "Shia Ithna Ashari (Jafari)"),
    ("Gulf", "Gulf Region"),
    ("Kuwait", "Kuwait"),
    ("Qatar", "Qatar"),
    ("Singapore", "Singapore, Malaysia and Indonesia"),
    ("Turkey", "Diyanet, Turkey"),
]


def plain(text: str) -> str:
    """Lower case without accents, for matching what people type."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


_cities: list[tuple[str, str, str, int, float, float]] | None = None


def cities() -> list[tuple[str, str, str, int, float, float]]:
    global _cities
    if _cities is None:
        rows = []
        try:
            with gzip.open(CITIES, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    name, code, country, people, lat, lon = line.rstrip("\n").split("\t")
                    rows.append((name, code, country, int(people), float(lat), float(lon)))
        except (OSError, ValueError):
            rows = []
        _cities = rows
    return _cities


def search(text: str) -> list[tuple[str, str, str, int, float, float]]:
    query = plain(text.strip())
    if len(query) < 2:
        return []
    city_part, _, country_part = query.partition(",")
    city_part, country_part = city_part.strip(), country_part.strip()
    found = [c for c in cities() if plain(c[0]).startswith(city_part)
             and (not country_part or plain(c[2]).startswith(country_part) or plain(c[1]) == country_part)]
    if len(found) < MAX_RESULTS:
        found += [c for c in cities() if city_part in plain(c[0]) and c not in found]
    if not found:
        # Other spellings ("Hargeisa" for Hargeysa).
        names = {plain(c[0]) for c in cities()}
        close = set(difflib.get_close_matches(city_part, names, n=MAX_RESULTS, cutoff=0.75))
        found = [c for c in cities() if plain(c[0]) in close]
    return found[:MAX_RESULTS]  # the list is sorted by population already


class PrayerPage(Page):
    title = "Prayer times"
    icon = "preferences-system-time-symbolic"
    keywords = ("prayer", "prayers", "salah", "salat", "namaz", "adhan", "azan", "fajr", "dhuhr", "asr",
                "maghrib", "isha", "islam", "muslim", "qibla")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.settings = settings(KULAN_SCHEMA)
        if not has_key(self.settings, "prayer-times"):
            self.add_group(None, "Prayer times need a newer version of Kulan OS. Update, then sign in again.")
            return
        top = self.add_group(None, "Shows the next prayer in the top bar and notifies you at each prayer time. "
                                   "Times are worked out on this computer from your city, not looked up "
                                   "online.")
        self.add_rows(top, switch_row(self.settings, "prayer-times", "Prayer times"))

        place = self.add_group("City")
        self.city_row = Adw.ActionRow(title="Prayer times for")
        place.add(self.city_row)
        self.search_row = Adw.EntryRow(title="Search for a city")
        self.search_timer = 0
        self.search_row.connect("changed", self.on_changed)
        place.add(self.search_row)
        self.results = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                   margin_top=6, visible=False)
        self.results.connect("row-activated", self.on_pick)
        place.add(self.results)

        calc = self.add_group("Calculation")
        self.add_rows(
            calc,
            choice_row("Method", "Ask your local mosque if you’re unsure", METHODS,
                       lambda: self.settings.get_string("prayer-method"),
                       lambda v: self.settings.set_string("prayer-method", v)),
            choice_row("Asr", None, [("standard", "Standard (Shafi’i, Maliki, Hanbali)"), ("hanafi", "Hanafi")],
                       lambda: self.settings.get_string("prayer-asr"),
                       lambda v: self.settings.set_string("prayer-asr", v)),
            switch_row(self.settings, "prayer-notify", "Notify at prayer times",
                       "A notification and a soft sound when each prayer time comes"),
        )
        self.add_group(None, "City list: GeoNames (CC BY 4.0).")
        self.watch(self.settings, "changed::prayer-city", lambda *_a: self.shown())
        self.shown()

    def shown(self) -> None:
        if not has_key(self.settings, "prayer-city"):
            return
        city = self.settings.get_string("prayer-city")
        if city:
            lat, lon = self.settings.get_double("prayer-latitude"), self.settings.get_double("prayer-longitude")
            self.city_row.set_subtitle(GLib.markup_escape_text(
                f"{city} ({abs(lat):.2f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.2f}° {'E' if lon >= 0 else 'W'})"))
        else:
            self.city_row.set_subtitle("No city yet. Search for yours below.")

    def on_changed(self, _entry: Adw.EntryRow) -> None:
        # Search once typing pauses, not on every key.
        if self.search_timer:
            GLib.source_remove(self.search_timer)
        self.search_timer = GLib.timeout_add(250, self.on_search)

    def on_search(self) -> bool:
        self.search_timer = 0
        entry = self.search_row
        while (row := self.results.get_row_at_index(0)) is not None:
            self.results.remove(row)
        found = search(entry.get_text())
        for name, code, country, people, lat, lon in found:
            row = Adw.ActionRow(title=GLib.markup_escape_text(f"{name}, {country}"),
                                subtitle=f"{people:,} people", activatable=True)
            row.city = (name, code, country, lat, lon)
            self.results.append(row)
        self.results.set_visible(bool(found))
        return GLib.SOURCE_REMOVE

    def teardown(self) -> None:
        if getattr(self, "search_timer", 0):
            GLib.source_remove(self.search_timer)
            self.search_timer = 0
        super().teardown()

    def on_pick(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        name, code, country, lat, lon = row.city
        # The city name last: Kulan Shell shows prayer times once it is set,
        # so the coordinates must already be there.
        self.settings.set_string("prayer-country", code)
        self.settings.set_double("prayer-latitude", lat)
        self.settings.set_double("prayer-longitude", lon)
        self.settings.set_string("prayer-city", f"{name}, {country}")
        self.search_row.set_text("")
        self.results.set_visible(False)
        self.toast(f"Prayer times for {name}")  # the window escapes toast text
