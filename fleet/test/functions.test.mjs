// The fleet and fleet-admin Edge Functions against a real Postgres, with every
// migration applied: People (accounts, passwords both ways, roles, disable,
// remove, setup codes), the Owner and admins, the new actions, groups and
// policies (2.8b), the weekly report, admin-rights reviews and the log (2.8c).
//
//   cd fleet/test && npm ci && FLEET_TEST_DB=postgres://postgres@localhost:5432/postgres node --experimental-strip-types functions.test.mjs
//
// The database is wiped first (schemas fleet and stats): never point this at
// a real one. Deno.serve and Supabase Auth are stood in for; the SQL is real.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import postgres from "postgres";

const here = path.dirname(fileURLToPath(import.meta.url));
const DB = process.env.FLEET_TEST_DB;
if (!DB || !/localhost|127\.0\.0\.1|\/tmp/.test(DB)) {
  console.error("FLEET_TEST_DB must be a throwaway local database");
  process.exit(64);
}
const db = postgres(DB, { onnotice: () => {} });
let failures = 0;
const ok = (cond, msg, extra) => {
  if (cond) return;
  failures++;
  console.error(`FAIL: ${msg}`, extra === undefined ? "" : JSON.stringify(extra));
};

// --- a fresh database with every migration -----------------------------------------------
await db.unsafe(`drop schema if exists fleet cascade; drop schema if exists stats cascade;
  do $$ begin create role anon; exception when duplicate_object then null; end $$;
  do $$ begin create role authenticated; exception when duplicate_object then null; end $$;
  create schema if not exists extensions; create extension if not exists pgcrypto schema extensions;
  create schema if not exists auth; create table if not exists auth.users (id text primary key, email text);`);
// (Supabase keeps pgcrypto in the "extensions" schema.)
const migrations = path.join(here, "..", "supabase", "migrations");
for (const f of fs.readdirSync(migrations).filter((f) => f.endsWith(".sql")).sort()) {
  await db.unsafe(fs.readFileSync(path.join(migrations, f), "utf8"));
}
// The 2.8 migrations run again (in order) change nothing (if not exists / or replace / drop if exists).
for (const f of fs.readdirSync(migrations).filter((f) => f >= "20261024" && f.endsWith(".sql")).sort()) {
  await db.unsafe(fs.readFileSync(path.join(migrations, f), "utf8"));
}

// --- the functions, with Deno and Supabase Auth stood in for ------------------------------
const handlers = {};
let serving = "";
globalThis.Deno = {
  env: { get: (k) => ({ SUPABASE_DB_URL: DB, SUPABASE_URL: "http://auth.test", SUPABASE_ANON_KEY: "anon",
                        SUPABASE_SERVICE_ROLE_KEY: "service", FLEET_FACTOR_KEY: btoa("k".repeat(32)) })[k] },
  serve: (h) => { handlers[serving] = h; },
};
const realFetch = globalThis.fetch;
// Stand-in Supabase Auth: a token is an unsigned JWT with the email and the
// session's assurance level; authFactors holds each email's authenticator apps.
const authFactors = {};
const b64url = (o) => btoa(JSON.stringify(o)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const tokenFor = (email, aal = "aal1") => `${b64url({ alg: "none" })}.${b64url({ email, aal })}.sig`;
const authCalls = [];
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u === "http://auth.test/auth/v1/user") {
    const auth = new Headers(init?.headers).get("Authorization") ?? "";
    try {
      const { email } = JSON.parse(atob(auth.replace(/^Bearer /, "").split(".")[1].replaceAll("-", "+").replaceAll("_", "/")));
      return new Response(JSON.stringify({ id: `id-${email}`, email, email_confirmed_at: "2026-01-01", factors: authFactors[email] ?? [] }),
                          { status: 200 });
    } catch {
      return new Response("{}", { status: 401 });
    }
  }
  const admin = u.match(/^http:\/\/auth\.test\/auth\/v1\/admin\/users\/id-([^/]+)\/factors(?:\/(.+))?$/);
  if (admin) {
    authCalls.push([init?.method ?? "GET", u]);
    const email = decodeURIComponent(admin[1]);
    if (new Headers(init?.headers).get("Authorization") !== "Bearer service") return new Response("{}", { status: 401 });
    if ((init?.method ?? "GET") === "DELETE") {
      authFactors[email] = (authFactors[email] ?? []).filter((f) => f.id !== admin[2]);
      return new Response("{}", { status: 200 });
    }
    return new Response(JSON.stringify(authFactors[email] ?? []), { status: 200 });
  }
  return realFetch(url, init);
};
const run = path.join(here, ".run");
fs.mkdirSync(run, { recursive: true });
for (const name of ["fleet", "fleet-admin"]) {
  const src = fs.readFileSync(path.join(here, "..", "supabase", "functions", name, "index.ts"), "utf8")
    .replace('import postgres from "npm:postgres@3.4.5";', 'import postgres from "postgres";');
  const file = path.join(run, `${name}.ts`);
  fs.writeFileSync(file, src);
  // policy.ts: the same file in both functions (checked in CI); either copy will do here.
  fs.copyFileSync(path.join(here, "..", "supabase", "functions", name, "policy.ts"), path.join(run, "policy.ts"));
  serving = name;
  await import(file);
}
fs.rmSync(run, { recursive: true, force: true });

const call = async (fn, route, body, token) => {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await handlers[fn](new Request(`https://x.test/functions/v1/${fn}${route}`, {
    method: "POST", headers, body: JSON.stringify(body),
  }));
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null };
};
const admin = (who, body, aal = "aal1") => call("fleet-admin", "", body, tokenFor(who, aal));
const computer = (c, route, body = {}) => call("fleet", route, { device_id: c.device_id, ...body }, c.token);
// A well-formed SHA-512 crypt hash standing in for each password (crypt's alphabet only).
const HASH = (s) => `$6$saltsalt$${s.replace(/[^A-Za-z0-9]/g, ".").padEnd(20, "x")}`;

