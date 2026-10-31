"""Online accounts: the Google and Microsoft 365 accounts signed in on this
computer (mail in Geary, calendar, contacts, files), and adding or removing
one.

The accounts are GNOME Online Accounts' (read with its client library,
asynchronously). Signing in happens in GNOME's own sign-in window, opened at
the right provider: `gnome-control-center online-accounts add google` (or
`add ms_graph` for Microsoft 365), as GNOME 48 does it.
"""
from __future__ import annotations

from gi.repository import Adw, Gio, GLib, Gtk

from .common import Page, open_gnome_panel, plain_error

try:
    import gi

    gi.require_version("Goa", "1.0")
    from gi.repository import Goa
except (ImportError, ValueError):  # gir1.2-goa-1.0 isn't installed
    Goa = None

# (provider type, what people call it, what signing in gives them)
PROVIDERS = [
    ("google", "Google", "Gmail, Google Calendar, contacts and Google Drive"),
    ("ms_graph", "Microsoft 365", "Outlook mail, calendar, contacts and OneDrive"),
]
PROVIDER_NAMES = {"google": "Google", "ms_graph": "Microsoft 365", "exchange": "Microsoft Exchange",
                  "windows_live": "Microsoft", "owncloud": "Nextcloud", "imap_smtp": "Email",
                  "webdav": "Calendar and contacts", "kerberos": "Enterprise sign-in", "fedora": "Fedora"}


def provider_label(provider_type: str, provider_name: str = "") -> str:
    return PROVIDER_NAMES.get(provider_type) or provider_name or "Online account"


def account_subtitle(provider_type: str, provider_name: str, needs_attention: bool) -> str:
    label = provider_label(provider_type, provider_name)
    return f"{label} · Signed out. Sign in again to keep using it" if needs_attention else label


class AccountsPage(Page):
    title = "Online accounts"
    icon = "org.gnome.Settings-online-accounts-symbolic|goa-panel-symbolic"
    keywords = ("online accounts", "accounts", "google", "gmail", "microsoft", "microsoft 365", "office 365",
                "outlook", "onedrive", "exchange", "calendar", "contacts", "email", "mail", "nextcloud",
                "sign in", "drive")

    def __init__(self, window) -> None:
        super().__init__(window)
        self.client = None
        self.rows: list[Gtk.Widget] = []
        self.add_group(None, "Sign in once to get your mail, calendar, contacts and files in this computer’s "
                             "apps. Google and Microsoft 365 open their own sign-in page, so your password "
                             "isn’t kept on this computer.")
        self.list_group = self.add_group("Signed in")
        self.empty = Adw.ActionRow(title="No accounts yet", subtitle="Add one below")
        self.list_group.add(self.empty)

        add = self.add_group("Add an account")
        for provider, name, what in PROVIDERS:
            row = Adw.ActionRow(title=f"Add {name}", subtitle=what, activatable=True, subtitle_lines=0)
            row.add_suffix(Gtk.Image(icon_name="list-add-symbolic"))
            row.connect("activated", lambda _r, p=provider: self.open_accounts("add", p))
            add.add(row)
        other = Adw.ActionRow(title="Other accounts", subtitle="Nextcloud, Microsoft Exchange, other email and more",
                              activatable=True, subtitle_lines=0)
        other.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        other.connect("activated", lambda _r: self.open_accounts())
        add.add(other)

        if Goa is None:
            self.empty.set_title("Accounts can’t be shown here")
            self.empty.set_subtitle("Add or remove them with the buttons below")
            return
        Goa.Client.new(None, self.got_client)

    def got_client(self, _source, result) -> None:
        if self.torn_down:
            return
        try:
            self.client = Goa.Client.new_finish(result)
        except GLib.Error as exc:
            print(f"kulan-settings: online accounts: {exc.message}")
            self.empty.set_title("Accounts can’t be shown right now")
            self.empty.set_subtitle("Sign out and back in, then try again")
            return
        for signal in ("account-added", "account-removed", "account-changed"):
            self.watch(self.client, signal, lambda *_a: self.fill())
        self.fill()

    def shown(self) -> None:
        if self.client is not None:
            self.fill()

    def fill(self) -> None:
        if self.torn_down or self.client is None:
            return
        for row in self.rows:
            self.list_group.remove(row)
        self.rows = []
        accounts = [obj.get_account() for obj in self.client.get_accounts() if obj.get_account() is not None]
        accounts.sort(key=lambda a: (provider_label(a.props.provider_type, a.props.provider_name),
                                     (a.props.presentation_identity or "").lower()))
        self.empty.set_visible(not accounts)
        for account in accounts:
            row = self.account_row(account)
            self.list_group.add(row)
            self.rows.append(row)

    def account_row(self, account) -> Adw.ActionRow:
        attention = bool(account.props.attention_needed)
        row = Adw.ActionRow(title=GLib.markup_escape_text(account.props.presentation_identity or account.props.identity
                                                          or provider_label(account.props.provider_type)),
                            subtitle=GLib.markup_escape_text(account_subtitle(
                                account.props.provider_type, account.props.provider_name, attention)),
                            subtitle_lines=0)
        row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic" if attention else "avatar-default-symbolic"))
        account_id = account.props.id
        if attention:
            fix = Gtk.Button(label="Sign in", valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
            fix.connect("clicked", lambda _b: self.open_accounts(account_id))
            row.add_suffix(fix)
        else:
            more = Gtk.Button(label="Options", valign=Gtk.Align.CENTER, css_classes=["flat"])
            more.update_property([Gtk.AccessibleProperty.LABEL],
                                 [f"Choose what {account.props.presentation_identity} is used for"])
            more.connect("clicked", lambda _b: self.open_accounts(account_id))
            row.add_suffix(more)
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Remove this account")
        remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Remove {account.props.presentation_identity}"])
        remove.connect("clicked", lambda _b: self.confirm_remove(account))
        row.add_suffix(remove)
        return row

    def open_accounts(self, *params: str) -> None:
        """GNOME's sign-in window, at a provider (add google) or an account (its id)."""
        if Gio.DesktopAppInfo.new("org.gnome.Settings.desktop") is None:
            self.toast("The sign-in window isn’t installed. Install Settings in Software")
            return
        open_gnome_panel("online-accounts", *params)

    def confirm_remove(self, account) -> None:
        name = account.props.presentation_identity or provider_label(account.props.provider_type)
        dialog = Adw.AlertDialog(heading=f"Remove {name}?",
                                 body="Its mail, calendar, contacts and files stop showing on this computer. "
                                      "Nothing is deleted from the account itself.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def answered(_d, response: str) -> None:
            if response != "remove":
                return

            def done(acc, result) -> None:
                try:
                    acc.call_remove_finish(result)
                except GLib.Error as exc:
                    if not self.torn_down:
                        self.toast(f"Couldn’t remove it. {plain_error(exc)}")
                    return
                if not self.torn_down:
                    self.toast(f"Removed {name}")
            account.call_remove(None, done)
        dialog.connect("response", answered)
        dialog.present(self.window)
