// Kulan OS fleet server, protocol v1 (the other end of /usr/bin/kulan-fleet).
//
//   POST /fleet/v1/enrol          {code, hostname, os, kulan_version} -> {device_id, token, organisation}
//   POST /fleet/v1/report         Bearer token, {device_id, ...status, apps?, passwords?, people_status?, usb_seen?,
//                                 sign_ins?, factors?, factors_removed?, pushed?}
//                                 -> {actions: [{id, type, payload?}], policy: {...}, accounts: [...]}
//   POST /fleet/v1/actions/{id}   Bearer token, {device_id, status: done|failed, detail}
//   POST /fleet/v1/leave          Bearer token, {device_id}
//   POST /fleet/v1/help/{id}      remote help (2.9a), below
//   POST /fleet/v1/help-requests  Bearer token, {device_id, username, text, screenshot?} -> {id} (2.9c)
//
// People (Kulan OS 2.8): each reply lists the organisation's accounts allowed
// on that computer, with their password hashes (one-way; never a password),
// and people removed in the last 30 days so the computer removes them too.
// A report may carry hashes of passwords people changed on that computer;
// the server takes one only from a computer that person may use.
//
// Groups (2.8b): a computer's policy is its organisation's and its groups',
// the stricter rule winning (policy.ts), with the approved USB drives when
// it says "approved only". People may be allowed on a group's computers.
//
// Who uses which computer (2.8c): a report may list each account's last
// sign-in there and whether it's signed in now; nothing else about it.
//
// Two-step sign-in (2.8d): a person sets up a security key or a phone code on
// one computer; it comes here with that computer's report and goes to every
// computer they may use, in their account ("two_step"). A phone code's
// secret is kept sealed (AES-GCM, FLEET_FACTOR_KEY, which only this function
// has) and opened only to send it to the person's own computers.
//
// Work email (2.8e): someone with a mailbox Kulan Fleet manages gets
// mail: {address, provider} in their account, for the computer to offer to
// connect it (GNOME Online Accounts for "google"; Outlook on the web for
// "microsoft", 2.12, which computers before 2.12 ignore); computers with
// Google Workspace mailboxes on them get Geary, the mail app, as a required
// app (unless blocked).
//
// Remote help (2.9a, help.ts): each report reply gives the computer its wake
// topic ("wake": {topic, url, key}, Supabase Realtime), where fleet-admin says
// "report now", so a request arrives in seconds. A remote-help request
// {session_id} is then picked up by the computer (kulan-fleet, its own key):
//   {op: "claim", device_id, user}          -> {token, admin, control_asked, ice, organisation}
//   {op: "unavailable", device_id, detail}  nobody is signed in, or it can't be done here
// and the rest is the helper's in that person's session, with the session's own key:
//   {op: "answer", allow, control}          the person allowed (and, if asked, control) or declined
//   {op: "signal", offer?, candidates?, since?}   -> {state, control, answer, candidates (the admin's, from since)}
//                                           (the computer makes the offer once allowed; the admin answers)
//   {op: "connected"} | {op: "end", detail?} | {op: "failed", detail?}
// The screen itself goes straight to the admin (WebRTC), never through here.
//
// Actions (2.9b, actions.ts): run-fix {fix}, message {title, body} and
// run-script {script_id, sha256, name}. A script's text is fetched with
//   POST /fleet/v1/scripts/{action id}   Bearer token, {device_id} -> {body, sha256}
// only while that request is out to this computer, and only if the script
// still has the SHA-256 the request was made with. A fix's or script's output
// (64 KB) and exit code come back with the result. Each reply's policy says
// whether the organisation allows scripts ("scripts": true), and lists the
// Wi-Fi networks ("networks", passwords opened only for this) and printers
// ("printers") its groups give it.
//
// Signed scripts (2.10): each reply's policy carries the Owner's public script
// key ("script_key", and "script_key_endorsement", the old key's signature of
// it, after the Owner changed their code), and a run-script request carries
// the Owner's signature of the script's SHA-256 ("signature"). The computer
// runs only what that key signed (kulan-fleet's scriptkey.py).
//
// Get help (2.9c): someone at the computer writes what's wrong in Kulan
// Settings, and may add a screenshot they saw first (base64 JPEG or PNG, 360
// KB at most); it reaches the organisation's admins (fleet-admin). Each
// report reply lists the computer's help requests of the last 30 days
// ("help_requests": {id, username, text, state, reply, ...}), so the person
// sees theirs, and the admin's answer when it's done.
//
// Approval (2.10): a computer that joined with a code an admin other than
// the Owner made waits for the Owner to approve it. Until then its reports are
// kept, but it gets its policy, its help requests and its wake topic only: no
// accounts (so no password hashes or phone codes), no Wi-Fi passwords, no
// printers, no requests, and nothing it says about passwords or second steps
// is taken. Reports are limited to 20 in 10 minutes per computer.
//
// Computers authenticate with their own key (made here at enrolment; only its
// SHA-256 is stored), so the platform's JWT check is off for this function.
// Data lives in the private "fleet" schema, reached over a direct Postgres
// connection, never through the public Data API.
import postgres from "npm:postgres@3.4.5";
import { mergePolicies } from "./policy.ts";
import * as acts from "./actions.ts";
import * as help from "./help.ts";
import * as secrets from "./secrets.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
// Any JSON-able value as jsonb (postgres.js's types want its own JSON type).
const json = (v: unknown) => sql.json(v as Parameters<typeof sql.json>[0]);
const MAX_BODY = 512 * 1024; // a report with a full app list
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart", "erase",
                         "shutdown", "lost-mode-on", "lost-mode-off", "install-app", "remote-help",
                         "run-fix", "run-script", "message"]);
