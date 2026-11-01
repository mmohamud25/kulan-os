"""Fingerprint sign-in (2.12): the reader through fprintd, on Profile.

fprintd keeps each person's fingerprints on this computer only (in
/var/lib/fprint, readable by root alone); nothing is sent anywhere. With one
added, the sign-in screen, the lock screen and password prompts (installing
apps, administrator changes, sudo) also take a touch of the reader
(libpam-fprintd, and GDM's own fingerprint sign-in). The password always
works too.

fprintd's D-Bus API (net.reactivated.Fprint): the Manager's
GetDefaultDevice; on the device, ListEnrolledFingers(user), Claim(user),
EnrollStart(finger) with EnrollStatus(result, done) signals, EnrollStop,
DeleteEnrolledFingers2 and Release. Everything here is asynchronous: the
window never waits for the reader. KULAN_FPRINT_BUS=session: a stand-in
fprintd on the session bus (the self-test).
"""
from __future__ import annotations

import os
from typing import Callable

from gi.repository import Gio, GLib

NAME = "net.reactivated.Fprint"
MANAGER_PATH = "/net/reactivated/Fprint/Manager"
MANAGER = "net.reactivated.Fprint.Manager"
DEVICE = "net.reactivated.Fprint.Device"

# fprintd's finger names, as people say them.
FINGERS = {
    "right-index-finger": "Right index finger", "left-index-finger": "Left index finger",
    "right-thumb": "Right thumb", "left-thumb": "Left thumb",
    "right-middle-finger": "Right middle finger", "left-middle-finger": "Left middle finger",
    "right-ring-finger": "Right ring finger", "left-ring-finger": "Left ring finger",
    "right-little-finger": "Right little finger", "left-little-finger": "Left little finger",
}

# What each enrollment step means, in plain words (fprintd's EnrollStatus results).
STEPS = {
    "enroll-stage-passed": "Got it. Lift your finger, then touch the reader again",
    "enroll-retry-scan": "That didn’t read well. Touch the reader again",
    "enroll-swipe-too-short": "Too quick. Touch the reader again, a little longer",
    "enroll-finger-not-centered": "Put your finger in the middle of the reader",
    "enroll-remove-and-retry": "Lift your finger, then touch the reader again",
}
FAILED = {
    "enroll-failed": "The fingerprint wasn’t added. Try again",
    "enroll-data-full": "The reader is full. Remove a fingerprint first",
    "enroll-duplicate": "That finger is already added (perhaps for someone else on this computer)",
    "enroll-disconnected": "The reader stopped answering. Try again",
    "enroll-unknown-error": "The reader had a problem. Try again",
}


def bus_type() -> Gio.BusType:
    return Gio.BusType.SESSION if os.environ.get("KULAN_FPRINT_BUS") == "session" else Gio.BusType.SYSTEM


def plain(error: GLib.Error | Exception) -> str:
    """fprintd's errors in plain words."""
    text = str(getattr(error, "message", error))
    for code, words in (("PermissionDenied", "Permission wasn’t given"),
                        ("NotAuthorized", "Permission wasn’t given"),
                        ("AlreadyInUse", "The reader is busy. Close anything else using it, then try again"),
                        ("NoSuchDevice", "The fingerprint reader isn’t there any more"),
                        ("NoEnrolledPrints", "No fingerprints are added"),
                        ("ServiceUnknown", "The fingerprint service isn’t running"),
                        ("Timeout", "The reader didn’t answer. Try again")):
        if code in text:
            return words
    return "The reader had a problem. Try again"


def finger_names(fingers) -> str:
    return ", ".join(FINGERS.get(f, f.replace("-", " ").capitalize()) for f in fingers)


