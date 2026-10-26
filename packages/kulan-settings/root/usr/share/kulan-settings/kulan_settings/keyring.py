"""Saved passwords (the keyring) and automatic sign-in.

Your saved passwords (Wi-Fi, browsers, online accounts) are kept in a
keyring that is locked with a password. Normally the password you sign in
with unlocks it. With "Sign in automatically" nothing is typed, so the
first app that needs a saved password asks for the keyring's password.

Two ways to stop that, both offered in Kulan Settings › Users:

  Turn off automatic sign-in (recommended): your sign-in password unlocks
      the "login" keyring again. If your passwords are in another keyring
      (one made when an app first asked, often "Default keyring"), they are
      moved into the login keyring so that one unlock covers everything:
      straight away if the login keyring can be unlocked now, otherwise at
      the next sign-in (/usr/lib/kulan/keyring-merge).
  Keep automatic sign-in and stop asking: the keyring's password is removed,
      so its contents are stored on the disk without their own lock (disk
      encryption still protects them while the computer is off).

Everything goes through the keyring service's own D-Bus interface (GNOME
Keyring); the keyring files are only read to see whether they have a
password. Nothing is deleted: the old keyring is left as it was.
"""
from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Gio, GLib

SECRETS = "org.freedesktop.secrets"
SERVICE_PATH = "/org/freedesktop/secrets"
SERVICE = "org.freedesktop.Secret.Service"
COLLECTION = "org.freedesktop.Secret.Collection"
ITEM = "org.freedesktop.Secret.Item"
# GNOME Keyring's own extra methods (unlock, change and create with a password
# given directly, rather than through its prompt).
INTERNAL = "org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface"
LOGIN_PATH = "/org/freedesktop/secrets/collection/login"
NO_PATH = "/"
# How a keyring file with a password starts; one without is plain text.
ENCRYPTED_MAGIC = b"GnomeKeyring\n\r\0\n"
MARKER_NAME = "keyring-merge"


class KeyringError(Exception):
    """Something to tell the person, in plain words."""


# --- what's on the disk (no service needed) -------------------------------------------

def keyrings_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "keyrings"


def marker() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "kulan" / MARKER_NAME


def default_name(folder: Path | None = None) -> str:
    """The keyring apps save new passwords into ("login" unless changed)."""
    folder = folder or keyrings_dir()
    try:
        name = (folder / "default").read_text().strip()
    except OSError:
        name = ""
    return name or "login"


def has_password(name: str, folder: Path | None = None) -> bool:
    """Whether that keyring's file is locked with a password."""
    folder = folder or keyrings_dir()
    try:
        with open(folder / f"{name}.keyring", "rb") as f:
            return f.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC
    except OSError:
        return False


def asks_after_automatic_sign_in(automatic: bool, folder: Path | None = None) -> bool:
    """True when automatic sign-in is on and a keyring apps use has a
    password, so something will ask to unlock it after signing in."""
    if not automatic:
        return False
    names = {default_name(folder), "login"}
    return any(has_password(n, folder) for n in names)


def separate_default(folder: Path | None = None) -> bool:
    """Whether passwords go into a keyring other than "login" (which the
    sign-in password can't unlock), and it exists."""
    folder = folder or keyrings_dir()
    name = default_name(folder)
    return name != "login" and (folder / f"{name}.keyring").exists()


# --- the keyring service --------------------------------------------------------------

