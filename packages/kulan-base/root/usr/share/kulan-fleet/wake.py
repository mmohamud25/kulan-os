"""Report now when the organisation asks (Kulan Fleet 2.9a).

Each computer has a broadcast topic of its own on Kulan Fleet's server
(Supabase Realtime), given in every report's reply ("wake": {topic, url,
key}) and kept in /var/lib/kulan/fleet-wake.json (root only). The listener
(kulan-fleet listen, the kulan-fleet-listen service) keeps one WebSocket open
to it; when an admin asks this computer something, the server sends "wake"
there and the computer reports at once instead of at its next hourly report.
A wake carries nothing and makes the computer do nothing but report: what to
do still comes only in the report's reply, from the server it joined.

The WebSocket client is here (the standard library only, the little of
RFC 6455 that's needed: text frames, ping and pong, close), with Phoenix
channels on top as Supabase Realtime speaks them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import select
import socket
import ssl
import struct
import time
import urllib.parse

TOPIC = re.compile(r"^wake-[0-9a-f]{48}$")
KEY = re.compile(r"^[A-Za-z0-9._-]{20,1024}$")
HEARTBEAT_S = 25
MAX_FRAME = 1 << 20
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Closed(Exception):
    """The connection ended (or was never right)."""


def clean_info(raw, server: str) -> dict | None:
    """The reply's wake info, if well-formed and on the same host as the
    server this computer joined (wss://, or ws:// only for a local test server)."""
    if not isinstance(raw, dict):
        return None
    topic, url, key = raw.get("topic"), raw.get("url"), raw.get("key")
    if not (isinstance(topic, str) and TOPIC.match(topic) and isinstance(url, str) and isinstance(key, str)
            and KEY.match(key)):
        return None
    parts, home = urllib.parse.urlsplit(url), urllib.parse.urlsplit(server)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme not in ("wss", "ws") or (parts.scheme == "ws" and not local) or not parts.hostname \
            or parts.hostname != home.hostname or parts.query or parts.fragment:
        return None
    return {"topic": topic, "url": url, "key": key}


class WebSocket:
    def __init__(self, sock: socket.socket, leftover: bytes = b""):
        self.sock = sock
        self.buf = leftover

    def _read(self, n: int, deadline: float) -> bytes:
        while len(self.buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            if not (isinstance(self.sock, ssl.SSLSocket) and self.sock.pending()):
                ready, _, _ = select.select([self.sock], [], [], left)
                if not ready:
                    raise TimeoutError
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Closed("the server closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode: int, data: bytes) -> None:
        head = bytes([0x80 | opcode])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode())

    def recv_text(self, timeout: float) -> str | None:
        """The next text message, or None if none came in time."""
        wait_until = time.monotonic() + timeout
        message, kind = b"", None
        while True:
            try:
                # A new message waits until wait_until; the rest of one that started, 10 seconds more.
                b1, b2 = self._read(2, wait_until if not message else time.monotonic() + 10)
            except TimeoutError:
                if message:
                    raise Closed("a message stopped halfway")
                return None
            opcode, n = b1 & 0x0F, b2 & 0x7F
            frame_end = time.monotonic() + 10
            try:
                if n == 126:
                    n = struct.unpack("!H", self._read(2, frame_end))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._read(8, frame_end))[0]
                if n > MAX_FRAME or b2 & 0x80:  # too big, or masked (a server never masks)
                    raise Closed("not a frame this listener takes")
                data = self._read(n, frame_end)
            except TimeoutError:
                raise Closed("a frame stopped halfway") from None
            if opcode == 0x9:
                self._send(0xA, data)
            elif opcode == 0x8:
                raise Closed("the server closed the connection")
            elif opcode in (0x1, 0x2, 0x0):
                if opcode != 0x0:
                    kind = opcode
                message += data
                if len(message) > MAX_FRAME:
                    raise Closed("message too big")
                if b1 & 0x80:
                    if kind == 0x1:
                        return message.decode("utf-8", "replace")
                    message, kind = b"", None

    def close(self) -> None:
        try:
            self._send(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def connect(url: str, timeout: float = 20, context: ssl.SSLContext | None = None) -> WebSocket:
    parts = urllib.parse.urlsplit(url)
    secure = parts.scheme == "wss"
    port = parts.port or (443 if secure else 80)
    sock = socket.create_connection((parts.hostname, port), timeout=timeout)
    try:
        if secure:
            sock = (context or ssl.create_default_context()).wrap_socket(sock, server_hostname=parts.hostname)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        host = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: kulan-fleet/1\r\n\r\n").encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(4096)
            if not chunk or len(raw) > 16384:
                raise Closed("the server didn't answer as a WebSocket server")
            raw += chunk
        head, _, leftover = raw.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if not re.match(r"^HTTP/1\.1 101\b", lines[0]):
            raise Closed(f"the server said {lines[0][9:60]}")
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != want:
            raise Closed("the server's handshake wasn't right")
        sock.settimeout(None)
        return WebSocket(sock, leftover)
    except BaseException:
        sock.close()
        raise


def _is_jwt(key: str) -> bool:
    return key.startswith("eyJ") and key.count(".") == 2


def listen(info: dict, on_wake, still_current=lambda: True, heartbeat_s: float = HEARTBEAT_S,
           context: ssl.SSLContext | None = None) -> str:
    """Joins the computer's topic and calls on_wake() for each wake, until
    the connection drops (raises Closed or OSError) or still_current() says
    the info changed (returns "changed")."""
    query = urllib.parse.urlencode({"apikey": info["key"], "vsn": "1.0.0"})
    ws = connect(f"{info['url']}?{query}", context=context)
    channel = f"realtime:{info['topic']}"
    ref = 1
    try:
        payload = {"config": {"broadcast": {"ack": False, "self": False}, "presence": {"key": ""},
                              "postgres_changes": [], "private": False}}
        if _is_jwt(info["key"]):
            payload["access_token"] = info["key"]
        ws.send_text(json.dumps({"topic": channel, "event": "phx_join", "payload": payload, "ref": str(ref),
                                 "join_ref": str(ref)}))
        deadline = time.monotonic() + 15
        while True:
            text = ws.recv_text(max(0.1, deadline - time.monotonic()))
            if text is None:
                raise Closed("the server didn't let this computer listen")
            msg = _message(text)
            if msg.get("event") == "phx_reply" and msg.get("ref") == str(ref):
                status = (msg.get("payload") or {}).get("status")
                if status != "ok":
                    raise Closed(f"the server didn't let this computer listen ({status})")
                break
        next_beat = time.monotonic() + heartbeat_s
        last_heard = time.monotonic()
        while True:
            text = ws.recv_text(max(0.1, next_beat - time.monotonic()))
            now = time.monotonic()
            if text is not None:
                last_heard = now
                msg = _message(text)
                event, topic = msg.get("event"), msg.get("topic")
                if topic == channel and event == "broadcast" and (msg.get("payload") or {}).get("event") == "wake":
                    on_wake()
                elif topic == channel and event in ("phx_error", "phx_close"):
                    raise Closed("the server ended this computer's listening")
            if now >= next_beat:
                if now - last_heard > 3 * heartbeat_s:
                    raise Closed("the server stopped answering")
                if not still_current():
                    return "changed"
                ref += 1
                ws.send_text(json.dumps({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": str(ref)}))
                next_beat = now + heartbeat_s
    finally:
        ws.close()


def _message(text: str) -> dict:
    try:
        msg = json.loads(text)
    except ValueError:
        return {}
    return msg if isinstance(msg, dict) else {}