// --- an organisation, its Owner, two computers ---------------------------------------------
const BOSS = "boss@kulan.test", OWNER = "owner@acme.test", HELPER = "helper@acme.test";
await db`insert into fleet.admins (email, organisation_id) values (${BOSS}, null)`;
let r = await admin("nobody@x.test", { op: "overview" });
ok(r.status === 403, "not an admin: nothing", r);
r = await admin(BOSS, { op: "new-organisation", name: "Acme", owner_email: "Owner@Acme.test" });
ok(r.status === 200 && /^[A-Z2-9]{5}(-[A-Z2-9]{5}){3}$/.test(r.body.recovery_code), "organisation with a recovery code", r);
const org = r.body.id;
let recovery = r.body.recovery_code;
r = await admin(OWNER, { op: "overview" });
ok(r.status === 200 && r.body.organisations.length === 1 && r.body.organisations[0].is_owner === true &&
   r.body.organisations[0].owner_email === OWNER, "the Owner is an admin of it and its Owner", r.body?.organisations);
r = await admin(OWNER, { op: "add-admin", organisation_id: org, email: HELPER });
ok(r.status === 200, "the Owner adds an admin", r);
r = await admin(HELPER, { op: "add-admin", organisation_id: org, email: "friend@x.test" });
ok(r.status === 403, "an admin can't add admins", r);
r = await admin(HELPER, { op: "remove-admin", organisation_id: org, email: OWNER });
ok(r.status === 403, "an admin can't remove the Owner", r);
r = await admin(OWNER, { op: "remove-admin", organisation_id: org, email: OWNER });
ok(r.status === 400, "the Owner stays an admin", r);

r = await admin(OWNER, { op: "new-code", organisation_id: org, hours: 1, uses: 2 });
const code = r.body.code;
const enrol = async (hostname) => (await call("fleet", "/v1/enrol", { code, hostname, os: "Kulan OS", kulan_version: "2.8" })).body;
const A = await enrol("laptop-a"), B = await enrol("laptop-b");
ok(A.device_id && B.device_id && A.token, "two computers joined", { A, B });

// Another organisation's computer, for "only your own computers".
r = await admin(BOSS, { op: "new-organisation", name: "Other" });
const other = r.body.id;
const otherCode = (await admin(BOSS, { op: "new-code", organisation_id: other })).body.code;
const C = (await call("fleet", "/v1/enrol", { code: otherCode, hostname: "elsewhere" })).body;

// --- adding people ----------------------------------------------------------------------
const add = (who, p) => admin(who, { op: "add-person", organisation_id: org, password_hash: HASH("setup"), ...p });
r = await add(HELPER, { username: "amina", full_name: "Amina: Yusuf,\nHR", email: "Amina@Acme.test", role: "employee" });
ok(r.status === 200 && r.body.uid === 70000, "an admin adds an employee, first uid", r);
const amina = r.body.id;
r = await add(HELPER, { username: "omar", role: "admin" });
ok(r.status === 403, "an admin can't make an admin", r);
r = await add(OWNER, { username: "omar", full_name: "Omar Ali", role: "admin", all_computers: false, computers: [B.device_id] });
ok(r.status === 200 && r.body.uid === 70001, "the Owner adds an admin for laptop-b only", r);
const omar = r.body.id;
r = await add(OWNER, { username: "mo", role: "owner" });
ok(r.status === 200, "the Owner's own account", r);
r = await add(OWNER, { username: "mo2", role: "owner" });
ok(r.status === 409, "only one Owner's account", r);
r = await add(OWNER, { username: "amina" });
ok(r.status === 409, "username taken", r);
for (const bad of ["root", "Amina", "1abc", "a:b", "x".repeat(33), ""]) {
  r = await add(OWNER, { username: bad });
  ok(r.status === 400, `bad username refused: ${bad}`, r);
}
r = await add(OWNER, { username: "nohash", password_hash: "plain-password" });
ok(r.status === 400, "a password that isn't a hash is refused", r);
r = await add(OWNER, { username: "nohash", password_hash: "$6$x:0:0:::::" });
ok(r.status === 400, "a hash that could spill into /etc/shadow is refused", r);
r = await add(OWNER, { username: "elsewhere", all_computers: false, computers: [C.device_id] });
ok(r.status === 400, "another organisation's computer refused", r);
r = await admin("nobody@x.test", { op: "add-person", organisation_id: org, username: "z", password_hash: HASH("s") });
ok(r.status === 403, "a stranger adds nobody", r);
r = await admin(BOSS, { op: "people", organisation_id: org });
ok(r.status === 200 && r.body.people.length === 3, "global admin sees the people", r);
const shown = r.body.people.find((p) => p.username === "amina");
ok(shown.full_name === "Amina Yusuf HR" && shown.email === "amina@acme.test", "name cleaned for /etc/passwd, email lower-case", shown);
ok(!JSON.stringify(r.body).includes("password_hash") && !JSON.stringify(r.body).includes("$6$"), "hashes never shown to admins");

// --- the computers get their accounts ------------------------------------------------------
const report = (c, extra = {}) => computer(c, "/v1/report", { hostname: c === A ? "laptop-a" : "laptop-b", ...extra });
r = await report(A);
const namesA = r.body.accounts.map((a) => a.username).sort();
ok(r.status === 200 && JSON.stringify(namesA) === '["amina","mo"]', "laptop-a: amina and mo, not omar", r.body.accounts);
const aA = r.body.accounts.find((a) => a.username === "amina");
ok(aA.uid === 70000 && aA.role === "employee" && aA.enabled === true && aA.must_change === true &&
   aA.password_hash === HASH("setup") && typeof aA.password_changed_at === "number" && !("email" in aA),
   "amina as the computer reads her", aA);
r = await report(B);
ok(r.body.accounts.map((a) => a.username).sort().join() === "amina,mo,omar", "laptop-b: omar too", r.body.accounts);
r = await computer(C, "/v1/report");
ok(r.body.accounts.length === 0, "another organisation's computer gets nobody", r.body);

