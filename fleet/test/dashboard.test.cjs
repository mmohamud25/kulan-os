// The web dashboard (fleet/dashboard) in a real browser: sign-in faked, fleet-admin answered
// by a stand-in, every 2.8b flow clicked through (the policy with USB and apps,
// groups, installing an app on a group, approving a drive, a person's groups).
//   node dashboard.test.cjs ../dashboard     (needs playwright and its Chromium)
const { chromium } = require("playwright");
const path = require("path"), http = require("http"), fs = require("fs");
const root = path.resolve(process.argv[2] || path.join(__dirname, "../dashboard"));
const srv = http.createServer((q, r) => { const f = path.join(root, q.url.split("?")[0] === "/" ? "index.html" : q.url.split("?")[0]);
  fs.readFile(f, (e, b) => { if (e) { r.writeHead(404); r.end(); return; } r.writeHead(200, { "content-type": f.endsWith(".js") ? "text/javascript" : "text/html" }); r.end(b); }); });
srv.listen(18899);
const org = "11111111-1111-4111-8111-111111111111";
const AAL2 = "eyJhYWwiOiJhYWwyIiwiZXhwIjo5OTk5OTk5OTk5LCJzdWIiOiJ4In0";  // {"aal":"aal2","exp":9999999999,"sub":"x"}
const AAL2_TOKEN = `x.${AAL2}.x`;
const state = { needCode: true, auth: [], verifiedWith: null, policy: { firewall: true, usb: "block" }, groups: [], calls: [], approved: [],
                mail: null };
function reply(b) {
  state.calls.push(b);
  switch (b.op) {
    case "overview": return { email: "a@x", global: true, organisations: [{ id: org, name: "Org", policy: state.policy }],
      devices: [{ id: "d1", organisation_id: org, hostname: "laptop-a", last_seen: new Date().toISOString() }], actions: [], alerts: [] };
    case "people": return { people: [{ id: "p1", username: "amina", full_name: "Amina", role: "employee", enabled: true, all_computers: false, computers: [], groups: ["g1"], on: [],
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
  await page.addInitScript(() => localStorage.setItem("sb-xegnwqxpegznsufulclx-auth-token", JSON.stringify({ access_token: "x.eyJleHAiOjk5OTk5OTk5OTksInN1YiI6IngifQ.x", refresh_token: "r", token_type: "bearer", expires_at: 9999999999, expires_in: 3600, user: { id: "x", email: "a@x" } })));
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
  if (errors.length) throw "page errors: " + errors.join("; ");
  await page.setViewportSize({ width: 390, height: 800 });
  const wide = await page.evaluate(() => document.documentElement.scrollWidth);
  if (wide > 390) throw "the page scrolls sideways on a phone: " + wide;
  console.log("dashboard: every flow passed");
  await browser.close(); srv.close();
})().catch((e) => { console.log("FAIL", e); process.exit(1); });
