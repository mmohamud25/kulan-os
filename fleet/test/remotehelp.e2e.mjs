// Remote help (2.9a) end to end: the real fleet and fleet-admin functions
// against a real Postgres, served over HTTP; the dashboard's viewer
// (remotehelp.js) in a real Chromium; and the computer's helper
// (kulan-remote-help, GStreamer's WebRTC) with a test picture instead of the
// screen. The picture must arrive, the admin's mouse and keyboard must reach
// the computer only once allowed, and either side must be able to end it.
//
//   cd fleet/test && FLEET_TEST_DB=postgres://postgres@localhost:5432/postgres node --experimental-strip-types remotehelp.e2e.mjs
//   (needs playwright with its Chromium, and python3 with GStreamer's webrtcbin, nice, vpx and PyGObject)
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import postgres from "postgres";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const DB = process.env.FLEET_TEST_DB;
if (!DB || !/localhost|127\.0\.0\.1|\/tmp/.test(DB)) {
  console.error("FLEET_TEST_DB must be a throwaway local database");
  process.exit(64);
}
const PY = process.env.PYTHON || "python3";
const { chromium } = createRequire(import.meta.url)("playwright");
const db = postgres(DB, { onnotice: () => {} });
let failures = 0;
const ok = (cond, msg, extra) => {
  if (cond) { console.log(`ok: ${msg}`); return; }
  failures++;
  console.error(`FAIL: ${msg}`, extra === undefined ? "" : JSON.stringify(extra).slice(0, 2000));
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(what, fn, ms = 20000) {
  const end = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) { ok(false, `in time: ${what}`); return v; }
    await sleep(200);
  }
}

// --- the database and the functions -----------------------------------------------------------
await db.unsafe(`drop schema if exists fleet cascade; drop schema if exists stats cascade; drop schema if exists auth cascade;
  do $$ begin create role anon; exception when duplicate_object then null; end $$;
  do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
  create schema if not exists extensions; create extension if not exists pgcrypto schema extensions;
  create schema if not exists auth; create table if not exists auth.users (id text primary key, email text);`);
const migrations = path.join(repo, "fleet/supabase/migrations");
for (const f of fs.readdirSync(migrations).filter((f) => f.endsWith(".sql")).sort()) {
  await db.unsafe(fs.readFileSync(path.join(migrations, f), "utf8"));
}

const server = http.createServer();
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;
const BASE = `http://127.0.0.1:${PORT}`;
const env = { SUPABASE_DB_URL: DB, SUPABASE_URL: "http://auth.test", SUPABASE_ANON_KEY: "anon", SUPABASE_SERVICE_ROLE_KEY: "service",
              FLEET_FACTOR_KEY: btoa("k".repeat(32)), FLEET_DASHBOARD_ORIGINS: BASE };
const handlers = {};
let serving = "";
globalThis.Deno = { env: { get: (k) => env[k] }, serve: (h) => { handlers[serving] = h; } };
const realFetch = globalThis.fetch;
const b64url = (o) => btoa(JSON.stringify(o)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const tokenFor = (email) => `${b64url({ alg: "none" })}.${b64url({ email, aal: "aal1" })}.sig`;
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u === "http://auth.test/auth/v1/user") {
    const auth = new Headers(init?.headers).get("Authorization") ?? "";
    try {
      const { email } = JSON.parse(atob(auth.replace(/^Bearer /, "").split(".")[1].replaceAll("-", "+").replaceAll("_", "/")));
      return new Response(JSON.stringify({ id: `id-${email}`, email, email_confirmed_at: "2026-01-01", factors: [] }), { status: 200 });
    } catch {
      return new Response("{}", { status: 401 });
    }
  }
  if (u === "http://auth.test/realtime/v1/api/broadcast") return new Response(null, { status: 202 });
  return realFetch(url, init);
};
const run = path.join(here, ".run-e2e");
fs.mkdirSync(run, { recursive: true });
for (const name of ["fleet", "fleet-admin"]) {
  const dir = path.join(run, name);
  fs.mkdirSync(dir, { recursive: true });
  for (const f of fs.readdirSync(path.join(repo, "fleet/supabase/functions", name))) {
    let src = fs.readFileSync(path.join(repo, "fleet/supabase/functions", name, f), "utf8");
    if (f === "index.ts") src = src.replace('import postgres from "npm:postgres@3.4.5";', 'import postgres from "postgres";');
    fs.writeFileSync(path.join(dir, f), src);
  }
  serving = name;
  await import(path.join(dir, "index.ts"));
}