const MAX_USB_SEEN = 20;
const MAX_APPS = 3000;
const USERNAME = /^[a-z][a-z0-9_-]{0,31}$/;
// crypt(3) hashes: yescrypt, SHA-512, SHA-256, bcrypt; nothing that could
// spill into /etc/shadow's other fields.
const HASH = /^\$(y|gy|6|5|2b|2y|7)\$[./A-Za-z0-9$=,]{8,250}$/;
const MAX_PASSWORDS = 50;
const MAX_SIGN_INS = 200;
const MAX_FACTORS = 10; // per person
const MAX_POLICY_STATUS = 300;
const REPORTS_PER_10_MIN = 20;
const U2F = /^[A-Za-z0-9+\/=_-]{16,1024},[A-Za-z0-9+\/=_-]{16,1024},(es256|eddsa|rs256),[+a-z]{0,40}$/;
const TOTP_HEX = /^[0-9a-f]{40}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// Passwords changed on a computer: [{username, password_hash}] (well-formed only).
export function cleanPasswords(value: unknown): { username: string; hash: string }[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.slice(0, MAX_PASSWORDS).flatMap((p) => {
    if (typeof p !== "object" || p === null) return [];
    const { username, password_hash } = p as Record<string, unknown>;
    if (typeof username !== "string" || !USERNAME.test(username) || seen.has(username)) return [];
    if (typeof password_hash !== "string" || !HASH.test(password_hash)) return [];
    seen.add(username);
    return [{ username, hash: password_hash }];
  });
}

// How the accounts are doing on a computer ({username: "created" ...}), kept in its report.
export function cleanPeopleStatus(value: unknown): Record<string, string> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const out: Record<string, string> = {};
  for (const [user, status] of Object.entries(value).slice(0, 500)) {
    if (USERNAME.test(user) && typeof status === "string") out[user] = status.slice(0, 160);
  }
  return out;
}

// USB drives a computer has seen (for an admin to approve one): the drive's
// vendor, product and serial and its name, nothing about what's on it.
export function cleanUsbSeen(value: unknown): Record<string, string>[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const out: Record<string, string>[] = [];
  for (const d of value.slice(0, MAX_USB_SEEN)) {
    if (typeof d !== "object" || d === null) continue;
    const { vendor_id, product_id, serial, name } = d as Record<string, unknown>;
    if (typeof vendor_id !== "string" || !/^[0-9a-f]{4}$/.test(vendor_id)) continue;
    if (typeof product_id !== "string" || !/^[0-9a-f]{4}$/.test(product_id)) continue;
    if (typeof serial !== "string" || !/^[A-Za-z0-9._-]{4,64}$/.test(serial)) continue;
    out.push({ vendor_id, product_id, serial, name: typeof name === "string" ? name.replace(/[^\w .,()+-]/g, "").slice(0, 80) : "" });
  }
  return out;
}

// Who signs in on a computer: [{username, last (seconds since 1970) | null, now}].
export function cleanSignIns(value: unknown): { username: string; last: number | null; now: boolean }[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const seen = new Set<string>();
  return value.slice(0, MAX_SIGN_INS).flatMap((s) => {
    if (typeof s !== "object" || s === null) return [];
    const { username, last, now } = s as Record<string, unknown>;
    if (typeof username !== "string" || !USERNAME.test(username) || seen.has(username)) return [];
    seen.add(username);
    const at = typeof last === "number" && Number.isInteger(last) && last > 946684800 && last < 4102444800 ? last : null;
    return [{ username, last: at, now: now === true }];
  });
}

// Second steps set up on a computer: [{username, id, kind: key|code, label, data}].
export type FactorIn = { username: string; id: string; kind: "key" | "code"; label: string; data: string };
export function cleanFactors(value: unknown): FactorIn[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 20).flatMap((f) => {
    if (typeof f !== "object" || f === null) return [];
    const { username, id, kind, label, data } = f as Record<string, unknown>;
    if (typeof username !== "string" || !USERNAME.test(username) || typeof id !== "string" || !UUID.test(id)) return [];
    if (kind === "key" ? !(typeof data === "string" && U2F.test(data))
        : kind === "code" ? !(typeof data === "string" && TOTP_HEX.test(data)) : true) return [];
    const name = typeof label === "string" ? label.replace(/[^\w .,'()+-]/g, "").trim().slice(0, 60) : "";
    return [{ username, id, kind: kind as FactorIn["kind"], label: name, data: data as string }];
  });
}

export function cleanRemovedFactors(value: unknown): { username: string; id: string }[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 20).flatMap((f) => {
    const { username, id } = (typeof f === "object" && f !== null ? f : {}) as Record<string, unknown>;
    return typeof username === "string" && USERNAME.test(username) && typeof id === "string" && UUID.test(id)
      ? [{ username, id }] : [];
  });
}

let sealKey: CryptoKey | null | undefined;
async function factorKey(): Promise<CryptoKey | null> {
  if (sealKey !== undefined) return sealKey;
  try {
    const raw = Uint8Array.from(atob(Deno.env.get("FLEET_FACTOR_KEY") ?? ""), (c) => c.charCodeAt(0));
    sealKey = raw.length === 32 ? await crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]) : null;
  } catch {
    sealKey = null;
  }
  return sealKey;
}

