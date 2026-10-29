"""Printers: the computer's printers (CUPS), what they're doing, the default
one, a test page, and adding a printer found on the network or plugged in.

Modern printers (IPP Everywhere, AirPrint) need no driver: they're added with
"lpadmin -m everywhere". Everything runs as the person using the computer;
Debian lets members of the lpadmin group (the first account) manage printers
without a password. Printers that need a maker's driver are added in GNOME
Settings, linked from here."""
from __future__ import annotations

import re
import shutil
from collections.abc import Callable

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, plain_error

TEST_PAGE = "/usr/share/cups/data/testprint"
STATES = {"idle": "Ready", "printing": "Printing", "disabled": "Paused"}


def run_async(argv: list[str], done: Callable[[bool, str, str], None]) -> None:
    """Run a CUPS command without blocking the window; done(ok, out, err)."""
    try:
        proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
    except GLib.Error as exc:
        done(False, "", exc.message)
        return

    def finished(p: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        try:
            _ok, out, err = p.communicate_utf8_finish(result)
        except GLib.Error as exc:
            done(False, "", exc.message)
            return
        done(p.get_successful(), out or "", err or "")

    proc.communicate_utf8_async(None, None, finished)


def parse_printers(lpstat_l: str, default: str, jobs: str) -> list[dict]:
    """Printers from "lpstat -l -p", the default from "lpstat -d" and the
    waiting jobs from "lpstat -o"."""
    printers: list[dict] = []
    first_detail = False
    for line in lpstat_l.splitlines():
        match = re.match(r"^printer (\S+) (?:is )?(idle|now printing|disabled)", line)
        if match:
            state = "printing" if match.group(2) == "now printing" else match.group(2)
            printers.append({"name": match.group(1), "state": state, "description": match.group(1),
                             "reason": ""})
            first_detail = True
            continue
        if not printers:
            continue
        stripped = line.strip()
        # A paused printer's reason is the line right after it ("Paused", or
        # the reason it was stopped, such as "Unplugged or turned off").
        if first_detail and printers[-1]["state"] == "disabled" and stripped and ":" not in stripped \
                and stripped.lower() != "paused":
            printers[-1]["reason"] = stripped
        first_detail = False
        if stripped.startswith("Description:"):
            printers[-1]["description"] = stripped.split(":", 1)[1].strip() or printers[-1]["name"]
    match = re.search(r"system default destination: (\S+)", default)
    default_name = match.group(1) if match else ""
    counts: dict[str, int] = {}
    for line in jobs.splitlines():
        job = line.split(" ", 1)[0]
        name = job.rsplit("-", 1)[0]
        counts[name] = counts.get(name, 0) + 1
    for printer in printers:
        printer["default"] = printer["name"] == default_name
        printer["jobs"] = counts.get(printer["name"], 0)
    return printers


def parse_found(lpinfo: str, known_uris: set[str]) -> list[dict]:
    """Printers "lpinfo -l -v" can see that aren't added yet: network ones
    announced over DNS-SD (driverless) and USB ones."""
    found: list[dict] = []
    current: dict | None = None
    for line in lpinfo.splitlines():
        if line.startswith("Device: uri = "):
            current = {"uri": line.split("=", 1)[1].strip()}
            found.append(current)
        elif current is not None and "=" in line:
            key, _sep, value = line.strip().partition(" = ")
            current[key.strip()] = value.strip()
    usable = []
    for item in found:
        uri = item.get("uri", "")
        if uri in known_uris or not uri.startswith(("dnssd://", "ipp://", "ipps://", "usb://")):
            continue
        name = item.get("info") or item.get("make-and-model") or uri.split("/")[2]
        usable.append({"uri": uri, "name": GLib.uri_unescape_string(name, None) or name,
                       "where": "On your network" if not uri.startswith("usb://") else "Plugged in (USB)"})
    return usable


def queue_name(name: str) -> str:
    """A CUPS queue name from a printer's name: letters, digits, - and _."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return (cleaned or "Printer")[:60]


class PrintersPage(Page):
    title = "Printers"
    icon = "printer-symbolic"
    keywords = ("printer", "printers", "printing", "print", "test page", "default printer", "cups", "airprint",
                "scanner")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.available = shutil.which("lpstat") is not None
        self.printers: list[dict] = []
        self.rows: list[Gtk.Widget] = []

        self.list_group = self.add_group()
        self.empty = Adw.StatusPage(icon_name="printer-symbolic", title="No printers yet",
                                    description="Printers on your network or plugged in by USB can be added here.",
                                    visible=False)
        self.list_group.add(self.empty)

        add_group = self.add_group()
        add_row = Adw.ButtonRow(title="Add a Printer…", start_icon_name="list-add-symbolic")
        add_row.connect("activated", lambda _r: self.show_add())
        add_group.add(add_row)

        more = self.add_group(description="Printers that need their maker’s software, and scanners, are set "
                                          "up in the Settings app.")
        gnome = Adw.ActionRow(title="More printer options", subtitle="In the Settings app", activatable=True)
        gnome.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        gnome.connect("activated", lambda _r: self.open_gnome())
        more.add(gnome)

        if not self.available:
            self.empty.set_title("Printing isn’t installed")
            self.empty.set_description("Install the CUPS printing system to use printers.")
            self.empty.set_visible(True)
            add_row.set_sensitive(False)
            return
        self.refresh_id = GLib.timeout_add_seconds(5, self.refresh_tick)
        self.reload()

    def teardown(self) -> None:
        if getattr(self, "refresh_id", 0):
            GLib.source_remove(self.refresh_id)
            self.refresh_id = 0
        super().teardown()

    def shown(self) -> None:
        self.reload()

    def refresh_tick(self) -> bool:
        if not self.torn_down and self.get_mapped():
            self.reload()
        return GLib.SOURCE_CONTINUE

    # --- the list --------------------------------------------------------------------
    def reload(self) -> None:
        results: dict[str, str] = {}

        def collect(key: str):
            def done(_ok: bool, out: str, _err: str) -> None:
                results[key] = out
                if len(results) == 3 and not self.torn_down:
                    self.printers = parse_printers(results["printers"], results["default"], results["jobs"])
                    self.render()
            return done

        env = ["env", "LC_ALL=C"]
        run_async([*env, "lpstat", "-l", "-p"], collect("printers"))
        run_async([*env, "lpstat", "-d"], collect("default"))
        run_async([*env, "lpstat", "-o"], collect("jobs"))

    def render(self) -> None:
        for row in self.rows:
            self.list_group.remove(row)
        self.rows = []
        self.empty.set_visible(not self.printers)
        for printer in sorted(self.printers, key=lambda p: (not p["default"], p["description"].lower())):
            row = Adw.ExpanderRow(title=GLib.markup_escape_text(printer["description"]),
                                  subtitle=GLib.markup_escape_text(self.status_text(printer)))
            row.add_prefix(Gtk.Image(icon_name="printer-symbolic", pixel_size=24))
            if printer["default"]:
                row.add_suffix(Gtk.Label(label="Default", css_classes=["dim-label", "caption"]))
            name = printer["name"]
            actions = [("Print a Test Page", lambda n=name: self.test_page(n), True)]
            if not printer["default"]:
                actions.append(("Make Default", lambda n=name: self.make_default(n), True))
            if printer["jobs"]:
                actions.append((f"Cancel {printer['jobs']} Waiting", lambda n=name: self.cancel_jobs(n), True))
            if printer["state"] == "disabled":
                actions.append(("Resume", lambda n=name: self.lp_admin(["cupsenable", n], "Resumed"), True))
            else:
                actions.append(("Pause", lambda n=name: self.lp_admin(["cupsdisable", n], "Paused"), True))
            actions.append(("Remove…", lambda p=printer: self.confirm_remove(p), False))
            for label, callback, normal in actions:
                action = Adw.ButtonRow(title=label)
                if not normal:
                    action.add_css_class("destructive-action")
                action.connect("activated", lambda _r, cb=callback: cb())
                row.add_row(action)
            self.list_group.add(row)
            self.rows.append(row)

    @staticmethod
    def status_text(printer: dict) -> str:
        text = STATES.get(printer["state"], printer["state"].capitalize())
        if printer["jobs"]:
            text += f" · {printer['jobs']} waiting" if printer["state"] != "printing" else \
                f" · {printer['jobs']} in the queue"
        if printer.get("reason") and printer["state"] == "disabled":
            text += f" · {printer['reason']}"
        return text

    # --- actions ---------------------------------------------------------------------------
    def lp_admin(self, argv: list[str], success: str) -> None:
        def done(ok: bool, _out: str, err: str) -> None:
            if self.torn_down:
                return
            if ok:
                self.toast(success)
            elif "Forbidden" in err or "not authorized" in err.lower() or "permission" in err.lower():
                self.toast("Only an administrator can change printers")
            else:
                self.toast(f"That didn’t work. {plain_error(err or 'failed')}")
            self.reload()

        run_async(argv, done)

    def test_page(self, name: str) -> None:
        source = TEST_PAGE if GLib.file_test(TEST_PAGE, GLib.FileTest.EXISTS) else None
        if source is None:
            self.toast("The test page isn’t installed")
            return
        self.lp_admin(["lp", "-d", name, "-t", "Test page", source], "Test page sent")

    def make_default(self, name: str) -> None:
        """For everyone (an administrator), else just for this account."""
        def done(ok: bool, _out: str, _err: str) -> None:
            if self.torn_down:
                return
            if ok:
                self.toast("Default printer changed")
                self.reload()
            else:
                self.lp_admin(["lpoptions", "-d", name], "Default printer changed for you")

        run_async(["lpadmin", "-d", name], done)

    def cancel_jobs(self, name: str) -> None:
        self.lp_admin(["cancel", "-a", name], "Waiting jobs cancelled")

    def confirm_remove(self, printer: dict) -> None:
        dialog = Adw.AlertDialog(heading=f"Remove {printer['description']}?",
                                 body="You can add it again later.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", lambda _d, r: r == "remove" and self.lp_admin(
            ["lpadmin", "-x", printer["name"]], "Printer removed"))
        dialog.present(self.window)

    def open_gnome(self) -> None:
        try:
            Gio.Subprocess.new(["gnome-control-center", "printers"], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            self.toast("The Settings app isn’t installed")

    # --- adding --------------------------------------------------------------------------
    def show_add(self) -> None:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(description="Looking for printers on your network and plugged in…")
        spinner = Adw.Spinner(height_request=32, margin_top=12)
        group.add(spinner)
        page.add(group)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        dialog = Adw.Dialog(title="Add a Printer", content_width=520, content_height=460, child=view)
        dialog.present(self.window)
        known = set()

        def got_known(_ok: bool, out: str, _err: str) -> None:
            for line in out.splitlines():
                match = re.match(r"^device for \S+: (\S+)", line)
                if match:
                    known.add(match.group(1))
            run_async(["env", "LC_ALL=C", "lpinfo", "-l", "-v", "--include-schemes", "dnssd,usb",
                       "--timeout", "8"], found)

        def found(ok: bool, out: str, err: str) -> None:
            if self.torn_down:
                return
            group.remove(spinner)
            printers = parse_found(out, known) if ok else []
            if not ok and ("Forbidden" in err or "not authorized" in err.lower()):
                group.set_description("Only an administrator can add printers.")
                return
            if not printers:
                group.set_description("No new printers found. Check the printer is on and connected, "
                                      "then try again.")
                return
            group.set_description("")
            for item in printers:
                row = Adw.ActionRow(title=GLib.markup_escape_text(item["name"]),
                                    subtitle=GLib.markup_escape_text(item["where"]))
                row.add_prefix(Gtk.Image(icon_name="printer-symbolic"))
                button = Gtk.Button(label="Add", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
                button.connect("clicked", lambda b, it=item: self.add_printer(it, b, dialog))
                row.add_suffix(button)
                group.add(row)

        run_async(["env", "LC_ALL=C", "lpstat", "-v"], got_known)

    def add_printer(self, item: dict, button: Gtk.Button, dialog: Adw.Dialog) -> None:
        button.set_sensitive(False)
        button.set_label("Adding…")
        name = queue_name(item["name"])

        def done(ok: bool, _out: str, err: str) -> None:
            if self.torn_down:
                return
            if ok:
                self.toast(f"{item['name']} added")
                dialog.close()
                self.reload()
                return
            button.set_sensitive(True)
            button.set_label("Add")
            if "Forbidden" in err or "not authorized" in err.lower():
                self.toast("Only an administrator can add printers")
            else:
                # Not a driverless printer: it needs its maker's driver.
                self.toast("This printer needs its maker’s software. Add it in the Settings app.")
                self.open_gnome()

        run_async(["lpadmin", "-p", name, "-E", "-v", item["uri"], "-m", "everywhere",
                   "-D", item["name"]], done)