// The page, the viewer, and both functions on one HTTP server.
const PAGE = `<!doctype html><meta charset="utf-8"><title>help</title>
<video id="v" autoplay muted playsinline tabindex="0" style="width: 960px; height: 540px; object-fit: contain; background: #000"></video>
<p id="status"></p><p id="control"></p><p id="end"></p>
<script type="module">
import { RemoteHelp } from "/dashboard/remotehelp.js";
const api = async (body) => {
  const res = await fetch("/functions/v1/fleet-admin", { method: "POST", headers: { "Content-Type": "application/json",
    Authorization: "Bearer ${tokenFor("admin@acme.test")}" }, body: JSON.stringify(body) });
  const out = await res.json();
  if (!res.ok) throw new Error(out.error || res.status);
  return out;
};
window.statuses = [];
window.startHelp = async (deviceId, control) => {
  document.getElementById("end").textContent = "";
  window.help = new RemoteHelp({ api, deviceId, hostname: "laptop-a", control, video: document.getElementById("v"),
    onStatus: (t) => { window.statuses.push(t); document.getElementById("status").textContent = t; },
    onControl: (c) => { document.getElementById("control").textContent = c ? "control" : "view"; },
    onEnd: (t) => { document.getElementById("end").textContent = t; } });
  return (await window.help.start()).session_id;
};
</script>`;
server.on("request", async (req, res) => {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const url = req.url.split("?")[0];
  if (url === "/") { res.writeHead(200, { "content-type": "text/html" }); res.end(PAGE); return; }
  if (url.startsWith("/dashboard/")) {
    const f = path.join(repo, "fleet", url);
    if (!f.startsWith(path.join(repo, "fleet/dashboard")) || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { "content-type": "text/javascript" });
    res.end(fs.readFileSync(f));
    return;
  }
  const fn = url.startsWith("/functions/v1/fleet-admin") ? "fleet-admin" : url.startsWith("/functions/v1/fleet") ? "fleet" : null;
  if (!fn) { res.writeHead(404); res.end(); return; }
  const out = await handlers[fn](new Request(`${BASE}${req.url}`, { method: req.method, headers: req.headers,
                                                                   body: req.method === "POST" ? Buffer.concat(chunks) : undefined }));
  res.writeHead(out.status, Object.fromEntries(out.headers));
  res.end(Buffer.from(await out.arrayBuffer()));
});

const call = async (fn, route, body, token) => {
  const res = await realFetch(`${BASE}/functions/v1/${fn}${route}`, { method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(body) });
  return { status: res.status, body: await res.json() };
};

// --- an organisation, an admin, a computer on 2.9a ------------------------------------------
await db`insert into fleet.admins (email, organisation_id) values ('boss@kulan.test', null)`;
const admin = (email, body) => call("fleet-admin", "", body, tokenFor(email));
const org = (await admin("boss@kulan.test", { op: "new-organisation", name: "Acme", owner_email: "admin@acme.test" })).body.id;
const code = (await admin("admin@acme.test", { op: "new-code", organisation_id: org })).body.code;
const A = (await call("fleet", "/v1/enrol", { code, hostname: "laptop-a", kulan_version: "2.9.1 (build 130)" })).body;
const report = () => call("fleet", "/v1/report", { device_id: A.device_id, hostname: "laptop-a" }, A.token);
ok((await report()).status === 200, "the computer reports");

// The computer's side, as kulan-fleet does it: the request, picked up, the helper started.
const agentDir = fs.mkdtempSync(path.join(os.tmpdir(), "kulan-help-"));
const agentFile = path.join(agentDir, "kulan-remote-help");
fs.writeFileSync(agentFile, fs.readFileSync(path.join(repo, "packages/kulan-settings/root/usr/lib/kulan/kulan-remote-help"), "utf8")
  .replace('"/usr/share/kulan-fleet"', JSON.stringify(path.join(repo, "packages/kulan-base/root/usr/share/kulan-fleet"))));
async function computerTakes(sessionId, answer, extraEnv = {}) {
  const r = await report();
  const req = r.body.actions.find((a) => a.type === "remote-help" && a.payload.session_id === sessionId);
  ok(!!req, "the request comes with the report", r.body.actions);
  const claimed = await call("fleet", `/v1/help/${sessionId}`, { op: "claim", device_id: A.device_id, user: "amina" }, A.token);
  ok(claimed.status === 200, "picked up", claimed);
  const start = { ...claimed.body, server: `${BASE}/functions/v1/fleet`, session_id: sessionId };
  const child = spawn(PY, ["-I", agentFile], { env: { PATH: process.env.PATH, KULAN_REMOTE_HELP: JSON.stringify(start),
                                                     KULAN_REMOTE_HELP_TEST: answer, GST_DEBUG: "0", ...extraEnv } });
  const out = { text: "", exited: null };
  child.stdout.on("data", (d) => { out.text += d; });
  child.stderr.on("data", (d) => { out.text += d; });
  child.on("exit", (c) => { out.exited = c; });
  return out;
}