// A phone code's secret, sealed for keeping: "v1:" + base64(12-byte IV + AES-GCM ciphertext).
export async function sealCode(hex: string, key: CryptoKey): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(hex)));
  const all = new Uint8Array(12 + ct.length);
  all.set(iv);
  all.set(ct, 12);
  return "v1:" + btoa(String.fromCharCode(...all));
}

export async function openCode(sealed: string, key: CryptoKey): Promise<string | null> {
  try {
    if (!sealed.startsWith("v1:")) return null;
    const all = Uint8Array.from(atob(sealed.slice(3)), (c) => c.charCodeAt(0));
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: all.slice(0, 12) }, key, all.slice(12));
    const hex = new TextDecoder().decode(plain);
    return TOTP_HEX.test(hex) ? hex : null;
  } catch {
    return null;
  }
}

type PersonRow = Record<string, any>;

// One person as the computer reads it (kulan-fleet's people.py).
export function account(p: PersonRow, twoStep?: { required: boolean; factors: Record<string, string>[];
                                                  codes_unavailable?: boolean }, provider?: string | null): Record<string, unknown> {
  if (p.removed_at) return { username: p.username, uid: p.uid, removed: true, remove_files: p.remove_files === true };
  return {
    username: p.username, full_name: p.full_name ?? "", uid: p.uid, role: p.role, enabled: p.enabled === true,
    password_hash: p.password_hash ?? null, must_change: p.must_change === true,
    password_changed_at: Number(p.password_changed_at ?? 0),
    two_step: twoStep ?? { required: false, factors: [] },
    ...(hasMail(p) && provider ? { mail: { address: p.mailbox, provider } } : {}),
  };
}

// A working mailbox Kulan Fleet manages for someone who may sign in.
export const hasMail = (p: PersonRow) => !p.removed_at && p.enabled === true && typeof p.mailbox === "string" &&
  p.mailbox_state === "active";
const MAIL_APP = { source: "debian", id: "geary" } as const;

// The apps a computer sends once a day: names, versions and where from, nothing else.
function cleanApps(value: unknown): { name: string; version: string; source: string }[] | null {
  if (!Array.isArray(value)) return null;
  return value.slice(0, MAX_APPS).flatMap((a) => {
    if (typeof a !== "object" || a === null) return [];
    const name = text((a as Record<string, unknown>).name, 120)?.trim();
    const source = (a as Record<string, unknown>).source;
    if (!name || (source !== "debian" && source !== "flatpak")) return [];
    return [{ name, version: text((a as Record<string, unknown>).version, 80) ?? "", source }];
  });
}

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

function newToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function text(value: unknown, max: number): string | null {
  return typeof value === "string" ? value.slice(0, max) : null;
}

// What a computer or its person says about a session: plain words, short.
export function cleanDetail(value: unknown): string | null {
  return typeof value === "string" ? value.replace(/[^\p{L}\p{N}\p{P}\p{Zs}]/gu, "").trim().slice(0, 200) || null : null;
}

function publishableKey(): string {
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_PUBLISHABLE_KEYS") ?? "{}");
    if (keys.default) return keys.default;
  } catch { /* fall back */ }
  return Deno.env.get("SUPABASE_ANON_KEY") ?? "";
}

// Where a computer listens for "report now" (Supabase Realtime broadcast).
export function wakeInfo(topic: string): { topic: string; url: string; key: string } {
  const base = (Deno.env.get("SUPABASE_URL") ?? "").replace(/^http/, "ws");
  return { topic, url: `${base}/realtime/v1/websocket`, key: publishableKey() };
}

function newWakeTopic(): string {
  return "wake-" + Array.from(crypto.getRandomValues(new Uint8Array(24)), (b) => b.toString(16).padStart(2, "0")).join("");
}

