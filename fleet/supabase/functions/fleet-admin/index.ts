// Kulan fleet dashboard API. Called by the dashboard page (GitHub Pages) with
// the signed-in person's Supabase session token. Only emails listed in
// fleet.admins get anything; an admin with an organisation sees only that
// organisation, one without sees all.
//
//   POST {op: "overview"}                                  -> organisations (with policy), computers,
//                                                             recent actions, alerts
//   POST {op: "report", device_id}                         -> a computer's latest full report
//   POST {op: "apps", device_id}                           -> the apps on a computer (sent daily)
//   POST {op: "log", organisation_id}                      -> who changed what, newest first
//   POST {op: "new-organisation", name}                    -> {id}            (admins for all organisations)
//   POST {op: "new-code", organisation_id, hours, uses}    -> {code}          (shown once)
//   POST {op: "set-policy", organisation_id, policy}       -> {policy}
//   POST {op: "action", device_id, type, confirm?}         -> {id}
//   POST {op: "cancel-action", action_id}                  -> {}              (only while it waits)
//        "erase" needs confirm = the computer's name, and only works on a
//        computer that's encrypted and whose user allowed it there.
//
// People (Kulan OS 2.8): the accounts on the organisation's computers.
//   POST {op: "people", organisation_id}                   -> people (no hashes), each computer's status
//   POST {op: "add-person", organisation_id, username, full_name, email?, role,
//         all_computers, computers?, password_hash}         -> {id, uid}
//   POST {op: "edit-person", person_id, full_name?, email?, role?, all_computers?, computers?}
//   POST {op: "set-person-enabled", person_id, enabled}
//   POST {op: "new-setup-code", person_id, password_hash}
//   POST {op: "remove-person", person_id, remove_files, confirm}   (confirm = their username)
//        password_hash is the crypt(3) hash of a setup code the Kulan Fleet app
//        made and shows the admin once: the code itself never comes here.
// The Owner (the organisation's owner_email, or an admin for all
// organisations) is the only one who gives or takes the admin or owner role
// and changes who the Kulan Fleet admins are:
//   POST {op: "admins", organisation_id}                   -> admins, owner
//   POST {op: "add-admin" | "remove-admin", organisation_id, email}
//   POST {op: "set-owner", organisation_id, email}         -> {recovery_code} (admins for all organisations)
//   POST {op: "recover-owner", recovery_code}               -> {organisation, recovery_code}
//        signed in with a new email: the recovery code (shown once when the
//        Owner was set) makes that email the Owner, and a new code is given.
//
// Groups (2.8b): computers and people in groups, a policy per group (the
// stricter rule wins, policy.ts), approved USB drives, an app installed once
// on a group's computers.
//   POST {op: "groups", organisation_id}                   -> groups with members and policy
//   POST {op: "add-group", organisation_id, name, policy?} -> {id}
//   POST {op: "edit-group", group_id, name?, policy?, devices?, people?}
//   POST {op: "remove-group", group_id, confirm}           (confirm = its name)
//   POST {op: "device-policy", device_id}                  -> its policy, groups and any conflicts
//   POST {op: "drives", organisation_id}                   -> approved drives and drives computers have seen
//   POST {op: "approve-drive", organisation_id, vendor_id, product_id, serial, name}
//   POST {op: "remove-drive", drive_id}
//   POST {op: "group-action", group_id, type, payload?}    -> {count}  (refresh, install-updates,
//                                                             install-app, lock-screen, restart, shutdown)
//   add-person and edit-person also take groups: the groups whose computers they may use.
//
// Security and activity (2.8c):
//   POST {op: "weekly-report", organisation_id}            -> {week, summary, previous}
//        worked out now from the computers' last reports, people and the
//        activity log; this week's snapshot is kept so next week can compare.
//   POST {op: "access-review", organisation_id}            -> who has administrator rights, the last review
//   POST {op: "review-access", organisation_id, remove_people?, remove_admins?}   (the Owner)
//        records the review; takes the admin role from the people listed and
//        Kulan Fleet access from the admins listed. Due every 90 days.
//   "log" takes from, to (ISO dates), who, what (text to find), before (an
//   entry id, for the next page) and limit (up to 10000), for exporting.
//   "people" and "overview" include who signs in where (the computers' reports).
//
// Stronger sign-in (2.8d):
//   Kulan Fleet admins: an authenticator app code after the email code
//   (Supabase MFA). Anyone who has set one up must use it ("aal2" session);
//   the Owner can require it for every admin of the organisation:
//   POST {op: "set-admin-two-step", organisation_id, required}          (the Owner)
//   POST {op: "reset-admin-two-step", organisation_id, email}           (the Owner: a lost phone)
//   Without the code where it's needed, every op answers 403 with
//   two_step: "code" (type it) or "set-up" (set one up first).
//   On the computers: policy two_step ("admins" | "everyone"); people's
//   second steps are listed in "people" (never their data), and
//   POST {op: "reset-two-step", person_id} removes them (a lost key or phone).
//
// Work email (2.8e Google Workspace, google.ts; 2.12 Microsoft 365, microsoft.ts; both through mail.ts):
// connect-mail takes provider "google" (the default) or "microsoft" (tenant_id, client_id,
// client_secret, domain, usage_location). Google Workspace: the Owner connects a service
// account with domain-wide delegation; mailboxes are then made, suspended,
// renamed and deleted with People.
//   POST {op: "mail", organisation_id}                     -> the connection (never its key)
//   POST {op: "connect-mail", organisation_id, key, admin_email, domain?}   (the Owner; checked first)
//   POST {op: "disconnect-mail", organisation_id}          (the Owner)
//   POST {op: "mail-users", organisation_id}               -> the domain's mailboxes, who has each (the Owner)
//   POST {op: "mailbox", person_id, action, address?}      action: create | link (the Owner) | unlink |
//        new-password | retry | forget; create and new-password give a first password, shown once
//   add-person takes mailbox: {action: "create" | "link", address}; set-person-enabled suspends
//   and unsuspends; edit-person renames; remove-person needs mailbox: "suspend" | "delete".
//   Fleet's own change never waits for Google: if Google refuses or can't be
//   reached, the person's mailbox_todo says what's left (an alert) and "retry" does it.
//   A mailbox that is a Google admin account is changed only by the Owner.
//
// Remote help (2.9a, help.ts): an admin sees, and if the person allows,
// controls, the screen of someone signed in on a computer; they're asked
// first, every time. The setting-up messages pass through here, the screen
// goes straight between them (WebRTC, Cloudflare's relay when it must).
//   POST {op: "help-start", device_id, control}            -> {session_id, ice, relay}
//   POST {op: "help-signal", session_id, answer?, candidates?, since?}
//        -> {state, control, detail, offer, candidates (the computer's, from since), count}
//        (the computer makes the offer once the person allows it; the admin's side answers)
//        (only the admin who started it; asked about every second while it runs)
//   POST {op: "help-end", session_id}
// Requests ("action", "group-action", "help-start") wake the computers they're
// for (a Realtime broadcast), so they arrive in seconds, not at the next report.
//
// Actions (2.9b, actions.ts): "action" and "group-action" also take
//   run-fix {fix}            one of Kulan's ready-made fixes (FIXES)
//   message {title, body}    a notice on the screens of whoever uses the computer
//   run-script {script_id}   one of the organisation's scripts, once the Owner allows
//                            them, and only for an admin who typed their authenticator code
//   POST {op: "message-person", person_id, title, body}     -> {count} (the computers they're signed in on)
//   POST {op: "action-output", action_id}                  -> {output, exit_code}
//   POST {op: "scripts", organisation_id}                  -> {allowed, scripts}
//   POST {op: "set-scripts-allowed", organisation_id, allowed}   (the Owner, with the code)
//   POST {op: "add-script" | "edit-script", ..., name, description?, body}   (with the code)
//   POST {op: "remove-script", script_id}
//   POST {op: "add-network", group_id, ssid, security, password?, hidden?}  (the password is kept sealed)
//   POST {op: "remove-network", network_id}
//   POST {op: "add-printer", group_id, name, uri} | {op: "remove-printer", printer_id}
//   "groups" lists each group's networks (never a password) and printers.
//
// Get help (2.9c): someone at a computer writes what's wrong in Kulan Settings,
// with a screenshot if they add one. "overview" lists open requests (and those
// done in the last week) and makes each open one an alert; then
//   POST {op: "help-request", request_id}                 -> {request} with the screenshot (base64)
//   POST {op: "close-help", request_id, reply?}           marks it done; the answer goes to their screen
// Start remote help from it with "help-start" (the computer's device_id).
//
// Every change is written to fleet.audit with the admin's email.
import postgres from "npm:postgres@3.4.5";
import * as google from "./google.ts";
import * as mail from "./mail.ts";
import * as microsoft from "./microsoft.ts";
import * as acts from "./actions.ts";
import * as help from "./help.ts";
import { cleanPolicy, mergePolicies } from "./policy.ts";
import * as secrets from "./secrets.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
// Any JSON-able value as jsonb (postgres.js's types want its own JSON type).
const json = (v: unknown) => sql.json(v as Parameters<typeof sql.json>[0]);
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ALLOWED_ORIGINS = (Deno.env.get("FLEET_DASHBOARD_ORIGINS") ?? "https://os.kulangroup.com")
  .split(",").map((o) => o.trim()).filter(Boolean);
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart", "erase",
                         "shutdown", "lost-mode-on", "lost-mode-off", "install-app", "run-fix", "run-script", "message"]);
// What may be asked of a whole group at once (never erase or lost mode).
const GROUP_ACTIONS = new Set(["refresh", "install-updates", "install-app", "lock-screen", "restart", "shutdown",
                               "run-fix", "run-script", "message"]);
const HEX4 = /^[0-9a-f]{4}$/;
const SERIAL = /^[A-Za-z0-9._-]{4,64}$/;
const STALE_DAYS = 3;
const USERNAME = /^[a-z][a-z0-9_-]{0,31}$/;
const RESERVED = new Set(["root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
                          "www-data", "backup", "list", "irc", "nobody", "admin", "sudo", "kulan", "gdm", "systemd"]);
const HASH = /^\$(y|gy|6|5|2b|2y|7)\$[./A-Za-z0-9$=,]{8,250}$/;
const EMAIL = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]+$/;
const ROLES = new Set(["owner", "admin", "employee"]);
const MAX_PEOPLE = 5000;
const REVIEW_DAYS = 90;
const DAY_MS = 86400_000;
const LIST_MAX = 50; // names listed per line of the weekly report
const MAX_BODY = 1024 * 1024; // a script, a service account's key
const HELP_SINCE = [2, 9, 1]; // the first Kulan OS that can take remote help (2.9a)

// Whether a computer's Kulan OS (its kulan_version, "2.9.1 (build 130)") is
// at least this one; true when it can't be told (a test build, say).
export function versionAtLeast(version: unknown, want: number[]): boolean {
  const m = typeof version === "string" ? version.match(/^(\d+)\.(\d+)(?:\.(\d+))?/) : null;
  if (!m) return true;
  const have = [Number(m[1]), Number(m[2]), Number(m[3] ?? 0)];
  for (let i = 0; i < 3; i++) if (have[i] !== want[i]) return have[i] > want[i];
  return true;
}

// A full name as the computers will show it: no characters /etc/passwd uses.
export function cleanName(v: unknown): string | null {
  if (v === undefined) return null;
  return typeof v === "string" ? v.replace(/[:,=\n\r\t\\]/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) : "";
}

export function cleanEmail(v: unknown): string | null | false {
  if (v === undefined || v === null || v === "") return null;
  if (typeof v !== "string") return false;
  const e = v.trim().toLowerCase();
  return EMAIL.test(e) && e.length <= 254 ? e : false;
}

export function usernameProblem(u: unknown): string | null {
  if (typeof u !== "string" || !USERNAME.test(u)) {
    return "a username is lower-case letters, digits, - or _, starting with a letter, up to 32";
  }
  return RESERVED.has(u) ? "that username is reserved for the system" : null;
}

function recoveryCode(): string {
  // 20 characters from 32 (100 bits), in groups of five: easy to write down.
  const letters = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  const chars = Array.from(bytes, (b) => letters[b % 32]).join("");
  return chars.match(/.{5}/g)!.join("-");
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

const normalCode = (v: unknown) => typeof v === "string" ? v.toUpperCase().replace(/[^A-Z0-9]/g, "") : "";

// An app to install once: {source: "debian" | "flatpak", id}.
export function cleanApp(v: unknown): { source: string; id: string } | null {
  const p = cleanPolicy({ required_apps: [v] });
  return p?.required_apps?.length === 1 ? p.required_apps[0] : null;
}

type Device = Record<string, any>;

// The Monday (UTC) a week starts, as YYYY-MM-DD.
export function mondayOf(d: Date): string {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7));
  return t.toISOString().slice(0, 10);
}

// When the admin-rights review is due: 90 days after the last one (or after
// the organisation was made, if there's been none).
export function reviewDue(lastAt: string | Date | null, orgCreated: string | Date | null, now: number) {
  const since = lastAt ? new Date(lastAt).getTime() : orgCreated ? new Date(orgCreated).getTime() : now;
  const days = Math.floor((now - since) / DAY_MS);
  return { due: days >= REVIEW_DAYS, days, never: !lastAt };
}

// The weekly security report, from the organisation's computers, people,
// the week's activity and the last review.
export function weeklySummary(devices: Device[], people: Record<string, any>[], activity: Record<string, any>[],
                              review: { at: string | null; by: string | null; due: boolean }, now: number,
                              twoStep?: string) {
  const names = (list: Device[]) => list.map((d) => d.hostname || "(no name)").slice(0, LIST_MAX);
  const live = devices.filter((d) => !d.left_at);
  const seen = (d: Device) => d.last_seen ? now - Date.parse(d.last_seen) : Infinity;
  const security = live.filter((d) => Number(d.updates?.security ?? 0) > 0);
  const failing = live.filter((d) => d.compliance_total && Number(d.compliance_passed) < Number(d.compliance_total));
  const notMet = live.map((d) => {
    const items = Object.entries(d.policy_status ?? {}).filter(([, v]) => v !== "met" && v !== "n/a").map(([k]) => k);
    return { hostname: d.hostname || "(no name)", items: items.slice(0, 10), more: Math.max(0, items.length - 10) };
  }).filter((d) => d.items.length);
  const versions: Record<string, number> = {};
  for (const d of live) versions[d.kulan_version || "unknown"] = (versions[d.kulan_version || "unknown"] ?? 0) + 1;
  const active = people.filter((p) => !p.removed_at);
  const verbs: Record<string, number> = {};
  for (const a of activity) {
    const verb = String(a.what).startsWith("asked") ? "asked computers" : String(a.what).split(" ")[0];
    verbs[verb] = (verbs[verb] ?? 0) + 1;
  }
  const notSeen = live.filter((d) => seen(d) > 7 * DAY_MS);
  const restart = live.filter((d) => d.updates?.restart_required === true);
  const unencrypted = live.filter((d) => d.state?.encrypted === false);
  const firewallOff = live.filter((d) => d.state?.firewall === false);
  const lost = live.filter((d) => d.state?.lost_mode === true);
  const disabled = active.filter((p) => !p.enabled);
  const unused = active.filter((p) => p.enabled && p.must_change && now - Number(p.password_changed_at) * 1000 > 7 * DAY_MS);
  const admins = active.filter((p) => p.role !== "employee");
  const noTwoStep = twoStep ? active.filter((p) => p.enabled && !p.has_two_step &&
    (twoStep === "everyone" || p.role !== "employee")) : [];
  const mailboxProblems = people.filter((p) => p.mailbox_todo);
  return {
    computers: {
      // The lists name at most 50; counts has how many there really are.
      counts: { not_seen_week: notSeen.length, security_waiting: security.length, restart_needed: restart.length,
                not_encrypted: unencrypted.length, firewall_off: firewallOff.length, compliance_failing: failing.length,
                policy_not_met: notMet.length, lost_mode: lost.length },
      total: live.length,
      reported_this_week: live.filter((d) => seen(d) <= 7 * DAY_MS).length,
      not_seen_week: names(notSeen),
      up_to_date: live.filter((d) => d.updates && Number(d.updates.waiting ?? 0) === 0).length,
      security_waiting: security.slice(0, LIST_MAX).map((d) => ({ hostname: d.hostname || "(no name)", count: Number(d.updates.security) })),
      restart_needed: names(restart),
      encrypted: live.filter((d) => d.state?.encrypted === true).length,
      not_encrypted: names(unencrypted),
      firewall_off: names(firewallOff),
      compliance_passing: live.filter((d) => d.compliance_total && Number(d.compliance_passed) === Number(d.compliance_total)).length,
      compliance_failing: failing.slice(0, LIST_MAX).map((d) => ({ hostname: d.hostname || "(no name)",
        failing: Number(d.compliance_total) - Number(d.compliance_passed) })),
      policy_not_met: notMet.slice(0, LIST_MAX),
      lost_mode: names(lost),
      versions,
    },
    people: {
      counts: { disabled: disabled.length, setup_code_unused: unused.length, admins: admins.length,
                two_step_missing: noTwoStep.length, mailbox_problems: mailboxProblems.length },
      total: active.length,
      disabled: disabled.map((p) => p.username).slice(0, LIST_MAX),
      setup_code_unused: unused.map((p) => p.username).slice(0, LIST_MAX),
      admins: admins.map((p) => p.username).slice(0, LIST_MAX),
      two_step: twoStep ?? null,
      two_step_missing: noTwoStep.map((p) => p.username).slice(0, LIST_MAX),
      mailbox_problems: mailboxProblems.slice(0, LIST_MAX).map((p) => ({ username: p.username, todo: p.mailbox_todo })),
    },
    activity: {
      changes: activity.length,
      by_kind: Object.entries(verbs).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([what, count]) => ({ what, count })),
    },
    review,
  };
}

