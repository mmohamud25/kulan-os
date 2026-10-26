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
await db.unsafe(`drop schema if exists fleet cascade; drop schema if exists stats cascade; drop schema if exists auth cascade;
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
  env: { get: (k) => env[k] },
  serve: (h) => { handlers[serving] = h; },
};
const realFetch = globalThis.fetch;
// Stand-in Supabase Auth: a token is an unsigned JWT with the email and the
// session's assurance level; authFactors holds each email's authenticator apps.
const authFactors = {};
const b64url = (o) => btoa(JSON.stringify(o)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const tokenFor = (email, aal = "aal1") => `${b64url({ alg: "none" })}.${b64url({ email, aal })}.sig`;
const authCalls = [];
const wakes = [];
const cf = { down: false, calls: [] };
const env = { SUPABASE_DB_URL: DB, SUPABASE_URL: "http://auth.test", SUPABASE_ANON_KEY: "anon",
              SUPABASE_SERVICE_ROLE_KEY: "service", FLEET_FACTOR_KEY: btoa("k".repeat(32)) };
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
  if (u.startsWith("https://oauth2.googleapis.com/") || u.startsWith("https://admin.googleapis.com/")) return fakeGoogle(u, init);
  // Stand-in Supabase Realtime (the wake broadcasts) and Cloudflare's relay credentials (2.9a).
  if (u === "http://auth.test/realtime/v1/api/broadcast") {
    if (new Headers(init?.headers).get("Authorization") !== "Bearer service") return new Response("{}", { status: 401 });
    wakes.push(...JSON.parse(init.body).messages.map((m) => `${m.event} ${m.topic}`));
    return new Response(null, { status: 202 });
  }
  const turn = u.match(/^https:\/\/rtc\.live\.cloudflare\.com\/v1\/turn\/keys\/([^/]+)\/credentials\/generate-ice-servers$/);
  if (turn) {
    if (cf.down) throw new TypeError("fetch failed");
    cf.calls.push({ key: turn[1], auth: new Headers(init?.headers).get("Authorization"), body: JSON.parse(init.body) });
    return new Response(JSON.stringify({ iceServers: [
      { urls: ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"] },
      { urls: ["turn:turn.cloudflare.com:3478?transport=udp", "turn:turn.cloudflare.com:53?transport=udp",
               "turns:turn.cloudflare.com:5349?transport=tcp", "javascript:alert(1)"],
        username: "u-" + cf.calls.length, credential: "c-" + cf.calls.length }] }), { status: 201 });
  }
  return realFetch(url, init);
};

// Stand-in Google (2.8e): the token endpoint checks the service account's
// signed request with its public key, as Google does; the Directory API keeps
// mailboxes in memory. google.down makes Google unreachable.
const { generateKeyPairSync, createVerify } = await import("node:crypto");
const SA = generateKeyPairSync("rsa", { modulusLength: 2048, publicKeyEncoding: { type: "spki", format: "pem" },
                                        privateKeyEncoding: { type: "pkcs8", format: "pem" } });
const SA_EMAIL = "kulan-fleet@acme-mail.iam.gserviceaccount.com";
const gstate = { down: false, delegated: true, users: new Map(), calls: [], tokens: 0, ids: 0 };
const gjson = (status, body) => new Response(body === undefined ? null : JSON.stringify(body), { status });
async function fakeGoogle(u, init) {
  if (gstate.down) throw new TypeError("fetch failed");
  const method = init?.method ?? "GET";
  gstate.calls.push(`${method} ${u.replace(/^https:\/\/[^/]+/, "")}`);
  if (u === "https://oauth2.googleapis.com/token") {
    const form = new URLSearchParams(String(init.body));
    const [h, c, s] = String(form.get("assertion")).split(".");
    const verify = createVerify("RSA-SHA256");
    verify.update(`${h}.${c}`);
    const claims = JSON.parse(Buffer.from(c, "base64url").toString());
    const goodSig = verify.verify(SA.publicKey, Buffer.from(s, "base64url"));
    if (form.get("grant_type") !== "urn:ietf:params:oauth:grant-type:jwt-bearer" || !goodSig || claims.iss !== SA_EMAIL ||
        claims.aud !== "https://oauth2.googleapis.com/token" || claims.exp - claims.iat !== 3600) {
      return gjson(400, { error: "invalid_grant", error_description: "Invalid JWT Signature." });
    }
    if (!gstate.delegated || claims.scope !== "https://www.googleapis.com/auth/admin.directory.user") {
      return gjson(401, { error: "unauthorized_client", error_description: "Client is unauthorized to retrieve access tokens using this method" });
    }
    if (claims.sub !== "it@acme.test") return gjson(400, { error: "invalid_grant", error_description: "Invalid email or User ID" });
    gstate.tokens++;
    return gjson(200, { access_token: "ya29.test", expires_in: 3599, token_type: "Bearer" });
  }
  if (new Headers(init?.headers).get("Authorization") !== "Bearer ya29.test") return gjson(401, { error: { message: "Login Required" } });
  const m = u.match(/^https:\/\/admin\.googleapis\.com\/admin\/directory\/v1\/users(?:\/([^?]+))?(?:\?(.*))?$/);
  if (!m) return gjson(404, { error: { message: "not found" } });
  const who = m[1] ? decodeURIComponent(m[1]) : null;
  const body = init?.body ? JSON.parse(init.body) : {};
  // Every mailbox has Google's id; a user is found by id, address or alias, as Google does.
  for (const x of gstate.users.values()) x.id ??= `g${++gstate.ids}`;
  const find = (key) => [...gstate.users.values()].find((x) => x.id === key || x.primaryEmail === key || (x.aliases ?? []).includes(key));
  const show = (x) => ({ id: x.id, primaryEmail: x.primaryEmail, name: { ...x.name, fullName: `${x.name.givenName} ${x.name.familyName}` },
                         suspended: x.suspended, isAdmin: x.isAdmin, isDelegatedAdmin: !!x.isDelegatedAdmin });
  if (!who && method === "GET") {
    const q = new URLSearchParams(m[2] ?? "");
    const list = [...gstate.users.values()].filter((x) => x.primaryEmail.endsWith("@" + q.get("domain")));
    return gjson(200, { users: list.slice(0, Number(q.get("maxResults") ?? 500)).map(show) });
  }
  if (!who && method === "POST") {
    if (find(body.primaryEmail)) return gjson(409, { error: { message: "Entity already exists." } });
    gstate.users.set(body.primaryEmail, { ...body, id: `g${++gstate.ids}`, suspended: false, isAdmin: false });
    return gjson(200, show(gstate.users.get(body.primaryEmail)));
  }
  const x = find(who);
  if (!x) return gjson(404, { error: { message: "Resource Not Found: userKey" } });
  if (method === "GET") return gjson(200, show(x));
  if (method === "PATCH") {
    Object.assign(x, body, body.name ? { name: { ...x.name, ...body.name } } : {});
    return gjson(200, show(x));
  }
  if (method === "DELETE") {
    gstate.users.delete(x.primaryEmail);
    return gjson(204);
  }
  return gjson(400, { error: { message: "bad request" } });
}
const run = path.join(here, ".run");
fs.mkdirSync(run, { recursive: true });
for (const name of ["fleet", "fleet-admin"]) {
  const src = fs.readFileSync(path.join(here, "..", "supabase", "functions", name, "index.ts"), "utf8")
    .replace('import postgres from "npm:postgres@3.4.5";', 'import postgres from "postgres";');
  const file = path.join(run, `${name}.ts`);
  fs.writeFileSync(file, src);
  // policy.ts: the same file in both functions (checked in CI); either copy will do here.
  fs.copyFileSync(path.join(here, "..", "supabase", "functions", name, "policy.ts"), path.join(run, "policy.ts"));
  // help.ts too (2.9a): the same in both, like policy.ts.
  fs.copyFileSync(path.join(here, "..", "supabase", "functions", name, "help.ts"), path.join(run, "help.ts"));
  // actions.ts and secrets.ts (2.9b): the same in both too.
  for (const f of ["actions.ts", "secrets.ts"]) fs.copyFileSync(path.join(here, "..", "supabase", "functions", name, f), path.join(run, f));
  if (name === "fleet-admin") {
    fs.copyFileSync(path.join(here, "..", "supabase", "functions", name, "google.ts"), path.join(run, "google.ts"));
  }
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

  // A phone code the server can't open (its key changed): computers are told to keep theirs.
  await report(A, { factors: [{ username: "mo", id: id(6), kind: "code", label: "Phone", data: SECRET }] });
  await db`update fleet.person_factors set data = 'v1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' where id = ${id(6)}`;
  const moA = (await report(A)).body.accounts.find((x) => x.username === "mo").two_step;
  ok(moA.codes_unavailable === true && moA.factors.length === 0, "a code that can't be opened: flagged, not silently dropped", moA);
  await db`delete from fleet.person_factors where id = ${id(6)}`;

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

  // Not someone who's an admin elsewhere: their authenticator app guards that too.
  r = await admin(NEWOWNER, { op: "add-admin", organisation_id: org, email: BOSS }, "aal2");
  await db`insert into auth.users (id, email) values (${"id-" + BOSS}, ${BOSS})`;
  authFactors[BOSS] = [{ id: "fb", factor_type: "totp", status: "verified" }];
  r = await admin(NEWOWNER, { op: "reset-admin-two-step", organisation_id: org, email: BOSS }, "aal2");
  ok(r.status === 403 && authFactors[BOSS].length === 1, "an Owner can't reset someone who's an admin elsewhere", r);
  await admin(NEWOWNER, { op: "remove-admin", organisation_id: org, email: BOSS }, "aal2");
  delete authFactors[BOSS];

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

// --- Google Workspace mailboxes (2.8e) -------------------------------------------------------
{
  const KEY = JSON.stringify({ type: "service_account", project_id: "acme-mail", private_key_id: "abc",
                               private_key: SA.privateKey, client_email: SA_EMAIL, client_id: "112233445566778899001" });
  const accountOf = async (c, who) => (await report(c)).body.accounts.find((x) => x.username === who);
  const row = async (who) => (await db`select mailbox, mailbox_state, mailbox_todo, mailbox_error, enabled, removed_at
                                       from fleet.people where organisation_id = ${org} and username = ${who}`)[0];
  r = await admin(HELPER, { op: "mail", organisation_id: org });
  ok(r.status === 200 && r.body.connection === null && r.body.mailboxes === 0, "not connected yet", r.body);

  // Connecting: the Owner only, a real service account key, checked with Google first.
  r = await admin(HELPER, { op: "connect-mail", organisation_id: org, key: KEY, admin_email: "it@acme.test" });
  ok(r.status === 403, "only the Owner connects Google Workspace", r);
  r = await admin(NEWOWNER, { op: "connect-mail", organisation_id: org, key: '{"type":"authorized_user"}', admin_email: "it@acme.test" });
  ok(r.status === 400 && r.body.error.includes("JSON key"), "not a service account key", r);
  gstate.delegated = false;
  r = await admin(NEWOWNER, { op: "connect-mail", organisation_id: org, key: KEY, admin_email: "it@acme.test" });
  ok(r.status === 400 && r.body.error.includes("domain-wide delegation") && r.body.client_id === "112233445566778899001",
     "no domain-wide delegation yet: says so, with the client ID to give it", r.body);
  gstate.delegated = true;
  r = await admin(NEWOWNER, { op: "connect-mail", organisation_id: org, key: KEY, admin_email: "someone@acme.test" });
  ok(r.status === 400 && r.body.error.includes("super admin"), "acting as someone Google won't accept", r.body);
  ok((await db`select 1 from fleet.mail_connections`).length === 0, "nothing kept until it works");
  r = await admin(NEWOWNER, { op: "connect-mail", organisation_id: org, key: KEY, admin_email: "IT@acme.test" });
  ok(r.status === 200 && r.body.domain === "acme.test", "connected (the domain from the super admin's email)", r);
  const [conn] = await db`select private_key, client_email, client_id, admin_email from fleet.mail_connections where organisation_id = ${org}`;
  ok(conn.private_key.startsWith("m1:") && !conn.private_key.includes("PRIVATE KEY") && conn.client_email === SA_EMAIL &&
     conn.admin_email === "it@acme.test", "the private key is kept sealed", { ...conn, private_key: conn.private_key.slice(0, 8) });
  r = await admin(HELPER, { op: "mail", organisation_id: org });
  ok(r.body.connection?.domain === "acme.test" && r.body.connection.client_id === "112233445566778899001" &&
     !JSON.stringify(r.body).includes("PRIVATE") && !JSON.stringify(r.body).includes("m1:"), "admins see the connection, never its key", r.body);
  r = await admin(HELPER, { op: "overview" });
  ok(r.body.organisations.find((o) => o.id === org).mail_domain === "acme.test", "the overview says which domain", 0);

  // A new person with a new mailbox.
  r = await add(HELPER, { username: "leyla", full_name: "Leyla Ahmed Nur", mailbox: { action: "create", address: "bad address" } });
  ok(r.status === 400, "a mailbox address that isn't one", r);
  r = await add(HELPER, { username: "leyla", full_name: "Leyla Ahmed Nur", mailbox: { action: "link", address: "leyla@acme.test" } });
  ok(r.status === 403, "only the Owner links an existing mailbox", r);
  r = await add(HELPER, { username: "leyla", full_name: "Leyla Ahmed Nur", mailbox: { action: "create", address: "Leyla@Acme.test" } });
  ok(r.status === 200 && r.body.mailbox === "leyla@acme.test" && /^([A-Za-z0-9]{4}-){3}[A-Za-z0-9]{4}$/.test(r.body.mailbox_password),
     "added with a mailbox and a first Google password (shown once)", r.body);
  const leyla = r.body.id;
  const g = gstate.users.get("leyla@acme.test");
  ok(g && g.name.givenName === "Leyla Ahmed" && g.name.familyName === "Nur" && g.changePasswordAtNextLogin === true &&
     g.password === r.body.mailbox_password, "made in Google: the name from People, a new password at first sign-in", g);
  r = await add(HELPER, { username: "leyla2", mailbox: { action: "create", address: "leyla@acme.test" } });
  ok(r.status === 409 && r.body.error.includes("leyla"), "one person per mailbox", r);
  gstate.users.set("taken@acme.test", { primaryEmail: "taken@acme.test", name: { givenName: "T", familyName: "T" }, suspended: false, isAdmin: false });
  r = await add(HELPER, { username: "tariq", mailbox: { action: "create", address: "taken@acme.test" } });
  ok(r.status === 200 && r.body.mailbox_error?.includes("already in Google Workspace") && !r.body.mailbox_password,
     "an address Google already has: they're added, the mailbox isn't (the Owner can link it)", r.body);
  const tariq = r.body.id;
  r = await admin(NEWOWNER, { op: "mailbox", person_id: tariq, action: "link", address: "taken@acme.test" });
  ok(r.status === 200 && (await row("tariq")).mailbox === "taken@acme.test", "the Owner links it", r);

  // The computers: the work email, and the mail app.
  let a = await accountOf(A, "leyla");
  ok(JSON.stringify(a.mail) === JSON.stringify({ address: "leyla@acme.test", provider: "google" }), "the computer gets leyla's work email", a);
  let pa = (await report(A)).body.policy;
  ok(pa.required_apps?.some((x) => x.source === "debian" && x.id === "geary"), "and Geary to read it", pa.required_apps);
  ok(!(await computer(C, "/v1/report")).body.policy.required_apps?.some((x) => x.id === "geary"), "not another organisation's computer");
  ok(!("mail" in (await accountOf(A, "hodan"))), "nobody else's work email");

  // Disable and enable: suspended and back.
  r = await admin(HELPER, { op: "set-person-enabled", person_id: leyla, enabled: false });
  ok(r.status === 200 && !r.body.mailbox_error && gstate.users.get("leyla@acme.test").suspended === true &&
     (await row("leyla")).mailbox_state === "suspended", "disabled: the mailbox is suspended", r.body);
  ok(!("mail" in (await accountOf(A, "leyla"))), "and the computers no longer offer it");
  r = await admin(HELPER, { op: "set-person-enabled", person_id: leyla, enabled: true });
  ok(gstate.users.get("leyla@acme.test").suspended === false && (await row("leyla")).mailbox_state === "active", "enabled: unsuspended", r.body);

  // Google can't be reached: Fleet's change happens anyway; what's left is an alert and can be retried.
  gstate.down = true;
  r = await admin(HELPER, { op: "set-person-enabled", person_id: leyla, enabled: false });
  let lr = await row("leyla");
  ok(r.status === 200 && r.body.mailbox_error?.includes("couldn't be reached") && lr.enabled === false && lr.mailbox_todo === "suspend" &&
     lr.mailbox_state === "active", "Google down: disabled in Fleet at once; the suspension is still to do", { body: r.body, lr });
  ok(!("mail" in (await accountOf(A, "leyla"))), "the computers stop offering her mailbox at once");
  r = await admin(HELPER, { op: "overview" });
  ok(r.body.alerts.some((x) => x.kind === "mailbox" && x.text.startsWith("leyla's mailbox leyla@acme.test: still to suspend")) &&
     r.body.alerts.some((x) => x.kind === "mail" && x.text.includes("couldn't be reached")), "alerts: the mailbox, and Google", r.body.alerts.slice(0, 3));
  r = await admin(HELPER, { op: "weekly-report", organisation_id: org });
  ok(r.body.summary.people.counts.mailbox_problems === 1 && r.body.summary.people.mailbox_problems[0].todo === "suspend",
     "in the weekly report", r.body.summary.people);
  gstate.down = false;
  r = await admin(HELPER, { op: "mailbox", person_id: leyla, action: "retry" });
  lr = await row("leyla");
  ok(r.status === 200 && !r.body.mailbox_error && lr.mailbox_todo === null && lr.mailbox_state === "suspended" &&
     gstate.users.get("leyla@acme.test").suspended === true, "retried: suspended", { body: r.body, lr });
  r = await admin(HELPER, { op: "overview" });
  ok(!r.body.alerts.some((x) => x.kind === "mailbox" || x.kind === "mail"), "the alerts go once it works", r.body.alerts.slice(0, 3));
  // Disabled while Google was down, enabled again before the retry: nothing left to do.
  gstate.down = true;
  await admin(HELPER, { op: "set-person-enabled", person_id: leyla, enabled: true });
  ok((await row("leyla")).mailbox_todo === "unsuspend", "enabled while Google is down: still to unsuspend");
  await admin(HELPER, { op: "set-person-enabled", person_id: leyla, enabled: false });
  ok((await row("leyla")).mailbox_todo === null, "disabled again: the mailbox is already suspended, nothing to do");
  gstate.down = false;
  await admin(HELPER, { op: "set-person-enabled", person_id: leyla, enabled: true });

  // A new name in People, a new Google password.
  r = await admin(HELPER, { op: "edit-person", person_id: leyla, full_name: "Leyla Nur" });
  ok(r.status === 200 && gstate.users.get("leyla@acme.test").name.familyName === "Nur" &&
     gstate.users.get("leyla@acme.test").name.givenName === "Leyla", "renamed in Google too", gstate.users.get("leyla@acme.test").name);
  r = await admin(HELPER, { op: "mailbox", person_id: leyla, action: "new-password" });
  ok(r.status === 200 && r.body.mailbox_password && gstate.users.get("leyla@acme.test").password === r.body.mailbox_password &&
     gstate.users.get("leyla@acme.test").changePasswordAtNextLogin === true, "a new Google password, changed at next sign-in", r.body);

  // A Google admin account: only the Owner changes it (a Fleet admin can't take over Google through Fleet).
  gstate.users.set("ceo@acme.test", { primaryEmail: "ceo@acme.test", name: { givenName: "C", familyName: "E" }, suspended: false, isAdmin: true });
  r = await add(HELPER, { username: "kofi", full_name: "Kofi Mensah" });
  const kofi = r.body.id;
  r = await admin(NEWOWNER, { op: "mailbox", person_id: kofi, action: "link", address: "ceo@acme.test" });
  ok(r.status === 200, "the Owner links a Google admin's mailbox", r);
  r = await admin(HELPER, { op: "mailbox", person_id: kofi, action: "new-password" });
  ok(r.status === 403 && gstate.users.get("ceo@acme.test").password === undefined, "an admin can't reset a Google admin's password", r);
  r = await admin(HELPER, { op: "remove-person", person_id: kofi, confirm: "kofi", mailbox: "delete" });
  ok(r.status === 403 && (await row("kofi")).removed_at === null && gstate.users.has("ceo@acme.test"),
     "nor delete it (refused before anything changes)", r);
  r = await admin(HELPER, { op: "set-person-enabled", person_id: kofi, enabled: false });
  ok(r.body.mailbox_error?.includes("Google admin account") && (await row("kofi")).enabled === false &&
     gstate.users.get("ceo@acme.test").suspended === false, "disabling still disables in Fleet; the Owner must suspend it", r.body);
  r = await admin(NEWOWNER, { op: "mailbox", person_id: kofi, action: "retry" });
  ok(r.status === 200 && gstate.users.get("ceo@acme.test").suspended === true, "the Owner's retry suspends it", r.body);

  // Existing mailboxes, for linking.
  r = await admin(HELPER, { op: "mail-users", organisation_id: org });
  ok(r.status === 403, "only the Owner lists the domain's mailboxes", r);
  r = await admin(NEWOWNER, { op: "mail-users", organisation_id: org });
  const users = r.body.users ?? [];
  ok(users.find((x) => x.address === "ceo@acme.test")?.username === "kofi" && users.find((x) => x.address === "ceo@acme.test")?.admin &&
     users.some((x) => x.address === "leyla@acme.test" && x.username === "leyla"), "with who has each", users);
  r = await admin(HELPER, { op: "people", organisation_id: org });
  const lp = r.body.people.find((x) => x.username === "leyla");
  ok(lp.mailbox === "leyla@acme.test" && lp.mailbox_state === "active" && lp.mailbox_todo === null, "People shows the mailbox", lp);

  // Removing someone: the admin chooses suspend or delete.
  r = await admin(HELPER, { op: "remove-person", person_id: leyla, confirm: "leyla" });
  ok(r.status === 400 && r.body.mailbox === "leyla@acme.test", "removing someone with a mailbox: choose what happens to it", r);
  r = await admin(HELPER, { op: "remove-person", person_id: leyla, confirm: "leyla", mailbox: "delete" });
  ok(r.status === 200 && !gstate.users.has("leyla@acme.test") && (await row("leyla")).mailbox_state === "deleted", "deleted in Google", r);
  r = await admin(HELPER, { op: "remove-person", person_id: tariq, confirm: "tariq", mailbox: "suspend" });
  ok(r.status === 200 && gstate.users.get("taken@acme.test").suspended === true, "or suspended", r);
  // Google down while removing: removed at once; the deletion waits, and isn't forgotten after 30 days.
  r = await add(HELPER, { username: "sahra", mailbox: { action: "create", address: "sahra@acme.test" } });
  const sahra = r.body.id;
  gstate.down = true;
  r = await admin(HELPER, { op: "remove-person", person_id: sahra, confirm: "sahra", mailbox: "delete" });
  ok(r.status === 200 && r.body.mailbox_error && (await row("sahra")).removed_at && (await row("sahra")).mailbox_todo === "delete",
     "Google down: removed anyway, the deletion still to do", r.body);
  r = await admin(HELPER, { op: "people", organisation_id: org });
  ok(r.body.removed_with_mailbox_todo.some((x) => x.username === "sahra"), "listed to retry", r.body.removed_with_mailbox_todo);
  await db`update fleet.people set removed_at = now() - interval '40 days' where id = ${sahra}`;
  await db`select fleet.prune_people()`;
  ok((await row("sahra"))?.mailbox_todo === "delete", "kept past 30 days while its mailbox waits");
  gstate.down = false;
  r = await admin(HELPER, { op: "mailbox", person_id: sahra, action: "retry" });
  ok(r.status === 200 && !gstate.users.has("sahra@acme.test") && (await row("sahra")).mailbox_todo === null, "retried: deleted", r.body);
  await db`select fleet.prune_people()`;
  ok(!(await row("sahra")), "then forgotten as usual");
  r = await add(HELPER, { username: "sahra2", mailbox: { action: "create", address: "sahra@acme.test" } });
  ok(r.status === 200 && r.body.mailbox === "sahra@acme.test", "a deleted mailbox's address can be used again", r.body);

  // --- from the security review ---------------------------------------------------------
  // Another organisation can't borrow this one's Google connection: not by naming the same service
  // account and super admin with another key (Google is always asked when connecting), not by
  // copying the sealed key into its own row (it's bound to this organisation).
  const KEY2 = JSON.stringify({ type: "service_account", client_email: SA_EMAIL, client_id: "112233445566778899001",
    private_key: generateKeyPairSync("rsa", { modulusLength: 2048, privateKeyEncoding: { type: "pkcs8", format: "pem" },
                                              publicKeyEncoding: { type: "spki", format: "pem" } }).privateKey });
  await db`insert into fleet.admins (email, organisation_id) values ('other.owner@other.test', ${other})`;
  await db`update fleet.organisations set owner_email = 'other.owner@other.test' where id = ${other}`;
  r = await admin("other.owner@other.test", { op: "connect-mail", organisation_id: other, key: KEY2, admin_email: "it@acme.test" });
  ok(r.status === 409 && r.body.error.includes("another organisation"), "another organisation's Owner can't connect a domain already connected", r);
  const before = gstate.tokens;
  r = await admin(BOSS, { op: "connect-mail", organisation_id: other, key: KEY2, admin_email: "it@acme.test" });
  ok(r.status === 400 && gstate.tokens === before && (await db`select 1 from fleet.mail_connections where organisation_id = ${other}`).length === 0,
     "even Kulan's admins: a different key with the same names isn't accepted (Google is asked, not a remembered token)", r);
  await db`insert into fleet.mail_connections (organisation_id, domain, admin_email, client_email, private_key)
           select ${other}, domain, admin_email, client_email, private_key from fleet.mail_connections where organisation_id = ${org}`;
  r = await admin(BOSS, { op: "mail-users", organisation_id: other });
  ok(r.status === 502 && r.body.error.includes("can't open"), "a sealed key copied to another organisation doesn't open there", r);
  await db`delete from fleet.mail_connections where organisation_id = ${other}`;

  // Only the Owner changes an admin's mailbox: an admin renaming one leaves Google alone (the Owner can retry).
  r = await add(NEWOWNER, { username: "abdi", full_name: "Abdi Admin", role: "admin", mailbox: { action: "create", address: "abdi@acme.test" } });
  const abdi = r.body.id;
  r = await admin(HELPER, { op: "edit-person", person_id: abdi, full_name: "Hacked Name" });
  ok(r.body.mailbox_error?.includes("only the Owner") && gstate.users.get("abdi@acme.test").name.familyName === "Admin" &&
     (await row("abdi")).mailbox_todo === "rename", "an admin renaming an admin: not in Google; left for the Owner", r.body);
  r = await admin(HELPER, { op: "mailbox", person_id: abdi, action: "retry" });
  ok(r.status === 403, "nor can they retry it", r);
  r = await admin(NEWOWNER, { op: "mailbox", person_id: abdi, action: "retry" });
  ok(!r.body.mailbox_error && gstate.users.get("abdi@acme.test").name.familyName === "Name", "the Owner's retry does it", r.body);

  // A rename never hides a suspend: disabled while Google was down, then renamed: still suspended.
  r = await add(HELPER, { username: "ayaan", full_name: "Ayaan Omar", mailbox: { action: "create", address: "ayaan@acme.test" } });
  const ayaan = r.body.id;
  gstate.down = true;
  await admin(HELPER, { op: "set-person-enabled", person_id: ayaan, enabled: false });
  await admin(HELPER, { op: "edit-person", person_id: ayaan, full_name: "Ayaan O. Omar" });
  ok((await row("ayaan")).mailbox_todo === "suspend", "Google down: a rename doesn't replace the waiting suspend");
  gstate.down = false;
  r = await admin(HELPER, { op: "edit-person", person_id: ayaan, full_name: "Ayaan Omar Ali" });
  let ar = await row("ayaan");
  ok(!r.body.mailbox_error && ar.mailbox_todo === null && ar.mailbox_state === "suspended" && gstate.users.get("ayaan@acme.test").suspended === true &&
     gstate.users.get("ayaan@acme.test").name.familyName === "Ali", "Google back: the suspend done first, then the rename", { body: r.body, ar });

  // Only the mailbox Fleet made: if the address now belongs to someone else (an alias), nothing is done to them.
  r = await add(HELPER, { username: "bilan", mailbox: { action: "create", address: "bilan@acme.test" } });
  const bilan = r.body.id;
  gstate.users.delete("bilan@acme.test");
  gstate.users.set("helper.g@acme.test", { primaryEmail: "helper.g@acme.test", name: { givenName: "H", familyName: "G" }, suspended: false,
                                           isAdmin: false, aliases: ["bilan@acme.test"] });
  r = await admin(HELPER, { op: "mailbox", person_id: bilan, action: "new-password" });
  ok(r.status === 404 && gstate.users.get("helper.g@acme.test").password === undefined, "her mailbox gone, its address another's alias: no new password for them", r);
  r = await admin(HELPER, { op: "set-person-enabled", person_id: bilan, enabled: false });
  ok(gstate.users.get("helper.g@acme.test").suspended === false && r.body.mailbox_error, "and they aren't suspended", r.body);
  // A delegated admin's mailbox is a Google admin's too.
  gstate.users.set("delegate@acme.test", { primaryEmail: "delegate@acme.test", name: { givenName: "D", familyName: "A" }, suspended: false,
                                            isAdmin: false, isDelegatedAdmin: true });
  r = await add(HELPER, { username: "deqa" });
  const deqa = r.body.id;
  await admin(NEWOWNER, { op: "mailbox", person_id: deqa, action: "link", address: "delegate@acme.test" });
  r = await admin(HELPER, { op: "mailbox", person_id: deqa, action: "new-password" });
  ok(r.status === 403, "a delegated Google admin's mailbox: only the Owner", r);

  // Offboarding can't be quietly dropped: an admin can't forget or unlink a waiting suspend.
  gstate.down = true;
  await admin(HELPER, { op: "set-person-enabled", person_id: deqa, enabled: false });
  gstate.down = false;
  for (const action of ["forget", "unlink"]) {
    r = await admin(HELPER, { op: "mailbox", person_id: deqa, action });
    ok(r.status === 403 && (await row("deqa")).mailbox_todo === "suspend", `an admin can't ${action} a waiting suspend`, r);
  }
  // Mailboxes are made on the connected domain (only the Owner chooses another).
  r = await add(HELPER, { username: "zahra", mailbox: { action: "create", address: "ceo@acme-other.test" } });
  ok(r.body.mailbox_error?.includes("acme.test") && !gstate.users.has("ceo@acme-other.test"), "an admin: only on the connected domain", r.body);

  // Disconnecting keeps what's left to do (an alert), and removed people's waits are in the weekly report.
  ok((await row("bilan")).mailbox_state === "active", "(her mailbox, gone from Google, is still on record)");
  gstate.down = true;
  r = await admin(HELPER, { op: "remove-person", person_id: bilan, confirm: "bilan", mailbox: "delete" });
  gstate.down = false;
  r = await admin(HELPER, { op: "weekly-report", organisation_id: org });
  ok(r.body.summary.people.mailbox_problems.some((x) => x.username === "bilan"), "a removed person's waiting mailbox is in the weekly report",
     r.body.summary.people.mailbox_problems);

  // The log, then disconnecting.
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "mailbox" });
  const ml = r.body.entries.map((e) => e.what);
  for (const want of ["made a mailbox for leyla", "suspended leyla's mailbox", "unsuspended leyla's mailbox", "renamed leyla's mailbox",
                      "linked tariq's mailbox", "deleted leyla's mailbox", "deleted sahra's mailbox"]) {
    ok(ml.includes(want), `in the activity log: ${want}`, ml);
  }
  r = await admin(NEWOWNER, { op: "log", organisation_id: org, what: "google" });
  ok(r.body.entries.some((e) => e.what === "connected Google Workspace" && e.detail.service_account === SA_EMAIL) &&
     r.body.entries.some((e) => e.what === "gave leyla a new Google password"), "connecting and passwords too", r.body.entries.map((e) => e.what));
  ok(!JSON.stringify((await db`select detail from fleet.audit`).map((x) => x.detail)).includes("PRIVATE KEY") &&
     !JSON.stringify((await db`select detail from fleet.audit`).map((x) => x.detail)).match(/[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}-[A-Za-z0-9]{4}"/),
     "no key or password in the log");
  r = await admin(HELPER, { op: "disconnect-mail", organisation_id: org });
  ok(r.status === 403, "only the Owner disconnects", r);
  r = await admin(NEWOWNER, { op: "disconnect-mail", organisation_id: org });
  ok(r.status === 200 && (await db`select 1 from fleet.mail_connections`).length === 0, "disconnected: the key is gone", r);
  ok((await row("deqa")).mailbox_todo === "suspend", "what was left to do is kept after disconnecting (still an alert)");
  r = await admin(HELPER, { op: "mailbox", person_id: deqa, action: "retry" });
  ok(r.body.mailbox_error === "Google Workspace isn't connected", "retrying says why it can't", r.body);
  ok(!("mail" in (await accountOf(A, "sahra2"))) && !(await report(A)).body.policy.required_apps?.some((x) => x.id === "geary"),
     "the computers stop offering work email", 0);
  r = await admin(HELPER, { op: "remove-person", person_id: kofi, confirm: "kofi" });
  ok(r.status === 200, "not connected: removing needs no mailbox choice", r);
  ok(gstate.tokens === 1, "Google's access token is reused, not asked for every call", gstate.tokens);
}

