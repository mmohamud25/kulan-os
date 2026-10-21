"""Search with questions: a GNOME Shell search provider.

Type a question in Activities, such as "the invoice from last month". After
a short pause, the assistant turns it into search words, a kind of file and
dates, and GNOME's own file index (LocalSearch, formerly Tracker) finds the
files. Only the question goes to the model, on this computer; the index is
read directly.
"""
from __future__ import annotations

import datetime
import json
import re
import threading
from pathlib import Path

import gi

try:  # LocalSearch's library: "Tsparql" from 3.8, "Tracker" before
    gi.require_version("Tsparql", "3.0")
    from gi.repository import Tsparql  # noqa: E402
except (ValueError, ImportError):
    gi.require_version("Tracker", "3.0")
    from gi.repository import Tracker as Tsparql  # noqa: E402
from gi.repository import Gio, GLib  # noqa: E402

from . import engine  # noqa: E402

BUS_NAME = "org.kulan.Assistant.SearchProvider"
OBJECT_PATH = "/org/kulan/Assistant/SearchProvider"
INDEX_NAMES = ("org.freedesktop.LocalSearch3", "org.freedesktop.Tracker3.Miner.Files")
DEBOUNCE_MS = 900
MIN_WORDS = 3
MAX_RESULTS = 8
IDLE_EXIT_S = 300

INTERFACE = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet"><arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/></method>
    <method name="GetSubsearchResultSet"><arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/><arg type="as" name="results" direction="out"/></method>
    <method name="GetResultMetas"><arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" name="metas" direction="out"/></method>
    <method name="ActivateResult"><arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/><arg type="u" name="timestamp" direction="in"/></method>
    <method name="LaunchSearch"><arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/></method>
  </interface>
</node>
"""

KINDS = {
    "document": "nfo:Document", "pdf": "nfo:PaginatedTextDocument", "spreadsheet": "nfo:Spreadsheet",
    "presentation": "nfo:Presentation", "image": "nfo:Image", "photo": "nfo:Image", "video": "nfo:Video",
    "audio": "nfo:Audio", "music": "nfo:Audio",
}
SCHEMA = {
    "type": "object",
    "properties": {
        "words": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "kind": {"type": "string", "enum": ["any", *KINDS]},
        "after": {"type": ["string", "null"]},
        "before": {"type": ["string", "null"]},
    },
    "required": ["words", "kind", "after", "before"],
}
SAFE_WORD = re.compile(r"[^\w\- ]+", re.UNICODE)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def enabled() -> bool:
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup("org.kulan.assistant", True) is None:
        return False
    return Gio.Settings.new("org.kulan.assistant").get_boolean("question-search")


def understand(question: str) -> dict | None:
    """Ask the model what the question is looking for, as JSON."""
    today = datetime.date.today()
    prompt = (f"Today is {today:%A %d %B %Y} ({today.isoformat()}). Someone is searching their files with this "
              f"request:\n\n{question}\n\nGive the 1 to 4 most useful words to search for (words that would be in "
              "the file's name or text, not words like 'file', 'find', 'my' or dates), the kind of file "
              "(or 'any'), and the date range it was changed in as YYYY-MM-DD, or null when no date is given.")
    body_messages = [{"role": "system", "content": "You turn file-search requests into JSON. Reply with JSON only."},
                     {"role": "user", "content": prompt}]
    engine.ensure_running(timeout=60)
    req = engine._request("/v1/chat/completions", {  # noqa: SLF001 - same module family
        "messages": body_messages, "temperature": 0, "max_tokens": 200,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_object", "schema": SCHEMA},
    })
    import urllib.request
    with urllib.request.urlopen(req, timeout=90) as resp:
        content = json.loads(resp.read())["choices"][0]["message"]["content"]
    engine.touch_last_use()
    try:
        parsed = json.loads(engine.THINK.sub("", content))
    except ValueError:
        return None
    words = [SAFE_WORD.sub(" ", str(w)).strip() for w in parsed.get("words", [])][:4]
    words = [w for w in words if w]
    kind = parsed.get("kind") if parsed.get("kind") in KINDS else "any"
    after = parsed.get("after") if isinstance(parsed.get("after"), str) and DATE.match(parsed["after"]) else None
    before = parsed.get("before") if isinstance(parsed.get("before"), str) and DATE.match(parsed["before"]) else None
    return {"words": words, "kind": kind, "after": after, "before": before}


def sparql(query: dict) -> list[str]:
    """LocalSearch queries for the files: one on their text, one on their names.

    Words are letters, digits, dashes and spaces only (see understand()), so
    they can't change the query."""
    words = [SAFE_WORD.sub(" ", w).strip() for w in query["words"]]
    words = [w for w in words if w]
    if not words:
        return []
    kind = KINDS.get(query["kind"])
    kind_filter = f"?ie a {kind} ." if kind else ""
    name_kind = f"?ie nie:isStoredAs ?f ; a {kind} ." if kind else ""
    dates = []
    if query["after"]:
        dates.append(f'?modified >= "{query["after"]}T00:00:00Z"^^xsd:dateTime')
    if query["before"]:
        dates.append(f'?modified <= "{query["before"]}T23:59:59Z"^^xsd:dateTime')
    date_filter = f"FILTER ({' && '.join(dates)})" if dates else ""
    fts = " ".join(words)
    names = " || ".join(f'CONTAINS(LCASE(?name), "{w.lower()}")' for w in words)
    text = f"""
SELECT DISTINCT ?url ?modified WHERE {{
  ?ie fts:match "{fts}" ; nie:isStoredAs ?f .
  {kind_filter}
  ?f nie:url ?url ; nfo:fileLastModified ?modified .
  {date_filter}
}} ORDER BY DESC(?modified) LIMIT {MAX_RESULTS}
"""
    name = f"""
SELECT DISTINCT ?url ?modified WHERE {{
  {name_kind}
  ?f a nfo:FileDataObject ; nfo:fileName ?name ; nie:url ?url ; nfo:fileLastModified ?modified .
  FILTER ({names})
  {date_filter}
}} ORDER BY DESC(?modified) LIMIT {MAX_RESULTS}
"""
    return [text, name]


