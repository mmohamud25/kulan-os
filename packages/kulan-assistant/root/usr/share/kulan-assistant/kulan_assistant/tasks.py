"""What the assistant can do with a piece of text, and how it's asked."""
from __future__ import annotations

import json
import locale
import os
from collections.abc import Iterator
from dataclasses import dataclass

from . import engine

SYSTEM = ("You are Kulan Assistant, a writing helper built into Kulan OS. You run entirely on this person's "
          "computer. Do exactly the task asked. Reply with the result only: no greeting, no explanation of what "
          "you changed, no quotation marks around it, unless the task asks for an explanation.")

# Long documents are cut to fit the model's context window, leaving room for
# the instructions and the reply. Counted in the model's own tokens when the
# engine is running (Chinese or Amharic use far more tokens per character
# than English); otherwise a cautious 1.2 characters per token.
TEXT_TOKENS = engine.CONTEXT - 1500
MAX_INPUT_CHARS = int(TEXT_TOKENS * 1.2)
TOO_LONG = "\n[…the rest was too long and was left out]"


def fit(text: str) -> str:
    """The text, cut to TEXT_TOKENS of the model's tokens."""
    if len(text) <= MAX_INPUT_CHARS:
        return text  # fits however it is tokenised
    try:
        engine.ensure_running()
        resp = engine.request("/tokenize", {"content": text[:TEXT_TOKENS * 8]}, timeout=60)
        with resp:
            tokens = json.loads(resp.read())["tokens"]
        if len(tokens) <= TEXT_TOKENS and len(text) <= TEXT_TOKENS * 8:
            return text
        resp = engine.request("/detokenize", {"tokens": tokens[:TEXT_TOKENS]}, timeout=60)
        with resp:
            return json.loads(resp.read())["content"] + TOO_LONG
    except (engine.EngineHTTPError, OSError, ValueError, KeyError):
        return text[:MAX_INPUT_CHARS] + TOO_LONG


@dataclass(frozen=True)
class Task:
    id: str
    title: str          # shown on menus and the window
    icon: str
    instruction: str    # what the model is asked; {language} is filled in
    max_tokens: int = 1024
    replaces: bool = True  # the result can replace the selected text


TASKS = [
    Task("proofread", "Proofread", "tools-check-spelling-symbolic",
         "Correct the spelling, grammar and punctuation of the text below. Keep the wording, tone and meaning "
         "as they are; change only what is wrong. Keep the same language."),
    Task("rewrite", "Rewrite", "edit-symbolic",
         "Rewrite the text below so it reads clearly and naturally. Keep the meaning and the language."),
    Task("friendly", "Make friendlier", "face-smile-symbolic",
         "Rewrite the text below in a warm, friendly tone. Keep the meaning and the language."),
    Task("professional", "Make professional", "x-office-document-symbolic",
         "Rewrite the text below in a clear, professional tone suitable for work. Keep the meaning and the "
         "language."),
    Task("shorter", "Make shorter", "format-justify-left-symbolic",
         "Make the text below shorter and more direct, keeping every important point. Keep the language."),
    Task("summary", "Summarise", "view-list-bullet-symbolic",
         "Summarise the text below in a short paragraph, in the same language.", replaces=False),
    Task("key-points", "Key points", "view-list-bullet-symbolic",
         "List the key points of the text below as short bullet points (start each with \"• \"), in the same "
         "language.", replaces=False),
    Task("translate", "Translate", "preferences-desktop-locale-symbolic",
         "Translate the text below into {language}. If it is already in {language}, translate it into English.",
         replaces=True),
    Task("explain", "Explain", "dialog-question-symbolic",
         "Explain the text below in simple words, in {language}: what it says and anything that may be unclear "
         "(terms, numbers, abbreviations).", max_tokens=700, replaces=False),
]
WRITING_TASKS = ["proofread", "rewrite", "friendly", "professional", "shorter", "summary", "key-points", "translate"]

LANGUAGES = {"en": "English", "ar": "Arabic", "fr": "French", "es": "Spanish", "de": "German",
             "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish", "tr": "Turkish", "ru": "Russian",
             "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "hi": "Hindi", "ur": "Urdu", "sw": "Swahili",
             "am": "Amharic", "fa": "Persian", "id": "Indonesian", "pl": "Polish", "uk": "Ukrainian"}


def task(task_id: str) -> Task:
    found = next((t for t in TASKS if t.id == task_id), None)
    if found is None:
        raise KeyError(task_id)
    return found


def target_language() -> str:
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup("org.kulan.assistant", True) is not None:
            chosen = Gio.Settings.new("org.kulan.assistant").get_string("translate-to").strip()
            if chosen:
                return chosen
    except Exception:  # noqa: BLE001 - no settings: use the computer's language
        pass
    lang = (os.environ.get("LANGUAGE") or os.environ.get("LANG") or locale.getlocale()[0] or "en").split(":")[0]
    return LANGUAGES.get(lang[:2].lower(), "English")


def messages_for(task_id: str, text: str) -> list[dict]:
    t = task(task_id)
    text = fit(text.strip())
    instruction = t.instruction.format(language=target_language())
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{instruction}\n\n<text>\n{text}\n</text>"}]


def run(task_id: str, text: str) -> Iterator[str]:
    t = task(task_id)
    yield from engine.chat(messages_for(task_id, text), max_tokens=t.max_tokens)
