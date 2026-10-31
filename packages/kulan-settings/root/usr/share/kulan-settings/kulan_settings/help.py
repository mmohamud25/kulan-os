"""Get help: on a computer in an organisation (Kulan Fleet 2.9c), the person
writes what's wrong and may add a screenshot they choose and see first; it
reaches the organisation's admins, who can answer it or ask to see the
screen (which only happens if the person allows it). Their requests, and any
answer, are listed here. On other computers: where to find help with Kulan OS.

Sending goes through pkexec and /usr/lib/kulan/fleet-get-help, which sends
it as this person with the computer's own key (no password is asked: only
the person at the computer can do it)."""
from __future__ import annotations

import base64
import os
import pwd
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk

from .about import kulan_conf
from .common import Page, plain_error

PUBLIC = Path("/var/lib/kulan/fleet-public.json")
MINE_DIR = Path("/var/lib/kulan/fleet-help")
HELPER = "/usr/lib/kulan/fleet-get-help"
TEXT_MAX = 2000
SCREENSHOT_MAX = 340_000  # bytes once made smaller; the server takes up to 360 KB
SIZES = ((1600, 82), (1600, 68), (1280, 68), (1024, 62), (800, 55))
SUPPORT_EMAIL = "support@kulangroup.com"


def shrink(pixbuf: GdkPixbuf.Pixbuf) -> bytes | None:
    """A screenshot small enough to send: a JPEG of at most 340 KB, made
    smaller step by step (None if even the smallest is too big)."""
    width, height = pixbuf.get_width(), pixbuf.get_height()
    if pixbuf.get_has_alpha():  # JPEG has no transparency: on white
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
        flat.fill(0xFFFFFFFF)
        pixbuf.composite(flat, 0, 0, width, height, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
        pixbuf = flat
    for longest, quality in SIZES:
        scale = min(1.0, longest / max(width, height, 1))
        image = pixbuf if scale >= 1 else pixbuf.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)),
                                                              GdkPixbuf.InterpType.BILINEAR)
        ok, data = image.save_to_bufferv("jpeg", ["quality"], [str(quality)])
        if ok and len(data) <= SCREENSHOT_MAX:
            return bytes(data)
    return None


def when(stamp: str | None) -> str:
    """"2026-10-26T14:02:00Z" as "26 Oct, 14:02" (local time)."""
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone()
    except (TypeError, ValueError):
        return ""
    today = datetime.now(timezone.utc).astimezone().date()
    return at.strftime("Today, %H:%M") if at.date() == today else at.strftime("%-d %b, %H:%M")


def me() -> str:
    """This account's name (from the system, never from the environment)."""
    return pwd.getpwuid(os.getuid()).pw_name


def my_requests(user: str | None = None) -> list[dict]:
    try:
        data = json.loads((MINE_DIR / f"{user or me()}.json").read_text())
    except (OSError, ValueError):
        return []
    requests = data.get("requests") if isinstance(data, dict) else None
    return [r for r in requests if isinstance(r, dict)] if isinstance(requests, list) else []


