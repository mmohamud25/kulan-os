// The web dashboard (fleet/dashboard) in a real browser: sign-in faked, fleet-admin answered
// by a stand-in, every flow clicked through (2.8b to 2.9b).
//   node dashboard.test.cjs ../dashboard     (needs playwright and its Chromium)
const { chromium } = require("playwright");
const path = require("path"), http = require("http"), fs = require("fs");
const root = path.resolve(process.argv[2] || path.join(__dirname, "../dashboard"));
const srv = http.createServer((q, r) => { const f = path.join(root, q.url.split("?")[0] === "/" ? "index.html" : q.url.split("?")[0]);
  fs.readFile(f, (e, b) => { if (e) { r.writeHead(404); r.end(); return; } r.writeHead(200, { "content-type": f.endsWith(".js") ? "text/javascript" : "text/html" }); r.end(b); }); });
srv.listen(18899);
const org = "11111111-1111-4111-8111-111111111111";
const AAL2 = "eyJhYWwiOiJhYWwyIiwiZXhwIjo5OTk5OTk5OTk5LCJzdWIiOiJ4In0";  // {"aal":"aal2","exp":9999999999,"sub":"x"}
const AAL2_TOKEN = `eyJhbGciOiJub25lIn0.${AAL2}.c2lnbg`;
const state = { needCode: true, auth: [], verifiedWith: null, policy: { firewall: true, usb: "block" }, groups: [], calls: [], approved: [],
                mail: null, allowScripts: false, scripts: [] };
