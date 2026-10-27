"""Scripts the Owner signed (Kulan Fleet 2.10).

An organisation's scripts run as root, so the computer doesn't take the
server's word for them. The Owner has a script signing code that only they
hold (never the server); from it comes an Ed25519 key. The computer keeps
the organisation's public key in /etc/kulan/fleet.json (root only) and runs
a script only if the Owner's key signed its SHA-256:

    kulan-fleet-script v1\\n<sha256 hex>\\n

The key is kept the first time the computer is told it. A new key is taken
only if the old one signed it (the Owner changed their code):

    kulan-fleet-script-key v1\\n<new public key, base64>\\n

Otherwise (the code was lost and set up again) scripts stop here until
someone with the computer's admin password runs `kulan-fleet trust-script-key`.
"""
from __future__ import annotations

import base64
import hashlib
import re

KEY = re.compile(r"^[A-Za-z0-9+/]{43}=$")  # 32 bytes
SIG = re.compile(r"^[A-Za-z0-9+/]{86}==$")  # 64 bytes
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def script_message(sha256: str) -> bytes:
    return f"kulan-fleet-script v1\n{sha256}\n".encode()


def key_message(new_key: str) -> bytes:
    return f"kulan-fleet-script-key v1\n{new_key}\n".encode()


def fingerprint(key: str) -> str:
    """A short way to compare keys by eye: 4 groups of 4."""
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:16].upper()
    return " ".join(digest[i:i + 4] for i in range(0, 16, 4))


def verify(key: str, signature: str, message: bytes) -> bool:
    if not (isinstance(key, str) and KEY.match(key) and isinstance(signature, str) and SIG.match(signature)):
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:  # can't check, so never runs
        return False
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(base64.b64decode(signature), message)
        return True
    except (InvalidSignature, ValueError):
        return False


def clean(policy: dict) -> dict:
    """The key (and the old key's signature of it) as sent with the policy, if well-formed."""
    out = {}
    if isinstance(policy.get("script_key"), str) and KEY.match(policy["script_key"]):
        out["script_key"] = policy["script_key"]
        if isinstance(policy.get("script_key_endorsement"), str) and SIG.match(policy["script_key_endorsement"]):
            out["script_key_endorsement"] = policy["script_key_endorsement"]
    return out


def update(config: dict, sent: dict) -> tuple[bool, str | None]:
    """Keep the organisation's key: (config changed, a problem to report)."""
    new = sent.get("script_key")
    if not new:
        return False, None
    pinned = config.get("script_key")
    if not pinned:
        config["script_key"] = new
        return True, None
    if pinned == new:
        if config.pop("script_key_offered", None):
            return True, None
        return False, None
    if verify(pinned, sent.get("script_key_endorsement", ""), key_message(new)):
        config["script_key"] = new
        config.pop("script_key_offered", None)
        return True, None
    changed = config.get("script_key_offered") != new
    config["script_key_offered"] = new
    return changed, ("the organisation's script signing key changed without the old key's signature; scripts "
                     "won't run here until an administrator of this computer runs kulan-fleet trust-script-key")


def check(config: dict, payload: dict) -> str | None:
    """None if the Owner's key signed this script request; else why not."""
    key = config.get("script_key")
    if not key:
        return "this organisation hasn't set up script signing"
    sha = payload.get("sha256")
    if not (isinstance(sha, str) and SHA256.match(sha)):
        return "not a script request"
    if not verify(key, payload.get("signature", ""), script_message(sha)):
        return "the script isn't signed with the organisation's script signing key"
    return None