// --- passwords follow the person -----------------------------------------------------------
r = await report(A, {
  passwords: [{ username: "amina", password_hash: HASH("amina-own"), changed_at: 1 },
              { username: "omar", password_hash: HASH("not-here"), changed_at: 2 },
              { username: "mo", password_hash: "bad:hash", changed_at: 3 },
              { username: "ghost", password_hash: HASH("ghost"), changed_at: 4 }],
  people_status: { amina: "created", mo: "created", "bad name": "x" },
});
const aA2 = r.body.accounts.find((a) => a.username === "amina");
ok(aA2.password_hash === HASH("amina-own") && aA2.must_change === false && aA2.password_changed_at > aA.password_changed_at - 1,
   "amina's new password taken (the same reply carries it)", aA2);
const [omarRow] = await db`select password_hash from fleet.people where id = ${omar}`;
ok(omarRow.password_hash === HASH("setup"), "omar's password not taken from a computer he can't use", omarRow);
const [mo] = await db`select password_hash from fleet.people where username = 'mo'`;
ok(mo.password_hash === HASH("setup"), "a malformed hash isn't taken");
const [stored] = await db`select report from fleet.reports order by received_at desc, id desc limit 1`;
ok(!JSON.stringify(stored.report).includes("passwords") && !JSON.stringify(stored.report).includes("amina-own"),
   "hashes are never kept in the reports", stored.report);
ok(stored.report.people_status?.amina === "created" && !("bad name" in stored.report.people_status), "status kept, cleaned", stored.report);
r = await report(B);
ok(r.body.accounts.find((a) => a.username === "amina").password_hash === HASH("amina-own"), "laptop-b gets amina's new password");
const [log1] = await db`select email, what, detail from fleet.audit where what like '%password%' order by at desc limit 1`;
ok(log1?.email === "amina" && log1.detail.computer === "laptop-a", "the password change is in the activity log", log1);
r = await admin(HELPER, { op: "people", organisation_id: org });
const onA = r.body.people.find((p) => p.username === "amina").on.find((o) => o.hostname === "laptop-a");
ok(onA?.status === "created", "admins see how each account is doing on each computer", r.body.people);
r = await report(A, { passwords: [{ username: "amina", password_hash: HASH("amina-own") }] });
const [{ count: logs }] = await db`select count(*)::int from fleet.audit where what like '%password%'`;
ok(logs === 1, "the same password again changes nothing", logs);

// --- roles and computers --------------------------------------------------------------------
r = await admin(HELPER, { op: "edit-person", person_id: amina, role: "admin" });
ok(r.status === 403, "an admin can't promote", r);
r = await admin(HELPER, { op: "edit-person", person_id: amina, full_name: "Amina Y. Yusuf", all_computers: false, computers: [A.device_id] });
ok(r.status === 200, "an admin renames and picks computers", r);
r = await report(B);
ok(!r.body.accounts.some((a) => a.username === "amina"), "amina no longer on laptop-b (it removes her account, keeping files)");
r = await admin(HELPER, { op: "edit-person", person_id: amina, computers: [C.device_id] });
ok(r.status === 400, "not another organisation's computer", r);
r = await admin(OWNER, { op: "edit-person", person_id: amina, role: "admin", all_computers: true });
ok(r.status === 200, "the Owner promotes amina", r);
r = await report(B);
ok(r.body.accounts.find((a) => a.username === "amina")?.role === "admin", "amina is an admin on laptop-b", r.body.accounts);
r = await admin(OWNER, { op: "edit-person", person_id: amina, role: "owner" });
ok(r.status === 409, "still only one Owner's account", r);
r = await admin(HELPER, { op: "set-person-enabled", person_id: amina, enabled: false });
ok(r.status === 403, "an admin can't disable an admin", r);
r = await admin(OWNER, { op: "edit-person", person_id: amina, role: "employee" });
ok(r.status === 200, "the Owner makes amina an employee again", r);

// --- disable, setup code, remove -----------------------------------------------------------
r = await admin(HELPER, { op: "set-person-enabled", person_id: amina, enabled: false });
ok(r.status === 200, "an admin disables an employee", r);
r = await report(A, { passwords: [{ username: "amina", password_hash: HASH("while-disabled") }] });
const aD = r.body.accounts.find((a) => a.username === "amina");
ok(aD.enabled === false && aD.password_hash === HASH("amina-own"), "disabled: sent as disabled; her new password isn't taken", aD);
r = await admin(HELPER, { op: "new-setup-code", person_id: amina, password_hash: HASH("code-2") });
ok(r.status === 200, "a new setup code", r);
r = await report(A);
const aC = r.body.accounts.find((a) => a.username === "amina");
ok(aC.password_hash === HASH("code-2") && aC.must_change === true && aC.password_changed_at > aD.password_changed_at,
   "the new setup code reaches the computer, change at first sign-in", aC);
r = await admin(HELPER, { op: "set-person-enabled", person_id: amina, enabled: true });
r = await admin(HELPER, { op: "remove-person", person_id: amina, remove_files: false, confirm: "Amina" });
ok(r.status === 400, "removing needs their username typed", r);
r = await admin(HELPER, { op: "remove-person", person_id: omar, confirm: "omar" });
ok(r.status === 403, "an admin can't remove an admin", r);
r = await admin(HELPER, { op: "remove-person", person_id: amina, remove_files: true, confirm: "amina" });
ok(r.status === 200, "an admin removes amina, files deleted", r);
r = await report(A);
const aR = r.body.accounts.find((a) => a.username === "amina");
ok(JSON.stringify(aR) === JSON.stringify({ username: "amina", uid: 70000, removed: true, remove_files: true }),
   "removed: the computer is told, nothing else about her", aR);