class Keyring:
    """A small client for the Secret Service, with a plain (unencrypted)
    session: secrets only travel over the private session bus."""

    def __init__(self, bus: Gio.DBusConnection | None = None) -> None:
        try:
            self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.session = self.call(SERVICE_PATH, SERVICE, "OpenSession",
                                     GLib.Variant("(sv)", ("plain", GLib.Variant("s", ""))), "(vo)")[1]
        except GLib.Error as e:
            raise KeyringError("Saved passwords can't be changed now. The keyring service isn't running.") from e

    def call(self, path: str, iface: str, method: str, args: GLib.Variant | None, reply: str | None = None):
        out = self.bus.call_sync(SECRETS, path, iface, method, args,
                                 GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 30000, None)
        return out.unpack() if out is not None else None

    def prop(self, path: str, iface: str, name: str):
        return self.call(path, "org.freedesktop.DBus.Properties", "Get", GLib.Variant("(ss)", (iface, name)), "(v)")[0]

    def secret(self, value: str | bytes, content_type: str = "text/plain") -> tuple:
        """A Secret struct (oayays) for a plain session, as a tuple to put in a call."""
        data = value.encode() if isinstance(value, str) else value
        return (self.session, b"", data, content_type or "text/plain")

    # Collections -------------------------------------------------------------------
    def default_path(self) -> str:
        return self.call(SERVICE_PATH, SERVICE, "ReadAlias", GLib.Variant("(s)", ("default",)), "(o)")[0]

    def collections(self) -> list[str]:
        return list(self.prop(SERVICE_PATH, SERVICE, "Collections"))

    def exists(self, path: str) -> bool:
        return path != NO_PATH and path in self.collections()

    def label(self, path: str) -> str:
        return self.prop(path, COLLECTION, "Label")

    def locked(self, path: str) -> bool:
        return bool(self.prop(path, COLLECTION, "Locked"))

    def unlock(self, path: str, password: str) -> bool:
        """Unlock with its password; False if the password is wrong."""
        if not self.locked(path):
            return True
        try:
            self.call(SERVICE_PATH, INTERNAL, "UnlockWithMasterPassword",
                      GLib.Variant("(o(oayays))", (path, self.secret(password))))
        except GLib.Error:
            return False
        return not self.locked(path)

    def lock(self, path: str) -> None:
        self.call(SERVICE_PATH, SERVICE, "Lock", GLib.Variant("(ao)", ([path],)), "(aoo)")

    def change_password(self, path: str, old: str, new: str) -> None:
        """New password; an empty one stores the keyring without a lock."""
        try:
            self.call(SERVICE_PATH, INTERNAL, "ChangeWithMasterPassword",
                      GLib.Variant("(o(oayays)(oayays))", (path, self.secret(old), self.secret(new))))
        except GLib.Error as e:
            raise KeyringError("That password didn't unlock your saved passwords.") from e

    def create(self, label: str, password: str) -> str:
        """A new keyring (used by the tests)."""
        props = {"org.freedesktop.Secret.Collection.Label": GLib.Variant("s", label)}
        return self.call(SERVICE_PATH, INTERNAL, "CreateWithMasterPassword",
                         GLib.Variant("(a{sv}(oayays))", (props, self.secret(password))), "(o)")[0]

    def set_default(self, path: str) -> None:
        self.call(SERVICE_PATH, SERVICE, "SetAlias", GLib.Variant("(so)", ("default", path)))

    # Items -------------------------------------------------------------------------
    def items(self, path: str) -> list[str]:
        return list(self.prop(path, COLLECTION, "Items"))

    def read_item(self, item: str) -> tuple[str, dict[str, str], bytes, str]:
        label = self.prop(item, ITEM, "Label")
        attributes = dict(self.prop(item, ITEM, "Attributes"))
        _session, _params, value, content_type = self.call(item, ITEM, "GetSecret",
                                                           GLib.Variant("(o)", (self.session,)), "((oayays))")[0]
        return label, attributes, bytes(value), content_type

    def add_item(self, path: str, label: str, attributes: dict[str, str], value: bytes, content_type: str) -> str:
        props = {"org.freedesktop.Secret.Item.Label": GLib.Variant("s", label),
                 "org.freedesktop.Secret.Item.Attributes": GLib.Variant("a{ss}", attributes)}
        secret = self.secret(value, content_type)
        item, prompt = self.call(path, COLLECTION, "CreateItem",
                                 GLib.Variant("(a{sv}(oayays)b)", (props, secret, True)), "(oo)")
        if item == NO_PATH:
            raise KeyringError(f"Couldn't copy “{label}”.")
        return item

    def move_all(self, source: str, target: str) -> int:
        """Copy every saved password from source into target (replacing the
        same entry if it's already there), check each arrived, then make
        target the default. Source is left as it was. Returns how many."""
        copied = 0
        for item in self.items(source):
            label, attributes, value, content_type = self.read_item(item)
            new = self.add_item(target, label, attributes, value, content_type)
            if self.read_item(new)[2] != value:
                raise KeyringError(f"“{label}” didn't copy correctly. Nothing else was changed.")
            copied += 1
        self.set_default(target)
        return copied


# --- the two fixes ----------------------------------------------------------------------

def stop_asking(password: str, keyring: Keyring | None = None, folder: Path | None = None) -> list[str]:
    """Keep automatic sign-in: remove the password from the keyrings apps use
    (the default one and "login"). Returns their names as shown to people."""
    keyring = keyring or Keyring()
    folder = folder or keyrings_dir()
    paths = []
    default = keyring.default_path()
    for path in (default, LOGIN_PATH):
        if keyring.exists(path) and path not in paths:
            paths.append(path)
    if not paths:
        raise KeyringError("There's no keyring to change yet.")
    changed, wrong = [], []
    for path in paths:
        name = keyring.label(path)
        if not keyring.unlock(path, password):
            wrong.append(name)
            continue
        keyring.change_password(path, password, "")
        changed.append(name)
    if wrong and not changed:
        raise KeyringError("That password didn't unlock your saved passwords. Try your sign-in password, or "
                           "the one you chose when an app first asked.")
    if wrong:
        raise KeyringError(f"Done for {', '.join(changed)}, but that password didn't unlock "
                           f"{', '.join(wrong)}. Try its own password.")
    return changed


