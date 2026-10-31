"""Joining an organisation: Kulan's own Kulan Fleet server, and making sense
of what someone types or pastes into "Join an organisation".

One place for the server address: kulan-fleet (join with no server given)
and Kulan Settings › About both use KULAN_SERVER, so people only type the
join code their IT team gave them. Kulan Fleet for IT admins
(kulan-fleet-admin) shows the same address.

What people may enter:
  ABCD-EFGH-JK2M                  a join code, for Kulan's server
  https://fleet.example#ABCD-EFGH a server and a code together (a link)
  https://fleet.example?code=…    the same, as a query
and, under "Use a different server", another organisation's own server.
"""
from __future__ import annotations

import re
import urllib.parse

KULAN_SERVER = "https://xegnwqxpegznsufulclx.supabase.co/functions/v1/fleet"
# Codes Kulan's server makes are three groups of four capitals and digits;
# other servers (and the test server) may make others, so only the shape is checked.
CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")


class JoinError(ValueError):
    """What someone entered can't be used; str() is for people."""


def same_server(a: str, b: str) -> bool:
    return a.strip().rstrip("/").lower() == b.strip().rstrip("/").lower()


def parse(entered: str, other_server: str = "") -> tuple[str, str]:
    """(server, code) from the code field (a code or a link) and the optional
    other server. Raises JoinError with a plain reason."""
    text = "".join(entered.split())  # codes are often pasted with spaces or line breaks
    server = other_server.strip()
    code = text
    if "#" in text:
        left, _sep, code = text.rpartition("#")
        server = left or server
    elif text.lower().startswith(("https://", "http://")):
        parts = urllib.parse.urlsplit(text)
        found = urllib.parse.parse_qs(parts.query).get("code")
        if not found:
            raise JoinError("That’s a server address. Enter the join code your IT team gave you")
        code = found[0].strip()
        server = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    server = (server or KULAN_SERVER).rstrip("/")
    if not server.lower().startswith(("https://", "http://")):
        server = "https://" + server
    if not code:
        raise JoinError("Enter the join code your IT team gave you")
    if not CODE.fullmatch(code):
        raise JoinError("That isn’t a join code. Check it with your IT team")
    if same_server(server, KULAN_SERVER):
        server, code = KULAN_SERVER, code.upper()
    return server, code