r = await admin(HELPER, { op: "people", organisation_id: org });
ok(!r.body.people.some((p) => p.username === "amina"), "not in the People list");
r = await admin(HELPER, { op: "edit-person", person_id: amina, full_name: "x" });
ok(r.status === 404, "a removed person can't be changed", r);
r = await add(HELPER, { username: "amina" });
ok(r.status === 409 && r.body.error.includes("30 days"), "her username isn't reused for 30 days", r);
r = await add(HELPER, { username: "hodan" });
ok(r.body.uid === 70003, "uids keep going up (not reused)", r);
await db`update fleet.people set removed_at = now() - interval '31 days' where id = ${amina}`;
await db`select fleet.prune_people()`;
r = await report(A);
ok(!r.body.accounts.some((a) => a.username === "amina"), "forgotten after 30 days");

// --- actions -------------------------------------------------------------------------------
for (const type of ["shutdown", "lost-mode-on", "lost-mode-off"]) {
  r = await admin(HELPER, { op: "action", device_id: A.device_id, type });
  ok(r.status === 200, `action ${type} accepted`, r);
}
r = await report(A);
ok(r.body.actions.map((a) => a.type).join() === "shutdown,lost-mode-on,lost-mode-off", "the computer gets them in order", r.body.actions);
await report(A, { state: { lost_mode: true, firewall: true } });
r = await admin(HELPER, { op: "overview" });
ok(r.body.alerts.some((x) => x.kind === "lost" && x.hostname === "laptop-a"), "a computer in lost mode is an alert", r.body.alerts);
r = await admin(HELPER, { op: "action", device_id: A.device_id, type: "format-disk" });
ok(r.status === 400, "unknown action refused", r);

// --- the Owner's recovery code -------------------------------------------------------------
const NEWOWNER = "owner.new@acme.test";
r = await admin(NEWOWNER, { op: "recover-owner", recovery_code: "AAAAA-BBBBB-CCCCC-DDDDD" });
ok(r.status === 403, "a wrong recovery code", r);
r = await admin(NEWOWNER, { op: "recover-owner", recovery_code: recovery.toLowerCase().replaceAll("-", " ") });
ok(r.status === 200 && r.body.recovery_code && r.body.recovery_code !== recovery && r.body.organisation?.id === org &&
   r.body.organisation.name === "Acme", "the recovery code alone moves ownership (typed loosely)", r);
r = await admin(NEWOWNER, { op: "admins", organisation_id: org });
ok(r.status === 200 && r.body.owner === NEWOWNER && r.body.is_owner && r.body.admins.some((a) => a.email === NEWOWNER),
   "the new email is the Owner and an admin", r.body);
r = await admin(OWNER, { op: "add-admin", organisation_id: org, email: "late@x.test" });
ok(r.status === 403, "the old email is no longer the Owner", r);
r = await admin("thief@x.test", { op: "recover-owner", recovery_code: recovery });
ok(r.status === 403, "an old recovery code doesn't work twice", r);
r = await admin(HELPER, { op: "set-owner", organisation_id: org, email: HELPER });
ok(r.status === 403, "only admins for all organisations set the Owner", r);
r = await admin(BOSS, { op: "set-owner", organisation_id: other, email: "o@other.test" });
ok(r.status === 200 && r.body.recovery_code, "the boss sets an Owner for an older organisation", r);