class HelpPage(Page):
    title = "Get help"
    icon = "help-browser-symbolic|system-help-symbolic"
    keywords = ("help", "support", "problem", "it", "admin", "ask", "screenshot", "broken", "issue", "contact")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.screenshot: bytes | None = None
        self.sending = False
        self.groups: list[Adw.PreferencesGroup] = []
        self.mine_group: Adw.PreferencesGroup | None = None
        self.mine_stamp: float | None = None
        self.rendered_for: str | None = ""
        self.render()
        # Joining or leaving changes the page (an hourly report doesn't: what's typed stays).
        self.monitor_file(str(PUBLIC), lambda: self.organisation() != self.rendered_for and not self.sending
                          and self.render())
        # Answers arrive with a report: looked for every 20 seconds while the page is open.
        GLib.timeout_add_seconds(20, self.poll_mine)

    def shown(self) -> None:
        self.render_mine()

    def poll_mine(self) -> bool:
        if self.torn_down:
            return False
        if self.rendered_for and self.mine_changed():
            self.render_mine()
        return True

    def mine_changed(self) -> bool:
        try:
            stamp = (MINE_DIR / f"{me()}.json").stat().st_mtime
        except OSError:
            stamp = None
        return stamp != self.mine_stamp

    @staticmethod
    def organisation() -> str | None:
        try:
            data = json.loads(PUBLIC.read_text())
        except (OSError, ValueError):
            return None
        return str(data.get("organisation") or "your organisation") if data.get("joined") else None

    def clear(self) -> None:
        for group in self.groups:
            self.page.remove(group)
        self.groups = []

    def group(self, title: str = "", description: str = "") -> Adw.PreferencesGroup:
        g = self.add_group(title, description)
        self.groups.append(g)
        return g

    def render(self) -> None:
        self.clear()
        self.mine_group = None
        org = self.organisation()
        self.rendered_for = org
        if org and Path(HELPER).exists():
            self.render_form(org)
            self.render_mine()
        else:
            self.rendered_for = None
            self.render_kulan()

    # --- not in an organisation -----------------------------------------------------------------
    def render_kulan(self) -> None:
        conf_url = kulan_conf().get("KULAN_SUPPORT_URL") or "https://os.kulangroup.com/help/"
        g = self.group("Help with Kulan OS")
        for title, subtitle, icon, target in (
                ("Kulan OS help", "Answers to common questions, and how to install and update", "help-browser-symbolic",
                 conf_url),
                ("Email Kulan support", SUPPORT_EMAIL, "mail-unread-symbolic", f"mailto:{SUPPORT_EMAIL}")):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            row.connect("activated", lambda _r, t=target: self.open(t))
            g.add(row)
        ask = self.group()
        row = Adw.ActionRow(title="Ask Kulan", subtitle="It can explain settings and fix common problems",
                            activatable=True)
        row.add_prefix(Gtk.Image(icon_name="kulan-assistant-symbolic"))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda _r: self.ask_kulan())
        ask.add(row)
        self.group(description="On a work or school computer, your IT team can help you from here once the "
                               "computer has joined your organisation (About Kulan OS › Join an organisation).")

    def ask_kulan(self) -> None:
        """Ask Kulan itself (not its settings); says so if Kulan Assistant isn't installed."""
        try:
            app = Gio.DesktopAppInfo.new("org.kulan.Assistant.desktop")  # TypeError when it isn't installed
            if app is not None and app.launch([], None):
                return
        except (GLib.Error, TypeError):
            pass
        self.toast("Ask Kulan needs Kulan Assistant. Turn it on in Add-ons")

    def open(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            self.toast(f"Couldn’t open it. {plain_error(exc)}")

    # --- in an organisation ------------------------------------------------------------------------
    def render_form(self, org: str) -> None:
        g = self.group(f"Ask {org} for help",
                       "Your organisation’s admins get what you write, and the screenshot if you add one. "
                       "If they ask to see your screen, you’re asked first.")
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10, left_margin=12,
                                 right_margin=12, accepts_tab=False)
        self.text.update_property([Gtk.AccessibleProperty.LABEL], ["What’s wrong?"])
        self.text.get_buffer().connect("changed", lambda _b: self.update_send())
        scroller = Gtk.ScrolledWindow(child=self.text, min_content_height=120, max_content_height=260,
                                      propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame = Gtk.Frame(child=scroller, css_classes=["view"])
        label = Gtk.Label(label="What’s wrong? What were you doing when it happened?", xalign=0, wrap=True,
                          css_classes=["heading"], mnemonic_widget=self.text, margin_bottom=6)
        self.count = Gtk.Label(xalign=1, css_classes=["dim-label", "caption"], margin_top=4)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(label)
        box.append(frame)
        box.append(self.count)
        g.add(box)

        shots = self.group()
        self.shot_row = Adw.ActionRow(title="Screenshot", subtitle="Optional. You choose what’s in it and see it "
                                                                   "before it’s sent", subtitle_lines=0)
        self.shot_row.add_prefix(Gtk.Image(icon_name="camera-photo-symbolic"))
        self.shot_button = Gtk.Button(label="Take a screenshot", valign=Gtk.Align.CENTER)
        self.shot_button.connect("clicked", lambda _b: self.take_screenshot())
        self.shot_row.add_suffix(self.shot_button)
        shots.add(self.shot_row)
        self.preview = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, height_request=200, can_shrink=True,
                                   visible=False, margin_top=6, margin_bottom=6,
                                   alternative_text="The screenshot that will be sent")
        shots.add(self.preview)

        actions = self.group()
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        self.spinner = Adw.Spinner(visible=False) if hasattr(Adw, "Spinner") else Gtk.Spinner(visible=False, spinning=True)
        self.send_button = Gtk.Button(label="Send", css_classes=["suggested-action", "pill"])
        self.send_button.connect("clicked", lambda _b: self.send(org))
        bar.append(self.spinner)
        bar.append(self.send_button)
        actions.add(bar)
        self.show_screenshot(self.screenshot)
        self.update_send()

    def body(self) -> str:
        buffer = self.text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def update_send(self) -> None:
        text = self.body()
        over = len(text) - TEXT_MAX
        self.count.set_label(f"{over} characters too long" if over > 0 else
                             f"{TEXT_MAX - len(text)} characters left" if len(text) > TEXT_MAX - 200 else "")
        self.send_button.set_sensitive(bool(text.strip()) and over <= 0 and not self.sending)

    def show_screenshot(self, data: bytes | None) -> None:
        self.screenshot = data
        if data is None:
            self.preview.set_paintable(None)
            self.preview.set_visible(False)
            self.shot_button.set_label("Take a screenshot")
            self.shot_row.set_subtitle("Optional. You choose what’s in it and see it before it’s sent")
            return
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            self.show_screenshot(None)
            return
        self.preview.set_paintable(texture)
        self.preview.set_visible(True)
        self.shot_button.set_label("Remove")
        self.shot_row.set_subtitle("This is what will be sent")

    def take_screenshot(self) -> None:
        if self.screenshot is not None:
            self.show_screenshot(None)
            return
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        token = f"kulan_help_{int(time.monotonic() * 1000)}"
        sender = bus.get_unique_name().lstrip(":").replace(".", "_")
        handle = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        subscription = []

        def answered(_conn, _sender, _path, _iface, _signal, params) -> None:
            bus.signal_unsubscribe(subscription[0])
            if self.torn_down:
                return
            response, results = params.unpack()
            self.shot_button.set_sensitive(True)
            if response != 0:
                return  # they cancelled
            uri = results.get("uri", "")
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(Gio.File.new_for_uri(uri).get_path() or "")
                data = shrink(pixbuf)
            except (GLib.Error, TypeError):
                data = None
            if data is None:
                self.toast("That screenshot couldn’t be used. Try a smaller part of the screen")
                return
            self.show_screenshot(data)

        subscription.append(bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
                                                 "Response", handle, None, Gio.DBusSignalFlags.NO_MATCH_RULE, answered))

        def called(conn: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
            try:
                conn.call_finish(result)
            except GLib.Error:
                bus.signal_unsubscribe(subscription[0])
                if not self.torn_down:
                    self.shot_button.set_sensitive(True)
                    self.toast("Screenshots aren’t available here")

        self.shot_button.set_sensitive(False)
        bus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.Screenshot",
                 "Screenshot", GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                                             "interactive": GLib.Variant("b", True)})),
                 GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, -1, None, called)

    def send(self, org: str) -> None:
        text = self.body().strip()
        if not text or self.sending:
            return
        payload = json.dumps({"text": text, "screenshot": base64.b64encode(self.screenshot).decode()
                              if self.screenshot else None})
        try:
            proc = Gio.Subprocess.new(["pkexec", HELPER], Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE
                                      | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as exc:
            self.toast(f"It wasn’t sent. {plain_error(exc)}")
            return
        self.sending = True
        self.spinner.set_visible(True)
        self.update_send()

        def finished(p: Gio.Subprocess, result: Gio.AsyncResult) -> None:
            self.sending = False
            try:
                _ok, _out, err = p.communicate_utf8_finish(result)
            except GLib.Error as exc:
                err = exc.message
            if self.torn_down:
                return
            self.spinner.set_visible(False)
            status = p.get_exit_status() if p.get_if_exited() else -1
            if status == 0:
                self.text.get_buffer().set_text("")
                self.show_screenshot(None)
                self.toast(f"Sent to {org}’s admins")
                self.render_mine()
            elif status in (126, 127):
                self.toast("It wasn’t sent. Only the person at the computer can ask for help")
            else:
                self.toast((err or "").strip().splitlines()[-1] if (err or "").strip() else
                           "It wasn’t sent. Try again in a minute")
            self.update_send()

        proc.communicate_utf8_async(payload, None, finished)

    def render_mine(self) -> None:
        if not self.rendered_for:
            return
        if self.mine_group is not None and self.mine_group in self.groups:
            self.page.remove(self.mine_group)
            self.groups.remove(self.mine_group)
        self.mine_group = None
        try:
            self.mine_stamp = (MINE_DIR / f"{me()}.json").stat().st_mtime
        except OSError:
            self.mine_stamp = None
        requests = my_requests()
        if not requests:
            return
        g = self.group("Your requests")
        self.mine_group = g
        for r in requests[:20]:
            done = r.get("state") == "done"
            first = (str(r.get("text") or "").splitlines() or [""])[0]
            sent = when(r.get("created_at"))
            if done and r.get("reply"):
                status = f"Answered: {r['reply']}"
            elif done:
                status = "Done"
            else:
                status = "Waiting for an answer"
            row = Adw.ActionRow(title=GLib.markup_escape_text(first[:120] or "Help request"),
                                subtitle=GLib.markup_escape_text(" · ".join(x for x in (sent, status) if x)),
                                subtitle_lines=0, title_lines=2)
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if done else "mail-send-symbolic",
                                     css_classes=["success"] if done else ["dim-label"]))
            g.add(row)