// --- the activity log ------------------------------------------------------------------------
r = await admin(NEWOWNER, { op: "log", organisation_id: org });
const whats = r.body.entries.map((e) => `${e.email}: ${e.what}`);
for (const want of [`${HELPER}: added amina`, `${OWNER}: added omar`, `${HELPER}: disabled amina`,
                    `${HELPER}: gave amina a new setup code`, `${HELPER}: removed amina`, `${OWNER}: added admin ${HELPER}`,
                    `${NEWOWNER}: became the Owner with the recovery code`]) {
  ok(whats.includes(want), `in the activity log: ${want}`, whats);
}

// --- actions: fixes, messages, scripts, Wi-Fi and printers (2.9b) ------------------------------
{
  const moId = (await db`select id from fleet.people where username = 'mo' and organisation_id = ${org}`)[0].id;
  // A fix, a message; nothing else is a fix.
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-fix", payload: { fix: "free-space" } });
  ok(r.status === 200, "a fix asked of a computer", r);
  const fixId = r.body.id;
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-fix", payload: { fix: "rm -rf /" } });
  ok(r.status === 400, "only Kulan's own fixes", r);
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "message",
                            payload: { title: "Restart\ttonight\u0000", body: "Updates are waiting.\nThanks!\u0007" } });
  ok(r.status === 200, "a message", r);
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "message", payload: { body: "no title" } });
  ok(r.status === 400, "a message needs a title", r);
  let rep = await report(B);
  const fixReq = rep.body.actions.find((a) => a.type === "run-fix"), msgReq = rep.body.actions.find((a) => a.type === "message");
  ok(fixReq?.payload.fix === "free-space" && msgReq?.payload.title === "Restart tonight" &&
     msgReq.payload.body === "Updates are waiting.\nThanks!", "the computer gets them, cleaned", rep.body.actions);
  ok(rep.body.policy.scripts === undefined, "scripts not allowed: the policy doesn't say they are", rep.body.policy);
  await computer(B, `/v1/actions/${fixId}`, { status: "done", detail: "freed 512 MB", output: "x".repeat(70000) + "\u0000", exit_code: 0 });
  r = await admin(HELPER, { op: "action-output", action_id: fixId });
  ok(r.status === 200 && r.body.output.length === 65536 && r.body.exit_code === 0 && !r.body.output.includes("\u0000"),
     "a fix's output is kept, at most 64 KB", { len: r.body.output?.length, code: r.body.exit_code });
  await computer(B, `/v1/actions/${msgReq.id}`, { status: "done", detail: "shown", output: "nope", exit_code: 3 });
  r = await admin(HELPER, { op: "action-output", action_id: msgReq.id });
  ok(r.body.output === null && r.body.exit_code === null, "only fixes and scripts keep an output", r.body);
  await db`insert into fleet.admins (email, organisation_id) values ('other.admin@x.test', ${other}) on conflict do nothing`;
  r = await admin("other.admin@x.test", { op: "action-output", action_id: fixId });
  ok(r.status === 403 || r.status === 404, "another organisation's admin sees no output", r);
  r = await admin(HELPER, { op: "overview" });
  const listed = r.body.actions.find((a) => a.id === fixId);
  ok(listed?.what === "free-space" && listed.has_output === true && listed.exit_code === 0, "the overview lists it with its output", listed);

  // Scripts: only once the Owner allows them, and only with the authenticator code.
  r = await admin(HELPER, { op: "add-script", organisation_id: org, name: "Hello", body: "echo hi" });
  ok(r.status === 403 && r.body.error.includes("Owner"), "no scripts until the Owner allows them", r);
  r = await admin(HELPER, { op: "set-scripts-allowed", organisation_id: org, allowed: true }, "aal2");
  ok(r.status === 403, "only the Owner allows scripts", r);
  r = await admin(NEWOWNER, { op: "set-scripts-allowed", organisation_id: org, allowed: true });
  ok(r.status === 403 && r.body.two_step, "the Owner needs the authenticator code to allow them", r);
  r = await admin(NEWOWNER, { op: "set-scripts-allowed", organisation_id: org, allowed: true }, "aal2");
  ok(r.status === 200, "the Owner allows scripts", r);
  r = await admin(HELPER, { op: "add-script", organisation_id: org, name: "Hello", body: "echo hi" });
  ok(r.status === 403 && r.body.two_step, "writing a script needs the code", r);
  r = await admin(HELPER, { op: "add-script", organisation_id: org, name: "Hello", body: "a\u0000b" }, "aal2");
  ok(r.status === 400, "a script is text", r);
  r = await admin(HELPER, { op: "add-script", organisation_id: org, name: "Hello", body: "x".repeat(65537) }, "aal2");
  ok(r.status === 400, "64 KB at most", r);
  r = await admin(HELPER, { op: "add-script", organisation_id: org, name: "Hello", description: "Says hi", body: "echo hi\r\n" }, "aal2");
  ok(r.status === 200 && /^[0-9a-f]{64}$/.test(r.body.sha256), "a script added", r);
  const scriptId = r.body.id, sha1 = r.body.sha256;
  r = await admin(HELPER, { op: "add-script", organisation_id: org, name: "hello", body: "echo again" }, "aal2");
  ok(r.status === 409, "names are unique", r);
  r = await admin(HELPER, { op: "scripts", organisation_id: org });
  ok(r.body.allowed === true && r.body.scripts.length === 1 && r.body.scripts[0].body === null && r.body.bodies === false,
     "listed without the code: names only, not what they say", r.body);
  r = await admin(HELPER, { op: "scripts", organisation_id: org }, "aal2");
  ok(r.body.scripts[0].body === "echo hi\n" && r.body.bodies === true, "with the code: what they say, line endings made plain", r.body);
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-script", payload: { script_id: scriptId } });
  ok(r.status === 403 && r.body.two_step, "running a script needs the code", r);
  r = await admin(HELPER, { op: "action", device_id: C.device_id, type: "run-script", payload: { script_id: scriptId } }, "aal2");
  ok(r.status === 404, "not on another organisation's computer", r);
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-script", payload: { script_id: scriptId } }, "aal2");
  ok(r.status === 200, "a script asked of a computer", r);
  const runId = r.body.id;
  rep = await report(B);
  const runReq = rep.body.actions.find((a) => a.type === "run-script");
  ok(runReq?.payload.sha256 === sha1 && runReq.payload.name === "Hello" && rep.body.policy.scripts === true,
     "the request carries the script's hash; the policy says scripts are allowed", { runReq, policy: rep.body.policy });
  r = await computer(B, `/v1/scripts/${runId}`);
  ok(r.status === 200 && r.body.body === "echo hi\n" && r.body.sha256 === sha1, "the computer fetches the script it was asked to run", r);
  r = await computer(A, `/v1/scripts/${runId}`);
  ok(r.status === 404 || r.status === 401, "another computer can't", r);
  r = await call("fleet", `/v1/scripts/${runId}`, { device_id: B.device_id });
  ok(r.status === 401, "nor anyone without the computer's key", r);
  await admin(HELPER, { op: "edit-script", script_id: scriptId, name: "Hello", body: "echo changed" }, "aal2");
  r = await computer(B, `/v1/scripts/${runId}`);
  ok(r.status === 404, "changed after it was asked for: not given out", r);
  await computer(B, `/v1/actions/${runId}`, { status: "failed", detail: "the script changed", exit_code: -1 });
  r = await admin(HELPER, { op: "action-output", action_id: runId });
  ok(r.status === 403 && r.body.two_step, "a script's output needs the code", r);
  r = await admin(HELPER, { op: "action-output", action_id: runId }, "aal2");
  ok(r.status === 200 && r.body.exit_code === -1, "with the code, it's shown", r);
  r = await call("fleet", "/v1/scripts/not-a-uuid-at-all-not-a-uuid-at-all-xx", { device_id: B.device_id }, B.token);
  ok(r.status === 404, "a request id that isn't one: not found, not a server error", r);
  // A script sent but never answered expires after a day, and can't be fetched any more.
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-script", payload: { script_id: scriptId } }, "aal2");
  const stale = r.body.id;
  await report(B);
  await db`update fleet.actions set sent_at = now() - interval '25 hours' where id = ${stale}`;
  await report(B);
  ok((await db`select status from fleet.actions where id = ${stale}`)[0].status === "expired", "sent a day ago, never answered: expired");
  r = await computer(B, `/v1/scripts/${stale}`);
  ok(r.status === 404, "and its script can't be fetched", r);
  // Turning scripts off cancels any still waiting, and they can't be fetched.
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-script", payload: { script_id: scriptId } }, "aal2");
  const waiting = r.body.id;
  await admin(NEWOWNER, { op: "set-scripts-allowed", organisation_id: org, allowed: false });
  ok((await db`select status from fleet.actions where id = ${waiting}`)[0].status === "cancelled", "scripts off: waiting ones cancelled");
  r = await admin(HELPER, { op: "action", device_id: B.device_id, type: "run-script", payload: { script_id: scriptId } }, "aal2");
  ok(r.status === 403, "and no new ones", r);
  ok(!("scripts" in (await report(B)).body.policy), "the computers are told scripts are off");
  r = await admin(HELPER, { op: "log", organisation_id: org, what: "script" });
  const sl = r.body.entries.map((e) => e.what);
  for (const want of ["allowed scripts on the computers", "added script Hello", "changed script Hello", "asked: run-script",
                      "stopped scripts on the computers"]) ok(sl.includes(want), `in the activity log: ${want}`, sl);
  ok(!JSON.stringify(r.body.entries).includes("echo"), "the script's text isn't in the log");
  r = await admin(HELPER, { op: "remove-script", script_id: scriptId });
  ok(r.status === 403 && r.body.two_step, "removing a script needs the code", r);
  r = await admin(HELPER, { op: "remove-script", script_id: scriptId }, "aal2");
  ok(r.status === 200, "a script removed", r);

  // A message to a person: the computers they're signed in on now.
  r = await admin(HELPER, { op: "message-person", person_id: moId, title: "Hi" });
  ok(r.status === 400 && r.body.error.includes("isn't signed in"), "not signed in anywhere: said so", r);
  await report(B, { sign_ins: [{ username: "mo", last: 1790000000, now: true }] });
  r = await admin(HELPER, { op: "message-person", person_id: moId, title: "Hi", body: "Lunch?" });
  ok(r.status === 200 && r.body.count === 1, "sent to the computer they're on", r);
  rep = await report(B);
  ok(rep.body.actions.some((a) => a.type === "message" && a.payload.title === "Hi"), "it arrives", rep.body.actions);

  // Wi-Fi networks and printers for a group.
  r = await admin(HELPER, { op: "add-group", organisation_id: org, name: "Office" });
  const office = r.body.id;
  await admin(HELPER, { op: "edit-group", group_id: office, devices: [B.device_id] });
  r = await admin(HELPER, { op: "add-network", group_id: office, ssid: "Acme Staff", security: "wpa-psk", password: "short" });
  ok(r.status === 400, "a WPA password is 8 to 63 characters", r);
  r = await admin(HELPER, { op: "add-network", group_id: office, ssid: "x".repeat(33), security: "open" });
  ok(r.status === 400, "a network name is 32 bytes at most", r);
  r = await admin(HELPER, { op: "add-network", group_id: office, ssid: "Acme Staff", security: "wpa-psk", password: "correct horse battery" });
  ok(r.status === 200, "a Wi-Fi network added", r);
  const netId = r.body.id;
  r = await admin(HELPER, { op: "add-network", group_id: office, ssid: "Acme Guest", security: "open", hidden: true });
  ok(r.status === 200, "an open one too", r);
  r = await admin(HELPER, { op: "add-network", group_id: office, ssid: "Acme Staff", security: "open" });
  ok(r.status === 409, "one of each name per group", r);
  const stored = (await db`select secret from fleet.group_networks where id = ${netId}`)[0].secret;
  ok(stored.startsWith("s1:") && !stored.includes("horse"), "the password is kept sealed", stored.slice(0, 8));
  r = await admin(HELPER, { op: "groups", organisation_id: org });
  const og = r.body.groups.find((g) => g.id === office);
  ok(og.networks.length === 2 && !JSON.stringify(og).includes("horse") && !("secret" in og.networks[0]), "admins never see the password", og.networks);
  r = await admin(HELPER, { op: "add-printer", group_id: office, name: "Front desk", uri: "file:///etc/passwd" });
  ok(r.status === 400, "printers are network printers (IPP)", r);
  r = await admin(HELPER, { op: "add-printer", group_id: office, name: "Front desk", uri: "ipp://192.168.1.20/ipp/print" });
  ok(r.status === 200, "a printer added", r);
  const prId = r.body.id;
  rep = await report(B);
  const staff = rep.body.networks.find((n) => n.ssid === "Acme Staff"), guest = rep.body.networks.find((n) => n.ssid === "Acme Guest");
  ok(staff?.password === "correct horse battery" && staff.security === "wpa-psk" && guest && !("password" in guest) && guest.hidden === true,
     "the group's computers get the networks, the password opened only for them", rep.body.networks);
  ok(rep.body.printers.length === 1 && rep.body.printers[0].uri === "ipp://192.168.1.20/ipp/print", "and the printer", rep.body.printers);
  await report(B, { pushed: { networks: { [staff.id]: "added", "not-an-id": "x" }, printers: { [prId]: "couldn't reach\u0007 the printer".padEnd(300, ".") }, junk: 1 } });
  const [{ last_report: pushedBack }] = await db`select last_report from fleet.devices where id = ${B.device_id}`;
  ok(JSON.stringify(pushedBack.pushed) === JSON.stringify({ networks: { [staff.id]: "added" }, printers: { [prId]: ("couldn't reach  the printer".padEnd(300, ".")).slice(0, 120) } }),
     "how they went comes back with the report, cleaned", pushedBack.pushed);
  rep = await report(A);
  ok(rep.body.networks.length === 0 && rep.body.printers.length === 0, "computers outside the group don't", rep.body);
  await db`update fleet.group_networks set secret = ${"s1:" + btoa("x".repeat(40))} where id = ${netId}`;
  rep = await report(B);
  const sealed = rep.body.networks.find((n) => n.ssid === "Acme Staff");
  ok(sealed?.unavailable === true && !("password" in sealed),
     "a password that can't be opened: listed without it, so computers keep the network", rep.body.networks);
  r = await admin(HELPER, { op: "remove-network", network_id: netId });
  ok(r.status === 200, "a network removed", r);
  r = await admin(HELPER, { op: "remove-printer", printer_id: prId });
  ok(r.status === 200 && (await report(B)).body.printers.length === 0, "a printer removed; the computers drop it", r);
  r = await admin(HELPER, { op: "group-action", group_id: office, type: "run-fix", payload: { fix: "reset-wifi" } });
  ok(r.status === 200 && r.body.count === 1, "a fix for a whole group", r);
  r = await admin(HELPER, { op: "log", organisation_id: org, what: "group Office" });
  const gl = r.body.entries.map((e) => e.what);
  for (const want of ["added Wi-Fi Acme Staff to group Office", "added printer Front desk to group Office",
                      "removed Wi-Fi Acme Staff from group Office", "asked group Office: run-fix"]) ok(gl.includes(want), `in the activity log: ${want}`, gl);
  ok(!JSON.stringify((await db`select detail from fleet.audit`).map((x) => x.detail)).includes("horse"), "no Wi-Fi password in the log");
  await report(B);
}

