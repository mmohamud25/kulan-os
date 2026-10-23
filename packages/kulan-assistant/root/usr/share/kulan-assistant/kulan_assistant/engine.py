"""The local engine: llama.cpp's server, started for one user on demand.

It listens on a Unix socket in the user's own runtime folder
($XDG_RUNTIME_DIR, which only they can open), not on a network port, and
also requires a key kept in their config folder (mode 0600). Other people
on the same computer can neither reach it nor pretend to be it. It stops itself after
IDLE_MINUTES without requests, which frees the model's memory.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import re
import secrets
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

ENGINE = Path("/usr/lib/kulan-assistant/engine/llama-server")
MODELS_DIR = Path("/var/lib/kulan-assistant/models")
REGISTRY = Path("/usr/share/kulan-assistant/models.json")
UNIT = "kulan-assistant-engine.service"
IDLE_MINUTES = 10
# Lighter mode (computers with little memory, /usr/lib/kulan/lighter-mode):
# the model's memory goes back sooner.
LIGHTER_IDLE_MINUTES = 2
LIGHTER_FLAG = Path("/run/kulan/lighter-mode")


def idle_minutes() -> int:
    return LIGHTER_IDLE_MINUTES if LIGHTER_FLAG.exists() else IDLE_MINUTES
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


def socket_path() -> Path:
    return runtime_dir() / "engine.sock"


def prepare_runtime_dir() -> Path:
    """$XDG_RUNTIME_DIR/kulan-assistant, owned by this user and closed to others."""
    path = runtime_dir()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    info = path.stat()
    if info.st_uid != os.getuid():
        raise EngineError("The assistant's private folder belongs to someone else; not using it.")
    return path


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


def _setting(key: str, default: str) -> str:
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.kulan.assistant", True) if source is not None else None
        if schema is not None and schema.has_key(key):
            return Gio.Settings.new("org.kulan.assistant").get_string(key)
    except Exception:  # noqa: BLE001 - no GSettings (CLI in a bare session): use the default
        pass
    return default


def chosen_model(kind: str = "writing") -> str:
    if kind == "dictation":
        return _setting("dictation-model", "whisper-base")
    return _setting("model", "qwen3-4b")


def installed_models(kind: str = "writing") -> list[dict]:
    return [m for m in models() if m.get("kind", "writing") == kind and model_path(m["id"]) is not None]


def usable_model(kind: str = "writing") -> str | None:
    """The chosen model if downloaded, else any downloaded one of that kind."""
    chosen = chosen_model(kind)
    info = model_info(chosen)
    if info and info.get("kind", "writing") == kind and model_path(chosen):
        return chosen
    first = next(iter(installed_models(kind)), None)
    return first["id"] if first else None


def readiness(kind: str = "writing") -> tuple[str, str] | None:
    """None when the assistant can answer; otherwise (what to tell the person,
    the Kulan Settings page that fixes it), to show before they try."""
    if not ENGINE.exists():
        return ("Kulan Assistant isn't fully installed. Turn it on in Kulan Settings › Add-ons.", "add-ons")
    if usable_model(kind) is None:
        if kind == "dictation":
            return ("Dictation needs a speech model first: a free download, chosen once in Kulan Settings › "
                    "Assistant.", "assistant")
        return ("Kulan Assistant needs a model first: a free download, chosen once in Kulan Settings › Assistant.",
                "assistant")
    return None


def open_settings(page: str) -> None:
    subprocess.Popen(["kulan-settings", page], start_new_session=True, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def touch_last_use() -> None:
    path = prepare_runtime_dir() / "last-use"
    path.touch()


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = str(path)

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class EngineHTTPError(EngineError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def request(path: str, body: dict | None = None, timeout: float = 5) -> http.client.HTTPResponse:
    """A request to this user's engine; the caller reads and closes the response.

    Raises OSError when the engine isn't there, EngineHTTPError on an error reply."""
    conn = _UnixConnection(socket_path(), timeout)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {api_key()}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        conn.request("POST" if data is not None else "GET", path, body=data, headers=headers)
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException):
        conn.close()
        raise
    if resp.status >= 400:
        detail = resp.read(2000).decode("utf-8", "replace")
        conn.close()
        raise EngineHTTPError(resp.status, f"The assistant couldn't answer (error {resp.status}). {detail[:200]}".strip())
    return resp


def status() -> str:
    """"ready", "loading" (started, model not loaded yet), "wrong-key" or "stopped"."""
    try:
        # /v1/models needs the key, so this also checks the engine is this user's.
        resp = request("/v1/models", timeout=2)
        resp.read()
        resp.close()
        return "ready"
    except EngineHTTPError as exc:
        return "wrong-key" if exc.status == 401 else "loading" if exc.status == 503 else "stopped"
    except (OSError, http.client.HTTPException, ValueError):
        return "stopped"


def healthy() -> bool:
    return status() == "ready"


def _service_failed() -> bool:
    out = subprocess.run(["systemctl", "--user", "is-failed", UNIT], capture_output=True, text=True, check=False)
    return out.stdout.strip() == "failed"


def ensure_running(timeout: float = 120) -> None:
    """Start the engine (as a systemd user service) and wait until the model is loaded."""
    touch_last_use()
    state = status()
    if state == "ready":
        return
    if not ENGINE.exists():
        raise EngineError("Kulan Assistant isn't fully installed. Turn it on in Kulan Settings › Add-ons.")
    if usable_model() is None:
        raise EngineError("Kulan Assistant needs a model first: a free download, chosen once in Kulan Settings › "
                          "Assistant.")
    api_key()  # create it before the service reads it
    if state == "wrong-key":
        # Started with an older key (the key file was replaced): start it again.
        subprocess.run(["systemctl", "--user", "stop", UNIT], capture_output=True, check=False)
    started = subprocess.run(["systemctl", "--user", "start", UNIT], capture_output=True, text=True, check=False)
    under_systemd = started.returncode == 0
    proc = None
    if not under_systemd:
        # Not under systemd (for example a test container): run it directly.
        proc = subprocess.Popen(["/usr/lib/kulan-assistant/kulan-assistant-engine"], start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy():
            return
        if (proc is not None and proc.poll() is not None) or (under_systemd and _service_failed()):
            raise EngineError("The assistant stopped while starting. The model may need more free memory than "
                              "this computer has: try a smaller one in Kulan Settings > Assistant.")
        time.sleep(0.5)
    raise EngineError("The assistant didn't start in time. The model may need more free memory than this "
                      "computer has: try a smaller one in Kulan Settings > Assistant.")


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
        resp = request("/v1/chat/completions", body, timeout=600)
        with resp:
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
    except EngineError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise EngineError(f"The assistant stopped unexpectedly: {exc}") from exc
    if buffer and not in_think:
        yield buffer
    touch_last_use()


def complete(messages: list[dict], max_tokens: int = 1024, temperature: float = 0.3) -> str:
    return THINK.sub("", "".join(chat(messages, max_tokens, temperature))).strip()