// --- groups and policies (2.8b) -----------------------------------------------------------
{
  const policyOf = async (c) => (await report(c)).body.policy;
  const deb = (id) => ({ source: "debian", id }), flat = (id) => ({ source: "flatpak", id });
  r = await admin(HELPER, { op: "set-policy", organisation_id: org, policy: { blocked_apps: [deb("kulan-base")] } });
  ok(r.status === 400, "Kulan OS itself can't be blocked", r);
  r = await admin(HELPER, { op: "set-policy", organisation_id: org, policy: { required_apps: [deb("not a package")] } });
  ok(r.status === 400, "an app name that isn't one is refused", r);
  r = await admin(HELPER, { op: "set-policy", organisation_id: org, policy: {
    firewall: true, screen_lock_minutes: 10, usb: "approved", required_apps: [deb("libreoffice"), deb("vlc")] } });
  ok(r.status === 200, "organisation policy with USB approved-only and required apps", r);
  r = await admin(HELPER, { op: "add-group", organisation_id: org, name: "Finance", policy: {
    screen_lock_minutes: 5, usb: "block", required_apps: [flat("org.gnome.Calculator")],
    blocked_apps: [deb("vlc"), flat("com.valvesoftware.Steam")] } });
  ok(r.status === 200 && r.body.id, "a group with its own policy", r);
  const finance = r.body.id;
  r = await admin(HELPER, { op: "add-group", organisation_id: org, name: "finance" });
  ok(r.status === 409, "group names are unique (whatever the capitals)", r);
  r = await admin(HELPER, { op: "edit-group", group_id: finance, devices: [B.device_id] });
  ok(r.status === 200, "laptop-b put in Finance", r);
  r = await admin(HELPER, { op: "edit-group", group_id: finance, devices: [C.device_id] });
  ok(r.status === 400, "another organisation's computer can't join a group", r);

  let pb = await policyOf(B);
  ok(pb.screen_lock_minutes === 5 && pb.usb === "block" && pb.firewall === true, "laptop-b: the stricter rules win", pb);
  ok(JSON.stringify(pb.required_apps) === JSON.stringify([deb("libreoffice"), flat("org.gnome.Calculator")]),
     "laptop-b: required apps joined, vlc dropped as it's blocked", pb.required_apps);
  ok(pb.blocked_apps.length === 2 && !("usb_drives" in pb), "laptop-b: blocked apps; no drive list when USB is blocked", pb);
  let pa = await policyOf(A);
  ok(pa.screen_lock_minutes === 10 && pa.usb === "approved" && !pa.blocked_apps && Array.isArray(pa.usb_drives) && pa.usb_drives.length === 0,
     "laptop-a: only the organisation's policy", pa);
  r = await admin(HELPER, { op: "device-policy", device_id: B.device_id });
  ok(r.body.conflicts.join() === "debian:vlc" && r.body.groups[0].name === "Finance", "conflicts and groups shown for a computer", r.body);

  // USB drives: seen on laptop-a, approved, then sent to the computers that allow approved drives.
  await report(A, { usb_seen: [{ vendor_id: "0781", product_id: "5581", serial: "4C530001", name: "SanDisk Ultra" },
                               { vendor_id: "zz", product_id: "1", serial: "x" }] });
  r = await admin(HELPER, { op: "drives", organisation_id: org });
  ok(r.body.seen.length === 1 && r.body.seen[0].serial === "4C530001" && r.body.seen[0].computers[0] === "laptop-a",
     "drives a computer has seen are listed (malformed ones dropped)", r.body);
  r = await admin(HELPER, { op: "approve-drive", organisation_id: org, name: "Finance backup drive", vendor_id: "0781", product_id: "5581", serial: "4C530001" });
  ok(r.status === 200, "a drive approved", r);
  r = await admin(HELPER, { op: "drives", organisation_id: org });
  ok(r.body.approved.length === 1 && r.body.seen.length === 0, "approved, no longer in the seen list", r.body);
  pa = await policyOf(A);
  ok(pa.usb_drives?.[0]?.serial === "4C530001", "laptop-a gets the approved drive", pa.usb_drives);

  // People allowed on a group's computers.
  r = await add(HELPER, { username: "farah", all_computers: false, groups: [finance] });
  ok(r.status === 200, "a person allowed on Finance's computers", r);
  const farah = r.body.id;
  ok((await report(B)).body.accounts.some((x) => x.username === "farah"), "farah's account on laptop-b (in Finance)");
  ok(!(await report(A)).body.accounts.some((x) => x.username === "farah"), "but not on laptop-a");
  r = await admin(HELPER, { op: "people", organisation_id: org });
  const fp = r.body.people.find((x) => x.username === "farah");
  ok(fp.on.map((o) => o.hostname).join() === "laptop-b" && fp.groups.length === 1, "the People list shows where through groups", fp);
  r = await admin(HELPER, { op: "edit-group", group_id: finance, devices: [A.device_id, B.device_id] });
  ok((await report(A)).body.accounts.some((x) => x.username === "farah"), "a computer added to the group gets her account too");

  // Asking a whole group.
  r = await admin(HELPER, { op: "group-action", group_id: finance, type: "install-app", payload: flat("org.gnome.Maps") });
  ok(r.status === 200 && r.body.count === 2, "install an app once on the group's computers", r);
  const acts = (await report(B)).body.actions;
  ok(acts.some((x) => x.type === "install-app" && x.payload?.id === "org.gnome.Maps"), "laptop-b gets it, with the app", acts);
  await report(A);
  r = await admin(HELPER, { op: "group-action", group_id: finance, type: "erase" });
  ok(r.status === 400, "erase can't be asked of a group", r);
  r = await admin(HELPER, { op: "group-action", group_id: finance, type: "install-app", payload: deb("bad name!") });
  ok(r.status === 400, "install-app needs a real app name", r);
  r = await admin(HELPER, { op: "action", device_id: A.device_id, type: "install-app", payload: deb("gimp") });
  ok(r.status === 200, "install-app on one computer", r);
  ok((await report(A)).body.actions.some((x) => x.payload?.id === "gimp"), "with its app");

  // Policy alerts from a computer.
  await report(A, { policy_status: { "app:install:debian:vlc": "couldn't install: not found", firewall: "met" } });
  r = await admin(HELPER, { op: "overview" });
  ok(r.body.alerts.some((x) => x.text.startsWith("vlc isn't installed as the policy says")), "an app the policy needs shows as an alert", r.body.alerts);

  // Removing the group: laptop-b back to the organisation's policy.
  r = await admin(HELPER, { op: "remove-group", group_id: finance, confirm: "finance" });
  ok(r.status === 400, "removing a group needs its exact name", r);
  r = await admin(HELPER, { op: "remove-group", group_id: finance, confirm: "Finance" });
  ok(r.status === 200, "group removed", r);
  pb = await policyOf(B);
  ok(pb.screen_lock_minutes === 10 && pb.usb === "approved" && !pb.blocked_apps, "laptop-b back to the organisation's policy", pb);
  ok(!(await report(B)).body.accounts.some((x) => x.username === "farah"), "and farah, allowed only through the group, is no longer on it");
  r = await admin(HELPER, { op: "log", organisation_id: org });
  const groupLog = r.body.entries.map((e) => e.what);
  for (const want of ["added group Finance", "changed group Finance", "approved USB drive Finance backup drive",
                      "asked group Finance: install-app", "removed group Finance"]) {
    ok(groupLog.includes(want), `in the activity log: ${want}`, groupLog);
  }
  void farah;
}