// Remote help, the computer's side (see the top of this file).
async function helpRoute(req: Request, id: string, body: Record<string, unknown>): Promise<Response> {
  const op = body.op;
  if (op === "claim" || op === "unavailable") {
    const dev = await device(req, body);
    if (!dev) return reply(401, { error: "unknown computer or key" });
    await sql`select fleet.expire_help_sessions()`;
    const [s] = await sql`select s.id, s.state, s.requested_by, s.control_asked, s.ice, s.organisation_id, o.name
                          from fleet.help_sessions s join fleet.organisations o on o.id = s.organisation_id
                          where s.id = ${id} and s.device_id = ${dev.id}`;
    if (!s) return reply(404, { error: "no such session" });
    if (s.state !== "requested") return reply(409, { error: `that session is ${s.state}`, state: s.state });
    if (op === "unavailable") {
      const detail = cleanDetail(body.detail) ?? "it can't be done on this computer now";
      await sql`update fleet.help_sessions set state = 'failed', detail = ${detail}, ended_at = now(), updated_at = now()
                where id = ${id} and state = 'requested'`;
      return reply(200, {});
    }
    const user = typeof body.user === "string" && /^[a-z][a-z0-9_-]{0,31}$/.test(body.user) ? body.user : null;
    if (!user) return reply(400, { error: "who is signed in?" });
    const token = help.sessionToken();
    const claimed = await sql`update fleet.help_sessions set state = 'asking', token_hash = ${await sha256(token)},
                                     answered_by = ${user}, updated_at = now()
                              where id = ${id} and state = 'requested' returning id`;
    if (!claimed.length) return reply(409, { error: "that session has changed" });
    // An admin for every organisation (Kulan's own support) says so, not as one of the organisation's own.
    const kulan = await sql`select 1 from fleet.admins where email = ${s.requested_by} and organisation_id is null`;
    return reply(200, { token, admin: s.requested_by, control_asked: s.control_asked, ice: s.ice,
                        organisation: kulan.length ? `Kulan Fleet support, for ${s.name}` : s.name });
  }

  // The helper in the person's session: the session's own key, only for this session.
  const auth = req.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return reply(401, { error: "no key" });
  const hash = await sha256(auth.slice(7).trim());
  if (!(await sql`select 1 from fleet.help_sessions where id = ${id} and token_hash = ${hash}`).length) {
    return reply(401, { error: "unknown session or key" });
  }
  await sql`select fleet.expire_help_sessions()`;
  const [s] = await sql`select * from fleet.help_sessions where id = ${id} and token_hash = ${hash}`;
  const log = (what: string, detail: Record<string, unknown> = {}) =>
    sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
        values (${s.organisation_id}, ${s.device_id}, ${s.answered_by}, ${what}, ${json({ session: s.id, admin: s.requested_by, ...detail })})`;
  const ended = !help.ACTIVE.includes(s.state);
  if (op === "answer") {
    if (s.state !== "asking") return reply(409, { error: `that session is ${s.state}`, state: s.state });
    const allow = body.allow === true;
    const control = allow && s.control_asked && body.control === true;
    await sql`update fleet.help_sessions set state = ${allow ? "allowed" : "declined"}, control = ${control},
                     detail = ${allow ? null : "they said no"}, updated_at = now(), ended_at = ${allow ? null : sql`now()`}
              where id = ${id} and state = 'asking'`;
    await log(allow ? (control ? "allowed remote help, with control" : "allowed remote help (view only)") : "declined remote help");
    return reply(200, { state: allow ? "allowed" : "declined", control });
  }
  if (op === "signal") {
    if (ended) return reply(200, { state: s.state, detail: s.detail });
    if (body.offer !== undefined) {
      const offer = help.cleanDescription(body.offer, "offer");
      if (!offer || s.state !== "allowed" || s.offer) return reply(400, { error: "not an offer that fits now" });
      await sql`update fleet.help_sessions set offer = ${json(offer)}, updated_at = now() where id = ${id} and offer is null`;
    }
    const candidates = help.cleanCandidates(body.candidates);
    if (candidates.length) await help.addCandidates(sql, id, "device_candidates", candidates);
    const since = Number.isInteger(body.since) && (body.since as number) >= 0 ? body.since as number : 0;
    const theirs = Array.isArray(s.admin_candidates) ? s.admin_candidates : [];
    return reply(200, { state: s.state, control: s.control, answer: s.answer ?? null,
                        candidates: theirs.slice(since), count: theirs.length });
  }
  if (op === "connected") {
    if (s.state !== "allowed") return reply(409, { error: `that session is ${s.state}`, state: s.state });
    await sql`update fleet.help_sessions set state = 'connected', updated_at = now() where id = ${id} and state = 'allowed'`;
    return reply(200, { state: "connected" });
  }
  if (op === "end" || op === "failed") {
    if (ended) return reply(200, { state: s.state });
    const detail = cleanDetail(body.detail) ?? (op === "end" ? "they stopped it" : "it couldn't connect");
    const done = await sql`update fleet.help_sessions set state = ${op === "end" ? "ended" : "failed"}, detail = ${detail},
                                  ended_at = now(), updated_at = now()
                           where id = ${id} and state = any(${help.ACTIVE}::text[]) returning id`;
    if (done.length && op === "end" && s.state !== "asking") await log("ended remote help", { detail });
    return reply(200, { state: op === "end" ? "ended" : "failed" });
  }
  return reply(400, { error: "unknown op" });
}

// The compliance checks a computer reports ({check: "met" ...}): a few hundred, short.
export function cleanPolicyStatus(value: unknown): Record<string, string> | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const out: Record<string, string> = {};
  for (const [key, status] of Object.entries(value).slice(0, MAX_POLICY_STATUS)) {
    if (/^[\w.:-]{1,80}$/.test(key) && typeof status === "string") out[key] = status.slice(0, 40);
  }
  return out;
}

// The request's body, at most max bytes (read in pieces, never all of a larger one).
export async function readBody(req: Request, max: number): Promise<string | null> {
  if (Number(req.headers.get("Content-Length") ?? "0") > max) return null;
  if (!req.body) return "";
  const reader = req.body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > max) {
      await reader.cancel().catch(() => {});
      return null;
    }
    parts.push(value);
  }
  const all = new Uint8Array(size);
  let at = 0;
  for (const p of parts) {
    all.set(p, at);
    at += p.length;
  }
  return new TextDecoder().decode(all);
}

async function device(req: Request, body: Record<string, unknown>) {
  const auth = req.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const hash = await sha256(auth.slice(7).trim());
  const rows = await sql`select id, organisation_id, hostname, approved_at is not null as approved from fleet.devices
                         where token_hash = ${hash} and left_at is null`;
  if (rows.length !== 1 || rows[0].id !== body.device_id) return null;
  return rows[0];
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  const raw = await readBody(req, MAX_BODY);
  if (raw === null) return reply(413, { error: "too large" });
  let body: Record<string, unknown>;
  try {
    const parsed = JSON.parse(raw || "{}");
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed;
  } catch {
    return reply(400, { error: "JSON object expected" });
  }
  const path = new URL(req.url).pathname.replace(/^.*\/fleet(?=\/v1\/)/, "");

  try {
    const helpPath = path.match(/^\/v1\/help\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/);
    if (helpPath) return await helpRoute(req, helpPath[1], body);

    if (path === "/v1/enrol") {
      const code = text(body.code, 64)?.trim().toUpperCase();
      if (!code) return reply(400, { error: "code missing" });
      const codeHash = await sha256(code);
      const token = newToken();
      const result = await sql.begin(async (tx) => {
        const codes = await tx`update fleet.enrolment_codes set uses_left = uses_left - 1
                               where code_hash = ${codeHash} and uses_left > 0 and expires_at > now()
                               returning organisation_id, trusted, created_by`;
        if (codes.length !== 1) return null;
        const { organisation_id: org, trusted, created_by } = codes[0];
        const hostname = text(body.hostname, 120);
        const [dev] = await tx`insert into fleet.devices (organisation_id, token_hash, hostname, os, kulan_version, last_seen,
                                                          approved_at, approved_by)
                               values (${org}, ${await sha256(token)}, ${hostname},
                                       ${text(body.os, 120)}, ${text(body.kulan_version, 60)}, now(),
                                       case when ${trusted}::boolean then now() end, ${trusted ? created_by : null})
                               returning id`;
        await tx`insert into fleet.audit (organisation_id, device_id, email, what, detail)
                 values (${org}, ${dev.id}, ${created_by ?? "the computer"},
                         ${trusted ? "a computer joined" : "a computer joined and waits for the Owner's approval"},
                         ${json({ computer: hostname })})`;
        const [o] = await tx`select name from fleet.organisations where id = ${org}`;
        return { device_id: dev.id, organisation: o.name, ...(trusted ? {} : { pending: true }) };
      });
      if (!result) return reply(403, { error: "that code isn't valid. It may be used, expired or mistyped" });
      return reply(200, { ...result, token });
    }

    const dev = await device(req, body);
    if (!dev) return reply(401, { error: "unknown computer or key" });

    if (path === "/v1/report") {
      const [{ recent }] = await sql`select count(*)::int as recent from fleet.reports
                                     where device_id = ${dev.id} and received_at > now() - interval '10 minutes'`;
      if (recent >= REPORTS_PER_10_MIN) return reply(429, { error: "too many reports from this computer. Try again later" });
      const apps = cleanApps(body.apps);
      delete body.apps; // kept on the computer's row, not in every hourly report
      const passwordsIn = cleanPasswords(body.passwords);
      delete body.passwords; // into fleet.people only, never into the reports
      const peopleStatus = cleanPeopleStatus(body.people_status);
      if (peopleStatus === undefined) delete body.people_status;
      else body.people_status = peopleStatus;
      const usbSeen = cleanUsbSeen(body.usb_seen);
      if (usbSeen === undefined) delete body.usb_seen;
      else body.usb_seen = usbSeen;
      const signIns = cleanSignIns(body.sign_ins);
      if (signIns === undefined) delete body.sign_ins;
      else body.sign_ins = signIns;
      const pushed = acts.cleanPushed(body.pushed);
      if (pushed === undefined) delete body.pushed;
      else body.pushed = pushed;
      const policyStatus = cleanPolicyStatus(body.policy_status);
      if (policyStatus === undefined) delete body.policy_status;
      else body.policy_status = policyStatus;
      // A computer waiting for approval: nothing it says about passwords or second steps is taken.
      const factorsAdded = dev.approved ? cleanFactors(body.factors) : [];
      const factorsRemoved = dev.approved ? cleanRemovedFactors(body.factors_removed) : [];
      const passwords = dev.approved ? passwordsIn : [];
      delete body.factors; // into fleet.person_factors only, never into the reports
      delete body.factors_removed;
      // Who was signed in here at the last report (before this one): an admin's
      // or the Owner's password or second step is taken only from a computer
      // they were signed in on, so one computer can't change theirs unseen.
      const [prev] = await sql`select last_report->'sign_ins' as sign_ins, hostname from fleet.devices where id = ${dev.id}`;
      const signedInBefore = new Set((cleanSignIns(prev?.sign_ins) ?? []).filter((x) => x.now).map((x) => x.username));
      // A person on this computer, if they may use it and aren't disabled or removed.
      const allowedHere = async (username: string): Promise<string | null> => {
        const rows = await sql`
          select pe.id from fleet.people pe
          where pe.organisation_id = ${dev.organisation_id} and pe.username = ${username}
            and pe.removed_at is null and pe.enabled
            and (pe.role = 'employee' or ${signedInBefore.has(username)})
            and (pe.all_computers
                 or exists (select 1 from fleet.person_computers pc where pc.person_id = pe.id and pc.device_id = ${dev.id})
                 or exists (select 1 from fleet.person_groups pg join fleet.group_devices gd on gd.group_id = pg.group_id
                            where pg.person_id = pe.id and gd.device_id = ${dev.id}))`;
        return rows.length ? rows[0].id : null;
      };
      for (const f of factorsAdded) {
        const pid = await allowedHere(f.username);
        if (!pid) continue;
        let data = f.data;
        if (f.kind === "code") {
          const key = await factorKey();
          if (!key) {
            console.error("fleet: FLEET_FACTOR_KEY isn't set: a phone code can't be kept");
            continue;
          }
          data = await sealCode(f.data, key);
        }
        const [{ n }] = await sql`select count(*)::int as n from fleet.person_factors where person_id = ${pid}`;
        if (n >= MAX_FACTORS) continue;
        const added = await sql`insert into fleet.person_factors (id, person_id, kind, label, data, created_on)
                                values (${f.id}, ${pid}, ${f.kind}, ${f.label}, ${data}, ${text(dev.hostname, 120)})
                                on conflict (id) do nothing returning id`;
        if (added.length) {
          await sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
                    values (${dev.organisation_id}, ${dev.id}, ${f.username},
                            ${f.kind === "key" ? "set up a security key for signing in" : "set up a phone code for signing in"},
                            ${json({ label: f.label, computer: dev.hostname })})`;
        }
      }
      for (const f of factorsRemoved) {
        const pid = await allowedHere(f.username);
        if (!pid) continue;
        const gone = await sql`delete from fleet.person_factors where id = ${f.id} and person_id = ${pid} returning kind, label`;
        if (gone.length) {
          await sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
                    values (${dev.organisation_id}, ${dev.id}, ${f.username}, 'removed a second step for signing in',
                            ${json({ kind: gone[0].kind, label: gone[0].label, computer: dev.hostname })})`;
        }
      }
      // A new password from a person on this computer: taken if they may use
      // it, aren't disabled or removed, and it really is new. The latest to
      // arrive wins (the server's clock, not the computer's).
      for (const p of passwords) {
        const changed = await sql`
          update fleet.people pe set password_hash = ${p.hash}, must_change = false,
                 password_changed_at = greatest(extract(epoch from now())::bigint, pe.password_changed_at + 1)
          where pe.organisation_id = ${dev.organisation_id} and pe.username = ${p.username}
            and pe.removed_at is null and pe.enabled and pe.password_hash is distinct from ${p.hash}
            and (pe.role = 'employee' or ${signedInBefore.has(p.username)})
            and (pe.all_computers
                 or exists (select 1 from fleet.person_computers pc where pc.person_id = pe.id and pc.device_id = ${dev.id})
                 or exists (select 1 from fleet.person_groups pg join fleet.group_devices gd on gd.group_id = pg.group_id
                            where pg.person_id = pe.id and gd.device_id = ${dev.id}))
          returning pe.id`;
        if (changed.length) {
          await sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
                    values (${dev.organisation_id}, ${dev.id}, ${p.username}, 'changed their password on a computer',
                            ${json({ username: p.username, computer: dev.hostname })})`;
        }
      }
      if (apps) await sql`update fleet.devices set apps = ${json(apps)}, apps_at = now() where id = ${dev.id}`;
      await sql`insert into fleet.reports (device_id, report) values (${dev.id}, ${json(body)})`;
      if (Math.random() < 0.01) await sql`select fleet.prune_reports(), fleet.prune_people()`;
      const [{ wake_topic }] = await sql`update fleet.devices set last_seen = now(), last_report = ${json(body)},
                  hostname = coalesce(${text(body.hostname, 120)}, hostname),
                  os = coalesce(${text(body.os, 120)}, os),
                  kulan_version = coalesce(${text(body.kulan_version, 60)}, kulan_version),
                  wake_topic = coalesce(wake_topic, ${newWakeTopic()})
                where id = ${dev.id} returning wake_topic`;
      const newName = text(body.hostname, 120);
      if (newName && prev?.hostname && newName !== prev.hostname) {
        await sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
                  values (${dev.organisation_id}, ${dev.id}, 'the computer', 'a computer was renamed',
                          ${json({ from: prev.hostname, to: newName })})`;
      }
      const helpRequests = await sql`select id, username, body, state, reply, created_at, done_at from fleet.help_requests
                                     where device_id = ${dev.id} and created_at > now() - interval '30 days'
                                     order by created_at desc limit 50`;
      const helpList = helpRequests.map((h) => ({ id: h.id, username: h.username, text: String(h.body).slice(0, 200),
                                                  state: h.state, reply: h.reply, created_at: h.created_at, done_at: h.done_at }));
      if (!dev.approved) {
        // Waiting for the Owner: its policy (no scripts, no USB drive list), its help requests, its wake topic.
        const [o] = await sql`select policy, script_key, script_key_endorsement from fleet.organisations
                              where id = ${dev.organisation_id}`;
        const { policy } = mergePolicies([o?.policy ?? {}]);
        const sentPending: Record<string, unknown> = { ...policy };
        if (o?.script_key) sentPending.script_key = o.script_key;
        if (o?.script_key && o.script_key_endorsement) sentPending.script_key_endorsement = o.script_key_endorsement;
        return reply(200, { pending: true, actions: [], policy: sentPending, help_requests: helpList, wake: wakeInfo(wake_topic) });
      }
      // A fix or script handed to the computer but never answered (it was switched off mid-run, say)
      // expires after a day: its script can't be fetched any more.
      await sql`update fleet.actions set status = 'expired', finished_at = now()
                where device_id = ${dev.id} and status = 'sent' and type in ('run-fix', 'run-script')
                  and sent_at < now() - interval '1 day'`;
      // An erase still waiting after 30 days expires instead (the computer was found, say).
      await sql`update fleet.actions set status = 'expired', finished_at = now()
                where device_id = ${dev.id} and status = 'pending' and type = 'erase'
                  and created_at < now() - interval '30 days'`;
      // In the order they were asked for.
      const actions = await sql`with sent as (
                                  update fleet.actions set status = 'sent', sent_at = now()
                                  where id in (select id from fleet.actions where device_id = ${dev.id}
                                               and status = 'pending' order by created_at limit 10)
                                  returning id, type, payload, created_at)
                                select id, type, payload from sent order by created_at`;
      // This computer's policy: the organisation's and its groups', the stricter rule winning.
      const [org] = await sql`select policy, allow_scripts, script_key, script_key_endorsement from fleet.organisations
                              where id = ${dev.organisation_id}`;
      const groups = await sql`select g.policy from fleet.groups g join fleet.group_devices gd on gd.group_id = g.id
                               where gd.device_id = ${dev.id}`;
      const { policy } = mergePolicies([org?.policy ?? {}, ...groups.map((g) => g.policy)]);
      const sent: Record<string, unknown> = { ...policy };
      if (org?.allow_scripts) sent.scripts = true;
      // The Owner's public key for scripts (the computer keeps the first it's told; 2.10).
      if (org?.script_key) sent.script_key = org.script_key;
      if (org?.script_key && org.script_key_endorsement) sent.script_key_endorsement = org.script_key_endorsement;
      // Wi-Fi networks and printers from this computer's groups (passwords opened only to send them here).
      const nets = await sql`select distinct on (n.ssid) n.id, n.group_id, n.ssid, n.security, n.secret, n.hidden
                             from fleet.group_networks n join fleet.group_devices gd on gd.group_id = n.group_id
                             where gd.device_id = ${dev.id} order by n.ssid, n.created_at`;
      const networks = [];
      for (const n of nets) {
        const password = n.secret ? await secrets.open(n.secret, "wifi", `wifi:${n.group_id}:${n.id}`) : null;
        if (n.secret && !password) {
          // Still listed, without the password: the computer keeps the network it has rather than dropping it.
          console.error("fleet: a Wi-Fi password can't be opened: is FLEET_FACTOR_KEY set, and unchanged?");
          networks.push({ id: n.id, ssid: n.ssid, security: n.security, hidden: n.hidden, unavailable: true });
          continue;
        }
        networks.push({ id: n.id, ssid: n.ssid, security: n.security, hidden: n.hidden, ...(password ? { password } : {}) });
      }
      const printers = await sql`select distinct on (lower(p.name)) p.id, p.name, p.uri
                                 from fleet.group_printers p join fleet.group_devices gd on gd.group_id = p.group_id
                                 where gd.device_id = ${dev.id} order by lower(p.name), p.created_at`;
      if (policy.usb === "approved") {
        sent.usb_drives = await sql`select vendor_id, product_id, serial from fleet.approved_drives
                                    where organisation_id = ${dev.organisation_id} order by created_at limit 500`;
      }
      // The people allowed on this computer, and anyone removed in the last 30 days.
      const people = await sql`
        select pe.id, pe.username, pe.full_name, pe.uid, pe.role, pe.enabled, pe.password_hash, pe.must_change,
               pe.password_changed_at, pe.removed_at, pe.remove_files, pe.mailbox, pe.mailbox_state
        from fleet.people pe
        where pe.organisation_id = ${dev.organisation_id}
          and (pe.removed_at is null or pe.removed_at > now() - interval '30 days')
          and (pe.all_computers
               or exists (select 1 from fleet.person_computers pc where pc.person_id = pe.id and pc.device_id = ${dev.id})
               or exists (select 1 from fleet.person_groups pg join fleet.group_devices gd on gd.group_id = pg.group_id
                          where pg.person_id = pe.id and gd.device_id = ${dev.id}))
        order by pe.uid`;
      // Their second steps for signing in, and whether this computer's policy asks for one.
      const live = people.filter((p) => !p.removed_at).map((p) => p.id);
      const factorRows = live.length ? await sql`select person_id, id, kind, label, data from fleet.person_factors
                                                  where person_id = any(${live}::uuid[]) order by created_at` : [];
      const key = factorRows.some((f) => f.kind === "code") ? await factorKey() : null;
      const factorsOf: Record<string, Record<string, string>[]> = {};
      const unopened = new Set<string>(); // phone codes this server couldn't open (its key missing or changed)
      for (const f of factorRows) {
        const data = f.kind === "code" ? (key ? await openCode(f.data, key) : null) : f.data;
        if (data) (factorsOf[f.person_id] ??= []).push({ id: f.id, kind: f.kind, label: f.label, data });
        else if (f.kind === "code") unopened.add(f.person_id);
      }
      if (unopened.size) console.error("fleet: phone codes can't be opened: is FLEET_FACTOR_KEY set, and unchanged?");
      const requiredFor = (p: PersonRow) => policy.two_step === "everyone" || (policy.two_step === "admins" && p.role !== "employee");
      // Work email: the organisation's mail provider, and the mail app where someone has a mailbox.
      const mailers = people.filter(hasMail);
      const [conn] = mailers.length ? await sql`select provider from fleet.mail_connections where organisation_id = ${dev.organisation_id}` : [];
      const provider: string | null = conn?.provider ?? null;
      // Geary for Google Workspace; Microsoft 365 mail opens in Outlook on the web (Geary can't read it).
      if (provider === "google" && !(policy.blocked_apps ?? []).some((a) => a.source === MAIL_APP.source && a.id === MAIL_APP.id) &&
          !(policy.required_apps ?? []).some((a) => a.source === MAIL_APP.source && a.id === MAIL_APP.id)) {
        sent.required_apps = [...(policy.required_apps ?? []), MAIL_APP];
      }
      return reply(200, {
        // This computer's help requests of the last 30 days, so each person sees theirs and any answer.
        help_requests: helpList,
        actions: actions.filter((a) => ACTIONS.has(a.type))
          .map((a) => a.payload ? { id: a.id, type: a.type, payload: a.payload } : { id: a.id, type: a.type }),
        policy: sent,
        accounts: people.map((p) => account(p, { required: requiredFor(p), factors: factorsOf[p.id] ?? [],
                                                 ...(unopened.has(p.id) ? { codes_unavailable: true } : {}) }, provider)),
        wake: wakeInfo(wake_topic),
        networks,
        printers: printers.map((p) => ({ id: p.id, name: p.name, uri: p.uri })),
      });
    }

    if (path === "/v1/help-requests") {
      const h = acts.cleanHelpRequest(body);
      if (!h) return reply(400, { error: "write what's wrong. A screenshot must be a JPEG or PNG of at most 360 KB" });
      // The name admins see only for an organisation account allowed on this computer:
      // a computer can't make a request look like it came from someone who doesn't use it.
      const [p] = await sql`
        select pe.full_name from fleet.people pe
        where pe.organisation_id = ${dev.organisation_id} and pe.username = ${h.username} and pe.removed_at is null
          and (pe.all_computers
               or exists (select 1 from fleet.person_computers pc where pc.person_id = pe.id and pc.device_id = ${dev.id})
               or exists (select 1 from fleet.person_groups pg join fleet.group_devices gd on gd.group_id = pg.group_id
                          where pg.person_id = pe.id and gd.device_id = ${dev.id}))`;
      // Never a flood on the admins: 5 an hour and 20 a day from one computer, 3 an hour from
      // one person there. Counted and kept in one step (one computer at a time).
      const row = await sql.begin(async (tx) => {
        await tx`select pg_advisory_xact_lock(hashtext(${"help:" + dev.id}))`;
        const [n] = await tx`select count(*) filter (where created_at > now() - interval '1 hour')::int as hour,
                                    count(*) filter (where created_at > now() - interval '1 hour' and username = ${h.username})::int as mine,
                                    count(*)::int as day
                             from fleet.help_requests where device_id = ${dev.id} and created_at > now() - interval '1 day'`;
        if (n.hour >= 5 || n.mine >= 3 || n.day >= 20) return null;
        const [added] = await tx`insert into fleet.help_requests (organisation_id, device_id, username, full_name, body, screenshot,
                                                                  screenshot_type)
                                 values (${dev.organisation_id}, ${dev.id}, ${h.username}, ${p?.full_name ?? ""}, ${h.body},
                                         ${h.screenshot}, ${h.type}) returning id, created_at`;
        return added;
      });
      if (!row) return reply(429, { error: "too many help requests from this computer. Try again later" });
      await sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
                values (${dev.organisation_id}, ${dev.id}, ${h.username}, 'asked for help',
                        ${json({ request: row.id, computer: dev.hostname, screenshot: h.screenshot !== null })})`;
      if (Math.random() < 0.05) await sql`select fleet.prune_help_requests()`;
      return reply(200, { id: row.id, created_at: row.created_at });
    }

    const done = path.match(/^\/v1\/actions\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/);
    if (done) {
      const status = body.status === "done" ? "done" : "failed";
      // A fix's or script's output and exit code (the first 64 KB).
      const output = typeof body.output === "string" ? new TextDecoder().decode(new TextEncoder().encode(body.output).slice(0, acts.OUTPUT_MAX))
        .replace(/\uFFFD$/, "").replaceAll("\0", "") : null;
      const exitCode = Number.isInteger(body.exit_code) ? Math.max(-1000, Math.min(1000, body.exit_code as number)) : null;
      const [a] = await sql`update fleet.actions set status = ${status}, detail = ${text(body.detail, 300)}, finished_at = now(),
                                   output = case when type in ('run-fix', 'run-script') then ${output} end,
                                   exit_code = case when type in ('run-fix', 'run-script') then ${exitCode}::int end
                            where id = ${done[1]} and device_id = ${dev.id} and status = 'sent' returning type, payload`;
      if (Math.random() < 0.01) await sql`select fleet.prune_action_outputs()`;
      // A computer that couldn't take a remote help request (an older Kulan OS, say): the session ends with why.
      if (a?.type === "remote-help" && status === "failed" && typeof a.payload?.session_id === "string") {
        await sql`update fleet.help_sessions set state = 'failed', ended_at = now(), updated_at = now(),
                         detail = ${cleanDetail(body.detail) ?? "the computer couldn't start it"}
                  where id = ${a.payload.session_id} and device_id = ${dev.id} and state = 'requested'`;
      }
      return reply(200, {});
    }

    const script = path.match(/^\/v1\/scripts\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/);
    if (script) {
      // Only the script of a request out to this computer now, still as it was asked for, while the Owner allows scripts.
      const [row] = await sql`select s.body, s.sha256 from fleet.actions a
                              join fleet.scripts s on s.id = (a.payload->>'script_id')::uuid
                              join fleet.organisations o on o.id = s.organisation_id
                              where a.id = ${script[1]} and a.device_id = ${dev.id} and a.type = 'run-script' and a.status = 'sent'
                                and s.organisation_id = ${dev.organisation_id} and o.allow_scripts
                                and s.sha256 = a.payload->>'sha256'`;
      if (!row) return reply(404, { error: "no such script, or it changed after it was asked for" });
      return reply(200, { body: row.body, sha256: row.sha256 });
    }

    if (path === "/v1/leave") {
      await sql`update fleet.devices set left_at = now(), token_hash = null, wake_topic = null where id = ${dev.id}`;
      await sql`update fleet.help_sessions set state = 'ended', detail = 'the computer left the organisation',
                       ended_at = now(), updated_at = now()
                where device_id = ${dev.id} and state = any(${help.ACTIVE}::text[])`;
      return reply(200, {});
    }
    return reply(404, { error: "unknown path" });
  } catch (e) {
    console.error("fleet:", e);
    return reply(500, { error: "server error" });
  }
});
