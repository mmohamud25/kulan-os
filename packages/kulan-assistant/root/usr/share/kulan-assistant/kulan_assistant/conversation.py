"""Ask Kulan's conversations, kept on this computer only.

Each conversation is one JSON file in ~/.local/share/kulan-assistant/
conversations (folder 0700, files 0600: only you can read them). Nothing is
sent anywhere. "Keep conversations" off (Kulan Settings › Assistant) keeps
the current one in memory only and saves nothing; turning it off doesn't
delete what's already there, "Delete all conversations" does.

A message is {"role": "user"|"assistant", "text", "time"} and, for the
assistant, "steps": [{"text", "changes", "action", "args", "ok", "message",
"found"}] as planned and then done. The action and its arguments are kept so
the model sees what it did before (for follow-ups like "and the Desktop
too"); undo isn't (it only works while the app is open).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import GLib

MAX_CONVERSATIONS = 100   # the oldest beyond this are removed
MAX_MESSAGES = 200        # per conversation, oldest dropped
HISTORY_TURNS = 4         # earlier exchanges the model sees (its memory is small)
TITLE_LEN = 60
ID_RULE = re.compile(r"^[0-9a-f]{16}$")


def folder() -> Path:
    return Path(GLib.get_user_data_dir()) / "kulan-assistant" / "conversations"


def keep_history() -> bool:
    """Kulan Settings › Assistant › Keep conversations (on unless turned off)."""
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.kulan.assistant", True) if source else None
        if schema is None or not schema.has_key("keep-conversations"):
            return True
        return Gio.Settings(schema_id="org.kulan.assistant").get_boolean("keep-conversations")
    except Exception:  # noqa: BLE001 - a setting we can't read means the default
        return True


def title_from(text: str) -> str:
    words = " ".join(text.split())
    return words if len(words) <= TITLE_LEN else words[:TITLE_LEN - 1].rsplit(" ", 1)[0] + "…"


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: secrets.token_hex(8))
    title: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    messages: list[dict] = field(default_factory=list)

    def add(self, role: str, text: str, steps: list[dict] | None = None) -> dict:
        message = {"role": role, "text": text, "time": time.time()}
        if steps is not None:
            message["steps"] = steps
        self.messages.append(message)
        del self.messages[:-MAX_MESSAGES]
        if role == "user" and not self.title:
            self.title = title_from(text)
        self.updated = message["time"]
        return message

    def history(self, turns: int = HISTORY_TURNS) -> list[dict]:
        """The last few exchanges, for the model: user text, and what the
        assistant said and planned. Messages still waiting (no reply) are left
        out."""
        pairs, question = [], None
        for m in self.messages:
            if m.get("role") == "user":
                question = m.get("text", "")
            elif m.get("role") == "assistant" and question is not None:
                plan = [{"action": s["action"], "args": s.get("args", {})}
                        for s in m.get("steps", []) if s.get("action")]
                pairs.append({"user": question, "reply": m.get("text", ""), "steps": plan})
                question = None
        return pairs[-turns:]

    def to_json(self) -> dict:
        return {"id": self.id, "title": self.title, "created": self.created, "updated": self.updated,
                "messages": self.messages}

    @classmethod
    def from_json(cls, data: dict) -> "Conversation":
        if not isinstance(data, dict) or not ID_RULE.match(str(data.get("id", ""))):
            raise ValueError("not a conversation")
        messages = [m for m in data.get("messages", []) if isinstance(m, dict)
                    and m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str)]
        return cls(id=data["id"], title=str(data.get("title", ""))[:TITLE_LEN + 1],
                   created=float(data.get("created", 0)), updated=float(data.get("updated", 0)),
                   messages=messages[-MAX_MESSAGES:])


class Store:
    """Saved conversations, newest first."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or folder()

    def _prepare(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path, 0o700)

    def list(self) -> list[Conversation]:
        out = []
        try:
            files = list(self.path.glob("*.json"))
        except OSError:
            return []
        for f in files:
            try:
                out.append(Conversation.from_json(json.loads(f.read_text())))
            except (OSError, ValueError, TypeError):
                continue  # a damaged file is skipped, never fatal
        return sorted(out, key=lambda c: -c.updated)

    def save(self, conv: Conversation) -> None:
        if not conv.messages:
            return
        self._prepare()
        fd, tmp = tempfile.mkstemp(dir=self.path, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(conv.to_json(), f, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path / f"{conv.id}.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.trim()

    def delete(self, conv_id: str) -> None:
        if ID_RULE.match(conv_id):
            (self.path / f"{conv_id}.json").unlink(missing_ok=True)

    def delete_all(self) -> None:
        for f in self.path.glob("*.json") if self.path.exists() else []:
            f.unlink(missing_ok=True)

    def trim(self) -> None:
        for conv in self.list()[MAX_CONVERSATIONS:]:
            self.delete(conv.id)


def self_test() -> None:
    import shutil

    tmp = Path(tempfile.mkdtemp())
    try:
        store = Store(tmp / "conversations")
        assert store.list() == []
        c = Conversation()
        store.save(c)
        assert store.list() == [], "an empty conversation isn't saved"
        c.add("user", "  Tidy   my Downloads folder please, it is a total mess and I can't find anything at all  ")
        assert c.title.endswith("…") and len(c.title) <= TITLE_LEN and "  " not in c.title, c.title
        c.add("assistant", "I'll tidy Downloads.", [{"text": "Tidy Downloads", "changes": True, "action": "tidy_folder",
                                                     "args": {"where": "downloads"}, "ok": True, "message": "Moved 3"}])
        c.add("user", "and the Desktop too")
        assert c.history() == [{"user": c.messages[0]["text"], "reply": "I'll tidy Downloads.",
                                 "steps": [{"action": "tidy_folder", "args": {"where": "downloads"}}]}], c.history()
        store.save(c)
        assert (tmp / "conversations").stat().st_mode & 0o777 == 0o700
        assert (tmp / "conversations" / f"{c.id}.json").stat().st_mode & 0o777 == 0o600
        back = store.list()
        assert len(back) == 1 and back[0].messages == c.messages and back[0].title == c.title
        # History keeps only the last few exchanges.
        d = Conversation()
        for i in range(10):
            d.add("user", f"q{i}")
            d.add("assistant", f"a{i}", [])
        assert [h["user"] for h in d.history()] == [f"q{i}" for i in range(10 - HISTORY_TURNS, 10)]
        d.updated = c.updated + 10
        store.save(d)
        assert [x.id for x in store.list()] == [d.id, c.id], "newest first"
        # A damaged or foreign file is skipped.
        (tmp / "conversations" / "junk.json").write_text("{not json")
        (tmp / "conversations" / "evil.json").write_text(json.dumps({"id": "../../x", "messages": []}))
        assert len(store.list()) == 2
        store.delete("../../etc")  # refused quietly
        store.delete(c.id)
        assert [x.id for x in store.list()] == [d.id]
        # Only the newest MAX_CONVERSATIONS are kept.
        for i in range(MAX_CONVERSATIONS + 3):
            e = Conversation(updated=1000 + i)
            e.messages.append({"role": "user", "text": "x", "time": 0})
            e.updated = 1000 + i
            store.save(e)
        assert len(store.list()) == MAX_CONVERSATIONS
        store.delete_all()
        assert store.list() == []
        long = Conversation()
        for i in range(MAX_MESSAGES + 5):
            long.add("user", str(i))
        assert len(long.messages) == MAX_MESSAGES and long.messages[0]["text"] == "5"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("conversation self-test passed")