const BOSS2 = "support@kulan.test";
await db`insert into fleet.admins (email, organisation_id) values (${BOSS2}, null)`;
// --- remote help (2.9a) ------------------------------------------------------------------------
{
  // Every report gives the computer where to listen for "report now".
  r = await report(A);
  const wakeA = r.body.wake;
  ok(/^wake-[0-9a-f]{48}$/.test(wakeA?.topic) && wakeA.url === "ws://auth.test/realtime/v1/websocket" && wakeA.key === "anon",
     "a report gives the wake topic", r.body.wake);
  ok((await report(A)).body.wake.topic === wakeA.topic, "the same topic every time");
  ok((await report(B)).body.wake.topic !== wakeA.topic, "each computer its own");
  // An action wakes its computer.
  wakes.length = 0;
  r = await admin(HELPER, { op: "action", device_id: A.device_id, type: "refresh" });
  ok(r.status === 200 && wakes.join() === `wake ${wakeA.topic}`, "an action wakes the computer", wakes);
  await report(A);

  // Asking again within 20 seconds is refused (a person isn't asked again and again): the tests age sessions instead.
  const aged = () => db`update fleet.help_sessions set created_at = created_at - interval '30 seconds'`;
  // Older computers can't.
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  ok(r.status === 400 && r.body.error.includes("2.9a"), "a computer before 2.9a: install its updates first", r);
  await report(A, { kulan_version: "2.9.1 (build 130)" });
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: C.device_id });
  ok(r.status === 404, "another organisation's computer: no", r);

  // Without Cloudflare's key: STUN only.
  wakes.length = 0;
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id, control: true });
  ok(r.status === 200 && r.body.relay === false && JSON.stringify(r.body.ice) === '[{"urls":["stun:stun.cloudflare.com:3478"]}]' &&
     r.body.woke === true && wakes.join() === `wake ${wakeA.topic}` && cf.calls.length === 0, "help-start: STUN only, computer woken", r.body);
  const s1 = r.body.session_id;
  r = await admin(NEWOWNER, { op: "help-start", device_id: A.device_id });
  ok(r.status === 409 && r.body.error.includes(HELPER), "one admin at a time on a computer", r);
  r = await admin(NEWOWNER, { op: "help-signal", session_id: s1 });
  ok(r.status === 404, "only the admin who asked follows the session", r);
  r = await admin(HELPER, { op: "help-signal", session_id: s1 });
  ok(r.body.state === "requested", "waiting for the computer", r.body);

  // The computer gets the request with its next report, and picks it up.
  r = await report(A);
  const req1 = r.body.actions.find((a) => a.type === "remote-help");
  ok(req1 && req1.payload.session_id === s1 && Object.keys(req1.payload).length === 1, "the request: the session only, no key", req1);
  r = await call("fleet", `/v1/help/${s1}`, { op: "claim", device_id: B.device_id, user: "amina" }, B.token);
  ok(r.status === 404, "another computer can't pick it up", r);
  r = await call("fleet", `/v1/help/${s1}`, { op: "claim", device_id: A.device_id, user: "Amina!" }, A.token);
  ok(r.status === 400, "a username that isn't one", r);
  r = await call("fleet", `/v1/help/${s1}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  ok(r.status === 200 && r.body.admin === HELPER && r.body.control_asked === true && r.body.organisation === "Acme" &&
     typeof r.body.token === "string" && r.body.token.length >= 40 && Array.isArray(r.body.ice), "claimed: who asks, and a key for this session", r.body);
  const st = r.body.token;
  ok((await db`select token_hash from fleet.help_sessions where id = ${s1}`)[0].token_hash !== st, "only the session key's hash is kept");
  r = await call("fleet", `/v1/help/${s1}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  ok(r.status === 409, "picked up once", r);
  const helper = (body, token = st, id = s1) => call("fleet", `/v1/help/${id}`, body, token);
  r = await helper({ op: "signal" }, A.token);
  ok(r.status === 401, "the computer's own key isn't the session's", r);
  r = await helper({ op: "answer", allow: true, control: true }, "wrong");
  ok(r.status === 401, "a wrong session key", r);
  r = await helper({ op: "signal", offer: { type: "offer", sdp: "v=0 fake" } });
  ok(r.status === 400, "no offer before they allow it", r);

  // They allow it, with control.
  r = await helper({ op: "answer", allow: true, control: true });
  ok(r.status === 200 && r.body.state === "allowed" && r.body.control === true, "allowed, with control", r.body);
  r = await helper({ op: "answer", allow: false });
  ok(r.status === 409, "answered once", r);
  r = await admin(HELPER, { op: "help-signal", session_id: s1, answer: { type: "answer", sdp: "v=0 early" } });
  ok(r.status === 409, "no answer before the computer's offer", r);
  r = await helper({ op: "signal", offer: { type: "answer", sdp: "v=0" } });
  ok(r.status === 400, "an offer must be an offer", r);
  r = await helper({ op: "signal", offer: { type: "offer", sdp: "v=0 the-offer" },
                     candidates: [{ candidate: "candidate:1 1 udp 1 10.0.0.2 5000 typ host", sdpMid: "0", sdpMLineIndex: 0 },
                                  { candidate: "<script>", sdpMid: "0" }, "junk"] });
  ok(r.status === 200 && r.body.state === "allowed" && r.body.control === true && r.body.answer === null, "the computer's offer", r.body);
  r = await helper({ op: "signal", offer: { type: "offer", sdp: "v=0 again" } });
  ok(r.status === 400, "one offer", r);
  r = await admin(HELPER, { op: "help-signal", session_id: s1 });
  ok(r.body.offer?.sdp === "v=0 the-offer" && r.body.candidates.length === 1 && r.body.count === 1 && r.body.who === "mo" &&
     r.body.candidates[0].candidate.startsWith("candidate:1"), "the admin gets the offer and the good candidate only", r.body);
  r = await admin(HELPER, { op: "help-signal", session_id: s1, since: 1, answer: { type: "offer", sdp: "v=0" } });
  ok(r.status === 400, "an answer must be an answer", r);
  r = await admin(HELPER, { op: "help-signal", session_id: s1, since: 1, answer: { type: "answer", sdp: "v=0 the-answer" },
                            candidates: [{ candidate: "candidate:2 1 udp 1 10.0.0.3 6000 typ host", sdpMid: "0", sdpMLineIndex: 0 }] });
  ok(r.status === 200 && r.body.candidates.length === 0, "since: only new ones", r.body);
  r = await admin(HELPER, { op: "help-signal", session_id: s1, answer: { type: "answer", sdp: "v=0 again" } });
  ok(r.status === 409, "one answer", r);
  r = await helper({ op: "signal", since: 0 });
  ok(r.body.answer?.sdp === "v=0 the-answer" && r.body.candidates.length === 1, "the computer gets the answer and candidate", r.body);
  const many = Array.from({ length: 30 }, (_, i) => ({ candidate: `candidate:${i} 1 udp 1 10.0.0.9 ${7000 + i} typ host`, sdpMid: "0" }));
  await helper({ op: "signal", candidates: many });
  await helper({ op: "signal", candidates: many });
  ok((await db`select jsonb_array_length(device_candidates) as n from fleet.help_sessions where id = ${s1}`)[0].n === 60,
     "60 candidates at most: as many as still fit are taken");
  r = await admin(HELPER, { op: "help-signal", session_id: s1, answer: { type: "answer", sdp: "v=0 the-answer" } });
  ok(r.status === 200, "the same answer again (a lost reply) is fine", r);
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  ok(r.status === 409 && r.body.error.includes("already connected"), "no new question on a screen the admin already sees", r);
  r = await helper({ op: "connected" });
  ok(r.body.state === "connected", "connected", r.body);
  r = await helper({ op: "end", detail: "Stopped by them\u0000\u001b[31m" });
  ok(r.body.state === "ended", "they stop it", r.body);
  r = await admin(HELPER, { op: "help-signal", session_id: s1 });
  ok(r.body.state === "ended" && r.body.detail === "Stopped by them[31m", "the admin sees it ended, and why (cleaned)", r.body);
  r = await helper({ op: "signal" });
  ok(r.body.state === "ended" && !("answer" in r.body), "after the end: nothing more", r.body);

  // Cloudflare's relay: credentials per session, port 53 and anything odd left out.
  env.CLOUDFLARE_TURN_KEY_ID = "key-1";
  env.CLOUDFLARE_TURN_TOKEN = "cf-token";
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s2 = r.body.session_id;
  const urls = r.body.ice.flatMap((x) => x.urls);
  ok(r.body.relay === true && cf.calls.length === 1 && cf.calls[0].key === "key-1" && cf.calls[0].auth === "Bearer cf-token" &&
     cf.calls[0].body.ttl === 3720 && !urls.some((u) => /:53\b/.test(u) || u.startsWith("javascript")) &&
     urls.includes("turns:turn.cloudflare.com:5349?transport=tcp") && r.body.ice.some((x) => x.username === "u-1" && x.credential === "c-1"),
     "the relay's credentials", r.body.ice);
  // The person says no.
  await report(A);
  r = await call("fleet", `/v1/help/${s2}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  ok(r.body.control_asked === false && r.body.ice.some((x) => x.username === "u-1"), "view only was asked; the computer gets the relay too", r.body);
  r = await helper({ op: "answer", allow: true, control: true }, r.body.token, s2);
  ok(r.body.control === false, "control can't be allowed when it wasn't asked", r.body);
  await admin(HELPER, { op: "help-end", session_id: s2 });
  r = await admin(HELPER, { op: "help-signal", session_id: s2 });
  ok(r.body.state === "ended" && r.body.detail === "the admin ended it", "the admin ends it", r.body);
  cf.down = true;
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  ok(r.status === 200 && r.body.relay === false, "Cloudflare unreachable: STUN only, still works", r.body);
  cf.down = false;
  const s3 = r.body.session_id;
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s4 = r.body.session_id;
  ok(r.status === 200 && (await db`select state, detail from fleet.help_sessions where id = ${s3}`)[0].detail === "started again" &&
     (await db`select status from fleet.actions where payload->>'session_id' = ${s3}`)[0].status === "cancelled",
     "the same admin starting again ends the old one", r);
  await report(A);
  r = await call("fleet", `/v1/help/${s4}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  r = await helper({ op: "answer", allow: false }, r.body.token, s4);
  r = await admin(HELPER, { op: "help-signal", session_id: s4 });
  ok(r.body.state === "declined" && r.body.detail === "they said no", "declined", r.body);

  // Nobody signed in; an older computer that fails the request; time running out.
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s5 = r.body.session_id;
  await report(A);
  await call("fleet", `/v1/help/${s5}`, { op: "unavailable", device_id: A.device_id, detail: "nobody is signed in" }, A.token);
  ok((await admin(HELPER, { op: "help-signal", session_id: s5 })).body.detail === "nobody is signed in", "nobody signed in");
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s6 = r.body.session_id;
  const act6 = (await report(A)).body.actions.find((a) => a.type === "remote-help");
  await computer(A, `/v1/actions/${act6.id}`, { status: "failed", detail: "unknown action remote-help" });
  ok((await admin(HELPER, { op: "help-signal", session_id: s6 })).body.state === "failed", "a computer that fails the request: failed");
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s7 = r.body.session_id;
  await db`update fleet.help_sessions set created_at = now() - interval '11 minutes' where id = ${s7}`;
  r = await admin(HELPER, { op: "help-signal", session_id: s7 });
  ok(r.body.state === "expired" && r.body.detail.includes("off or offline"), "never picked up: expired", r.body);
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s8 = r.body.session_id;
  await report(A);
  r = await call("fleet", `/v1/help/${s8}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  const t8 = r.body.token;
  await db`update fleet.help_sessions set updated_at = now() - interval '3 minutes' where id = ${s8}`;
  r = await helper({ op: "answer", allow: true }, t8, s8);
  ok(r.status === 409 && r.body.state === "expired", "not answered in 2 minutes: too late", r);
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s9 = r.body.session_id;
  await report(A);
  r = await call("fleet", `/v1/help/${s9}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  const t9 = r.body.token;
  await helper({ op: "answer", allow: true }, t9, s9);
  await db`update fleet.help_sessions set created_at = now() - interval '62 minutes' where id = ${s9}`;
  r = await helper({ op: "signal" }, t9, s9);
  ok(r.body.state === "ended" && r.body.detail.includes("an hour"), "an hour at most", r.body);
  // A computer that leaves ends its session.
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  await db`update fleet.help_sessions set state = 'ended' where device_id = ${A.device_id} and state in ('requested','asking','allowed','connected')`;
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  ok(r.status === 429, "asking the same computer again at once: wait", r);
  // An admin whose access is removed: their session ends at once.
  await admin(NEWOWNER, { op: "add-admin", organisation_id: org, email: "temp@acme.test" });
  await aged();
  await db`update fleet.help_sessions set state = 'ended' where device_id = ${A.device_id} and state in ('requested','asking','allowed','connected')`;
  r = await admin("temp@acme.test", { op: "help-start", device_id: A.device_id });
  const sT = r.body.session_id;
  ok(r.status === 200, "(an admin who'll be removed asks)", r);
  r = await call("fleet", `/v1/help/${sT}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  ok(r.body.organisation === "Acme", "an organisation's own admin: from Acme", r.body);
  await admin(NEWOWNER, { op: "remove-admin", organisation_id: org, email: "temp@acme.test" });
  ok((await db`select state, detail from fleet.help_sessions where id = ${sT}`)[0].detail === "their access was removed",
     "access removed: their session ends");
  // Kulan's own support (an admin for every organisation) says so.
  await aged();
  r = await admin(BOSS2, { op: "help-start", device_id: A.device_id });
  ok(r.status === 200, "(a Kulan support admin asks)", r);
  r = await call("fleet", `/v1/help/${r.body.session_id}`, { op: "claim", device_id: A.device_id, user: "mo" }, A.token);
  ok(r.body.organisation === "Kulan Fleet support, for Acme", "Kulan support says so to the person", r.body);
  // Two at once can't both be live on one computer.
  let dup = null;
  try { await db`insert into fleet.help_sessions (organisation_id, device_id, requested_by) values (${org}, ${A.device_id}, 'x@y')`; }
  catch (e) { dup = e.code; }
  ok(dup === "23505", "one live session per computer, kept by the database", dup);
  await db`update fleet.help_sessions set state = 'ended' where device_id = ${A.device_id} and state in ('requested','asking','allowed','connected')`;
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  const s10 = r.body.session_id;
  await computer(A, "/v1/leave");
  ok((await db`select state, detail from fleet.help_sessions where id = ${s10}`)[0].detail === "the computer left the organisation" &&
     (await db`select wake_topic from fleet.devices where id = ${A.device_id}`)[0].wake_topic === null,
     "a computer that leaves: its session ends, its wake topic goes");
  await aged();
  r = await admin(HELPER, { op: "help-start", device_id: A.device_id });
  ok(r.status === 400, "nothing on a computer that left", r);

  r = await admin(HELPER, { op: "log", organisation_id: org, what: "remote help" });
  const hl = r.body.entries.map((e) => `${e.email}: ${e.what}`);
  for (const want of ["mo: allowed remote help, with control", "mo: ended remote help", "mo: declined remote help",
                      `${HELPER}: ended remote help`]) ok(hl.includes(want), `in the activity log: ${want}`, hl);
  r = await admin(HELPER, { op: "log", organisation_id: org, what: "the screen" });
  ok(r.body.entries.some((e) => e.what === "asked to see and control the screen") &&
     r.body.entries.some((e) => e.what === "asked to see the screen"), "asking is logged", r.body.entries.map((e) => e.what));
  ok(!JSON.stringify((await db`select detail from fleet.audit`).map((x) => x.detail)).includes("v=0"), "nothing of the connection in the log");
  env.CLOUDFLARE_TURN_KEY_ID = env.CLOUDFLARE_TURN_TOKEN = undefined;
}

await db.end();
if (failures) {
  console.error(`${failures} fleet function check(s) failed`);
  process.exit(1);
}
console.log("fleet functions test passed");
process.exit(0);
