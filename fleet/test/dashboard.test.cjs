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
const state = { policy: { firewall: true, usb: "block" }, groups: [], calls: [], approved: [] };
function reply(b) {
  state.calls.push(b);
  switch (b.op) {
    case "overview": return { email: "a@x", global: true, organisations: [{ id: org, name: "Org", policy: state.policy }],
      devices: [{ id: "d1", organisation_id: org, hostname: "laptop-a", last_seen: new Date().toISOString() }], actions: [], alerts: [] };
    case "people": return { people: [{ id: "p1", username: "amina", full_name: "Amina", role: "employee", enabled: true, all_computers: false, computers: [], groups: ["g1"], on: [] }], is_owner: true };
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
    default: return {};
  }
}
(async () => {
  const browser = await chromium.launch(); const page = await browser.newPage();
  const errors = []; page.on("pageerror", (e) => errors.push(e.message)); page.on("console", (m) => m.type() === "error" && !m.text().includes("ERR_TUNNEL") && errors.push(m.text()));
  page.on("dialog", async (d) => { if (d.type() === "prompt") await d.accept(d.message().includes("name you'll") ? "Finance drive" : ""); else await d.accept(); });
  await page.route("**/functions/v1/fleet-admin", async (r) => r.fulfill({ status: 200, contentType: "application/json", headers: { "access-control-allow-origin": "*" }, body: JSON.stringify(reply(JSON.parse(r.request().postData() || "{}"))) }));
  await page.route("**/auth/v1/**", (r) => r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.addInitScript(() => localStorage.setItem("sb-xegnwqxpegznsufulclx-auth-token", JSON.stringify({ access_token: "x.eyJleHAiOjk5OTk5OTk5OTksInN1YiI6IngifQ.x", refresh_token: "r", token_type: "bearer", expires_at: 9999999999, expires_in: 3600, user: { id: "x", email: "a@x" } })));
  await page.goto("http://127.0.0.1:18899/");
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
  if (errors.length) throw "page errors: " + errors.join("; ");
  await page.setViewportSize({ width: 390, height: 800 });
  const wide = await page.evaluate(() => document.documentElement.scrollWidth);
  if (wide > 390) throw "the page scrolls sideways on a phone: " + wide;
  console.log("dashboard: every flow passed");
  await browser.close(); srv.close();
})().catch((e) => { console.log("FAIL", e); process.exit(1); });