const browser = await chromium.launch({ args: ["--disable-features=WebRtcHideLocalIpsWithMdns", "--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(e.message));
await page.goto(BASE + "/");

try {
  // 1. Allowed, with control: the picture arrives, the mouse and keyboard reach the computer.
  const s1 = await page.evaluate(() => window.startHelp(window.DEVICE, true).catch((e) => "error: " + e.message));
  ok(s1.startsWith("error"), "a computer id is needed", s1);
  await page.evaluate((id) => { window.DEVICE = id; }, A.device_id);
  const sid = await page.evaluate(() => window.startHelp(window.DEVICE, true));
  ok(/^[0-9a-f-]{36}$/.test(sid), "remote help asked from the viewer", sid);
  await until("the viewer waits for the computer", () => page.textContent("#status").then((t) => t.includes("Waiting for laptop-a")));
  const agent = await computerTakes(sid, "allow-control");
  await until("the helper asks the person", () => agent.text.includes("asked admin@acme.test Acme control"));
  await until("the picture arrives", () => page.evaluate(() => {
    const v = document.getElementById("v");
    return v.videoWidth === 1280 && v.videoHeight === 720 && v.currentTime > 0.5;
  }), 30000);
  ok((await page.textContent("#control")) === "control", "the viewer knows control was allowed");
  await until("connected, both sides", async () => (await page.textContent("#status")).startsWith("Connected") &&
                                                    agent.text.includes("status Connected"));
  const box = await page.locator("#v").boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 4);
  await page.mouse.down();
  await page.mouse.up();
  await page.keyboard.press("KeyA");
  await page.mouse.wheel(0, 300);
  await until("the click reaches the computer", () => /input NotifyPointerButton 272 1/.test(agent.text) && /input NotifyPointerButton 272 0/.test(agent.text));
  ok(/input NotifyPointerMotionAbsolute 0 639\.\d 179\.\d/.test(agent.text), "where the pointer is, in the screen's own size", agent.text.match(/input NotifyPointerMotionAbsolute.*/g));
  await until("the key reaches the computer", () => /input NotifyKeyboardKeycode 30 1/.test(agent.text) && /input NotifyKeyboardKeycode 30 0/.test(agent.text));
  await until("the wheel reaches the computer", () => /input NotifyPointerAxisDiscrete 0 3/.test(agent.text));
  // The admin ends it: the helper stops by itself.
  await page.evaluate(() => window.help.end());
  await until("the helper ends when the admin does", () => agent.exited === 0, 15000);
  ok(agent.text.includes("done The admin ended remote help."), "the person is told the admin ended it", agent.text.slice(-400));
  const [st] = await db`select state, detail from fleet.help_sessions where id = ${sid}`;
  ok(st.state === "ended" && st.detail === "the admin ended it", "ended on the server", st);

  // 2. View only: no input reaches the computer even if the viewer sends some.
  await db`update fleet.help_sessions set created_at = created_at - interval '30 seconds'`;  // asked again at once is refused
  const sid2 = await page.evaluate(() => window.startHelp(window.DEVICE, false));
  const agent2 = await computerTakes(sid2, "allow-control", { KULAN_REMOTE_HELP_TEST_STOP_AFTER: "8" });
  await until("view only: the picture arrives", () => page.evaluate(() => document.getElementById("v").videoWidth === 1280), 30000);
  ok((await page.textContent("#control")) === "view", "view only: no control");
  await page.evaluate(() => { window.help.control = true; window.help.send({ t: "kd", code: "KeyB" }); });
  await page.mouse.click(box.x + 10, box.y + 10);
  await until("the person stops it", () => page.textContent("#end").then((t) => t.includes("they stopped it")), 20000);
  ok(!agent2.text.includes("input "), "view only: nothing typed or clicked on the computer", agent2.text.match(/input .*/g));
  await until("the helper closes", () => agent2.exited === 0, 10000);

  // 3. Declined.
  await db`update fleet.help_sessions set created_at = created_at - interval '30 seconds'`;  // asked again at once is refused
  const sid3 = await page.evaluate(() => window.startHelp(window.DEVICE, true));
  const agent3 = await computerTakes(sid3, "decline");
  await until("the viewer is told no", () => page.textContent("#end").then((t) => t.startsWith("They said no.")));
  await until("the helper closes after no", () => agent3.exited === 0, 10000);
  ok(!agent3.text.includes("watching"), "declined: nothing shared");

  // The activity log has it all; nothing of the connection is in it.
  const log = (await admin("admin@acme.test", { op: "log", organisation_id: org })).body.entries.map((e) => `${e.email}: ${e.what}`);
  for (const want of ["admin@acme.test: asked to see and control the screen", "amina: allowed remote help, with control",
                      "admin@acme.test: ended remote help", "admin@acme.test: asked to see the screen", "amina: allowed remote help (view only)",
                      "amina: ended remote help", "amina: declined remote help"]) ok(log.includes(want), `in the activity log: ${want}`, log);
  ok(pageErrors.length === 0, "no errors in the page", pageErrors);
} finally {
  await browser.close();
  server.close();
  await db.end();
  fs.rmSync(run, { recursive: true, force: true });
  fs.rmSync(agentDir, { recursive: true, force: true });
}
if (failures) {
  console.error(`${failures} remote help check(s) failed`);
  process.exit(1);
}
console.log("remote help end-to-end test passed");
process.exit(0);