function reply(b) {
  state.calls.push(b);
  switch (b.op) {
    case "overview": return { email: "a@x", global: true, organisations: [{ id: org, name: "Org", policy: state.policy, allow_scripts: state.allowScripts }],
      devices: [{ id: "d1", organisation_id: org, hostname: "laptop-a", last_seen: new Date().toISOString(),
                  pushed: { networks: { n1: "added" }, printers: { pr1: "couldn't reach the printer; tried again at the next report" } } }],
      actions: [{ id: "a1", device_id: "d1", type: "run-fix", status: "done", detail: "no problems found", created_at: "2026-10-26T09:00:00Z",
                  requested_by: "a@x", exit_code: 0, has_output: true, what: "check-disk" }], alerts: [] };
    case "action-output": return { output: "== Disk errors in the last 7 days\nnone", exit_code: 0, status: "done", type: "run-fix" };
    case "scripts": return { allowed: state.allowScripts, scripts: state.scripts, is_owner: true };
    case "set-scripts-allowed": state.allowScripts = b.allowed; return {};
    case "add-script": state.scripts.push({ id: "s1", name: b.name, description: b.description, body: b.body, sha256: "ab".repeat(32),
                                             updated_at: "2026-10-26T09:00:00Z", updated_by: "a@x" }); return { id: "s1" };
    case "add-network": state.groups[0].networks = [{ id: "n1", ssid: b.ssid, security: b.security, hidden: b.hidden }]; return { id: "n1" };
    case "add-printer": state.groups[0].printers = [{ id: "pr1", name: b.name, uri: b.uri }]; return { id: "pr1" };
    case "message-person": return { count: 1 };
    case "people": return { people: [{ id: "p1", username: "amina", full_name: "Amina", role: "employee", enabled: true, all_computers: false, computers: [], groups: ["g1"], on: [],
                                        signed_in: [{ hostname: "laptop-a", last: 1790000000, now: true }],
                                        ...(state.mail ? { mailbox: "amina@acme.test", mailbox_state: "active", mailbox_todo: "rename", mailbox_error: "Google couldn't be reached" } : {}) }],
                            removed_with_mailbox_todo: [], is_owner: true };
    case "mail": return { connection: state.mail, mailboxes: state.mail ? 1 : 0, is_owner: true };
    case "connect-mail": state.mail = { domain: "acme.test", admin_email: b.admin_email, client_email: JSON.parse(b.key).client_email, client_id: "1122" }; return { domain: "acme.test" };
    case "add-person": return { id: "p2", uid: 70001, ...(b.mailbox ? { mailbox: b.mailbox.address, mailbox_password: "abcd-EFGH-2346-mnpq" } : {}) };
    case "mailbox": return b.action === "new-password" ? { mailbox: "amina@acme.test", mailbox_password: "wxyz-WXYZ-2346-mnpq" } : {};
    case "admins": return { admins: [], owner: "a@x", is_owner: true };
    case "groups": return { groups: state.groups };
    case "drives": return { approved: state.approved, seen: [{ vendor_id: "0951", product_id: "1666", serial: "ABCD1234", name: "Kingston", computers: ["laptop-a"] }] };
    case "set-policy": state.policy = b.policy; return { policy: b.policy };
    case "add-group": state.groups.push({ id: "g1", name: b.name, policy: {}, devices: [], people: [] }); return { id: "g1" };
    case "edit-group": Object.assign(state.groups.find((g) => g.id === b.group_id), { name: b.name, policy: b.policy, devices: b.devices }); return {};
    case "group-action": return { count: 1 };
    case "action": return {};
    case "approve-drive": state.approved.push({ id: "u1", name: b.name, vendor_id: b.vendor_id, product_id: b.product_id, serial: b.serial }); return {};
    case "edit-person": return {};
    case "help-start": return { session_id: "22222222-2222-4222-8222-222222222222", ice: [{ urls: ["stun:stun.cloudflare.com:3478"] }], relay: false, woke: true, signed_in: true };
    case "help-signal": state.helpPolls = (state.helpPolls || 0) + 1;
      return state.helpPolls < 2 ? { state: "asking", who: "amina", control: false, candidates: [], count: 0 }
                                 : { state: "declined", detail: "they said no", control: false, candidates: [], count: 0 };
    case "weekly-report": return { week: "2026-10-26", made_at: "2026-10-26T09:00:00Z",
      summary: { computers: { total: 1, reported_this_week: 1, up_to_date: 0, security_waiting: [{ hostname: "laptop-a", count: 2 }],
                              restart_needed: ["laptop-a"], counts: { restart_needed: 120 }, encrypted: 1, not_encrypted: [], firewall_off: [], compliance_passing: 1,
                              compliance_failing: [], policy_not_met: [], not_seen_week: [], lost_mode: [], versions: { "2.8.3": 1 } },
                 people: { total: 1, disabled: [], setup_code_unused: [], admins: [] }, activity: { changes: 3, by_kind: [{ what: "added", count: 3 }] },
                 review: { at: null, by: null, due: true } },
      previous: { week: "2026-10-19", summary: { computers: { total: 1, security_waiting: [] } } } };
    case "access-review": return { is_owner: true, due: true, last_review: null,
      people: [{ id: "p9", username: "omar", full_name: "Omar", role: "admin", where: ["laptop-a"], signed_in: [{ hostname: "laptop-a", last: 1790000000, now: false }] }],
      admins: [{ email: "a@x", owner: true, created_at: "2026-10-01T00:00:00Z" }, { email: "helper@x", owner: false, created_at: "2026-10-02T00:00:00Z" }],
      all_organisations_admins: [] };
    case "review-access": return { kept: 1, removed: 2 };
    case "log": return { entries: b.limit === 10000
        ? [{ id: 2, at: "2026-10-26T10:00:00Z", email: "a@x", what: "=cmd", hostname: "pc, one", detail: { n: 1 } }]
        : [{ id: 5, at: "2026-10-26T10:00:00Z", email: "a@x", what: "asked: refresh", hostname: "laptop-a" }], more: b.limit === 100 && !b.before };
    default: return {};
  }
}
(async () => {
  const browser = await chromium.launch(); const page = await browser.newPage({ acceptDownloads: true });
  const errors = []; page.on("pageerror", (e) => errors.push(e.message)); page.on("console", (m) => m.type() === "error" && !m.text().includes("ERR_TUNNEL") && !m.text().includes("status of 403") && errors.push(m.text()));
  page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(d.message().includes("name you'll") ? "Finance drive" : ""); else await d.accept(); });
  await page.route("**/functions/v1/fleet-admin", async (r) => {
    const body = JSON.parse(r.request().postData() || "{}");
    const aal2 = (r.request().headers().authorization || "").includes(AAL2);
    if (state.needCode && !aal2) {
      state.calls.push({ ...body, refused: true });
      return r.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: "type the code from your authenticator app", two_step: "code" }) });
    }
    return r.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(reply(body)) });
  });
  // Supabase Auth: the user (with an authenticator app), a challenge, and a verify that gives an aal2 session.
  await page.route("**/auth/v1/**", (r) => {
    const u = r.request().url(), m = r.request().method();
    state.auth.push(`${m} ${new URL(u).pathname}`);
    const json = (o) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(o) });
    if (u.endsWith("/auth/v1/user")) return json({ id: "x", email: "a@x", factors: [{ id: "f1", factor_type: "totp", status: "verified", friendly_name: "Phone" }] });
    if (u.endsWith("/factors/f1/challenge")) return json({ id: "c1", type: "totp", expires_at: 9999999999 });
    if (u.endsWith("/factors/f1/verify")) {
      state.verifiedWith = JSON.parse(r.request().postData() || "{}").code;
      return json({ access_token: AAL2_TOKEN, refresh_token: "r2", token_type: "bearer", expires_in: 3600, expires_at: 9999999999, user: { id: "x", email: "a@x" } });
    }
    return json({});
  });
  await page.addInitScript(() => localStorage.setItem("sb-xegnwqxpegznsufulclx-auth-token", JSON.stringify({ access_token: "eyJhbGciOiJub25lIn0.eyJleHAiOjk5OTk5OTk5OTksInN1YiI6IngifQ.c2lnbg", refresh_token: "r", token_type: "bearer", expires_at: 9999999999, expires_in: 3600, user: { id: "x", email: "a@x" } })));
  await page.goto("http://127.0.0.1:18899/");
  // 2.8d: the server wants the authenticator app's code first.
  await page.waitForSelector("#dlg-title:has-text(\"Your authenticator app's code\")");
  await page.fill("#dlg input[aria-label='6-digit code']", "123456");
  await page.click("#dlg-actions >> text=Continue");
  await page.waitForTimeout(500);
  if (state.verifiedWith !== "123456" || !state.auth.includes("POST /auth/v1/factors/f1/challenge")) throw "authenticator code not verified " + JSON.stringify(state.auth);
  await page.waitForSelector("#groups tr");
  // org policy keeps usb and adds an app
  if (await page.locator("#policy select[aria-label='USB drives']").inputValue() !== "block") throw "usb not shown";
  await page.fill("#policy input[aria-label='Blocked apps: app']", "kulan-base");
  await page.click("#policy >> text=Blocked apps >> xpath=.. >> button:has-text('Add')");
  if (!(await page.textContent("#policy")).includes("part of Kulan OS")) throw "protected not refused";
  await page.fill("#policy input[aria-label='Blocked apps: app']", "steam");
  await page.click("#p-save");
  await page.waitForTimeout(300);
  const sp = state.calls.filter((c) => c.op === "set-policy").pop();
  if (JSON.stringify(sp.policy) !== JSON.stringify({ firewall: true, usb: "block", blocked_apps: [{ source: "debian", id: "steam" }] })) throw "set-policy " + JSON.stringify(sp);
  // add group, set its policy and computers
  await page.click("#add-group");
  await page.fill("#dlg input", "Office");
  await page.click("#dlg-actions >> text=Add");
  await page.waitForSelector("#dlg-title:has-text('Office')");
  await page.selectOption("#dlg select[aria-label='USB drives']", "approved");
  await page.fill("#dlg input[aria-label='Required apps: app']", "org.videolan.VLC");
  await page.selectOption("#dlg select[aria-label='Required apps: from']", "flatpak");
  await page.check("#dlg .checks input[value='d1']");
  await page.click("#dlg-actions >> text=Save");
  await page.waitForTimeout(300);
  const eg = state.calls.filter((c) => c.op === "edit-group").pop();
  if (JSON.stringify(eg) !== JSON.stringify({ op: "edit-group", group_id: "g1", name: "Office", policy: { usb: "approved", required_apps: [{ source: "flatpak", id: "org.videolan.VLC" }] }, devices: ["d1"] })) throw "edit-group " + JSON.stringify(eg);
  // group install app
  await page.click("#groups >> text=Manage…");
  await page.selectOption("#dlg select[aria-label=\"Ask Office's computers to\"]", "install-app");
  await page.click("#dlg >> button:has-text('Send')");
  await page.fill("#dlg input[placeholder^='vlc']", "hello");
  await page.click("#dlg-actions >> text=Install");
  await page.waitForTimeout(300);
  const ga = state.calls.filter((c) => c.op === "group-action").pop();
  if (JSON.stringify(ga) !== JSON.stringify({ op: "group-action", group_id: "g1", type: "install-app", payload: { source: "debian", id: "hello" } })) throw "group-action " + JSON.stringify(ga);
  // approve a seen drive
  await page.click("#drives >> text=Approve…");
  await page.waitForTimeout(300);
  const ad = state.calls.filter((c) => c.op === "approve-drive").pop();
  if (!ad || ad.name !== "Finance drive" || ad.serial !== "ABCD1234") throw "approve " + JSON.stringify(ad);
  // person dialog offers the group, keeps it on save
  await page.click("#people >> text=Manage…");
  await page.click("#dlg-actions >> text=Save");
  await page.waitForTimeout(300);
  const ep = state.calls.filter((c) => c.op === "edit-person").pop();
  if (JSON.stringify(ep.groups) !== JSON.stringify(["g1"])) throw "edit-person " + JSON.stringify(ep);
  // 2.8c: the weekly report (printed), the admin-rights review, the log searched and exported.
  await page.evaluate(() => { window.printed = 0; window.print = () => { window.printed++; window.printedText = document.getElementById("print-area").textContent; }; });
  await page.click("#weekly");
  await page.waitForSelector("#dlg .report-line.bad");
  if (!(await page.textContent("#dlg")).includes("Restart needed on 120 computers") ||
      !(await page.textContent("#dlg")).includes("laptop-a and 119 more") ||
      !(await page.textContent("#dlg")).includes("2.8.3: 1 computer") ||
      !(await page.textContent("#dlg")).includes("Security updates waiting on 1 computer") ||
      !(await page.textContent("#dlg")).includes("+1 since last week")) throw "weekly report lines";
  await page.click("#dlg-actions >> text=Print or save as PDF");
  if (await page.evaluate(() => window.printed) !== 1 || !(await page.evaluate(() => window.printedText)).includes("Admin-rights review due")) throw "weekly report printed";
  await page.click("#review");
  await page.waitForSelector("#dlg-title:has-text('Admin-rights review')");
  await page.waitForSelector("#dlg input[aria-label='Keep']");
  const boxes = page.locator("#dlg input[aria-label='Keep']");
  if (!(await boxes.nth(1).isDisabled())) throw "the Owner can't be unticked";
  await boxes.nth(0).uncheck();
  await boxes.nth(2).uncheck();
  await page.click("#dlg-actions >> text=Finish review");
  await page.waitForTimeout(300);
  const ra = state.calls.filter((c) => c.op === "review-access").pop();
  if (JSON.stringify(ra) !== JSON.stringify({ op: "review-access", organisation_id: org, remove_people: ["p9"], remove_admins: ["helper@x"] })) throw "review-access " + JSON.stringify(ra);
  await page.fill("#log-who", "a@x");
  await page.fill("#log-from", "2026-10-01");
  await page.click("#log-find >> text=Search");
  await page.waitForSelector("#log tr");
  const lq = state.calls.filter((c) => c.op === "log").pop();
  if (lq.who !== "a@x" || lq.from !== "2026-10-01" || lq.limit !== 100) throw "log query " + JSON.stringify(lq);
  if (!(await page.textContent("#log")).includes("asked laptop-a: Report now")) throw "log shown";
  const [dl] = await Promise.all([page.waitForEvent("download"), page.click("#log-csv")]);
  const csv = fs.readFileSync(await dl.path(), "utf8");
  if (csv.split("\r\n")[1] !== `2026-10-26T10:00:00Z,a@x,'=cmd,"pc, one","{""n"":1}"`) throw "csv " + JSON.stringify(csv);
  // Big numbers: the counts, not the lists' length.
  await page.click("#log-find >> text=Search");
  await page.fill("#log-who", "someone-else");
  await page.waitForSelector("#log-more:not(.hidden)");
  await page.click("#log-more");
  await page.waitForTimeout(200);
  const more = state.calls.filter((c) => c.op === "log").pop();
  if (more.who !== "a@x") throw "Show more changed the search " + JSON.stringify(more);
  // The policy's two-step setting, and a person's reset.
  await page.selectOption("#policy select[aria-label='Two-step sign-in']", "admins");
  await page.click("#p-save");
  await page.waitForTimeout(300);
  if (state.calls.filter((c) => c.op === "set-policy").pop().policy.two_step !== "admins") throw "two_step not saved";
  // 2.8e: connect Google Workspace, a new person with a mailbox, a new Google password, removing with the mailbox deleted.
  if (!(await page.textContent("#mail-connect")).includes("Connect Google Workspace")) throw "mail card";
  await page.click("#mail-connect");
  await page.setInputFiles("#dlg input[type=file]", { name: "key.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ type: "service_account", client_email: "kf@acme-mail.iam.gserviceaccount.com", client_id: "1122", private_key: "-----BEGIN PRIVATE KEY-----\nAA\n-----END PRIVATE KEY-----\n" })) });
  await page.waitForSelector("#dlg >> text=client ID 1122");
  await page.fill("#dlg input[type=email]", "it@acme.test");
  await page.click("#dlg-actions >> text=Connect");
  await page.waitForSelector("#mail >> text=acme.test");
  const cm = state.calls.filter((c) => c.op === "connect-mail").pop();
  if (cm.admin_email !== "it@acme.test" || JSON.parse(cm.key).client_email !== "kf@acme-mail.iam.gserviceaccount.com") throw "connect-mail " + JSON.stringify(cm);
  await page.click("#add-person");
  await page.fill("#dlg input >> nth=0", "Leyla Nur");
  if (await page.inputValue("#dlg input[spellcheck=false] >> nth=1") !== "leyla@acme.test") throw "mailbox address not suggested";
  await page.click("#dlg-actions >> text=Add");
  await page.waitForSelector("#dlg-title:has-text('Setup code for Leyla Nur')");
  const ap = state.calls.filter((c) => c.op === "add-person").pop();
  if (JSON.stringify(ap.mailbox) !== JSON.stringify({ action: "create", address: "leyla@acme.test" })) throw "add-person mailbox " + JSON.stringify(ap);
  await page.click("#dlg-actions >> text=Done");
  await page.waitForSelector("#dlg-title:has-text('Google password for Leyla Nur')");
  if (!(await page.textContent("#dlg")).includes("abcd-EFGH-2346-mnpq")) throw "first Google password not shown";
  await page.click("#dlg-actions >> text=Done");
  await page.click("#people >> text=Manage…");
  await page.waitForSelector("#dlg >> text=amina@acme.test");
  if (!(await page.textContent("#dlg")).includes("name still to change")) throw "what's left to do not shown";
  await page.click("#dlg >> button:has-text('New Google password…')");
  await page.waitForSelector("#dlg-title:has-text('Google password for Amina')");
  if (!(await page.textContent("#dlg")).includes("wxyz-WXYZ-2346-mnpq")) throw "new Google password not shown";
  await page.click("#dlg-actions >> text=Done");
  await page.click("#people >> text=Manage…");
  await page.click("#dlg-actions >> text=Remove…");
  await page.selectOption("#dlg select[aria-label='Their mailbox']", "delete");
  await page.fill("#dlg input[spellcheck=false]", "amina");
  await page.click("#dlg-actions >> text=Remove");
  await page.waitForTimeout(300);
  const rp = state.calls.filter((c) => c.op === "remove-person").pop();
  if (rp?.mailbox !== "delete" || rp.confirm !== "amina") throw "remove-person mailbox " + JSON.stringify(rp);
  // 2.9b: a fix and a message for a computer, a fix's output, scripts allowed by the Owner, added and run,
  // a group's Wi-Fi and printers (how they went on its computers), a message to a person.
  const last = (op) => state.calls.filter((c) => c.op === op && !c.refused).pop();
  await page.selectOption("#devices select", "run-fix");
  await page.click("#devices >> button:has-text('Send')");
  await page.waitForSelector("#dlg-title:has-text('Run a fix on laptop-a')");
  await page.selectOption("#dlg select", "reset-wifi");
  if (!(await page.textContent("#dlg")).includes("offline for a few seconds")) throw "the fix isn't explained";
  await page.click("#dlg-actions >> text=Run");
  await page.waitForTimeout(300);
  if (JSON.stringify(last("action")) !== JSON.stringify({ op: "action", device_id: "d1", type: "run-fix", payload: { fix: "reset-wifi" } })) throw "run-fix " + JSON.stringify(last("action"));
  await page.selectOption("#devices select", "message");
  await page.click("#devices >> button:has-text('Send')");
  await page.click("#dlg-actions >> text=Send");
  if (!(await page.textContent("#dlg")).includes("Give the message a title")) throw "a message without a title was sent";
  await page.fill("#dlg input", "Restart tonight");
  await page.fill("#dlg textarea", "Save your work.");
  await page.click("#dlg-actions >> text=Send");
  await page.waitForTimeout(300);
  if (JSON.stringify(last("action").payload) !== JSON.stringify({ title: "Restart tonight", body: "Save your work." })) throw "message " + JSON.stringify(last("action"));
  if (await page.locator("#devices option[value='run-script']").count()) throw "scripts offered before the Owner allowed them";
  if (!(await page.textContent("#actions")).includes("Run a fix · Check the disk")) throw "the fix isn't named in Recent requests";
  await page.click("#actions >> button:has-text('Output')");
  await page.waitForSelector("#dlg-title:has-text('Check the disk on laptop-a')");
  if (!(await page.textContent("#dlg pre")).includes("Disk errors in the last 7 days")) throw "output not shown";
  await page.click("#dlg-actions >> text=Close");
  if (!(await page.textContent("#scripts-sub")).startsWith("Off.") || !(await page.isHidden("#add-script"))) throw "scripts shown as allowed";
  await page.check("#scripts-allow");
  await page.waitForSelector("#add-script:not(.hidden)");
  if (last("set-scripts-allowed")?.allowed !== true) throw "set-scripts-allowed " + JSON.stringify(last("set-scripts-allowed"));
  await page.click("#add-script");
  await page.fill("#dlg input >> nth=0", "Clear the print queue");
  await page.fill("#dlg textarea", "cancel -a\n");
  await page.click("#dlg-actions >> text=Save");
  await page.waitForSelector("#scripts >> text=Clear the print queue");
  if (last("add-script")?.body !== "cancel -a\n" || last("add-script").organisation_id !== org) throw "add-script " + JSON.stringify(last("add-script"));
  await page.selectOption("#devices select", "run-script");
  await page.click("#devices >> button:has-text('Send')");
  await page.waitForSelector("#dlg-title:has-text('Run a script on laptop-a')");
  if (!(await page.textContent("#dlg pre")).includes("cancel -a")) throw "the script isn't shown before it's run";
  await page.click("#dlg-actions >> text=Run");
  await page.waitForTimeout(300);
  if (JSON.stringify(last("action")) !== JSON.stringify({ op: "action", device_id: "d1", type: "run-script", payload: { script_id: "s1" } })) throw "run-script " + JSON.stringify(last("action"));
  await page.click("#groups >> text=Wi-Fi and printers…");
  await page.fill("#dlg input[placeholder='Office Wi-Fi']", "Acme Staff");
  await page.fill("#dlg input[type=password]", "correct horse");
  await page.click("#dlg >> button:has-text('Add network')");
  await page.waitForSelector("#dlg li >> text=Acme Staff");
  if (JSON.stringify(last("add-network")) !== JSON.stringify({ op: "add-network", group_id: "g1", ssid: "Acme Staff", security: "wpa-psk", password: "correct horse", hidden: false })) throw "add-network " + JSON.stringify(last("add-network"));
  if (!(await page.textContent("#dlg")).includes("added on 1 of 1")) throw "how the network went isn't shown";
  await page.fill("#dlg input[placeholder='Front desk']", "Front desk");
  await page.fill("#dlg input[placeholder^='ipp://']", "ipp://192.168.1.20/ipp/print");
  await page.click("#dlg >> button:has-text('Add printer')");
  await page.waitForSelector("#dlg li >> text=Front desk");
  if (!(await page.textContent("#dlg")).includes("laptop-a: couldn't reach the printer")) throw "a printer's problem isn't shown";
  await page.click("#dlg-actions >> text=Close");
  await page.click("#people >> text=Manage…");
  await page.click("#dlg-actions >> text=Send a message…");
  await page.fill("#dlg input", "Hello");
  await page.click("#dlg-actions >> text=Send");
  await page.waitForTimeout(300);
  if (JSON.stringify(last("message-person")) !== JSON.stringify({ op: "message-person", person_id: "p1", title: "Hello", body: "" })) throw "message-person " + JSON.stringify(last("message-person"));
  // 2.9a: remote help asked from a computer's row; the person says no.
  await page.selectOption("#devices select", "remote-help");
  await page.click("#devices >> button:has-text('Send')");
  await page.waitForSelector("#dlg-title:has-text('Remote help on laptop-a')");
  if (!(await page.textContent("#dlg")).includes("asked first and sees your email")) throw "remote help: consent not explained";
  await page.check("#dlg input[type=checkbox]");
  await page.click("#dlg-actions >> text=Ask to see the screen");
  await page.waitForSelector("#help-dialog[open]");
  const hs = state.calls.find((c) => c.op === "help-start");
  if (JSON.stringify(hs) !== JSON.stringify({ op: "help-start", device_id: "d1", control: true })) throw "help-start " + JSON.stringify(hs);
  await page.waitForSelector("#help-status:has-text('Asking amina on laptop-a')", { timeout: 5000 });
  await page.waitForSelector("#help-status:has-text('They said no. (they said no)')", { timeout: 5000 });
  if ((await page.textContent("#help-end")) !== "Close") throw "remote help: no Close after it ended";
  await page.click("#help-end");
  if (await page.locator("#help-dialog[open]").count()) throw "remote help: viewer didn't close";
  if (errors.length) throw "page errors: " + errors.join("; ");
  await page.setViewportSize({ width: 390, height: 800 });
  const wide = await page.evaluate(() => document.documentElement.scrollWidth);
  if (wide > 390) throw "the page scrolls sideways on a phone: " + wide;
  console.log("dashboard: every flow passed");
  await browser.close(); srv.close();
})().catch((e) => { console.log("FAIL", e); process.exit(1); });