def self_test(window) -> None:
    """kulan-settings --self-test: a big screenshot made small enough, dates,
    each person's own requests, and the page for a computer in an organisation."""
    import tempfile
    big = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 3840, 2160)
    big.fill(0x336699FF)
    data = shrink(big)
    assert data is not None and data[:3] == b"\xff\xd8\xff" and len(data) <= SCREENSHOT_MAX, "a 4K screenshot wasn't made small"
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    assert max(loader.get_pixbuf().get_width(), loader.get_pixbuf().get_height()) <= 1600, "the screenshot is still too large"
    assert when("not a date") == "" and when(None) == "", "a missing date isn't hidden"
    assert when(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")).startswith("Today, "), "today isn't 'Today'"
    global PUBLIC, MINE_DIR, HELPER
    saved = PUBLIC, MINE_DIR, HELPER
    with tempfile.TemporaryDirectory() as tmp:
        PUBLIC, MINE_DIR, HELPER = Path(tmp) / "public.json", Path(tmp), "/bin/true"
        PUBLIC.write_text(json.dumps({"joined": True, "organisation": "Acme"}))
        (MINE_DIR / f"{me()}.json").write_text(json.dumps({"requests": [
            {"id": "1", "text": "Printer <b>broken</b>\nsecond line", "state": "done", "reply": "Fixed it"},
            {"id": "2", "text": "Slow", "state": "open"}, "not a request"]}))
        try:
            assert [r["id"] for r in my_requests()] == ["1", "2"], "the person's requests weren't read"
            page = HelpPage(window)
            assert page.rendered_for == "Acme" and page.mine_group is not None, "the form or the requests are missing"
            assert not page.send_button.get_sensitive(), "Send works with nothing written"
            page.text.get_buffer().set_text("x" * (TEXT_MAX + 1))
            assert not page.send_button.get_sensitive() and "too long" in page.count.get_label(), "too long isn't caught"
            page.text.get_buffer().set_text("The printer won't print")
            assert page.send_button.get_sensitive(), "Send doesn't work with something written"
            page.show_screenshot(data)
            assert page.preview.get_visible() and page.shot_button.get_label() == "Remove", "the screenshot isn't shown first"
            page.show_screenshot(None)
            PUBLIC.write_text(json.dumps({"joined": False}))
            page.render()
            assert page.rendered_for is None and page.mine_group is None, "a computer that left still offers to ask its organisation"
            page.teardown()
        finally:
            PUBLIC, MINE_DIR, HELPER = saved