def index() -> Tsparql.SparqlConnection | None:
    for name in INDEX_NAMES:
        try:
            return Tsparql.SparqlConnection.bus_new(name, None, None)
        except GLib.Error:
            continue
    return None


def find(question: str) -> list[str]:
    query = understand(question)
    if not query:
        return []
    queries = sparql(query)
    connection = index()
    if not queries or connection is None:
        return []
    found: dict[str, str] = {}
    for text in queries:
        try:
            cursor = connection.query(text, None)
        except GLib.Error:
            continue
        while cursor.next(None):
            url = cursor.get_string(0)[0]
            if url and url.startswith("file://") and url not in found:
                found[url] = cursor.get_string(1)[0] or ""
    # Newest first, from both queries together.
    return sorted(found, key=lambda u: found[u], reverse=True)[:MAX_RESULTS]


class Provider:
    def __init__(self, loop: GLib.MainLoop) -> None:
        self.loop = loop
        self.pending: tuple[Gio.DBusMethodInvocation, str] | None = None
        self.timer = 0
        self.generation = 0
        self.idle_timer = GLib.timeout_add_seconds(IDLE_EXIT_S, self.idle_exit)

    def idle_exit(self) -> bool:
        self.loop.quit()
        return GLib.SOURCE_REMOVE

    def keep_alive(self) -> None:
        GLib.source_remove(self.idle_timer)
        self.idle_timer = GLib.timeout_add_seconds(IDLE_EXIT_S, self.idle_exit)

    def on_call(self, _conn, _sender, _path, _iface, method, params, invocation) -> None:
        self.keep_alive()
        args = params.unpack()
        if method in ("GetInitialResultSet", "GetSubsearchResultSet"):
            terms = args[-1]
            self.search(invocation, " ".join(terms).strip())
        elif method == "GetResultMetas":
            invocation.return_value(GLib.Variant("(aa{sv})", ([self.meta(i) for i in args[0]],)))
        elif method == "ActivateResult":
            try:
                Gio.AppInfo.launch_default_for_uri(args[0], None)
            except GLib.Error:
                pass
            invocation.return_value(None)
        elif method == "LaunchSearch":
            invocation.return_value(None)

    def search(self, invocation: Gio.DBusMethodInvocation, question: str) -> None:
        # A newer search replaces the one waiting.
        if self.pending:
            self.pending[0].return_value(GLib.Variant("(as)", ([],)))
            self.pending = None
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        self.generation += 1
        if len(question.split()) < MIN_WORDS or not enabled() or engine.usable_model() is None:
            invocation.return_value(GLib.Variant("(as)", ([],)))
            return
        self.pending = (invocation, question)
        generation = self.generation
        self.timer = GLib.timeout_add(DEBOUNCE_MS, self.start, generation)

    def start(self, generation: int) -> bool:
        self.timer = 0
        if not self.pending or generation != self.generation:
            return GLib.SOURCE_REMOVE
        invocation, question = self.pending
        self.pending = None

        def work() -> None:
            try:
                results = find(question)
            except Exception:  # noqa: BLE001 - a search must never break Activities
                results = []
            GLib.idle_add(lambda: invocation.return_value(GLib.Variant("(as)", (results,))) and False)

        threading.Thread(target=work, daemon=True).start()
        return GLib.SOURCE_REMOVE

    @staticmethod
    def meta(identifier: str) -> dict:
        file = Gio.File.new_for_uri(identifier)
        name = file.get_basename() or identifier
        description = ""
        icon = Gio.ThemedIcon.new("text-x-generic")
        try:
            info = file.query_info("standard::icon,time::modified", Gio.FileQueryInfoFlags.NONE, None)
            icon = info.get_icon() or icon
            modified = info.get_modification_date_time()
            parent = file.get_parent()
            where = GLib.filename_display_name(str(Path(parent.get_path()).relative_to(Path.home()))) \
                if parent and parent.get_path() and Path(parent.get_path()).is_relative_to(Path.home()) else ""
            when = modified.to_local().format("%-d %b %Y") if modified else ""
            description = " · ".join(x for x in (where or "Home", when) if x)
        except GLib.Error:
            pass
        return {"id": GLib.Variant("s", identifier), "name": GLib.Variant("s", name),
                "description": GLib.Variant("s", description), "gicon": GLib.Variant("s", icon.to_string())}


def serve() -> int:
    loop = GLib.MainLoop()
    provider = Provider(loop)
    node = Gio.DBusNodeInfo.new_for_xml(INTERFACE)

    def on_bus(connection, _name) -> None:
        connection.register_object(OBJECT_PATH, node.interfaces[0], provider.on_call, None, None)

    Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, on_bus, None,
                     lambda *_a: loop.quit())
    loop.run()
    return 0
