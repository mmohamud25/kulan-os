"""The local engine: llama.cpp's server, started for one user on demand.

It listens on 127.0.0.1 only, on a port derived from the user's ID, and
requires a key kept in the user's own config folder (mode 0600), so other
people on the same computer can't use it. It stops itself after
IDLE_MINUTES without requests, which frees the model's memory.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

ENGINE = Path("/usr/lib/kulan-assistant/engine/llama-server")
MODELS_DIR = Path("/var/lib/kulan-assistant/models")
REGISTRY = Path("/usr/share/kulan-assistant/models.json")
UNIT = "kulan-assistant-engine.service"
IDLE_MINUTES = 10
CONTEXT = 8192
THINK = re.compile(r"<think>.*?</think>\s*", re.S)


class EngineError(Exception):
    """Something a person can act on: no model, engine missing, out of memory."""


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "kulan-assistant"


def runtime_dir() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    return base / "kulan-assistant"


def port() -> int:
    """One port per user: 21000-40999, from the user ID."""
    return 21000 + os.getuid() % 20000


def key_file() -> Path:
    return config_dir() / "key"


def api_key() -> str:
    path = key_file()
    try:
        key = path.read_text().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    return key


def models() -> list[dict]:
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return []


def model_info(model_id: str) -> dict | None:
    return next((m for m in models() if m.get("id") == model_id), None)


def model_path(model_id: str) -> Path | None:
    info = model_info(model_id)
    if not info:
        return None
    path = MODELS_DIR / info["file"]
    return path if path.is_file() else None


def chosen_model() -> str:
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is not None and source.lookup("org.kulan.assistant", True) is not None:
            return Gio.Settings.new("org.kulan.assistant").get_string("model")
    except Exception:  # noqa: BLE001 - no GSettings (CLI in a bare session): use the default
        pass
    return "qwen3-4b"


def installed_models() -> list[dict]:
    return [m for m in models() if model_path(m["id"]) is not None]


def usable_model() -> str | None:
    """The chosen model if downloaded, else any downloaded one."""
    chosen = chosen_model()
    if model_path(chosen):
        return chosen
    first = next(iter(installed_models()), None)
    return first["id"] if first else None


def touch_last_use() -> None:
    path = runtime_dir() / "last-use"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _request(path: str, body: dict | None = None, timeout: float = 5) -> urllib.request.Request:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port()}{path}", data=data,
                                 method="POST" if data is not None else "GET")
    req.add_header("Authorization", f"Bearer {api_key()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return req


def healthy() -> bool:
    try:
        with urllib.request.urlopen(_request("/health"), timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_running(timeout: float = 120) -> None:
    """Start the engine (as a systemd user service) and wait until the model is loaded."""
    touch_last_use()
    if healthy():
        return
    if not ENGINE.exists():
        raise EngineError("The assistant engine isn't installed. Reinstall Kulan Assistant from Add-ons.")
    if usable_model() is None:
        raise EngineError("No model yet. Choose one in Kulan Settings > Assistant.")
    api_key()  # create it before the service reads it
    started = subprocess.run(["systemctl", "--user", "start", UNIT], capture_output=True, text=True, check=False)
    if started.returncode != 0:
        # Not under systemd (for example a test container): run it directly.
        subprocess.Popen(["/usr/lib/kulan-assistant/kulan-assistant-engine"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy():
            return
        time.sleep(0.5)
    raise EngineError("The assistant didn't start. The model may need more free memory than this computer has.")


def chat(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.3) -> Iterator[str]:
    """Stream the reply, piece by piece. Hidden "thinking" is removed."""
    ensure_running()
    body = {
        "messages": messages,
        "stream": True,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # Qwen3: answer directly, without a reasoning section.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    buffer = ""
    in_think = False
    try:
        with urllib.request.urlopen(_request("/v1/chat/completions", body), timeout=600) as resp:
            for raw in resp:
                touch_last_use()
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content") or ""
                except (ValueError, KeyError, IndexError):
                    continue
                buffer += delta
                # Drop any <think>...</think> the model still writes.
                while True:
                    if in_think:
                        end = buffer.find("</think>")
                        if end < 0:
                            buffer = buffer[-8:]
                            break
                        buffer = buffer[end + 8:].lstrip()
                        in_think = False
                    start = buffer.find("<think>")
                    if start < 0:
                        keep = len(buffer) - 7 if len(buffer) > 7 else 0
                        if keep:
                            yield buffer[:keep]
                            buffer = buffer[keep:]
                        break
                    if start:
                        yield buffer[:start]
                    buffer = buffer[start + 7:]
                    in_think = True
    except urllib.error.HTTPError as exc:
        raise EngineError(f"The assistant couldn't answer (error {exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise EngineError(f"The assistant stopped unexpectedly: {exc}") from exc
    if buffer and not in_think:
        yield buffer
    touch_last_use()


def complete(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.3) -> str:
    return THINK.sub("", "".join(chat(messages, max_tokens, temperature))).strip()
