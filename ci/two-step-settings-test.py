#!/usr/bin/python3
"""Kulan Settings' two-step sign-in (2.8d): the row and every dialog build,
and the phone code's QR address is what authenticator apps read. Run under
xvfb-run, with a made-up status file for this account."""
import getpass, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else "/usr/share/kulan-settings")
import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw
Adw.init()
from kulan_settings import about
status = Path(tempfile.mkdtemp()) / "public.json"
about.AboutPage.TWO_STEP_PUBLIC = status
status.write_text(json.dumps({getpass.getuser(): {"required": True, "ready": False, "factors": []}}))
mine = about.AboutPage.my_two_step()
assert mine == {"required": True, "ready": False, "factors": []}, mine
class Win(Adw.ApplicationWindow):
    toasts = []
    def toast(self, text): self.toasts.append(text)
app = Adw.Application(application_id="org.kulan.TwoStepTest")
def go(_a):
    win = Win(application=app)
    page = about.AboutPage.__new__(about.AboutPage)
    page.window = win
    page.render_org = lambda: None
    row = page.two_step_row(mine)
    assert row.get_subtitle() == "Your organisation asks you to set it up", row.get_subtitle()
    status.write_text(json.dumps({getpass.getuser(): {"required": True, "ready": True, "factors": [
        {"id": "00000000-0000-4000-8000-000000000001", "kind": "key", "label": "YubiKey"},
        {"id": "00000000-0000-4000-8000-000000000002", "kind": "code", "label": "Pixel"}]}}))
    assert page.two_step_row(page.my_two_step()).get_subtitle() == "On: YubiKey, Pixel"
    for d in (page.open_two_step(), page.add_two_step_key(), page.add_two_step_code()):
        d.force_close()
    uri = about.AboutPage.otpauth_uri(bytes(range(20)), "amina")
    assert uri == "otpauth://totp/Kulan%20OS:amina?secret=AAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQT&issuer=Kulan%20OS&algorithm=SHA1&digits=6&period=30", uri
    status.write_text("{}")
    assert about.AboutPage.my_two_step() is None
    print("two-step settings: every dialog built")
    app.quit()
app.connect("activate", go)
app.run([])