// Each username's sign-ins across the computers: {username: [{hostname, device_id, last, now}]}.
export function signInsByUser(devices: Device[]): Record<string, { hostname: string; device_id: string; last: number | null; now: boolean }[]> {
  const out: Record<string, { hostname: string; device_id: string; last: number | null; now: boolean }[]> = {};
  for (const d of devices) {
    if (d.left_at) continue;
    for (const s of Array.isArray(d.sign_ins) ? d.sign_ins : []) {
      if (typeof s?.username !== "string") continue;
      (out[s.username] ??= []).push({ hostname: d.hostname || "(no name)", device_id: d.id, last: s.last ?? null, now: s.now === true });
    }
  }
  for (const list of Object.values(out)) list.sort((a, b) => (b.last ?? 0) - (a.last ?? 0));
  return out;
}

// What needs an admin's attention, worked out from each computer's last report.
function alertsFor(d: Device, policy: Record<string, unknown>, now: number): { kind: string; text: string }[] {
  if (d.left_at) return [];
  const out: { kind: string; text: string }[] = [];
  const seen = d.last_seen ? Date.parse(d.last_seen) : 0;
  if (!seen || now - seen > STALE_DAYS * 86400_000) {
    out.push({ kind: "offline", text: `Not seen for ${seen ? Math.floor((now - seen) / 86400_000) : "many"} days` });
  }
  const security = Number(d.updates?.security ?? 0);
  if (security > 0) out.push({ kind: "updates", text: `${security} security update${security === 1 ? "" : "s"} waiting` });
  if (d.state?.lost_mode === true) out.push({ kind: "lost", text: "In lost mode. Nobody can sign in" });
  if (d.state?.firewall === false) out.push({ kind: "firewall", text: "Firewall is off" });
  if (d.state?.encrypted === false && policy.require_encryption) {
    out.push({ kind: "encryption", text: "Not encrypted, and the organisation requires it" });
  }
  const total = Number(d.hardware?.disk_total_gb ?? 0), free = Number(d.hardware?.disk_free_gb ?? 0);
  if (total > 0 && free / total < 0.1) out.push({ kind: "disk", text: `Disk almost full (${free} GB free)` });
  if (d.compliance_mode === "on" && d.compliance_total && d.compliance_passed < d.compliance_total) {
    out.push({ kind: "compliance", text: `${d.compliance_total - d.compliance_passed} compliance checks failing` });
  }
  for (const [key, status] of Object.entries(d.policy_status ?? {})) {
    if (status === "met" || status === "n/a") continue;
    const app = key.match(/^app:(install|block):(debian|flatpak):(.+)$/);
    out.push({ kind: "policy", text: app
      ? `${app[3]} ${app[1] === "install" ? "isn't installed" : "isn't removed"} as the policy says: ${String(status).slice(0, 120)}`
      : `Policy not met: ${key.replaceAll("_", " ")}` });
  }
  return out;
}

function publishableKey(): string {
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_PUBLISHABLE_KEYS") ?? "{}");
    if (keys.default) return keys.default;
  } catch { /* fall back */ }
  return Deno.env.get("SUPABASE_ANON_KEY") ?? "";
}

function cors(origin: string | null): Record<string, string> {
  const allowed = origin && ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Headers": "authorization, content-type, apikey, x-client-info",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

// The session's assurance level from its access token (already checked by
// Supabase Auth in signedIn): "aal2" after an authenticator app code.
export function aalOf(token: string): string {
  try {
    const part = token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/");
    const claims = JSON.parse(atob(part + "=".repeat((4 - (part.length % 4)) % 4)));
    return claims?.aal === "aal2" ? "aal2" : "aal1";
  } catch {
    return "aal1";
  }
}

async function signedIn(req: Request): Promise<{ email: string; id: string | null; aal: string; hasCode: boolean } | null> {
  const auth = req.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const res = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { Authorization: auth, apikey: publishableKey() },
  });
  if (!res.ok) return null;
  const user = await res.json();
  if (!user?.email || !user?.email_confirmed_at) return null;
  const hasCode = Array.isArray(user.factors) &&
    user.factors.some((f: Record<string, unknown>) => f?.factor_type === "totp" && f?.status === "verified");
  return { email: String(user.email).toLowerCase(), id: typeof user.id === "string" ? user.id : null,
           aal: aalOf(auth.slice(7).trim()), hasCode };
}

// Supabase Auth's admin API (the service key, only here): an admin's
// authenticator apps, to remove them when the Owner resets a lost phone.
async function authAdmin(path: string, method = "GET"): Promise<Response> {
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  return await fetch(`${SUPABASE_URL}/auth/v1/admin${path}`, { method, headers: { Authorization: `Bearer ${key}`, apikey: key } });
}

// The request's JSON body ({} if it isn't JSON), or null if it's over max bytes (read in pieces).
async function readJson(req: Request, max: number): Promise<Record<string, any> | null> {
  if (Number(req.headers.get("Content-Length") ?? "0") > max) return null;
  if (!req.body) return {};
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
  for (const part of parts) {
    all.set(part, at);
    at += part.length;
  }
  try {
    const v = JSON.parse(new TextDecoder().decode(all));
    return typeof v === "object" && v !== null && !Array.isArray(v) ? v : {};
  } catch {
    return {};
  }
}

function uuid(v: unknown): string | null {
  return typeof v === "string" && /^[0-9a-f-]{36}$/i.test(v) ? v : null;
}