// --- security and activity (2.8c) ---------------------------------------------------------
{
  // Who signs in where: kept from the computers' reports, malformed entries dropped.
  const t = Math.floor(Date.now() / 1000);
  await report(A, { sign_ins: [{ username: "mo", last: t - 3600, now: true }, { username: "Bad:Name", last: t },
                               { username: "local", last: "yesterday" }, { username: "mo", last: 1 }] });
  await report(B, { sign_ins: [{ username: "omar", last: t - 86400 * 40, now: false }, { username: "mo", last: t - 60 }],
                    updates: { waiting: 3, security: 2, restart_required: true },
                    state: { encrypted: false, firewall: false }, compliance: { passed: 8, total: 10 } });
  r = await admin(HELPER, { op: "overview" });
  const devA = r.body.devices.find((d) => d.hostname === "laptop-a");
  ok(JSON.stringify(devA.sign_ins.map((x) => [x.username, x.last, x.now])) === JSON.stringify([["mo", t - 3600, true], ["local", null, false]]),
     "a computer's sign-ins: well-formed, once each, odd times dropped", devA.sign_ins);
  r = await admin(HELPER, { op: "people", organisation_id: org });
  const mo = r.body.people.find((p) => p.username === "mo");
  ok(mo.signed_in.length === 2 && mo.signed_in[0].hostname === "laptop-b" && mo.signed_in[1].now === true,
     "a person's sign-ins, latest first, across computers", mo.signed_in);

  // The weekly report.
  r = await admin(HELPER, { op: "weekly-report", organisation_id: other });
  ok(r.status === 404, "not another organisation's report", r);
  r = await admin(HELPER, { op: "weekly-report", organisation_id: org });
  const w = r.body;
  ok(r.status === 200 && /^\d{4}-\d{2}-\d{2}$/.test(w.week) && new Date(w.week + "T00:00:00Z").getUTCDay() === 1,
     "the week starts on a Monday", w.week);
  ok(w.summary.computers.total === 2 && w.summary.computers.reported_this_week === 2, "computers counted", w.summary.computers);
  ok(w.summary.computers.security_waiting[0]?.hostname === "laptop-b" && w.summary.computers.security_waiting[0].count === 2 &&
     w.summary.computers.restart_needed.join() === "laptop-b" && w.summary.computers.not_encrypted.join() === "laptop-b" &&
     w.summary.computers.firewall_off.join() === "laptop-b" && w.summary.computers.compliance_failing[0]?.failing === 2,
     "what needs doing, by computer", w.summary.computers);
  const peopleNow = (await admin(HELPER, { op: "people", organisation_id: org })).body.people.length;
  ok(w.summary.people.admins.sort().join() === "mo,omar" && w.summary.people.total === peopleNow, "people and who has admin rights", w.summary.people);
  ok(w.summary.activity.changes > 0 && w.summary.activity.by_kind.length > 0, "the week's changes", w.summary.activity);
  ok(w.previous === null, "nothing to compare with in the first week", w.previous);
  const lastWeek = new Date(Date.parse(w.week) - 7 * 86400_000).toISOString().slice(0, 10);
  await db`insert into fleet.weekly_reports (organisation_id, week, summary)
           values (${org}, ${lastWeek}, ${db.json({ computers: { total: 1, security_waiting: [] } })})`;
  r = await admin(HELPER, { op: "weekly-report", organisation_id: org });
  ok(r.body.previous?.week === lastWeek && r.body.previous.summary.computers.total === 1, "last week's snapshot to compare with", r.body.previous);
  const [{ n }] = await db`select count(*)::int as n from fleet.weekly_reports where organisation_id = ${org}`;
  ok(n === 2, "one snapshot per week, however often it's opened", n);

  // A bigger organisation: lists name 50, the counts are right, a big report still opens.
  const items = Object.fromEntries(Array.from({ length: 40 }, (_, i) => [`app:install:flatpak:org.example.App${i}`, "couldn't install: not found"]));
  await db`insert into fleet.devices (organisation_id, hostname, last_seen, last_report)
           select ${other}, 'pc-' || g, now(), ${db.json({ updates: { waiting: 1, security: 1, restart_required: true },
                                                         state: { firewall: false }, policy_status: items })}
           from generate_series(1, 60) g`;
  r = await admin(BOSS, { op: "weekly-report", organisation_id: other });
  const big = r.body.summary?.computers;
  ok(r.status === 200 && big.counts.restart_needed === 60 && big.restart_needed.length === 50 &&
     big.counts.security_waiting === 60 && big.counts.firewall_off === 60 && big.counts.policy_not_met === 60,
     "60 computers: 50 named, 60 counted", big?.counts);
  ok(big.policy_not_met[0].items.length === 10 && big.policy_not_met[0].more === 30, "a computer's unmet items: 10 named, the rest counted", big.policy_not_met[0]);
  const [{ kept }] = await db`select count(*)::int as kept from fleet.weekly_reports where organisation_id = ${other}`;
  ok(kept === 1, "and its snapshot is kept", kept);
  r = await admin(BOSS, { op: "log", organisation_id: other, from: "2026-02-30" });
  ok(r.status === 400, "a date that doesn't exist is refused, not a server error", r);

  // The admin-rights review.
  r = await admin(HELPER, { op: "access-review", organisation_id: org });
  const rv = r.body;
  ok(r.status === 200 && rv.people.map((p) => p.username).join() === "mo,omar" && rv.is_owner === false,
     "who has administrator rights on the computers", rv.people);
  const omarRow = rv.people.find((p) => p.username === "omar");
  ok(omarRow.where.join() === "laptop-b" && omarRow.signed_in[0]?.last === t - 86400 * 40, "where, and when they last signed in", omarRow);
  ok(rv.admins.some((a) => a.email === NEWOWNER && a.owner) && rv.admins.some((a) => a.email === HELPER) &&
     rv.all_organisations_admins.some((a) => a.email === BOSS), "Kulan Fleet admins, the Owner marked", rv.admins);
  ok(rv.last_review === null && rv.due === false, "a new organisation: no review due yet", rv);
  r = await admin(NEWOWNER, { op: "overview" });
  ok(!r.body.alerts.some((x) => x.kind === "review" && x.organisation_id === org), "no review alert yet");
  await db`update fleet.organisations set created_at = now() - interval '100 days' where id = ${org}`;
  r = await admin(NEWOWNER, { op: "overview" });
  const alert = r.body.alerts.find((x) => x.kind === "review" && x.organisation_id === org);
  ok(r.body.alerts.findIndex((x) => x.kind === "review") < r.body.alerts.findIndex((x) => x.kind !== "review"),
     "the review alert comes before computers' alerts (lists show the first 30)");
  ok(alert && alert.device_id === null && alert.title === "Admin rights" && alert.text.startsWith("No admin-rights review yet"),
     "after 90 days without a review: an alert", r.body.alerts);
  r = await admin(HELPER, { op: "review-access", organisation_id: org });
  ok(r.status === 403, "only the Owner reviews admin rights", r);
  const moId = rv.people.find((p) => p.username === "mo").id;
  r = await admin(NEWOWNER, { op: "review-access", organisation_id: org, remove_people: [moId] });
  ok(r.status === 400, "the Owner's own account keeps its rights", r);
  r = await admin(NEWOWNER, { op: "review-access", organisation_id: org, remove_admins: [NEWOWNER] });
  ok(r.status === 400, "the Owner stays a Kulan Fleet admin", r);
  r = await admin(NEWOWNER, { op: "review-access", organisation_id: org, remove_admins: ["stranger@x.test"] });
  ok(r.status === 400, "only admins on the list", r);
  r = await admin(NEWOWNER, { op: "review-access", organisation_id: org, remove_people: [omarRow.id], remove_admins: [OWNER.toUpperCase()] });
  ok(r.status === 200 && r.body.removed === 2 && r.body.kept === 3, "omar's admin role and the old Owner's access taken away", r);
  ok((await report(B)).body.accounts.find((x) => x.username === "omar")?.role === "employee", "omar is an employee on laptop-b now");
  r = await admin(OWNER, { op: "overview" });
  ok(r.status === 403, "the old Owner's email no longer gets in", r);
  r = await admin(NEWOWNER, { op: "access-review", organisation_id: org });
  ok(r.body.last_review?.reviewed_by === NEWOWNER && r.body.last_review.removed === 2 && r.body.due === false, "the review is recorded", r.body.last_review);
  r = await admin(NEWOWNER, { op: "overview" });
  ok(!r.body.alerts.some((x) => x.kind === "review" && x.organisation_id === org), "the alert goes after a review");

  // The activity log, filtered and in pages, for exporting.
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "reviewed admin" });
  ok(r.body.entries.length === 1 && r.body.entries[0].what === "reviewed admin rights (kept 3, removed 2)" &&
     r.body.entries[0].detail.admin_role_taken.join() === "omar", "in the log, with what was taken away", r.body.entries);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, who: "HELPER@", limit: 3 });
  ok(r.body.entries.length === 3 && r.body.entries.every((e) => e.email === HELPER) && r.body.more === true, "by who, a page at a time", r.body);
  const next = await admin(NEWOWNER, { op: "log", organisation_id: org, who: "helper@", limit: 3, before: r.body.entries[2].id });
  ok(next.body.entries.length === 3 && next.body.entries[0].id < r.body.entries[2].id, "the next page", next.body);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "laptop-a" });
  ok(r.body.entries.length > 0 && r.body.entries.every((e) => e.hostname === "laptop-a" || e.what.includes("laptop-a")), "by computer", r.body.entries.length);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, from: "2000-01-01", to: "2000-12-31" });
  ok(r.body.entries.length === 0, "by date", r.body);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "100%_" });
  ok(r.status === 200 && r.body.entries.length === 0, "search text taken literally", r.body);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, limit: 50000 });
  ok(r.status === 200 && r.body.entries.length <= 10000, "at most 10,000 a page", r.body.entries.length);
}

