// The fleet and fleet-admin Edge Functions against a real Postgres, with every
// migration applied: People (accounts, passwords both ways, roles, disable,
// remove, setup codes), the Owner and admins, the new actions.
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
  create schema if not exists extensions; create extension if not exists pgcrypto schema extensions;`);
// (Supabase keeps pgcrypto in the "extensions" schema.)
const migrations = path.join(here, "..", "supabase", "migrations");
for (const f of fs.readdirSync(migrations).filter((f) => f.endsWith(".sql")).sort()) {
  await db.unsafe(fs.readFileSync(path.join(migrations, f), "utf8"));
}
// The migrations run again change nothing (if not exists / or replace / drop if exists).
await db.unsafe(fs.readFileSync(path.join(migrations, "20261024120000_fleet_people.sql"), "utf8"));

// --- the functions, with Deno and Supabase Auth stood in for ------------------------------
const handlers = {};
let serving = "";
globalThis.Deno = {
  env: { get: (k) => ({ SUPABASE_DB_URL: DB, SUPABASE_URL: "http://auth.test", SUPABASE_ANON_KEY: "anon" })[k] },
  serve: (h) => { handlers[serving] = h; },
};
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  if (String(url) === "http://auth.test/auth/v1/user") {
    const auth = new Headers(init?.headers).get("Authorization") ?? "";
    const m = auth.match(/^Bearer user:(.+)$/);
    return m ? new Response(JSON.stringify({ email: m[1], email_confirmed_at: "2026-01-01" }), { status: 200 })
      : new Response("{}", { status: 401 });
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
const admin = (who, body) => call("fleet-admin", "", body, `user:${who}`);
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
ok(r.status === 200 && r.body.uid === 61000, "an admin adds an employee, first uid", r);
const amina = r.body.id;
r = await add(HELPER, { username: "omar", role: "admin" });
ok(r.status === 403, "an admin can't make an admin", r);
r = await add(OWNER, { username: "omar", full_name: "Omar Ali", role: "admin", all_computers: false, computers: [B.device_id] });
ok(r.status === 200 && r.body.uid === 61001, "the Owner adds an admin for laptop-b only", r);
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
ok(aA.uid === 61000 && aA.role === "employee" && aA.enabled === true && aA.must_change === true &&
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
ok(JSON.stringify(aR) === JSON.stringify({ username: "amina", uid: 61000, removed: true, remove_files: true }),
   "removed: the computer is told, nothing else about her", aR);
r = await admin(HELPER, { op: "people", organisation_id: org });
ok(!r.body.people.some((p) => p.username === "amina"), "not in the People list");
r = await admin(HELPER, { op: "edit-person", person_id: amina, full_name: "x" });
ok(r.status === 404, "a removed person can't be changed", r);
r = await add(HELPER, { username: "amina" });
ok(r.status === 409 && r.body.error.includes("30 days"), "her username isn't reused for 30 days", r);
r = await add(HELPER, { username: "hodan" });
ok(r.body.uid === 61003, "uids keep going up (not reused)", r);
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
r = await admin(HELPER, { op: "action", device_id: A.device_id, type: "format-disk" });
ok(r.status === 400, "unknown action refused", r);

// --- the Owner's recovery code -------------------------------------------------------------
const NEWOWNER = "owner.new@acme.test";
r = await admin(NEWOWNER, { op: "recover-owner", organisation_id: org, recovery_code: "AAAAA-BBBBB-CCCCC-DDDDD" });
ok(r.status === 403, "a wrong recovery code", r);
r = await admin(NEWOWNER, { op: "recover-owner", organisation_id: org, recovery_code: recovery.toLowerCase().replaceAll("-", " ") });
ok(r.status === 200 && r.body.recovery_code && r.body.recovery_code !== recovery, "the recovery code moves ownership", r);
r = await admin(NEWOWNER, { op: "admins", organisation_id: org });
ok(r.status === 200 && r.body.owner === NEWOWNER && r.body.is_owner && r.body.admins.some((a) => a.email === NEWOWNER),
   "the new email is the Owner and an admin", r.body);
r = await admin(OWNER, { op: "add-admin", organisation_id: org, email: "late@x.test" });
ok(r.status === 403, "the old email is no longer the Owner", r);
r = await admin("thief@x.test", { op: "recover-owner", organisation_id: org, recovery_code: recovery });
ok(r.status === 403, "an old recovery code doesn't work twice", r);
r = await admin(HELPER, { op: "set-owner", organisation_id: org, email: HELPER });
ok(r.status === 403, "only admins for all organisations set the Owner", r);
r = await admin(BOSS, { op: "set-owner", organisation_id: other, email: "o@other.test" });
ok(r.status === 200 && r.body.recovery_code, "the boss sets an Owner for an older organisation", r);

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