def merge_into_login(keyring: Keyring | None = None, password: str | None = None,
                     prompt: bool = True) -> int | None:
    """Move passwords from a separate default keyring into "login", so the
    sign-in password unlocks them all. None when it can't be done yet (no
    login keyring, or it's locked and no password was given): it is then left
    for the next sign-in. With prompt, the keyring service asks for the other
    keyring's password if it's locked. Returns how many were moved (0 if
    nothing to do)."""
    keyring = keyring or Keyring()
    default = keyring.default_path()
    if default in (NO_PATH, LOGIN_PATH) or not keyring.exists(default):
        return 0
    if not keyring.exists(LOGIN_PATH):
        return None
    if keyring.locked(LOGIN_PATH) and not (password and keyring.unlock(LOGIN_PATH, password)):
        return None
    if keyring.locked(default) and not (password and keyring.unlock(default, password)):
        # Its own password is different: the keyring service asks for it once.
        if not prompt:
            return None
        _unlocked, asking = keyring.call(SERVICE_PATH, SERVICE, "Unlock", GLib.Variant("(ao)", ([default],)), "(aoo)")
        if asking != NO_PATH and not run_prompt(keyring, asking):
            return None
    return keyring.move_all(default, LOGIN_PATH)


def run_prompt(keyring: Keyring, prompt: str, timeout_s: int = 300) -> bool:
    """Show the keyring service's own password prompt; True if unlocked."""
    loop = GLib.MainLoop()
    result = {"ok": False}

    def completed(_c, _s, _p, _i, _sig, params) -> None:
        dismissed, _value = params.unpack()
        result["ok"] = not dismissed
        loop.quit()
    sub = keyring.bus.signal_subscribe(SECRETS, "org.freedesktop.Secret.Prompt", "Completed", prompt, None,
                                       Gio.DBusSignalFlags.NONE, completed)
    GLib.timeout_add_seconds(timeout_s, loop.quit)
    keyring.call(prompt, "org.freedesktop.Secret.Prompt", "Prompt", GLib.Variant("(s)", ("",)))
    loop.run()
    keyring.bus.signal_unsubscribe(sub)
    return result["ok"]


def schedule_merge() -> None:
    path = marker()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def self_test() -> None:
    """Against a real GNOME Keyring on a private session bus (CI starts one)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "default").write_text("Default_Keyring")
        (folder / "Default_Keyring.keyring").write_bytes(ENCRYPTED_MAGIC + b"rest")
        (folder / "login.keyring").write_text("[keyring]\ndisplay-name=Login\n")
        assert default_name(folder) == "Default_Keyring" and separate_default(folder)
        assert has_password("Default_Keyring", folder) and not has_password("login", folder)
        assert asks_after_automatic_sign_in(True, folder) and not asks_after_automatic_sign_in(False, folder)
        (folder / "Default_Keyring.keyring").write_text("[keyring]\n")
        assert not asks_after_automatic_sign_in(True, folder)
        (folder / "default").unlink()
        assert default_name(folder) == "login" and not separate_default(folder)

    k = Keyring()
    login_pw, other_pw = "sign-in-pw", "other-pw"
    assert k.exists(LOGIN_PATH), "start gnome-keyring-daemon --unlock first"
    other = k.create("Default keyring", other_pw)
    k.set_default(other)
    saved = {"wifi": b"hunter2", "browser": "pässwörd".encode()}
    for name, value in saved.items():
        k.add_item(other, f"{name} password", {"app": name}, value, "text/plain")
    k.lock(other)
    k.lock(LOGIN_PATH)
    # Merge waits while login is locked and no password is given…
    assert merge_into_login(k) is None and k.default_path() == other
    k.unlock(LOGIN_PATH, login_pw)
    assert merge_into_login(k, prompt=False) is None, "the other keyring is locked: no prompt from Settings"
    k.lock(LOGIN_PATH)
    # …and with the sign-in password (and the other keyring's own), moves everything.
    assert not k.unlock(other, "wrong")
    assert k.unlock(other, other_pw)
    assert merge_into_login(k, login_pw) == 2
    assert k.default_path() == LOGIN_PATH
    got = {k.read_item(i)[1].get("app"): k.read_item(i)[2] for i in k.items(LOGIN_PATH)}
    assert all(got.get(n) == v for n, v in saved.items()), got
    assert len(k.items(other)) == 2, "the old keyring is left as it was"
    assert merge_into_login(k) == 0, "nothing more to do"
    # Merging again doesn't make copies.
    k.set_default(other)
    assert merge_into_login(k, login_pw) == 2 and len(k.items(LOGIN_PATH)) == len(got)
    # Stop asking: a wrong password changes nothing; the right one removes the lock.
    k.lock(LOGIN_PATH)
    try:
        stop_asking("wrong", k)
        raise AssertionError("a wrong password was accepted")
    except KeyringError as e:
        assert "didn't unlock" in str(e)
    assert k.locked(LOGIN_PATH)
    assert stop_asking(login_pw, k) == ["Login"]
    k.lock(LOGIN_PATH)
    assert not k.locked(LOGIN_PATH) or k.unlock(LOGIN_PATH, ""), "no password needed any more"
    print("keyring self-test passed")


if __name__ == "__main__":
    self_test()