// --- stronger sign-in (2.8d) ------------------------------------------------------------
{
  const KEY = "kh0123456789abcdefKH,pk0123456789abcdefPK+/=,es256,+presence";
  const SECRET = "3132333435363738393031323334353637383930";
  const id = (n) => `00000000-0000-4000-8000-00000000000${n}`;
  r = await admin(HELPER, { op: "set-policy", organisation_id: org, policy: { two_step: "sometimes" } });
  ok(r.status === 400, "two_step is admins or everyone", r);
  r = await admin(HELPER, { op: "set-policy", organisation_id: org, policy: { firewall: true, two_step: "admins" } });
  ok(r.status === 200 && r.body.policy.two_step === "admins", "two-step sign-in for admins", r);

  // mo sets up a security key and a phone code on laptop-a; junk and other people's are ignored.
  await report(A, { factors: [
    { username: "mo", id: id(1), kind: "key", label: "YubiKey 5", data: KEY },
    { username: "mo", id: id(2), kind: "code", label: "Phone", data: SECRET },
    { username: "omar", id: id(3), kind: "code", label: "x", data: SECRET },      // omar can't use laptop-a
    { username: "mo", id: id(4), kind: "key", label: "x", data: "not a credential" },
    { username: "mo", id: "not-a-uuid", kind: "code", label: "x", data: SECRET },
    { username: "mo", id: id(5), kind: "sms", label: "x", data: SECRET }] });
  const stored = await db`select id, kind, data from fleet.person_factors order by id`;
  ok(stored.length === 2 && stored[0].data === KEY && stored[1].data.startsWith("v1:") && !stored[1].data.includes(SECRET),
     "a key kept as it is (a public key), a phone code's secret sealed", stored);
  const lastA = await db`select last_report from fleet.devices where id = ${A.device_id}`;
  ok(!JSON.stringify(lastA[0].last_report).includes(SECRET), "never kept in the computer's report");

  // Every computer mo may use gets them; who must use them follows the policy.
  r = await report(B);
  const moB = r.body.accounts.find((x) => x.username === "mo"), omarB = r.body.accounts.find((x) => x.username === "omar");
  ok(moB.two_step.required === true && moB.two_step.factors.length === 2 &&
     moB.two_step.factors.find((f) => f.kind === "code")?.data === SECRET &&
     moB.two_step.factors.find((f) => f.kind === "key")?.data === KEY, "laptop-b gets mo's key and code (opened)", moB.two_step);
  ok(omarB.two_step.required === false && omarB.two_step.factors.length === 0, "omar, an employee, isn't asked (admins only)", omarB.two_step);
  await admin(HELPER, { op: "set-policy", organisation_id: org, policy: { firewall: true, two_step: "everyone" } });
  ok((await report(B)).body.accounts.find((x) => x.username === "omar").two_step.required === true, "everyone: omar too");

  // Admins see what's set up, never the secrets.
  r = await admin(HELPER, { op: "people", organisation_id: org });
  const moP = r.body.people.find((x) => x.username === "mo");
  ok(moP.two_step.length === 2 && moP.two_step[0].label === "YubiKey 5" && moP.two_step[0].created_on === "laptop-a" &&
     !JSON.stringify(r.body).includes(SECRET) && !JSON.stringify(r.body).includes("v1:"), "people: what's set up, no secrets", moP.two_step);
  r = await admin(HELPER, { op: "weekly-report", organisation_id: org });
  ok(r.body.summary.people.two_step === "everyone" && r.body.summary.people.two_step_missing.includes("omar") &&
     !r.body.summary.people.two_step_missing.includes("mo"), "the weekly report lists who hasn't set it up", r.body.summary.people);

  // mo removes the phone code on laptop-b; an admin resets two-step (a lost key).
  await report(B, { factors_removed: [{ username: "mo", id: id(2) }, { username: "omar", id: id(1) }] });
  ok((await db`select id from fleet.person_factors`).map((x) => x.id).join() === id(1), "the code removed; someone else's can't be", 0);
  r = await admin(HELPER, { op: "reset-two-step", person_id: moP.id });
  ok(r.status === 403, "an admin can't reset the Owner's account's two-step", r);
  r = await admin(NEWOWNER, { op: "reset-two-step", person_id: moP.id });
  ok(r.status === 200 && r.body.removed === 1, "the Owner resets it", r);
  ok((await report(A)).body.accounts.find((x) => x.username === "mo").two_step.factors.length === 0, "and the computers drop it");

  // Kulan Fleet admins: an authenticator app code once set up; required by the Owner.
  authFactors[HELPER] = [{ id: "f1", factor_type: "totp", status: "verified" }];
  r = await admin(HELPER, { op: "overview" });
  ok(r.status === 403 && r.body.two_step === "code", "set up: the code is needed (an email code alone isn't enough)", r);
  r = await admin(HELPER, { op: "overview" }, "aal2");
  ok(r.status === 200 && r.body.me.aal === "aal2" && r.body.me.authenticator === true, "with the code, in", r.body?.me);
  r = await admin(HELPER, { op: "set-admin-two-step", organisation_id: org, required: true }, "aal2");
  ok(r.status === 403, "only the Owner requires it", r);
  r = await admin(NEWOWNER, { op: "set-admin-two-step", organisation_id: org, required: true });
  ok(r.status === 400 && r.body.two_step === "set-up", "the Owner sets theirs up first (no locking themselves out)", r);
  authFactors[NEWOWNER] = [{ id: "f2", factor_type: "totp", status: "verified" }];
  r = await admin(NEWOWNER, { op: "set-admin-two-step", organisation_id: org, required: true }, "aal2");
  ok(r.status === 200, "the Owner requires it for the organisation's admins", r);
  await admin(NEWOWNER, { op: "add-admin", organisation_id: org, email: "new.admin@acme.test" }, "aal2");
  r = await admin("new.admin@acme.test", { op: "overview" });
  ok(r.status === 403 && r.body.two_step === "set-up", "a new admin is asked to set one up", r);
  r = await admin(BOSS, { op: "overview" });
  ok(r.status === 200 && r.body.organisations.find((o) => o.id === org).admin_two_step === true, "Kulan's own admins aren't locked out", r.status);

  // A lost phone: the Owner resets that admin's authenticator app.
  await db`insert into auth.users (id, email) values (${"id-" + HELPER}, ${HELPER})`;
  r = await admin(HELPER, { op: "reset-admin-two-step", organisation_id: org, email: NEWOWNER }, "aal2");
  ok(r.status === 403, "an admin can't reset another's", r);
  r = await admin(NEWOWNER, { op: "reset-admin-two-step", organisation_id: org, email: "stranger@x.test" }, "aal2");
  ok(r.status === 400, "only the organisation's admins", r);
  r = await admin(NEWOWNER, { op: "reset-admin-two-step", organisation_id: org, email: HELPER }, "aal2");
  ok(r.status === 200 && r.body.removed === 1 && (authFactors[HELPER] ?? []).length === 0 &&
     authCalls.some(([m]) => m === "DELETE"), "the Owner resets it through Supabase Auth", r);
  r = await admin(HELPER, { op: "overview" });
  ok(r.status === 403 && r.body.two_step === "set-up", "they set it up again", r);
  r = await admin(NEWOWNER, { op: "set-admin-two-step", organisation_id: org, required: false }, "aal2");
  ok(r.status === 200, "the Owner stops requiring it", r);
  r = await admin(HELPER, { op: "overview" });
  ok(r.status === 200, "no code set up, not required: in with the email code", r.status);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "authenticator" }, "aal2");
  ok(r.body.entries.map((e) => e.what).includes(`reset ${HELPER}'s authenticator app`) &&
     r.body.entries.map((e) => e.what).includes("required an authenticator app for Kulan Fleet admins"), "in the log", r.body.entries);
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "sign" }, "aal2");
  ok(r.body.entries.some((e) => e.what === "set up a security key for signing in" && e.email === "mo") &&
     r.body.entries.some((e) => e.what === "removed a second step for signing in"), "the person's own changes too", r.body.entries.map((e) => e.what));
  delete authFactors[NEWOWNER];
}

// --- the activity log ------------------------------------------------------------------------
r = await admin(NEWOWNER, { op: "log", organisation_id: org });
const whats = r.body.entries.map((e) => `${e.email}: ${e.what}`);
for (const want of [`${HELPER}: added amina`, `${OWNER}: added omar`, `${HELPER}: disabled amina`,
                    `${HELPER}: gave amina a new setup code`, `${HELPER}: removed amina`, `${OWNER}: added admin ${HELPER}`,
                    `${NEWOWNER}: became the Owner with the recovery code`]) {
  ok(whats.includes(want), `in the activity log: ${want}`, whats);
}

await db.end();
if (failures) {
  console.error(`${failures} fleet function check(s) failed`);
  process.exit(1);
}
console.log("fleet functions test passed");
process.exit(0);