Deno.serve(async (req: Request) => {
  const headers = { ...cors(req.headers.get("Origin")), "Content-Type": "application/json" };
  const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers });
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers });
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  const origin = req.headers.get("Origin");
  if (origin && !ALLOWED_ORIGINS.includes(origin)) return reply(403, { error: "not from the dashboard" });

  try {
    const me = await signedIn(req);
    if (!me) return reply(401, { error: "please sign in" });
    const email = me.email;
    const body = await readJson(req, MAX_BODY);
    if (body === null) return reply(413, { error: "too large" });
    if (body?.op === "recover-owner") {
      const given = normalCode(body.recovery_code);
      if (given.length !== 20) return reply(403, { error: "that recovery code isn't right" });
      const fresh = recoveryCode();
      const moved = await sql.begin(async (tx) => {
        const rows = await tx`update fleet.organisations set owner_email = ${email}, recovery_hash = ${await sha256(normalCode(fresh))}
                              where recovery_hash = ${await sha256(given)} returning id, name`;
        if (rows.length !== 1) return null;
        const org = rows[0].id;
        await tx`insert into fleet.admins (email, organisation_id) values (${email}, ${org}) on conflict do nothing`;
        await tx`insert into fleet.audit (organisation_id, email, what) values (${org}, ${email}, 'became the Owner with the recovery code')`;
        return rows[0];
      });
      if (!moved) return reply(403, { error: "that recovery code isn't right" });
      return reply(200, { organisation: { id: moved.id, name: moved.name }, recovery_code: fresh });
    }
    const admins = await sql`select organisation_id from fleet.admins where email = ${email}`;
    if (admins.length === 0) return reply(403, { error: "this account isn't a fleet admin" });
    const global = admins.some((a) => a.organisation_id === null);
    const orgs: string[] = admins.map((a) => a.organisation_id).filter(Boolean);
    // An authenticator app code: needed by anyone who has set one up, and by
    // every admin of an organisation that requires it.
    if (me.aal !== "aal2") {
      const requiring = orgs.length ? await sql`select 1 from fleet.organisations where id = any(${orgs}::uuid[]) and admin_two_step` : [];
      if (me.hasCode || requiring.length) {
        return reply(403, me.hasCode
          ? { error: "type the code from your authenticator app", two_step: "code" }
          : { error: "your organisation requires an authenticator app for Kulan Fleet admins. Set one up first", two_step: "set-up" });
      }
    }

    const canOrg = (id: string) => global || orgs.includes(id);
    const audit = (org: string | null, device: string | null, what: string, detail: unknown = null) =>
      sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
          values (${org}, ${device}, ${email}, ${what}, ${detail === null ? null : json(detail)})`;
    const deviceOrg = async (id: string | null) => {
      if (!id) return null;
      const rows = await sql`select organisation_id from fleet.devices where id = ${id}`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0].organisation_id : null;
    };
    // "Report now" to these computers (best effort: otherwise their next report).
    const wakeDevices = async (ids: string[]) => {
      if (!ids.length) return 0;
      const rows = await sql`select wake_topic from fleet.devices where id = any(${ids}::uuid[]) and left_at is null
                             and wake_topic is not null`;
      return await help.wake(rows.map((r) => r.wake_topic));
    };
    // Someone who no longer has Kulan Fleet access to an organisation: their
    // remote help there ends at once (the computer stops sharing within seconds).
    const endHelpOf = async (org: string, who: string) => {
      const still = await sql`select 1 from fleet.admins where email = ${who} and (organisation_id is null or organisation_id = ${org})`;
      if (still.length) return;
      const ended = await sql`update fleet.help_sessions set state = 'ended', detail = 'their access was removed', ended_at = now(),
                                     updated_at = now()
                              where organisation_id = ${org} and requested_by = ${who} and state = any(${help.ACTIVE}::text[])
                              returning id, device_id`;
      for (const e of ended) {
        await sql`update fleet.actions set status = 'cancelled', finished_at = now()
                  where device_id = ${e.device_id} and type = 'remote-help' and status = 'pending' and payload->>'session_id' = ${e.id}`;
      }
    };
    const NOT_APPROVED = "that computer waits for the Owner's approval";
    const approved = async (id: string) =>
      (await sql`select 1 from fleet.devices where id = ${id} and approved_at is not null`).length === 1;
    const isOwner = async (org: string) => {
      if (global) return true;
      const rows = await sql`select owner_email from fleet.organisations where id = ${org}`;
      return rows.length === 1 && rows[0].owner_email === email;
    };
    // A person this admin may manage (not removed, unless removedToo: a
    // removed person's mailbox can still need finishing).
    const person = async (id: string | null, removedToo = false) => {
      if (!id) return null;
      const rows = await sql`select id, organisation_id, username, full_name, email, role, enabled, all_computers,
                                    mailbox, mailbox_state, mailbox_todo, mailbox_google_id, removed_at,
                                    coalesce((select array_agg(pc.device_id::text order by pc.device_id)
                                              from fleet.person_computers pc where pc.person_id = fleet.people.id), '{}') as computers,
                                    coalesce((select array_agg(pg.group_id::text order by pg.group_id)
                                              from fleet.person_groups pg where pg.person_id = fleet.people.id), '{}') as groups
                             from fleet.people
                             where id = ${id} and (removed_at is null or ${removedToo})`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0] : null;
    };
    // A group this admin may manage.
    const group = async (id: string | null) => {
      if (!id) return null;
      const rows = await sql`select id, organisation_id, name, policy from fleet.groups where id = ${id}`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0] : null;
    };
    // Chosen groups: only the organisation's own.
    const groupsOf = async (org: string, value: unknown): Promise<string[] | null> => {
      if (value === undefined) return [];
      if (!Array.isArray(value) || value.length > 200) return null;
      const ids = [...new Set(value.map(uuid))];
      if (ids.includes(null)) return null;
      const rows = ids.length ? await sql`select id from fleet.groups where organisation_id = ${org}
                                           and id = any(${ids as string[]}::uuid[])` : [];
      return rows.length === ids.length ? ids as string[] : null;
    };
    // Chosen people: only the organisation's own, not removed.
    const peopleOf = async (org: string, value: unknown): Promise<string[] | null> => {
      if (!Array.isArray(value) || value.length > 5000) return null;
      const ids = [...new Set(value.map(uuid))];
      if (ids.includes(null)) return null;
      const rows = ids.length ? await sql`select id from fleet.people where organisation_id = ${org} and removed_at is null
                                           and id = any(${ids as string[]}::uuid[])` : [];
      return rows.length === ids.length ? ids as string[] : null;
    };
    // Chosen computers: only the organisation's own, still in it.
    const computersOf = async (org: string, value: unknown): Promise<string[] | null> => {
      if (!Array.isArray(value) || value.length > 1000) return null;
      const ids = [...new Set(value.map(uuid))];
      if (ids.includes(null)) return null;
      const rows = ids.length ? await sql`select id from fleet.devices where organisation_id = ${org} and left_at is null
                                           and id = any(${ids as string[]}::uuid[])` : [];
      return rows.length === ids.length ? ids as string[] : null;
    };

    // --- Work email: Google Workspace (2.8e), Microsoft 365 (2.12) -----------------------------
    // The organisation's connection, its key (Google) or client secret (Microsoft) opened; null if it has none.
    const mailConn = async (org: string): Promise<mail.Connection | null> => {
      const [c] = await sql`select provider, domain, admin_email, client_email, client_id, private_key, tenant_id, licence,
                                   usage_location
                            from fleet.mail_connections where organisation_id = ${org}`;
      if (!c) return null;
      const secret = await mail.openText(c.private_key, org);
      if (!secret) {
        throw new mail.MailError(500, `Kulan Fleet can't open the ${mail.label(c.provider)} key. ` +
          `Connect ${mail.label(c.provider)} again, or contact Kulan`);
      }
      if (c.provider === "microsoft") {
        return { provider: "microsoft", organisation_id: org, domain: c.domain, tenant_id: c.tenant_id, client_id: c.client_id,
                 secret, licence: c.licence, usage_location: c.usage_location ?? "US" };
      }
      return { provider: "google", organisation_id: org, domain: c.domain, admin_email: c.admin_email,
               client_email: c.client_email, pem: secret };
    };
    const NOT_CONNECTED = "work email isn't connected (Google Workspace or Microsoft 365)";
    const why = (e: unknown) => e instanceof mail.MailError ? e.message : "something went wrong talking to the mail service";
    // Remembered on the connection, so a broken one is an alert (and a working one clears it).
    const noteGoogle = async (org: string, error: string | null) => {
      await sql`update fleet.mail_connections set checked_at = now(), last_error = ${error ? error.slice(0, 300) : null}
                where organisation_id = ${org}`;
    };
    // Only the Owner manages admins' and the Owner's accounts; the same for their mailboxes.
    const mayManage = async (p: Record<string, any>) => p.role === "employee" || await isOwner(p.organisation_id);
    const adminMailbox = (c: mail.Connection | null) =>
      `that mailbox is a ${mail.short(c?.provider)} admin account, so only the Owner can change it`;
    // What's left to do, strongest first: a rename never hides a suspend or a delete.
    const RANK: Record<string, number> = { rename: 1, suspend: 2, unsuspend: 2, delete: 3 };
    // The mailbox in Google that is this person's: by Google's id once known
    // (an address can be renamed, or become someone else's alias), else by an
    // address that is still its own. null if Google has no such mailbox.
    const theirMailbox = async (conn: mail.Connection, p: Record<string, any>) => {
      const u = await mail.getUser(conn, p.mailbox_google_id || p.mailbox);
      if (!u) return null;
      if (p.mailbox_google_id ? u.id !== p.mailbox_google_id : u.address !== p.mailbox) {
        throw new mail.MailError(409, `${p.mailbox} in ${mail.short(conn.provider)} is no longer the mailbox Kulan Fleet ` +
          `made or linked. Check it in the ${conn.provider === "microsoft" ? "Microsoft 365 admin center" : "Google Admin console"}, ` +
          "then link it again");
      }
      if (!p.mailbox_google_id && u.id) {
        await sql`update fleet.people set mailbox_google_id = ${u.id} where id = ${p.id}`;
        p.mailbox_google_id = u.id;
      }
      return u;
    };
    // Does something to a person's mailbox in Google, after Fleet's own change.
    // On success the person's mailbox state is updated; on failure what's left
    // to do and why are kept (an alert, and "retry") and the reason returned.
    const mailboxDo = async (p: Record<string, any>, todo: "suspend" | "unsuspend" | "delete" | "rename"): Promise<string | null> => {
      const org = p.organisation_id;
      const pending: string | null = p.mailbox_todo ?? null;
      const fail = async (msg: string) => {
        // The stronger of what was already waiting and this.
        const keep = pending && pending !== todo && RANK[pending] > RANK[todo] ? pending : todo;
        await sql`update fleet.people set mailbox_todo = ${keep}, mailbox_error = ${msg.slice(0, 300)} where id = ${p.id}`;
        return msg;
      };
      // A rename with a suspend, unsuspend or delete still waiting: that first.
      if (todo === "rename" && pending && pending !== "rename") {
        const err = await mailboxDo(p, pending as "suspend" | "unsuspend" | "delete");
        if (err) return err;
        const [now] = await sql`select mailbox, mailbox_state, mailbox_todo, mailbox_google_id from fleet.people where id = ${p.id}`;
        if (now.mailbox_state === "deleted") return null;
        p = { ...p, ...now };
        return await mailboxDo(p, "rename");
      }
      if (!(await mayManage(p))) return await fail("only the Owner changes an admin's or the Owner's mailbox");
      let conn: mail.Connection | null;
      try {
        conn = await mailConn(org);
      } catch (e) {
        return await fail(why(e));
      }
      if (!conn) return await fail(NOT_CONNECTED);
      try {
        const u = await theirMailbox(conn, p);
        let state: string = p.mailbox_state ?? "active";
        if (!u) {
          if (todo !== "delete") {
            await noteGoogle(org, null);
            return await fail(`${mail.short(conn.provider)} has no mailbox ${p.mailbox} any more`);
          }
          state = "deleted";
        } else {
          if (u.admin && !(await isOwner(org))) return await fail(adminMailbox(conn));
          if (todo === "suspend") {
            await mail.setSuspended(conn, u.id || p.mailbox, true);
            state = "suspended";
          } else if (todo === "unsuspend") {
            await mail.setSuspended(conn, u.id || p.mailbox, false);
            state = "active";
          } else if (todo === "delete") {
            await mail.deleteUser(conn, u.id || p.mailbox);
            state = "deleted";
          } else {
            const [now] = await sql`select full_name, username from fleet.people where id = ${p.id}`;
            await mail.rename(conn, u.id || p.mailbox, mail.splitName(now.full_name ?? "", now.username));
          }
          // A rename still waiting is done too, after a suspend or unsuspend.
          if (pending === "rename" && (todo === "suspend" || todo === "unsuspend")) {
            await mail.rename(conn, u.id || p.mailbox, mail.splitName(p.full_name ?? "", p.username));
          }
        }
        await noteGoogle(org, null);
        await sql`update fleet.people set mailbox_state = ${state}, mailbox_todo = null, mailbox_error = null where id = ${p.id}`;
        const done = { suspend: "suspended", unsuspend: "unsuspended", delete: "deleted", rename: "renamed" }[todo];
        await audit(org, null, `${done} ${p.username}'s mailbox`.slice(0, 80), { mailbox: p.mailbox });
        return null;
      } catch (e) {
        const msg = why(e);
        if (!(e instanceof mail.MailError) || e.status === 0 || e.status === 403 || e.status >= 500) await noteGoogle(org, msg);
        return await fail(msg);
      }
    };
    // A mailbox address an admin gave: well-formed, and nobody else's in the organisation.
    const freeAddress = async (org: string, value: unknown, exceptPerson: string | null = null): Promise<string | Response> => {
      const address = typeof value === "string" ? value.trim().toLowerCase() : "";
      if (!mail.ADDRESS.test(address)) return reply(400, { error: "check the mailbox address" });
      const taken = await sql`select username from fleet.people where organisation_id = ${org} and mailbox = ${address}
                              and mailbox_state is distinct from 'deleted' and id is distinct from ${exceptPerson}`;
      if (taken.length) return reply(409, { error: `${taken[0].username} already has that mailbox` });
      return address;
    };
    // Makes or links a person's mailbox (they have none yet). Returns what to tell the admin.
    const giveMailbox = async (p: Record<string, any>, action: unknown, address: string, conn: mail.Connection) => {
      if (action === "link") {
        const u = await mail.getUser(conn, address);
        if (!u) return { status: 404, body: { error: `${mail.short(conn.provider)} has no mailbox ${address}. Make one instead` } };
        if (u.address !== address) return { status: 400, body: { error: `that's an alias of ${u.address}. Link ${u.address} instead` } };
        await sql`update fleet.people set mailbox = ${address}, mailbox_state = ${u.suspended ? "suspended" : "active"},
                    mailbox_google_id = ${u.id || null}, mailbox_todo = null, mailbox_error = null where id = ${p.id}`;
        await audit(p.organisation_id, null, `linked ${p.username}'s mailbox`.slice(0, 80), { mailbox: address });
        // Linked to someone disabled: their mailbox is suspended too.
        if (!p.enabled && !u.suspended) {
          const err = await mailboxDo({ ...p, mailbox: address, mailbox_state: "active", mailbox_google_id: u.id || null,
                                        mailbox_todo: null }, "suspend");
          return { status: 200, body: { mailbox: address, ...(err ? { mailbox_error: err } : {}) } };
        }
        return { status: 200, body: { mailbox: address } };
      }
      if (address.split("@")[1] !== conn.domain && !(await isOwner(p.organisation_id))) {
        return { status: 403, body: { error: `mailboxes are made on ${conn.domain}. Only the Owner can choose another domain` } };
      }
      if (await mail.getUser(conn, address)) {
        return { status: 409, body: { error: `that address is already in ${mail.label(conn.provider)}. The Owner can link it instead` } };
      }
      const password = mail.tempPassword();
      const gid = await mail.createUser(conn, address, mail.splitName(p.full_name ?? "", p.username), password);
      await sql`update fleet.people set mailbox = ${address}, mailbox_state = 'active', mailbox_google_id = ${gid || null},
                  mailbox_todo = null, mailbox_error = null where id = ${p.id}`;
      await audit(p.organisation_id, null, `made a mailbox for ${p.username}`.slice(0, 80), { mailbox: address });
      let err: string | null = null;
      if (!p.enabled) err = await mailboxDo({ ...p, mailbox: address, mailbox_state: "active", mailbox_google_id: gid || null,
                                              mailbox_todo: null }, "suspend");
      return { status: 200, body: { mailbox: address, mailbox_password: password, ...(err ? { mailbox_error: err } : {}) } };
    };

    // What an action carries, checked: an app, a fix, a message or a script.
    // Returns the payload (null for none), or a reply saying why not.
    const payloadFor = async (org: string, type: string, raw: unknown): Promise<Record<string, unknown> | null | Response> => {
      if (type === "install-app") {
        const app = cleanApp(raw);
        return app ?? reply(400, { error: "choose a Debian package or Flathub app id" });
      }
      if (type === "run-fix") {
        const fix = (raw as Record<string, unknown> | null)?.fix;
        return typeof fix === "string" && Object.hasOwn(acts.FIXES, fix) ? { fix } : reply(400, { error: "choose one of Kulan's fixes" });
      }
      if (type === "message") {
        const m = acts.cleanMessage(raw);
        return m ?? reply(400, { error: "a message needs a title" });
      }
      if (type === "run-script") {
        const [o] = await sql`select allow_scripts from fleet.organisations where id = ${org}`;
        if (!o?.allow_scripts) return reply(403, { error: "the Owner hasn't allowed scripts for this organisation" });
        if (me.aal !== "aal2") {
          return reply(403, { error: "running a script needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        const sid = uuid((raw as Record<string, unknown> | null)?.script_id);
        const [sc] = sid ? await sql`select s.id, s.name, s.sha256, s.signature, o.script_key from fleet.scripts s
                                     join fleet.organisations o on o.id = s.organisation_id
                                     where s.id = ${sid} and s.organisation_id = ${org}` : [];
        if (!sc) return reply(404, { error: "no such script" });
        // Computers run only what the Owner's script signing key signed.
        if (!(await acts.verifySigned(sc.script_key, sc.signature, acts.scriptMessage(sc.sha256)))) {
          return reply(400, { error: "that script isn't signed with the organisation's script signing code. Sign it first" });
        }
        return { script_id: sc.id, sha256: sc.sha256, name: sc.name, signature: sc.signature };
      }
      return null;
    };
    const describe = (type: string, payload: Record<string, unknown> | null) =>
      type === "run-fix" ? { fix: payload?.fix } : type === "run-script" ? { script: payload?.name, sha256: payload?.sha256 }
        : type === "message" ? { title: payload?.title } : payload;

    switch (body.op) {
      case "overview": {
        const mailDomain = sql`(select m.domain from fleet.mail_connections m where m.organisation_id = o.id) as mail_domain,
                               (select m.provider from fleet.mail_connections m where m.organisation_id = o.id) as mail_provider`;
        const organisations = global
          ? await sql`select id, name, created_at, policy, owner_email, admin_two_step, allow_scripts, (${global} or owner_email = ${email}) as is_owner,
                             ${mailDomain}
                      from fleet.organisations o order by name`
          : await sql`select id, name, created_at, policy, owner_email, admin_two_step, allow_scripts, owner_email = ${email} as is_owner,
                             ${mailDomain}
                      from fleet.organisations o where id = any(${orgs}::uuid[]) order by name`;
        const ids = organisations.map((o) => o.id);
        const devices = await sql`
          select id, organisation_id, hostname, os, kulan_version, enrolled_at, last_seen, left_at,
                 approved_at, approved_at is not null as approved,
                 last_report->'updates' as updates, last_report->'tpm' as tpm,
                 last_report->'compliance'->'passed' as compliance_passed,
                 last_report->'compliance'->'total' as compliance_total,
                 last_report->'compliance'->'mode' as compliance_mode,
                 last_report->'hardware' as hardware, last_report->'state' as state,
                 last_report->'policy_status' as policy_status, last_report->'sign_ins' as sign_ins, last_report->'pushed' as pushed, apps_at,
                 jsonb_array_length(coalesce(apps, '[]'::jsonb)) as app_count
          from fleet.devices where organisation_id = any(${ids}::uuid[]) order by left_at nulls first, hostname`;
        const actions = await sql`
          select a.id, a.device_id, a.type, a.status, a.detail, a.created_at, a.finished_at, a.requested_by,
                 a.exit_code, a.output is not null as has_output,
                 case a.type when 'run-fix' then a.payload->>'fix' when 'run-script' then a.payload->>'name'
                             when 'message' then a.payload->>'title' end as what
          from fleet.actions a join fleet.devices d on d.id = a.device_id
          where d.organisation_id = any(${ids}::uuid[]) order by a.created_at desc limit 50`;
        const policies = Object.fromEntries(organisations.map((o) => [o.id, o.policy ?? {}]));
        const now = Date.now();
        const alerts = devices.flatMap((d) => alertsFor(d, policies[d.organisation_id] ?? {}, now)
          .map((a) => ({ ...a, device_id: d.id, organisation_id: d.organisation_id, hostname: d.hostname })));
        // Computers waiting for the Owner's approval come first.
        for (const d of devices.filter((x) => !x.approved && !x.left_at).reverse()) {
          alerts.unshift({ kind: "approve", device_id: d.id, organisation_id: d.organisation_id, hostname: d.hostname,
                           title: "New computer",
                           text: `${d.hostname ?? "A computer"} joined and waits for the Owner's approval. ` +
                                 "Until then it gets no accounts, Wi-Fi or printers" } as any);
        }
        // Phone codes kept but no key to open them: computers keep the ones they have, but new
        // ones can't be kept and nobody's codes reach a new computer. Kulan must fix it.
        if (!Deno.env.get("FLEET_FACTOR_KEY")) {
          const coded = await sql`select distinct p.organisation_id from fleet.person_factors f join fleet.people p on p.id = f.person_id
                                  where f.kind = 'code' and p.organisation_id = any(${ids}::uuid[])`;
          for (const c of coded) {
            alerts.unshift({ kind: "two-step", device_id: null, organisation_id: c.organisation_id, hostname: null,
                             title: "Two-step sign-in",
                             text: "Phone codes can't reach computers right now. Contact Kulan" } as any);
          }
        }
        // Google Workspace: a connection that stopped working, and mailboxes with something left to do.
        const broken = await sql`select organisation_id, provider, last_error from fleet.mail_connections
                                 where organisation_id = any(${ids}::uuid[]) and last_error is not null`;
        const waiting = await sql`select id, organisation_id, username, mailbox, mailbox_todo, mailbox_error from fleet.people
                                  where organisation_id = any(${ids}::uuid[]) and mailbox_todo is not null
                                  order by username limit 50`;
        const todoText = { suspend: "still to suspend", unsuspend: "still to unsuspend", delete: "still to delete",
                           rename: "name still to change" } as Record<string, string>;
        for (const m of [...waiting].reverse()) {
          alerts.unshift({ kind: "mailbox", device_id: null, organisation_id: m.organisation_id, hostname: null, person_id: m.id,
                           title: "Mailbox", text: `${m.username}'s mailbox ${m.mailbox}: ${todoText[m.mailbox_todo]} ` +
                                                    `(${m.mailbox_error ?? "the mail service didn't answer"}). Retry it from People.` } as any);
        }
        for (const b of broken) {
          alerts.unshift({ kind: "mail", device_id: null, organisation_id: b.organisation_id, hostname: null,
                           title: mail.label(b.provider), text: b.last_error } as any);
        }
        // The admin-rights review, every 90 days (an alert for the organisation, not a computer).
        const reviews = await sql`select distinct on (organisation_id) organisation_id, reviewed_at
                                  from fleet.access_reviews where organisation_id = any(${ids}::uuid[])
                                  order by organisation_id, reviewed_at desc`;
        const lastReview = Object.fromEntries(reviews.map((r) => [r.organisation_id, r.reviewed_at]));
        for (const o of organisations) {
          const due = reviewDue(lastReview[o.id] ?? null, o.created_at, now);
          if (due.due) {
            alerts.unshift({ kind: "review", device_id: null, organisation_id: o.id, hostname: null, title: "Admin rights",
                          text: due.never ? "No admin-rights review yet. Check who still needs administrator rights"
                                          : `Admin-rights review due. The last was ${due.days} days ago` } as any);
          }
        }
        // Help requests (2.9c): the open ones, and those done in the last week; never the screenshot here.
        const helpRequests = await sql`
          select h.id, h.organisation_id, h.device_id, d.hostname, h.username, h.full_name, h.body, h.state, h.reply,
                 h.created_at, h.done_at, h.done_by, h.screenshot is not null as has_screenshot
          from fleet.help_requests h join fleet.devices d on d.id = h.device_id
          where h.organisation_id = any(${ids}::uuid[]) and (h.state = 'open' or h.done_at > now() - interval '7 days')
          order by (h.state = 'open') desc, h.created_at desc limit 100`;
        for (const h of helpRequests.filter((x) => x.state === "open").reverse()) {
          alerts.unshift({ kind: "help", device_id: h.device_id, organisation_id: h.organisation_id, hostname: h.hostname,
                           request_id: h.id, title: "Help request",
                           text: `${h.full_name || h.username} asked for help: ${String(h.body).replace(/\s+/g, " ").slice(0, 120)}` } as any);
        }
        return reply(200, { email, global, organisations, devices, actions, alerts, help_requests: helpRequests,
                            me: { aal: me.aal, authenticator: me.hasCode } });
      }
      case "report": {
        const id = uuid(body.device_id);
        if (!(await deviceOrg(id))) return reply(404, { error: "no such computer" });
        const [row] = await sql`select last_report from fleet.devices where id = ${id}`;
        return reply(200, { report: row.last_report });
      }
      case "apps": {
        const id = uuid(body.device_id);
        if (!(await deviceOrg(id))) return reply(404, { error: "no such computer" });
        const [row] = await sql`select apps, apps_at from fleet.devices where id = ${id}`;
        return reply(200, { apps: row.apps ?? [], apps_at: row.apps_at });
      }
      case "log": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const day = (v: unknown) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) &&
          !isNaN(Date.parse(`${v}T00:00:00Z`)) && new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v ? v : null;
        if ((body.from !== undefined && body.from !== "" && !day(body.from)) || (body.to !== undefined && body.to !== "" && !day(body.to))) {
          return reply(400, { error: "write dates as 2026-10-26" });
        }
        const from = day(body.from), to = day(body.to);
        const find = (v: unknown) => typeof v === "string" && v.trim() ? `%${v.trim().slice(0, 80).replace(/[\\%_]/g, "\\$&")}%` : null;
        const who = find(body.who), what = find(body.what);
        const before = Number.isInteger(body.before) && body.before > 0 ? body.before : null;
        const limit = Math.min(10000, Math.max(1, Number.isInteger(body.limit) ? body.limit : 100));
        const entries = await sql`
          select a.id, a.at, a.email, a.what, a.detail, a.device_id, d.hostname
          from fleet.audit a left join fleet.devices d on d.id = a.device_id
          where a.organisation_id = ${org}
            ${from ? sql`and a.at >= ${from}::date` : sql``}
            ${to ? sql`and a.at < ${to}::date + 1` : sql``}
            ${who ? sql`and a.email ilike ${who}` : sql``}
            ${what ? sql`and (a.what ilike ${what} or d.hostname ilike ${what})` : sql``}
            ${before ? sql`and a.id < ${before}` : sql``}
          order by a.id desc limit ${limit + 1}`;
        const more = entries.length > limit;
        return reply(200, { entries: entries.slice(0, limit).map((e) => ({ ...e, id: Number(e.id) })), more });
      }
      case "set-policy": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const policy = cleanPolicy(body.policy);
        if (!policy) return reply(400, { error: "that policy has an app name that can't be used, or blocks part of Kulan OS" });
        await sql`update fleet.organisations set policy = ${json(policy)} where id = ${org}`;
        await audit(org, null, "changed the policy", policy);
        return reply(200, { policy });
      }
      case "new-organisation": {
        if (!global) return reply(403, { error: "only admins for all organisations can add one" });
        const name = typeof body.name === "string" ? body.name.trim().slice(0, 80) : "";
        if (!name) return reply(400, { error: "a name is needed" });
        const owner = cleanEmail(body.owner_email);
        if (owner === false) return reply(400, { error: "the Owner's email doesn't look right" });
        const code = recoveryCode();
        const org = await sql.begin(async (tx) => {
          const [o] = await tx`insert into fleet.organisations (name, owner_email, recovery_hash)
                               values (${name}, ${owner ?? email}, ${await sha256(normalCode(code))}) returning id`;
          if ((owner ?? email) !== email) {
            await tx`insert into fleet.admins (email, organisation_id) values (${owner ?? email}, ${o.id}) on conflict do nothing`;
          }
          return o;
        });
        await audit(org.id, null, "added the organisation", { name, owner: owner ?? email });
        return reply(200, { id: org.id, recovery_code: code });
      }

      // --- People ------------------------------------------------------------------------
      case "people": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const people = await sql`
          select p.id, p.username, p.full_name, p.email, p.uid, p.role, p.enabled, p.must_change,
                 p.password_changed_at, p.all_computers, p.created_at, p.created_by,
                 p.mailbox, p.mailbox_state, p.mailbox_todo, p.mailbox_error,
                 coalesce((select array_agg(pc.device_id) from fleet.person_computers pc where pc.person_id = p.id), '{}') as computers,
                 coalesce((select array_agg(pg.group_id) from fleet.person_groups pg where pg.person_id = p.id), '{}') as groups
          from fleet.people p where p.organisation_id = ${org} and p.removed_at is null order by p.username`;
        // Removed people whose mailbox still has something left to do, so it can be retried.
        const leaving = await sql`select id, username, full_name, mailbox, mailbox_state, mailbox_todo, mailbox_error, removed_at
                                  from fleet.people where organisation_id = ${org} and removed_at is not null
                                  and mailbox_todo is not null order by username`;
        const devices = await sql`select d.id, d.hostname, d.left_at, d.last_report->'people_status' as people_status,
                                         d.last_report->'sign_ins' as sign_ins,
                                         coalesce((select array_agg(gd.group_id) from fleet.group_devices gd where gd.device_id = d.id), '{}') as groups
                                  from fleet.devices d where d.organisation_id = ${org} and d.left_at is null`;
        const signIns = signInsByUser(devices);
        const factors = await sql`select f.person_id, f.id, f.kind, f.label, f.created_at, f.created_on
                                  from fleet.person_factors f join fleet.people p on p.id = f.person_id
                                  where p.organisation_id = ${org} order by f.created_at`;
        return reply(200, {
          people: people.map((p) => ({
            ...p, password_changed_at: Number(p.password_changed_at), signed_in: signIns[p.username] ?? [],
            two_step: factors.filter((f) => f.person_id === p.id)
              .map((f) => ({ id: f.id, kind: f.kind, label: f.label, created_at: f.created_at, created_on: f.created_on })),
            on: devices.filter((d) => p.all_computers || p.computers.includes(d.id)
                                     || d.groups.some((g: string) => p.groups.includes(g)))
              .map((d) => ({ device_id: d.id, hostname: d.hostname, status: d.people_status?.[p.username] ?? "not yet" })),
          })),
          removed_with_mailbox_todo: leaving,
          is_owner: await isOwner(org),
        });
      }
      case "add-person": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const problem = usernameProblem(body.username);
        if (problem) return reply(400, { error: problem });
        const role = ROLES.has(body.role) ? body.role : "employee";
        if (role !== "employee" && !(await isOwner(org))) {
          return reply(403, { error: "only the Owner can give someone the admin or owner role" });
        }
        if (typeof body.password_hash !== "string" || !HASH.test(body.password_hash)) {
          return reply(400, { error: "a setup code is needed" });
        }
        const email2 = cleanEmail(body.email);
        if (email2 === false) return reply(400, { error: "that email doesn't look right" });
        const all = body.all_computers !== false;
        const chosen = all ? [] : await computersOf(org, body.computers ?? []);
        if (chosen === null) return reply(400, { error: "choose computers from this organisation" });
        const chosenGroups = all ? [] : await groupsOf(org, body.groups);
        if (chosenGroups === null) return reply(400, { error: "choose groups from this organisation" });
        const old = await sql`select removed_at from fleet.people where organisation_id = ${org} and username = ${body.username}`;
        if (old.length) {
          return reply(409, { error: old[0].removed_at
            ? "someone with that username was removed in the last 30 days. Choose another username"
            : "someone already has that username" });
        }
        const [{ count }] = await sql`select count(*)::int as count from fleet.people where organisation_id = ${org}`;
        if (count >= MAX_PEOPLE) return reply(400, { error: "that's the most people an organisation can have" });
        // A mailbox with them (2.8e): checked before they're added, made after.
        let wantMailbox: { action: string; address: string } | null = null;
        let conn: mail.Connection | null = null;
        if (body.mailbox !== undefined && body.mailbox !== null) {
          const action = body.mailbox?.action;
          if (action !== "create" && action !== "link") return reply(400, { error: "the mailbox action is create or link" });
          if (action === "link" && !(await isOwner(org))) return reply(403, { error: "only the Owner links an existing mailbox" });
          const address = await freeAddress(org, body.mailbox?.address);
          if (address instanceof Response) return address;
          try {
            conn = await mailConn(org);
          } catch (e) {
            return reply(502, { error: why(e) });
          }
          if (!conn) return reply(400, { error: "connect Google Workspace or Microsoft 365 first" });
          wantMailbox = { action, address };
        }
        let added: Record<string, any> | null = null;
        for (let attempt = 0; attempt < 3 && !added; attempt++) {
          try {
            added = await sql.begin(async (tx) => {
              const [{ uid }] = await tx`select fleet.next_uid(${org}) as uid`;
              if (!uid) throw new Error("no uid left");
              const [p] = await tx`
                insert into fleet.people (organisation_id, username, full_name, email, uid, role, password_hash,
                                          must_change, password_changed_at, all_computers, created_by)
                values (${org}, ${body.username}, ${cleanName(body.full_name) ?? ""}, ${email2}, ${uid}, ${role},
                        ${body.password_hash}, true, extract(epoch from now())::bigint, ${all}, ${email})
                returning id, uid`;
              for (const d of chosen) await tx`insert into fleet.person_computers (person_id, device_id) values (${p.id}, ${d})`;
              for (const g of chosenGroups) await tx`insert into fleet.person_groups (person_id, group_id) values (${p.id}, ${g})`;
              return p;
            });
          } catch (e) {
            const code = (e as { code?: string }).code;
            const constraint = (e as { constraint_name?: string }).constraint_name;
            if (code === "23505" && constraint === "people_org_uid_idx") continue; // two added at once: next uid
            if (code === "23505" && constraint === "people_one_owner_idx") {
              return reply(409, { error: "the organisation already has an Owner's account" });
            }
            if (code === "23505") return reply(409, { error: "someone already has that username" });
            throw e;
          }
        }
        if (!added) return reply(500, { error: "couldn't assign a user ID. Try again" });
        await audit(org, null, `added ${body.username}`, { role, all_computers: all, computers: chosen.length, groups: chosenGroups.length });
        if (!wantMailbox || !conn) return reply(200, { id: added.id, uid: added.uid });
        // They're added whatever Google says; a mailbox that couldn't be made can be made from People later.
        const p = { id: added.id, organisation_id: org, username: body.username, full_name: cleanName(body.full_name) ?? "",
                    role, enabled: true };
        try {
          const r = await giveMailbox(p, wantMailbox.action, wantMailbox.address, conn);
          await noteGoogle(org, null);
          return reply(200, { id: added.id, uid: added.uid, ...(r.status === 200 ? r.body : { mailbox_error: r.body.error }) });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") {
            return reply(200, { id: added.id, uid: added.uid, mailbox_error: "someone else was given that mailbox just now" });
          }
          if (!(e instanceof mail.MailError) || e.status === 0 || e.status === 403 || e.status >= 500) await noteGoogle(org, why(e));
          return reply(200, { id: added.id, uid: added.uid, mailbox_error: why(e) });
        }
      }
      case "edit-person": {
        const p = await person(uuid(body.person_id));
        if (!p) return reply(404, { error: "no such person" });
        const org = p.organisation_id;
        const name = cleanName(body.full_name);
        const email2 = cleanEmail(body.email);
        if (body.email !== undefined && email2 === false) return reply(400, { error: "that email doesn't look right" });
        const role = body.role === undefined ? p.role : body.role;
        if (!ROLES.has(role)) return reply(400, { error: "the role is owner, admin or employee" });
        if (role !== p.role && !(await isOwner(org))) {
          return reply(403, { error: "only the Owner can give or take the admin or owner role" });
        }
        const all = body.all_computers === undefined ? p.all_computers : body.all_computers !== false;
        const chosen = all ? [] : await computersOf(org, body.computers ?? []);
        if (chosen === null) return reply(400, { error: "choose computers from this organisation" });
        const chosenGroups = all ? [] : await groupsOf(org, body.groups ?? []);
        if (chosenGroups === null) return reply(400, { error: "choose groups from this organisation" });
        const changeWhere = body.all_computers !== undefined || body.computers !== undefined || body.groups !== undefined;
        // An admin's or the Owner's account: only the Owner changes where it is or its email.
        if (p.role !== "employee" && (changeWhere || body.email !== undefined) && !(await isOwner(org))) {
          const sameWhere = !changeWhere || (all === p.all_computers && (all ||
            ([...chosen].sort().join() === p.computers.join() && [...chosenGroups].sort().join() === p.groups.join())));
          const sameEmail = body.email === undefined || email2 === (p.email ?? null);
          if (!sameWhere || !sameEmail) {
            return reply(403, { error: "only the Owner changes an admin's or the Owner's computers or email" });
          }
        }
        try {
          await sql.begin(async (tx) => {
            await tx`update fleet.people set full_name = coalesce(${name}, full_name),
                       email = ${body.email === undefined ? sql`email` : email2},
                       role = ${role}, all_computers = ${all}
                     where id = ${p.id}`;
            if (changeWhere) {
              await tx`delete from fleet.person_computers where person_id = ${p.id}`;
              for (const d of chosen) await tx`insert into fleet.person_computers (person_id, device_id) values (${p.id}, ${d})`;
              await tx`delete from fleet.person_groups where person_id = ${p.id}`;
              for (const g of chosenGroups) await tx`insert into fleet.person_groups (person_id, group_id) values (${p.id}, ${g})`;
            }
          });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "the organisation already has an Owner's account" });
          throw e;
        }
        // Only what really changed goes in the activity log.
        const sameComputers = !changeWhere || (all === p.all_computers
          && (all || ([...chosen].sort().join() === p.computers.join() && [...chosenGroups].sort().join() === p.groups.join())));
        const changed = [name !== null && name !== p.full_name && "name",
                         body.email !== undefined && email2 !== (p.email ?? null) && "email",
                         role !== p.role && `role ${role}`, !sameComputers && "computers"].filter(Boolean);
        if (!changed.length) return reply(200, {});
        await audit(org, null, `changed ${p.username}`.slice(0, 80), { changed });
        // Their mailbox's name follows (after Fleet's change; Google can be retried).
        if (changed.includes("name") && p.mailbox && p.mailbox_state !== "deleted") {
          const err = await mailboxDo({ ...p, full_name: name }, "rename");
          if (err) return reply(200, { mailbox_error: err });
        }
        return reply(200, {});
      }
      case "set-person-enabled": {
        const p = await person(uuid(body.person_id));
        if (!p) return reply(404, { error: "no such person" });
        if (typeof body.enabled !== "boolean") return reply(400, { error: "enabled is true or false" });
        if (p.role !== "employee" && !(await isOwner(p.organisation_id))) {
          return reply(403, { error: "only the Owner can disable an admin or the Owner" });
        }
        await sql`update fleet.people set enabled = ${body.enabled} where id = ${p.id}`;
        await audit(p.organisation_id, null, `${body.enabled ? "enabled" : "disabled"} ${p.username}`);
        // Their mailbox too: suspended while they're disabled (Fleet's change never waits for Google).
        if (p.mailbox && p.mailbox_state !== "deleted" && body.enabled !== p.enabled) {
          // mailbox_state is how Google last had it, whatever is still to do.
          const needed = body.enabled ? p.mailbox_state === "suspended" : p.mailbox_state === "active";
          if (needed) {
            const err = await mailboxDo(p, body.enabled ? "unsuspend" : "suspend");
            if (err) return reply(200, { mailbox_error: err });
          } else if (p.mailbox_todo === "suspend" || p.mailbox_todo === "unsuspend") {
            // Back to how the mailbox already is: nothing left to do.
            await sql`update fleet.people set mailbox_todo = null, mailbox_error = null where id = ${p.id}`;
          }
        }
        return reply(200, {});
      }
      case "new-setup-code": {
        const p = await person(uuid(body.person_id));
        if (!p) return reply(404, { error: "no such person" });
        if (typeof body.password_hash !== "string" || !HASH.test(body.password_hash)) {
          return reply(400, { error: "a setup code is needed" });
        }
        if (p.role !== "employee" && !(await isOwner(p.organisation_id))) {
          return reply(403, { error: "only the Owner can give an admin or the Owner a new setup code" });
        }
        await sql`update fleet.people set password_hash = ${body.password_hash}, must_change = true,
                    password_changed_at = greatest(extract(epoch from now())::bigint, password_changed_at + 1)
                  where id = ${p.id}`;
        await audit(p.organisation_id, null, `gave ${p.username} a new setup code`);
        return reply(200, {});
      }
      case "remove-person": {
        const p = await person(uuid(body.person_id));
        if (!p) return reply(404, { error: "no such person" });
        if (body.confirm !== p.username) return reply(400, { error: "type their username to confirm" });
        if (p.role !== "employee" && !(await isOwner(p.organisation_id))) {
          return reply(403, { error: "only the Owner can remove an admin or the Owner" });
        }
        // Their mailbox: the admin chooses each time, suspend or delete.
        const managed = p.mailbox && p.mailbox_state !== "deleted" &&
          (await sql`select 1 from fleet.mail_connections where organisation_id = ${p.organisation_id}`).length > 0;
        if (managed && body.mailbox !== "suspend" && body.mailbox !== "delete") {
          return reply(400, { error: "choose whether to suspend or delete their mailbox", mailbox: p.mailbox });
        }
        if (managed && (p.mailbox_state !== "suspended" || body.mailbox === "delete")) {
          // A Google admin account: only the Owner (checked before anything changes, when Google answers).
          try {
            const conn = await mailConn(p.organisation_id);
            const u = conn ? await theirMailbox(conn, p) : null;
            if (u?.admin && !(await isOwner(p.organisation_id))) return reply(403, { error: adminMailbox(conn) });
          } catch { /* Google can't be reached: Fleet's change goes ahead, the mailbox is retried */ }
        }
        const removeFiles = body.remove_files === true;
        await sql`update fleet.people set removed_at = now(), remove_files = ${removeFiles}, enabled = false
                  where id = ${p.id}`;
        await audit(p.organisation_id, null, `removed ${p.username}`, { files: removeFiles ? "deleted" : "kept",
                                                                        ...(managed ? { mailbox: body.mailbox } : {}) });
        if (managed && (body.mailbox === "delete" || p.mailbox_state === "active")) {
          const err = await mailboxDo(p, body.mailbox === "delete" ? "delete" : "suspend");
          if (err) return reply(200, { mailbox_error: err });
        } else if (managed) {
          await sql`update fleet.people set mailbox_todo = null, mailbox_error = null where id = ${p.id}`;  // already suspended
        }
        return reply(200, {});
      }

      // --- Admins and the Owner ---------------------------------------------------------
      case "admins": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const rows = await sql`select email, created_at from fleet.admins where organisation_id = ${org} order by email`;
        const [o] = await sql`select owner_email from fleet.organisations where id = ${org}`;
        return reply(200, { admins: rows, owner: o?.owner_email ?? null, is_owner: await isOwner(org) });
      }
      case "add-admin":
      case "remove-admin": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner can change who the admins are" });
        const who = cleanEmail(body.email);
        if (!who) return reply(400, { error: "that email doesn't look right" });
        if (body.op === "add-admin") {
          await sql`insert into fleet.admins (email, organisation_id) values (${who}, ${org}) on conflict do nothing`;
        } else {
          const [o] = await sql`select owner_email from fleet.organisations where id = ${org}`;
          if (o?.owner_email === who) return reply(400, { error: "the Owner stays an admin" });
          await sql`delete from fleet.admins where email = ${who} and organisation_id = ${org}`;
          await endHelpOf(org, who);
        }
        await audit(org, null, `${body.op === "add-admin" ? "added" : "removed"} admin ${who}`.slice(0, 80));
        return reply(200, {});
      }
      case "set-owner": {
        if (!global) return reply(403, { error: "only admins for all organisations can set the Owner" });
        const org = uuid(body.organisation_id);
        const who = cleanEmail(body.email);
        if (!org) return reply(404, { error: "no such organisation" });
        if (!who) return reply(400, { error: "that email doesn't look right" });
        const code = recoveryCode();
        const rows = await sql`update fleet.organisations set owner_email = ${who}, recovery_hash = ${await sha256(normalCode(code))}
                               where id = ${org} returning id`;
        if (!rows.length) return reply(404, { error: "no such organisation" });
        await sql`insert into fleet.admins (email, organisation_id) values (${who}, ${org}) on conflict do nothing`;
        await audit(org, null, `made ${who} the Owner`.slice(0, 80));
        return reply(200, { recovery_code: code });
      }
      case "new-code": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const hours = Math.min(720, Math.max(1, Number(body.hours) || 72));
        const uses = Math.min(500, Math.max(1, Number(body.uses) || 1));
        // The Owner's codes make computers that are trusted at once; another admin's wait for the Owner.
        const trusted = await isOwner(org);
        const [row] = await sql`select fleet.new_join_code(${org}, ${hours}, ${uses}, ${trusted}, ${email}) as code`;
        await audit(org, null, "made a join code", { hours, uses, approved: trusted });
        return reply(200, { code: row.code, hours, uses, approved: trusted });
      }
      case "codes": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const codes = await sql`select id, created_at, expires_at, uses, uses_left, trusted, created_by from fleet.enrolment_codes
                                where organisation_id = ${org} and uses_left > 0 and expires_at > now()
                                order by created_at desc limit 100`;
        return reply(200, { codes });
      }
      case "revoke-code": {
        const org = uuid(body.organisation_id);
        const id = uuid(body.code_id);
        if (!org || !canOrg(org) || !id) return reply(404, { error: "no such join code" });
        const gone = await sql`update fleet.enrolment_codes set uses_left = 0
                               where id = ${id} and organisation_id = ${org} and uses_left > 0 returning id`;
        if (!gone.length) return reply(404, { error: "no such join code" });
        await audit(org, null, "withdrew a join code");
        return reply(200, {});
      }
      case "approve-computer": {
        const id = uuid(body.device_id);
        const org = await deviceOrg(id);
        if (!org) return reply(404, { error: "no such computer" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner approves a new computer" });
        const done = await sql`update fleet.devices set approved_at = now(), approved_by = ${email}
                               where id = ${id} and approved_at is null and left_at is null returning hostname`;
        if (!done.length) return reply(400, { error: "that computer is already approved, or has left" });
        await audit(org, id, "approved a computer", { computer: done[0].hostname });
        await wakeDevices([id as string]);
        return reply(200, {});
      }
      case "remove-computer": {
        // Its key stops working at once; it's told nothing (a lost or stolen one shouldn't be warned).
        const id = uuid(body.device_id);
        const org = await deviceOrg(id);
        if (!org) return reply(404, { error: "no such computer" });
        const [d] = await sql`select hostname, left_at from fleet.devices where id = ${id}`;
        if (d.left_at) return reply(400, { error: "that computer has left the organisation" });
        if (typeof body.confirm !== "string" || body.confirm.trim() !== (d.hostname ?? "")) {
          return reply(400, { error: "type the computer's name to confirm" });
        }
        await sql.begin(async (tx) => {
          await tx`update fleet.devices set left_at = now(), token_hash = null, wake_topic = null where id = ${id}`;
          await tx`update fleet.actions set status = 'cancelled', finished_at = now()
                   where device_id = ${id} and status in ('pending', 'sent')`;
          await tx`update fleet.help_sessions set state = 'ended', detail = 'the computer was removed from the organisation',
                          ended_at = now(), updated_at = now()
                   where device_id = ${id} and state = any(${help.ACTIVE}::text[])`;
        });
        await audit(org, id, "removed a computer", { computer: d.hostname });
        return reply(200, {});
      }
      case "action": {
        const id = uuid(body.device_id);
        if (!ACTIONS.has(body.type)) return reply(400, { error: "not an action Kulan OS allows" });
        const org = await deviceOrg(id);
        if (!org) return reply(404, { error: "no such computer" });
        if (!(await approved(id as string))) return reply(400, { error: NOT_APPROVED });
        if (body.type === "erase") {
          const [d] = await sql`select hostname, left_at, last_report->'state' as state from fleet.devices where id = ${id}`;
          if (d.left_at) return reply(400, { error: "that computer has left the organisation" });
          if (!d.hostname || typeof body.confirm !== "string" || body.confirm.trim() !== d.hostname) {
            return reply(400, { error: "type the computer's name to confirm" });
          }
          if (!d.state?.erase_allowed) return reply(400, { error: "the person using that computer hasn't allowed remote erase" });
          if (!d.state?.encrypted) return reply(400, { error: "only encrypted computers can be erased remotely" });
        }
        const payload = await payloadFor(org, body.type, body.payload);
        if (payload instanceof Response) return payload;
        const [row] = await sql`insert into fleet.actions (device_id, type, requested_by, payload)
                                values (${id}, ${body.type}, ${email}, ${payload === null ? null : json(payload)}) returning id`;
        await audit(org, id, `asked: ${body.type}`, describe(body.type, payload));
        await wakeDevices([id as string]);
        return reply(200, { id: row.id });
      }
      // --- Groups --------------------------------------------------------------------------
      case "groups": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const groups = await sql`
          select g.id, g.name, g.policy, g.created_at, g.created_by,
                 coalesce((select array_agg(gd.device_id) from fleet.group_devices gd join fleet.devices d on d.id = gd.device_id
                           where gd.group_id = g.id and d.left_at is null), '{}') as devices,
                 coalesce((select array_agg(gp.person_id) from fleet.group_people gp join fleet.people p on p.id = gp.person_id
                           where gp.group_id = g.id and p.removed_at is null), '{}') as people,
                 coalesce((select jsonb_agg(jsonb_build_object('id', n.id, 'ssid', n.ssid, 'security', n.security, 'hidden', n.hidden)
                                            order by n.ssid) from fleet.group_networks n where n.group_id = g.id), '[]') as networks,
                 coalesce((select jsonb_agg(jsonb_build_object('id', pr.id, 'name', pr.name, 'uri', pr.uri) order by lower(pr.name))
                           from fleet.group_printers pr where pr.group_id = g.id), '[]') as printers
          from fleet.groups g where g.organisation_id = ${org} order by lower(g.name)`;
        return reply(200, { groups });
      }
      case "add-group": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const name = typeof body.name === "string" ? body.name.trim().replace(/\s+/g, " ").slice(0, 60) : "";
        if (!name) return reply(400, { error: "a group needs a name" });
        const policy = body.policy === undefined ? {} : cleanPolicy(body.policy);
        if (!policy) return reply(400, { error: "that policy has an app name that can't be used, or blocks part of Kulan OS" });
        try {
          const [g] = await sql`insert into fleet.groups (organisation_id, name, policy, created_by)
                                values (${org}, ${name}, ${json(policy)}, ${email}) returning id`;
          await audit(org, null, `added group ${name}`.slice(0, 80), { policy });
          return reply(200, { id: g.id });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "there's already a group with that name" });
          throw e;
        }
      }
      case "edit-group": {
        const g = await group(uuid(body.group_id));
        if (!g) return reply(404, { error: "no such group" });
        const org = g.organisation_id;
        const name = body.name === undefined ? g.name : (typeof body.name === "string" ? body.name.trim().replace(/\s+/g, " ").slice(0, 60) : "");
        if (!name) return reply(400, { error: "a group needs a name" });
        const policy = body.policy === undefined ? g.policy : cleanPolicy(body.policy);
        if (!policy) return reply(400, { error: "that policy has an app name that can't be used, or blocks part of Kulan OS" });
        const devices = body.devices === undefined ? null : await computersOf(org, body.devices);
        if (body.devices !== undefined && devices === null) return reply(400, { error: "choose computers from this organisation" });
        const members = body.people === undefined ? null : await peopleOf(org, body.people);
        if (body.people !== undefined && members === null) return reply(400, { error: "choose people from this organisation" });
        try {
          await sql.begin(async (tx) => {
            await tx`update fleet.groups set name = ${name}, policy = ${json(policy)} where id = ${g.id}`;
            if (devices) {
              await tx`delete from fleet.group_devices where group_id = ${g.id}`;
              for (const d of devices) await tx`insert into fleet.group_devices (group_id, device_id) values (${g.id}, ${d})`;
            }
            if (members) {
              await tx`delete from fleet.group_people where group_id = ${g.id}`;
              for (const m of members) await tx`insert into fleet.group_people (group_id, person_id) values (${g.id}, ${m})`;
            }
          });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "there's already a group with that name" });
          throw e;
        }
        const changed = [name !== g.name && "name", JSON.stringify(policy) !== JSON.stringify(g.policy) && "policy",
                         devices && "computers", members && "people"].filter(Boolean);
        if (changed.length) await audit(org, null, `changed group ${name}`.slice(0, 80), { changed, ...(changed.includes("policy") ? { policy } : {}) });
        return reply(200, {});
      }
      case "remove-group": {
        const g = await group(uuid(body.group_id));
        if (!g) return reply(404, { error: "no such group" });
        if (body.confirm !== g.name) return reply(400, { error: "type the group's name to confirm" });
        await sql`delete from fleet.groups where id = ${g.id}`;
        await audit(g.organisation_id, null, `removed group ${g.name}`.slice(0, 80));
        return reply(200, {});
      }
      case "device-policy": {
        const id = uuid(body.device_id);
        const org = await deviceOrg(id);
        if (!org) return reply(404, { error: "no such computer" });
        const [o] = await sql`select policy from fleet.organisations where id = ${org}`;
        const groups = await sql`select g.id, g.name, g.policy from fleet.groups g join fleet.group_devices gd on gd.group_id = g.id
                                 where gd.device_id = ${id} order by lower(g.name)`;
        const { policy, conflicts } = mergePolicies([o?.policy ?? {}, ...groups.map((g) => g.policy)]);
        return reply(200, { policy, conflicts, groups: groups.map((g) => ({ id: g.id, name: g.name })) });
      }
      case "drives": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const approved = await sql`select id, name, vendor_id, product_id, serial, created_at, created_by
                                   from fleet.approved_drives where organisation_id = ${org} order by lower(name)`;
        const rows = await sql`select hostname, last_report->'usb_seen' as seen from fleet.devices
                               where organisation_id = ${org} and left_at is null and last_report ? 'usb_seen'`;
        const known = new Set(approved.map((a) => `${a.vendor_id}:${a.product_id}:${a.serial}`));
        const seen = new Map<string, Record<string, unknown>>();
        for (const r of rows) {
          for (const d of Array.isArray(r.seen) ? r.seen : []) {
            const key = `${d.vendor_id}:${d.product_id}:${d.serial}`;
            if (known.has(key)) continue;
            const s = seen.get(key) ?? { ...d, computers: [] as string[] };
            (s.computers as string[]).push(r.hostname ?? "");
            seen.set(key, s);
          }
        }
        return reply(200, { approved, seen: [...seen.values()].slice(0, 200) });
      }
      case "approve-drive": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const { vendor_id, product_id, serial } = body;
        const name = typeof body.name === "string" ? body.name.trim().slice(0, 80) : "";
        if (!name || typeof vendor_id !== "string" || !HEX4.test(vendor_id) || typeof product_id !== "string" ||
            !HEX4.test(product_id) || typeof serial !== "string" || !SERIAL.test(serial)) {
          return reply(400, { error: "a drive needs a name, vendor, product and serial number" });
        }
        await sql`insert into fleet.approved_drives (organisation_id, name, vendor_id, product_id, serial, created_by)
                  values (${org}, ${name}, ${vendor_id}, ${product_id}, ${serial}, ${email})
                  on conflict (organisation_id, vendor_id, product_id, serial) do update set name = excluded.name`;
        await audit(org, null, `approved USB drive ${name}`.slice(0, 80), { vendor_id, product_id, serial });
        return reply(200, {});
      }
      case "remove-drive": {
        const id = uuid(body.drive_id);
        const rows = id ? await sql`select organisation_id, name from fleet.approved_drives where id = ${id}` : [];
        if (!rows.length || !canOrg(rows[0].organisation_id)) return reply(404, { error: "no such drive" });
        await sql`delete from fleet.approved_drives where id = ${id}`;
        await audit(rows[0].organisation_id, null, `removed USB drive ${rows[0].name}`.slice(0, 80));
        return reply(200, {});
      }
      case "group-action": {
        const g = await group(uuid(body.group_id));
        if (!g) return reply(404, { error: "no such group" });
        if (!GROUP_ACTIONS.has(body.type)) return reply(400, { error: "that can't be asked of a whole group" });
        const payload = await payloadFor(g.organisation_id, body.type, body.payload);
        if (payload instanceof Response) return payload;
        const rows = await sql`
          insert into fleet.actions (device_id, type, requested_by, payload)
          select gd.device_id, ${body.type}, ${email}, ${payload === null ? null : json(payload)}
          from fleet.group_devices gd join fleet.devices d on d.id = gd.device_id
          where gd.group_id = ${g.id} and d.left_at is null and d.approved_at is not null
          returning id, device_id`;
        await audit(g.organisation_id, null, `asked group ${g.name}: ${body.type}`.slice(0, 80),
                    { computers: rows.length, ...(describe(body.type, payload) ?? {}) });
        await wakeDevices(rows.map((r) => r.device_id));
        return reply(200, { count: rows.length });
      }
      // --- Security and activity (2.8c) ---------------------------------------------------
      case "weekly-report": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const now = Date.now();
        const [o] = await sql`select created_at from fleet.organisations where id = ${org}`;
        const devices = await sql`
          select id, hostname, kulan_version, last_seen, left_at, last_report->'updates' as updates,
                 last_report->'state' as state, last_report->'policy_status' as policy_status,
                 last_report->'compliance'->'passed' as compliance_passed, last_report->'compliance'->'total' as compliance_total
          from fleet.devices where organisation_id = ${org} and left_at is null order by hostname`;
        const people = await sql`select p.username, p.role, p.enabled, p.must_change, p.password_changed_at, p.removed_at, p.mailbox_todo,
                                        exists (select 1 from fleet.person_factors f where f.person_id = p.id) as has_two_step
                                 from fleet.people p where p.organisation_id = ${org}
                                   and (p.removed_at is null or p.mailbox_todo is not null) order by p.username`;
        // Two-step sign-in asked for by the organisation's policy or any group's (the strictest).
        const [{ policy: orgPolicy }] = await sql`select policy from fleet.organisations where id = ${org}`;
        const groupPolicies = await sql`select policy from fleet.groups where organisation_id = ${org}`;
        const twoStep = mergePolicies([orgPolicy ?? {}, ...groupPolicies.map((g) => g.policy)]).policy.two_step;
        const activity = await sql`select what from fleet.audit where organisation_id = ${org} and at > now() - interval '7 days'`;
        const [last] = await sql`select reviewed_at, reviewed_by from fleet.access_reviews where organisation_id = ${org}
                                 order by reviewed_at desc limit 1`;
        const due = reviewDue(last?.reviewed_at ?? null, o?.created_at ?? null, now);
        const summary = weeklySummary(devices, people, activity,
          { at: last?.reviewed_at ?? null, by: last?.reviewed_by ?? null, due: due.due }, now, twoStep);
        const week = mondayOf(new Date(now));
        try {  // the snapshot is for next week's comparison; the report doesn't depend on it
          await sql`insert into fleet.weekly_reports (organisation_id, week, summary) values (${org}, ${week}, ${json(summary)})
                    on conflict (organisation_id, week) do update set summary = excluded.summary, made_at = now()`;
        } catch (e) {
          console.error("fleet-admin: weekly snapshot not kept:", e);
        }
        const [prev] = await sql`select week, summary from fleet.weekly_reports where organisation_id = ${org} and week < ${week}::date
                                 order by week desc limit 1`;
        if (Math.random() < 0.05) await sql`select fleet.prune_weekly_reports()`;
        return reply(200, { week, made_at: new Date(now).toISOString(), summary,
                            previous: prev ? { week: String(prev.week instanceof Date ? prev.week.toISOString().slice(0, 10) : prev.week), summary: prev.summary } : null });
      }
      case "access-review": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const [o] = await sql`select owner_email, created_at from fleet.organisations where id = ${org}`;
        const people = await sql`
          select p.id, p.username, p.full_name, p.role, p.enabled, p.all_computers, p.created_at, p.created_by,
                 coalesce((select array_agg(pc.device_id) from fleet.person_computers pc where pc.person_id = p.id), '{}') as computers,
                 coalesce((select array_agg(pg.group_id) from fleet.person_groups pg where pg.person_id = p.id), '{}') as groups
          from fleet.people p where p.organisation_id = ${org} and p.removed_at is null and p.role <> 'employee' order by p.username`;
        const devices = await sql`select d.id, d.hostname, d.left_at, d.last_report->'sign_ins' as sign_ins,
                                         coalesce((select array_agg(gd.group_id) from fleet.group_devices gd where gd.device_id = d.id), '{}') as groups
                                  from fleet.devices d where d.organisation_id = ${org} and d.left_at is null order by d.hostname`;
        const signIns = signInsByUser(devices);
        const admins = await sql`select email, created_at from fleet.admins where organisation_id = ${org} order by email`;
        const everywhere = await sql`select email, created_at from fleet.admins where organisation_id is null order by email`;
        const [last] = await sql`select reviewed_at, reviewed_by, kept, removed from fleet.access_reviews
                                 where organisation_id = ${org} order by reviewed_at desc limit 1`;
        const due = reviewDue(last?.reviewed_at ?? null, o?.created_at ?? null, Date.now());
        return reply(200, {
          people: people.map((p) => ({
            id: p.id, username: p.username, full_name: p.full_name, role: p.role, enabled: p.enabled,
            created_at: p.created_at, created_by: p.created_by,
            where: p.all_computers ? "all" : devices.filter((d) => p.computers.includes(d.id) ||
                     d.groups.some((g: string) => p.groups.includes(g))).map((d) => d.hostname || "(no name)"),
            signed_in: signIns[p.username] ?? [],
          })),
          admins: admins.map((a) => ({ ...a, owner: a.email === o?.owner_email })),
          all_organisations_admins: everywhere,
          last_review: last ?? null, due: due.due, days_since: due.days, is_owner: await isOwner(org),
        });
      }
      case "review-access": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner reviews who has administrator rights" });
        const removePeople = Array.isArray(body.remove_people) ? [...new Set(body.remove_people.map(uuid))] : [];
        const removeAdmins = (Array.isArray(body.remove_admins) ? [...new Set(body.remove_admins.map(cleanEmail))] : []) as string[];
        if (removePeople.includes(null) || removeAdmins.some((e) => !e)) return reply(400, { error: "choose from the list" });
        const [o] = await sql`select owner_email from fleet.organisations where id = ${org}`;
        const listed = await sql`select id, username, role from fleet.people
                                 where organisation_id = ${org} and removed_at is null and role <> 'employee'`;
        const admins = await sql`select email from fleet.admins where organisation_id = ${org}`;
        const byId = new Map(listed.map((p) => [p.id, p]));
        const people = removePeople.map((id) => byId.get(id as string));
        if (people.some((p) => !p)) return reply(400, { error: "choose people from the list" });
        if (people.some((p) => p!.role === "owner")) return reply(400, { error: "the Owner's own account keeps its rights" });
        const adminSet = new Set(admins.map((a) => a.email));
        if (removeAdmins.some((e) => !adminSet.has(e as string))) return reply(400, { error: "choose admins from the list" });
        if (removeAdmins.includes(o?.owner_email)) return reply(400, { error: "the Owner stays an admin" });
        const total = listed.length + admins.length;
        const removed = people.length + removeAdmins.length;
        await sql.begin(async (tx) => {
          for (const p of people) await tx`update fleet.people set role = 'employee' where id = ${p!.id}`;
          for (const e of removeAdmins) await tx`delete from fleet.admins where email = ${e} and organisation_id = ${org}`;
          await tx`insert into fleet.access_reviews (organisation_id, reviewed_by, kept, removed, detail)
                   values (${org}, ${email}, ${total - removed}, ${removed},
                           ${json({ admin_role_taken: people.map((p) => p!.username), fleet_access_taken: removeAdmins })})`;
        });
        for (const e of removeAdmins) await endHelpOf(org, e);
        await audit(org, null, `reviewed admin rights (kept ${total - removed}, removed ${removed})`.slice(0, 80),
                    { admin_role_taken: people.map((p) => p!.username), fleet_access_taken: removeAdmins });
        return reply(200, { kept: total - removed, removed });
      }
      // --- Stronger sign-in (2.8d) ------------------------------------------------------------
      case "set-admin-two-step": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner can change this" });
        if (typeof body.required !== "boolean") return reply(400, { error: "required is true or false" });
        if (body.required && me.aal !== "aal2") {
          return reply(400, { error: "set up your own authenticator app first, so you aren't locked out", two_step: "set-up" });
        }
        await sql`update fleet.organisations set admin_two_step = ${body.required} where id = ${org}`;
        await audit(org, null, body.required ? "required an authenticator app for Kulan Fleet admins"
                                             : "stopped requiring an authenticator app for Kulan Fleet admins");
        return reply(200, {});
      }
      case "reset-admin-two-step": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner resets an admin's authenticator app" });
        const who = cleanEmail(body.email);
        if (!who) return reply(400, { error: "that email doesn't look right" });
        const isAdmin = await sql`select 1 from fleet.admins where email = ${who} and organisation_id = ${org}`;
        if (!isAdmin.length) return reply(400, { error: "choose one of the organisation's admins" });
        // Their authenticator app guards their whole Kulan Fleet sign-in: only
        // an Owner of every organisation they're in may reset it (Kulan otherwise).
        const elsewhere = await sql`select 1 from fleet.admins where email = ${who} and (organisation_id is null or organisation_id <> ${org})
                                    union all
                                    select 1 from fleet.organisations where owner_email = ${who} and id <> ${org}`;
        if (elsewhere.length && !global) {
          return reply(403, { error: "they're also an admin in another organisation. Ask Kulan to reset their authenticator app" });
        }
        const [user] = await sql`select id from auth.users where lower(email) = ${who} limit 1`;
        let removed = 0;
        if (user) {
          const list = await authAdmin(`/users/${user.id}/factors`);
          if (!list.ok) return reply(502, { error: "the sign-in service didn't respond. Try again" });
          for (const f of (await list.json()) as Record<string, unknown>[]) {
            if (f?.factor_type !== "totp" || typeof f.id !== "string") continue;
            const del = await authAdmin(`/users/${user.id}/factors/${f.id}`, "DELETE");
            if (!del.ok) return reply(502, { error: "the sign-in service didn't respond. Try again" });
            removed++;
          }
        }
        await audit(org, null, `reset ${who}'s authenticator app`.slice(0, 80), { removed });
        return reply(200, { removed });
      }
      case "reset-two-step": {
        const p = await person(uuid(body.person_id));
        if (!p) return reply(404, { error: "no such person" });
        if (p.role !== "employee" && !(await isOwner(p.organisation_id))) {
          return reply(403, { error: "only the Owner resets an admin's or the Owner's two-step sign-in" });
        }
        const gone = await sql`delete from fleet.person_factors where person_id = ${p.id} returning id`;
        await audit(p.organisation_id, null, `reset ${p.username}'s two-step sign-in`.slice(0, 80), { removed: gone.length });
        return reply(200, { removed: gone.length });
      }
      // --- Work email: Google Workspace (2.8e), Microsoft 365 (2.12) ---------------------------
      case "mail": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const [c] = await sql`select provider, domain, admin_email, client_email, client_id, tenant_id, licence_name,
                                     usage_location, connected_by, connected_at, checked_at, last_error
                              from fleet.mail_connections where organisation_id = ${org}`;
        const [{ n }] = await sql`select count(*)::int as n from fleet.people where organisation_id = ${org}
                                  and removed_at is null and mailbox is not null and mailbox_state is distinct from 'deleted'`;
        const [{ left }] = await sql`select count(*)::int as left from fleet.people where organisation_id = ${org}
                                     and mailbox_todo is not null`;
        return reply(200, { connection: c ?? null, mailboxes: n, left_to_do: left, scope: google.SCOPE,
                            permissions: microsoft.PERMISSIONS, is_owner: await isOwner(org) });
      }
      case "connect-mail": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const provider = body.provider === "microsoft" ? "microsoft" : "google";
        if (!(await isOwner(org))) return reply(403, { error: `only the Owner connects ${mail.label(provider)}` });
        // A connection to another provider is replaced only once it's been disconnected (its mailboxes are People's).
        const [had] = await sql`select provider from fleet.mail_connections where organisation_id = ${org}`;
        if (had && had.provider !== provider) {
          return reply(409, { error: `${mail.label(had.provider)} is connected. Disconnect it first` });
        }
        // After switching services, mailboxes from the old one can't be looked after: unlinked first (and
        // removed people's still to suspend or delete, stopped), so nothing is done to the wrong account.
        const switchBlocked = async (): Promise<Response | null> => {
          if (had) return null;
          const [last] = await sql`select what from fleet.audit where organisation_id = ${org}
                                   and what in ('connected Google Workspace', 'connected Microsoft 365')
                                   order by at desc, id desc limit 1`;
          const before = last ? (last.what === "connected Microsoft 365" ? "microsoft" : "google") : provider;
          if (before === provider) return null;
          const [{ n }] = await sql`select count(*)::int as n from fleet.people where organisation_id = ${org}
                                    and mailbox is not null and mailbox_state is distinct from 'deleted'
                                    and (removed_at is null or mailbox_todo is not null)`;
          if (!n) return null;
          return reply(409, { error: `${n === 1 ? "1 person still has a" : `${n} people still have`} ${mail.label(before)} ` +
                                     `mailbox${n === 1 ? "" : "es"} in Kulan Fleet. Unlink ${n === 1 ? "it" : "them"} in People ` +
                                     "first (for someone removed: Stop retrying)" });
        };
        const domainOk = (d: string) => /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(d);
        // One organisation per mail domain (Kulan's own admins may decide otherwise).
        const takenElsewhere = async (d: string) =>
          !global && (await sql`select 1 from fleet.mail_connections where domain = ${d} and organisation_id <> ${org}`).length > 0;
        if (provider === "microsoft") {
          const app = microsoft.parseApp(body);
          if (!app) {
            return reply(400, { error: "enter the app's Directory (tenant) ID and Application (client) ID from its Overview " +
                                       "page in Microsoft Entra, and a client secret's Value", permissions: microsoft.PERMISSIONS });
          }
          const domain = typeof body.domain === "string" ? body.domain.trim().toLowerCase() : "";
          if (!domainOk(domain)) return reply(400, { error: "enter your email domain, for example acme.com" });
          const country = typeof body.usage_location === "string" && body.usage_location.trim()
            ? body.usage_location.trim().toUpperCase() : "US";
          if (!microsoft.COUNTRY.test(country)) return reply(400, { error: "choose the country people's mailboxes are used in" });
          if (await takenElsewhere(domain)) return reply(409, { error: `${domain} is connected to another organisation. Contact Kulan` });
          const blocked = await switchBlocked();
          if (blocked) return blocked;
          const sealed = await mail.sealText(app.secret, org);
          if (!sealed) return reply(500, { error: "the server's encryption key is missing. Contact Kulan" });
          let licence: microsoft.Licence;
          try {
            licence = await microsoft.check({ organisation_id: org, domain, tenant_id: app.tenant_id, client_id: app.client_id,
                                              secret: app.secret, licence: null, usage_location: country });
          } catch (e) {
            return reply(400, { error: why(e), permissions: microsoft.PERMISSIONS });
          }
          await sql`insert into fleet.mail_connections (organisation_id, provider, domain, admin_email, client_email, client_id,
                                                        private_key, tenant_id, licence, licence_name, usage_location,
                                                        connected_by, checked_at)
                    values (${org}, 'microsoft', ${domain}, ${email}, ${app.client_id}, ${app.client_id}, ${sealed}, ${app.tenant_id},
                            ${licence.sku}, ${licence.name}, ${country}, ${email}, now())
                    on conflict (organisation_id) do update set provider = 'microsoft', domain = excluded.domain,
                      admin_email = excluded.admin_email, client_email = excluded.client_email, client_id = excluded.client_id,
                      private_key = excluded.private_key, tenant_id = excluded.tenant_id, licence = excluded.licence,
                      licence_name = excluded.licence_name, usage_location = excluded.usage_location,
                      connected_by = excluded.connected_by, connected_at = now(), checked_at = now(), last_error = null`;
          await audit(org, null, "connected Microsoft 365", { domain, tenant: app.tenant_id, app: app.client_id,
                                                              licence: licence.name, free: licence.free });
          return reply(200, { domain, licence: licence.name, free: licence.free });
        }
        const key = google.parseServiceAccount(body.key);
        if (!key) {
          return reply(400, { error: "that isn't a service account's JSON key. In Google Cloud, open the service account and " +
                                     "choose Keys › Add key › Create new key › JSON" });
        }
        const adminEmail = cleanEmail(body.admin_email);
        if (!adminEmail) return reply(400, { error: "enter a Google super admin's email" });
        const domain = typeof body.domain === "string" && body.domain.trim() ? body.domain.trim().toLowerCase() : adminEmail.split("@")[1];
        if (!domainOk(domain)) return reply(400, { error: "that domain doesn't look right" });
        if (await takenElsewhere(domain)) return reply(409, { error: `${domain} is connected to another organisation. Contact Kulan` });
        const blocked = await switchBlocked();
        if (blocked) return blocked;
        const sealed = await mail.sealText(key.private_key, org);
        if (!sealed) return reply(500, { error: "the server's encryption key is missing. Contact Kulan" });
        try {
          await google.check({ organisation_id: org, domain, admin_email: adminEmail, client_email: key.client_email,
                               pem: key.private_key });
        } catch (e) {
          return reply(400, { error: why(e), client_id: key.client_id, scope: google.SCOPE });
        }
        await sql`insert into fleet.mail_connections (organisation_id, provider, domain, admin_email, client_email, client_id,
                                                      private_key, connected_by, checked_at)
                  values (${org}, 'google', ${domain}, ${adminEmail}, ${key.client_email}, ${key.client_id}, ${sealed}, ${email}, now())
                  on conflict (organisation_id) do update set provider = 'google', domain = excluded.domain,
                    admin_email = excluded.admin_email, client_email = excluded.client_email, client_id = excluded.client_id,
                    private_key = excluded.private_key, tenant_id = null, licence = null, licence_name = null,
                    usage_location = null, connected_by = excluded.connected_by, connected_at = now(),
                    checked_at = now(), last_error = null`;
        await audit(org, null, "connected Google Workspace", { domain, acting_as: adminEmail, service_account: key.client_email });
        return reply(200, { domain });
      }
      case "disconnect-mail": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const [c] = await sql`select provider from fleet.mail_connections where organisation_id = ${org}`;
        if (!(await isOwner(org))) return reply(403, { error: `only the Owner disconnects ${c ? mail.label(c.provider) : "work email"}` });
        const gone = await sql`delete from fleet.mail_connections where organisation_id = ${org} returning domain, provider`;
        if (!gone.length) return reply(400, { error: NOT_CONNECTED });
        // What was left to do stays (an alert), so it isn't forgotten: done by hand, or after connecting again.
        const [{ n }] = await sql`select count(*)::int as n from fleet.people where organisation_id = ${org} and mailbox_todo is not null`;
        await audit(org, null, `disconnected ${mail.label(gone[0].provider)}`, { domain: gone[0].domain, mailboxes_left_to_do: n });
        return reply(200, {});
      }
      case "mail-users": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner links existing mailboxes" });
        let conn: mail.Connection | null;
        let users: mail.MailUser[];
        try {
          conn = await mailConn(org);
          if (!conn) return reply(400, { error: NOT_CONNECTED });
          users = await mail.listUsers(conn);
          await noteGoogle(org, null);
        } catch (e) {
          await noteGoogle(org, why(e));
          return reply(502, { error: why(e) });
        }
        const linked = await sql`select username, mailbox from fleet.people where organisation_id = ${org}
                                 and mailbox is not null and mailbox_state is distinct from 'deleted'`;
        const by = new Map(linked.map((l) => [l.mailbox, l.username]));
        return reply(200, { users: users.map((u) => ({ ...u, username: by.get(u.address) ?? null })) });
      }
      case "mailbox": {
        const p = await person(uuid(body.person_id), body.action === "retry" || body.action === "forget");
        if (!p) return reply(404, { error: "no such person" });
        const org = p.organisation_id;
        if (!(await mayManage(p))) return reply(403, { error: "only the Owner manages an admin's or the Owner's mailbox" });
        const action = body.action;
        if (action === "retry") {
          if (!p.mailbox_todo) return reply(200, {});
          const err = await mailboxDo(p, p.mailbox_todo);
          return reply(200, err ? { mailbox_error: err } : {});
        }
        const stopsOffboarding = p.mailbox_todo === "suspend" || p.mailbox_todo === "delete";
        if ((action === "forget" || action === "unlink") && stopsOffboarding && !(await isOwner(org))) {
          return reply(403, { error: `their mailbox is ${p.mailbox_todo === "delete" ? "still to delete" : "still to suspend"}. ` +
                                     "Retry it, or ask the Owner if it was done by hand" });
        }
        if (action === "forget") {
          if (!p.mailbox_todo) return reply(200, {});
          await sql`update fleet.people set mailbox_todo = null, mailbox_error = null where id = ${p.id}`;
          await audit(org, null, `stopped retrying ${p.username}'s mailbox`.slice(0, 80), { todo: p.mailbox_todo });
          return reply(200, {});
        }
        if (action === "unlink") {
          if (!p.mailbox) return reply(400, { error: "they have no mailbox in Kulan Fleet" });
          await sql`update fleet.people set mailbox = null, mailbox_state = null, mailbox_google_id = null, mailbox_todo = null,
                      mailbox_error = null where id = ${p.id}`;
          await audit(org, null, `unlinked ${p.username}'s mailbox`.slice(0, 80), { mailbox: p.mailbox });
          return reply(200, {});
        }
        let conn: mail.Connection | null;
        try {
          conn = await mailConn(org);
        } catch (e) {
          return reply(502, { error: why(e) });
        }
        if (!conn) return reply(400, { error: "connect Google Workspace or Microsoft 365 first" });
        try {
          if (action === "create" || action === "link") {
            if (p.mailbox && p.mailbox_state !== "deleted") return reply(400, { error: `they already have a mailbox, ${p.mailbox}` });
            if (action === "link" && !(await isOwner(org))) return reply(403, { error: "only the Owner links an existing mailbox" });
            const address = await freeAddress(org, body.address, p.id);
            if (address instanceof Response) return address;
            const r = await giveMailbox(p, action, address, conn);
            await noteGoogle(org, null);
            return reply(r.status, r.body);
          }
          if (action === "new-password") {
            if (!p.mailbox || p.mailbox_state === "deleted") return reply(400, { error: "they have no mailbox" });
            const u = await theirMailbox(conn, p);
            if (!u) return reply(404, { error: `${mail.short(conn.provider)} has no mailbox ${p.mailbox} any more` });
            if (u.admin && !(await isOwner(org))) return reply(403, { error: adminMailbox(conn) });
            const password = mail.tempPassword();
            await mail.setPassword(conn, u.id || p.mailbox, password);
            await noteGoogle(org, null);
            await audit(org, null, `gave ${p.username} a new ${mail.short(conn.provider)} password`.slice(0, 80), { mailbox: p.mailbox });
            return reply(200, { mailbox: p.mailbox, mailbox_password: password });
          }
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "someone else was given that mailbox just now" });
          if (!(e instanceof mail.MailError) || e.status === 0 || e.status === 403 || e.status >= 500) await noteGoogle(org, why(e));
          return reply(e instanceof mail.MailError && e.status === 409 ? 409 : 502, { error: why(e) });
        }
        return reply(400, { error: "the action is create, link, unlink, new-password, retry or forget" });
      }
      // --- Remote help (2.9a) ------------------------------------------------------------
      case "help-start": {
        const id = uuid(body.device_id);
        const org = await deviceOrg(id);
        if (!org) return reply(404, { error: "no such computer" });
        const [d] = await sql`select hostname, left_at, kulan_version, last_report->'sign_ins' as sign_ins
                              from fleet.devices where id = ${id}`;
        if (d.left_at) return reply(400, { error: "that computer has left the organisation" });
        if (!(await approved(id as string))) return reply(400, { error: NOT_APPROVED });
        if (!versionAtLeast(d.kulan_version, HELP_SINCE)) {
          return reply(400, { error: "remote help needs Kulan OS 2.9a or later on that computer. Install its updates first" });
        }
        await sql`select fleet.expire_help_sessions()`;
        const busy = await sql`select id, requested_by, state from fleet.help_sessions
                               where device_id = ${id} and state = any(${help.ACTIVE}::text[])`;
        if (busy.some((b) => b.requested_by !== email)) {
          return reply(409, { error: `${busy.find((b) => b.requested_by !== email)!.requested_by} is helping on that computer now` });
        }
        // Never a new question on a screen the admin is already seeing (they could answer it themselves).
        if (busy.some((b) => b.state === "allowed" || b.state === "connected")) {
          return reply(409, { error: "you're already connected to that computer. End that session first" });
        }
        // A person isn't asked again and again: one request every 20 seconds per computer, 20 an hour per admin.
        const [recent] = await sql`select
            count(*) filter (where device_id = ${id} and created_at > now() - interval '20 seconds')::int as here,
            count(*) filter (where requested_by = ${email} and created_at > now() - interval '1 hour')::int as hour
          from fleet.help_sessions where created_at > now() - interval '1 hour'`;
        if (recent.here > 0) return reply(429, { error: "wait a few seconds before asking that computer again" });
        if (recent.hour >= 20) return reply(429, { error: "too many remote help requests this hour. Try again later" });
        // Started again by the same admin (a closed tab, say): the old one ends.
        for (const b of busy) {
          await sql`update fleet.help_sessions set state = 'ended', detail = 'started again', ended_at = now(), updated_at = now()
                    where id = ${b.id} and state = any(${help.ACTIVE}::text[])`;
          await sql`update fleet.actions set status = 'cancelled', finished_at = now()
                    where device_id = ${id} and type = 'remote-help' and status = 'pending' and payload->>'session_id' = ${b.id}`;
        }
        const control = body.control === true;
        const ice = await help.iceServers();
        let session;
        try {
          session = await sql.begin(async (tx) => {
            const [s] = await tx`insert into fleet.help_sessions (organisation_id, device_id, requested_by, control_asked, ice)
                                 values (${org}, ${id}, ${email}, ${control}, ${json(ice)}) returning id`;
            await tx`insert into fleet.actions (device_id, type, requested_by, payload)
                     values (${id}, 'remote-help', ${email}, ${json({ session_id: s.id })})`;
            return s;
          });
        } catch (e) {  // another admin asked at the same moment (one live session per computer)
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "someone else is helping on that computer now" });
          throw e;
        }
        await audit(org, id, control ? "asked to see and control the screen" : "asked to see the screen", { session: session.id });
        if (Math.random() < 0.05) await sql`select fleet.prune_help_sessions()`;
        const woke = await wakeDevices([id as string]);
        const signedInNow = Array.isArray(d.sign_ins) && d.sign_ins.some((x: Record<string, unknown>) => x?.now === true);
        return reply(200, { session_id: session.id, ice, relay: ice.some((x) => x.urls.some((u) => u.startsWith("turn"))),
                            woke: woke > 0, signed_in: signedInNow, hostname: d.hostname });
      }
      case "help-signal":
      case "help-end": {
        const sid = uuid(body.session_id);
        await sql`select fleet.expire_help_sessions()`;
        const [s] = sid ? await sql`select * from fleet.help_sessions where id = ${sid}` : [];
        // Only the admin who asked: the person allowed them, by name.
        if (!s || !canOrg(s.organisation_id) || s.requested_by !== email) return reply(404, { error: "no such session" });
        const active = help.ACTIVE.includes(s.state);
        if (body.op === "help-end") {
          if (!active) return reply(200, { state: s.state });
          const done = await sql`update fleet.help_sessions set state = 'ended', detail = 'the admin ended it', ended_at = now(),
                                        updated_at = now() where id = ${sid} and state = any(${help.ACTIVE}::text[]) returning id`;
          await sql`update fleet.actions set status = 'cancelled', finished_at = now()
                    where device_id = ${s.device_id} and type = 'remote-help' and status = 'pending' and payload->>'session_id' = ${sid}`;
          if (done.length) await audit(s.organisation_id, s.device_id, "ended remote help", { session: sid });
          return reply(200, { state: "ended" });
        }
        if (active && body.answer !== undefined) {
          const answer = help.cleanDescription(body.answer, "answer");
          if (!answer) return reply(400, { error: "that isn't an answer" });
          // The same answer again (a reply that got lost) is fine; another one isn't.
          if (!s.offer || (s.answer && s.answer.sdp !== answer.sdp)) return reply(409, { error: "not now", state: s.state });
          if (!s.answer) {
            await sql`update fleet.help_sessions set answer = ${json(answer)}, updated_at = now() where id = ${sid} and answer is null`;
          }
        }
        const candidates = active ? help.cleanCandidates(body.candidates) : [];
        if (candidates.length) await help.addCandidates(sql, sid as string, "admin_candidates", candidates);
        const since = Number.isInteger(body.since) && body.since >= 0 ? body.since : 0;
        const theirs = Array.isArray(s.device_candidates) ? s.device_candidates : [];
        return reply(200, { state: s.state, control: s.control, detail: s.detail, who: s.answered_by,
                            offer: s.offer ?? null, candidates: theirs.slice(since), count: theirs.length });
      }
      // --- Actions (2.9b) --------------------------------------------------------------------
      case "message-person": {
        const p = await person(uuid(body.person_id));
        if (!p) return reply(404, { error: "no such person" });
        const msg = acts.cleanMessage(body);
        if (!msg) return reply(400, { error: "a message needs a title" });
        // The computers they're signed in on now (their last reports say so).
        const rows = await sql`
          insert into fleet.actions (device_id, type, requested_by, payload)
          select d.id, 'message', ${email}, ${json(msg)} from fleet.devices d
          where d.organisation_id = ${p.organisation_id} and d.left_at is null
            and exists (select 1 from jsonb_array_elements(coalesce(d.last_report->'sign_ins', '[]'::jsonb)) s
                        where s->>'username' = ${p.username} and (s->>'now')::boolean)
          returning device_id`;
        if (!rows.length) return reply(400, { error: `${p.username} isn't signed in on any computer now` });
        await audit(p.organisation_id, null, `sent ${p.username} a message`.slice(0, 80), { title: msg.title, computers: rows.length });
        await wakeDevices(rows.map((r) => r.device_id));
        return reply(200, { count: rows.length });
      }
      case "action-output": {
        const id = uuid(body.action_id);
        const [a] = id ? await sql`select a.output, a.exit_code, a.status, a.type, d.organisation_id from fleet.actions a
                                   join fleet.devices d on d.id = a.device_id where a.id = ${id}` : [];
        if (!a || !canOrg(a.organisation_id)) return reply(404, { error: "no such request" });
        if (a.type === "run-script" && me.aal !== "aal2") {  // a script's output may hold what it was run to find
          return reply(403, { error: "seeing a script's output needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        return reply(200, { output: a.output ?? null, exit_code: a.exit_code, status: a.status, type: a.type });
      }
      case "scripts": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const [o] = await sql`select allow_scripts, script_key, script_key_set_at from fleet.organisations where id = ${org}`;
        // What a script says (it may hold a key or a password) only after the authenticator app's code.
        const scripts = await sql`select id, name, description, ${me.aal === "aal2" ? sql`body` : sql`null`} as body, sha256,
                                         signature, created_at, created_by, updated_at, updated_by
                                  from fleet.scripts where organisation_id = ${org} order by lower(name)`;
        for (const sc of scripts) {
          sc.signed = await acts.verifySigned(o?.script_key, sc.signature, acts.scriptMessage(sc.sha256));
          delete sc.signature;
        }
        return reply(200, { allowed: o?.allow_scripts === true, scripts, is_owner: await isOwner(org),
                            bodies: me.aal === "aal2", script_key: o?.script_key ?? null, script_key_set_at: o?.script_key_set_at ?? null });
      }
      case "set-script-key": {
        // The Owner's script signing key: its public half only (the code it comes from never leaves the Owner).
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner sets up script signing" });
        if (me.aal !== "aal2") {
          return reply(403, { error: "script signing needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        const key = body.public_key;
        if (typeof key !== "string" || !acts.SCRIPT_KEY.test(key)) return reply(400, { error: "that isn't a script signing key" });
        const [o] = await sql`select script_key from fleet.organisations where id = ${org}`;
        if (o?.script_key === key) return reply(200, {});
        // A new code: signed by the old one (computers move to it by themselves), or a fresh start
        // (computers keep the old key until someone with their admin password takes the new one).
        let endorsement: string | null = null;
        if (o?.script_key) {
          if (await acts.verifySigned(o.script_key, body.endorsement, acts.keyMessage(key))) endorsement = body.endorsement as string;
          else if (body.reset !== true) return reply(400, { error: "type the current script signing code to change it" });
        }
        await sql.begin(async (tx) => {
          await tx`update fleet.organisations set script_key = ${key}, script_key_endorsement = ${endorsement},
                          script_key_set_at = now() where id = ${org}`;
          // Signed with the old key: to be signed again (computers check against the new one).
          await tx`update fleet.scripts set signature = null where organisation_id = ${org}`;
          await tx`update fleet.actions a set status = 'cancelled', finished_at = now()
                   from fleet.devices d where d.id = a.device_id and d.organisation_id = ${org}
                     and a.type = 'run-script' and a.status = 'pending'`;
        });
        await audit(org, null, !o?.script_key ? "set up script signing"
                                : endorsement ? "changed the script signing code" : "set up a new script signing code (the old one wasn't given)");
        return reply(200, { reset: !!o?.script_key && !endorsement });
      }
      case "sign-script": {
        const id = uuid(body.script_id);
        const [sc] = id ? await sql`select s.organisation_id, s.name, s.sha256, o.script_key from fleet.scripts s
                                    join fleet.organisations o on o.id = s.organisation_id where s.id = ${id}` : [];
        if (!sc || !canOrg(sc.organisation_id)) return reply(404, { error: "no such script" });
        if (me.aal !== "aal2") {
          return reply(403, { error: "signing a script needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        if (!(await acts.verifySigned(sc.script_key, body.signature, acts.scriptMessage(sc.sha256)))) {
          return reply(400, { error: "that signing code isn't the organisation's" });
        }
        await sql`update fleet.scripts set signature = ${body.signature as string} where id = ${id}`;
        await audit(sc.organisation_id, null, `signed script ${sc.name}`.slice(0, 80), { sha256: sc.sha256 });
        return reply(200, {});
      }
      case "set-scripts-allowed": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        if (!(await isOwner(org))) return reply(403, { error: "only the Owner allows scripts" });
        if (typeof body.allowed !== "boolean") return reply(400, { error: "allowed is true or false" });
        if (body.allowed && me.aal !== "aal2") {
          return reply(403, { error: "allowing scripts needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        if (body.allowed && !(await sql`select 1 from fleet.organisations where id = ${org} and script_key is not null`).length) {
          return reply(400, { error: "set up script signing first" });
        }
        await sql`update fleet.organisations set allow_scripts = ${body.allowed} where id = ${org}`;
        if (!body.allowed) {  // scripts still waiting don't run
          await sql`update fleet.actions a set status = 'cancelled', finished_at = now()
                    from fleet.devices d where d.id = a.device_id and d.organisation_id = ${org}
                      and a.type = 'run-script' and a.status = 'pending'`;
        }
        await audit(org, null, body.allowed ? "allowed scripts on the computers" : "stopped scripts on the computers");
        return reply(200, {});
      }
      case "add-script":
      case "edit-script": {
        const existing = body.op === "edit-script"
          ? (uuid(body.script_id) ? (await sql`select id, organisation_id, name from fleet.scripts where id = ${uuid(body.script_id)}`)[0] : null)
          : null;
        if (body.op === "edit-script" && (!existing || !canOrg(existing.organisation_id))) return reply(404, { error: "no such script" });
        const org = existing ? existing.organisation_id : uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const [o] = await sql`select allow_scripts from fleet.organisations where id = ${org}`;
        if (!o?.allow_scripts) return reply(403, { error: "the Owner hasn't allowed scripts for this organisation" });
        if (me.aal !== "aal2") {
          return reply(403, { error: "changing scripts needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        const name = typeof body.name === "string" ? body.name.replace(/[\p{Cc}]/gu, " ").replace(/\s+/g, " ").trim().slice(0, 80) : "";
        if (!name) return reply(400, { error: "a script needs a name" });
        const description = typeof body.description === "string" ? body.description.replace(/[\p{Cc}]/gu, " ").trim().slice(0, 300) : "";
        const script = acts.cleanScript(body.body);
        if (!script) return reply(400, { error: "a script is text, up to 64 KB" });
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(script))),
                                  (b) => b.toString(16).padStart(2, "0")).join("");
        // Signed in the browser (or the Fleet app) with the Owner's script signing code.
        const [k] = await sql`select script_key from fleet.organisations where id = ${org}`;
        if (!(await acts.verifySigned(k?.script_key, body.signature, acts.scriptMessage(digest)))) {
          return reply(400, { error: k?.script_key ? "that signing code isn't the organisation's" : "set up script signing first" });
        }
        const signature = body.signature as string;
        try {
          if (existing) {
            await sql`update fleet.scripts set name = ${name}, description = ${description}, body = ${script}, sha256 = ${digest},
                        signature = ${signature}, updated_at = now(), updated_by = ${email} where id = ${existing.id}`;
            await audit(org, null, `changed script ${name}`.slice(0, 80), { sha256: digest });
            return reply(200, { id: existing.id, sha256: digest });
          }
          const [row] = await sql`insert into fleet.scripts (organisation_id, name, description, body, sha256, signature, created_by, updated_by)
                                  values (${org}, ${name}, ${description}, ${script}, ${digest}, ${signature}, ${email}, ${email}) returning id`;
          await audit(org, null, `added script ${name}`.slice(0, 80), { sha256: digest });
          return reply(200, { id: row.id, sha256: digest });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "there's already a script with that name" });
          throw e;
        }
      }
      case "remove-script": {
        const id = uuid(body.script_id);
        const [sc] = id ? await sql`select organisation_id, name from fleet.scripts where id = ${id}` : [];
        if (!sc || !canOrg(sc.organisation_id)) return reply(404, { error: "no such script" });
        if (me.aal !== "aal2") {
          return reply(403, { error: "removing a script needs your authenticator app's code", two_step: me.hasCode ? "code" : "set-up" });
        }
        await sql`delete from fleet.scripts where id = ${id}`;
        await audit(sc.organisation_id, null, `removed script ${sc.name}`.slice(0, 80));
        return reply(200, {});
      }
      case "add-network": {
        const g = await group(uuid(body.group_id));
        if (!g) return reply(404, { error: "no such group" });
        const n = acts.cleanNetwork(body);
        if (!n) return reply(400, { error: "check the network name and password (8 to 63 characters)" });
        const id = crypto.randomUUID();
        let sealed: string | null = null;
        if (n.password !== null) {
          sealed = await secrets.seal(n.password, "wifi", `wifi:${g.id}:${id}`);
          if (!sealed) return reply(500, { error: "the server's encryption key is missing. Contact Kulan" });
        }
        try {
          await sql`insert into fleet.group_networks (id, group_id, ssid, security, secret, hidden, created_by)
                    values (${id}, ${g.id}, ${n.ssid}, ${n.security}, ${sealed}, ${n.hidden}, ${email})`;
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "the group already has that network" });
          throw e;
        }
        await audit(g.organisation_id, null, `added Wi-Fi ${n.ssid} to group ${g.name}`.slice(0, 80), { security: n.security });
        await wakeDevices((await sql`select device_id from fleet.group_devices where group_id = ${g.id}`).map((r) => r.device_id));
        return reply(200, { id });
      }
      case "remove-network":
      case "remove-printer": {
        const id = uuid(body.op === "remove-network" ? body.network_id : body.printer_id);
        const table = body.op === "remove-network" ? sql`fleet.group_networks` : sql`fleet.group_printers`;
        const label = body.op === "remove-network" ? sql`x.ssid` : sql`x.name`;
        const [x] = id ? await sql`select x.group_id, ${label} as label, g.name as group_name, g.organisation_id
                                   from ${table} x join fleet.groups g on g.id = x.group_id where x.id = ${id}` : [];
        if (!x || !canOrg(x.organisation_id)) return reply(404, { error: body.op === "remove-network" ? "no such network" : "no such printer" });
        await sql`delete from ${table} where id = ${id}`;
        await audit(x.organisation_id, null,
                    `removed ${body.op === "remove-network" ? "Wi-Fi" : "printer"} ${x.label} from group ${x.group_name}`.slice(0, 80));
        await wakeDevices((await sql`select device_id from fleet.group_devices where group_id = ${x.group_id}`).map((r) => r.device_id));
        return reply(200, {});
      }
      case "add-printer": {
        const g = await group(uuid(body.group_id));
        if (!g) return reply(404, { error: "no such group" });
        const pr = acts.cleanPrinter(body);
        if (!pr) return reply(400, { error: "check the printer's name and address (for example ipp://192.168.1.20/ipp/print)" });
        try {
          const [row] = await sql`insert into fleet.group_printers (group_id, name, uri, created_by)
                                  values (${g.id}, ${pr.name}, ${pr.uri}, ${email}) returning id`;
          await audit(g.organisation_id, null, `added printer ${pr.name} to group ${g.name}`.slice(0, 80), { uri: pr.uri });
          await wakeDevices((await sql`select device_id from fleet.group_devices where group_id = ${g.id}`).map((r) => r.device_id));
          return reply(200, { id: row.id });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "the group already has a printer with that name" });
          throw e;
        }
      }
      // --- Get help (2.9c) -----------------------------------------------------------------
      case "help-request": {
        const id = uuid(body.request_id);
        const [h] = id ? await sql`select h.id, h.organisation_id, h.device_id, d.hostname, h.username, h.full_name, h.body,
                                          h.state, h.reply, h.created_at, h.done_at, h.done_by, h.screenshot, h.screenshot_type
                                   from fleet.help_requests h join fleet.devices d on d.id = h.device_id where h.id = ${id}` : [];
        if (!h || !canOrg(h.organisation_id)) return reply(404, { error: "no such help request" });
        const { screenshot, ...rest } = h;
        return reply(200, { request: { ...rest, screenshot: screenshot ? acts.toBase64(new Uint8Array(screenshot)) : null } });
      }
      case "close-help": {
        const id = uuid(body.request_id);
        const [h] = id ? await sql`select organisation_id, device_id, username, state from fleet.help_requests where id = ${id}` : [];
        if (!h || !canOrg(h.organisation_id)) return reply(404, { error: "no such help request" });
        const answer = body.reply === undefined || body.reply === null || body.reply === "" ? null : acts.cleanReply(body.reply);
        if (body.reply && !answer) return reply(400, { error: "the answer is plain text, up to 500 characters" });
        const closed = await sql`update fleet.help_requests set state = 'done', reply = ${answer}, done_at = now(), done_by = ${email}
                                 where id = ${id} and state = 'open' returning id`;
        if (!closed.length) return reply(400, { error: "that help request is already done" });
        await audit(h.organisation_id, h.device_id, `answered ${h.username}'s help request`.slice(0, 80),
                    { request: id, answered: answer !== null });
        await wakeDevices([h.device_id]);  // the answer reaches their screen in seconds
        return reply(200, {});
      }
      case "cancel-action": {
        const id = uuid(body.action_id);
        const rows = id ? await sql`select a.device_id, a.type, d.organisation_id from fleet.actions a
                                    join fleet.devices d on d.id = a.device_id where a.id = ${id}` : [];
        if (!rows.length || !canOrg(rows[0].organisation_id)) return reply(404, { error: "no such request" });
        const done = await sql`update fleet.actions set status = 'cancelled', finished_at = now()
                               where id = ${id} and status = 'pending' returning id`;
        if (!done.length) return reply(400, { error: "too late. The computer already picked it up" });
        await audit(rows[0].organisation_id, rows[0].device_id, `cancelled: ${rows[0].type}`);
        return reply(200, {});
      }
      default:
        return reply(400, { error: "unknown op" });
    }
  } catch (e) {
    console.error("fleet-admin:", e);
    return reply(500, { error: "server error" });
  }
});