class Reader:
    """The default fingerprint reader, if this computer has one."""

    def __init__(self) -> None:
        self.bus: Gio.DBusConnection | None = None
        self.path: str | None = None
        self.stages = 0
        self.signal_id = 0
        self.claimed = False

    def call(self, path: str, iface: str, method: str, args: GLib.Variant | None,
             done: Callable[[GLib.Variant | None, GLib.Error | None], None], timeout_ms: int = 30000) -> None:
        def finish(bus, result) -> None:
            try:
                done(bus.call_finish(result), None)
            except GLib.Error as e:
                done(None, e)
        self.bus.call(NAME, path, iface, method, args, None, Gio.DBusCallFlags.NONE, timeout_ms, None, finish)

    def find(self, done: Callable[[bool], None]) -> None:
        """done(True) once the default reader is known; done(False) with no reader or no fprintd."""
        def got_bus(_src, result) -> None:
            try:
                self.bus = Gio.bus_get_finish(result)
            except GLib.Error:
                done(False)
                return
            self.call(MANAGER_PATH, MANAGER, "GetDefaultDevice", None, got_device, 10000)

        def got_device(reply, error) -> None:
            if error is not None or reply is None:
                done(False)  # no reader (NoSuchDevice), or no fprintd
                return
            self.path = reply.unpack()[0]
            self.call(self.path, "org.freedesktop.DBus.Properties", "Get",
                      GLib.Variant("(ss)", (DEVICE, "num-enroll-stages")), got_stages, 10000)

        def got_stages(reply, _error) -> None:
            try:
                self.stages = int(reply.unpack()[0]) if reply is not None else 0
            except (TypeError, ValueError):
                self.stages = 0
            done(True)
        Gio.bus_get(bus_type(), None, got_bus)

    def enrolled(self, user: str, done: Callable[[list[str] | None, str | None], None]) -> None:
        def got(reply, error) -> None:
            if error is not None:
                done([], None) if "NoEnrolledPrints" in error.message else done(None, plain(error))
                return
            done(list(reply.unpack()[0]), None)
        self.call(self.path, DEVICE, "ListEnrolledFingers", GLib.Variant("(s)", (user,)), got)

    def claim(self, done: Callable[[str | None], None]) -> None:
        # The person signed in (""): fprintd asks permission if it needs to.
        def claimed(_reply, error) -> None:
            self.claimed = error is None
            done(plain(error) if error else None)
        self.call(self.path, DEVICE, "Claim", GLib.Variant("(s)", ("",)), claimed, 120000)

    def release(self) -> None:
        if self.claimed and self.bus is not None and self.path:
            self.claimed = False
            self.call(self.path, DEVICE, "Release", None, lambda _r, _e: None)

    def enroll(self, finger: str, status: Callable[[str, bool], None], failed: Callable[[str], None]) -> None:
        """Claims the reader and starts adding a finger: status(result, done) for each
        touch; failed(words) if it can't start. stop() (or a finished one) releases it."""
        def on_signal(_bus, _sender, _path, _iface, signal, params) -> None:
            if signal != "EnrollStatus":
                return
            result, finished = params.unpack()
            if finished:
                self.stop(release=True)
            status(result, finished)

        def claimed(error: str | None) -> None:
            if error:
                failed(error)
                return
            self.signal_id = self.bus.signal_subscribe(NAME, DEVICE, "EnrollStatus", self.path, None,
                                                       Gio.DBusSignalFlags.NONE, on_signal)
            self.call(self.path, DEVICE, "EnrollStart", GLib.Variant("(s)", (finger,)), started)

        def started(_reply, error) -> None:
            if error is not None:
                self.stop(release=True)
                failed(plain(error))
        self.claim(claimed)

    def stop(self, release: bool = True) -> None:
        if self.signal_id:
            self.bus.signal_unsubscribe(self.signal_id)
            self.signal_id = 0
            self.call(self.path, DEVICE, "EnrollStop", None, lambda _r, _e: self.release() if release else None)
        elif release:
            self.release()

    def delete_all(self, done: Callable[[str | None], None]) -> None:
        def claimed(error: str | None) -> None:
            if error:
                done(error)
                return
            self.call(self.path, DEVICE, "DeleteEnrolledFingers2", None, deleted)

        def deleted(_reply, error) -> None:
            self.release()
            done(plain(error) if error else None)
        self.claim(claimed)
