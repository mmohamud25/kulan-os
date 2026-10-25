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
// Every change is written to fleet.audit with the admin's email.
import postgres from "npm:postgres@3.4.5";
import { cleanPolicy, mergePolicies } from "./policy.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
// Any JSON-able value as jsonb (postgres.js's types want its own JSON type).
const json = (v: unknown) => sql.json(v as Parameters<typeof sql.json>[0]);
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ALLOWED_ORIGINS = (Deno.env.get("FLEET_DASHBOARD_ORIGINS") ?? "https://os.kulangroup.com,https://mmohamud25.github.io")
  .split(",").map((o) => o.trim()).filter(Boolean);
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart", "erase",
                         "shutdown", "lost-mode-on", "lost-mode-off", "install-app"]);
// What may be asked of a whole group at once (never erase or lost mode).
const GROUP_ACTIONS = new Set(["refresh", "install-updates", "install-app", "lock-screen", "restart", "shutdown"]);
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
  return RESERVED.has(u) ? "that username is kept for the system" : null;
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
                              review: { at: string | null; by: string | null; due: boolean }, now: number) {
  const names = (list: Device[]) => list.map((d) => d.hostname || "(no name)").slice(0, LIST_MAX);
  const live = devices.filter((d) => !d.left_at);
  const seen = (d: Device) => d.last_seen ? now - Date.parse(d.last_seen) : Infinity;
  const security = live.filter((d) => Number(d.updates?.security ?? 0) > 0);
  const failing = live.filter((d) => d.compliance_total && Number(d.compliance_passed) < Number(d.compliance_total));
  const notMet = live.map((d) => ({ hostname: d.hostname || "(no name)",
    items: Object.entries(d.policy_status ?? {}).filter(([, v]) => v !== "met" && v !== "n/a").map(([k]) => k) }))
    .filter((d) => d.items.length);
  const versions: Record<string, number> = {};
  for (const d of live) versions[d.kulan_version || "unknown"] = (versions[d.kulan_version || "unknown"] ?? 0) + 1;
  const active = people.filter((p) => !p.removed_at);
  const verbs: Record<string, number> = {};
  for (const a of activity) {
    const verb = String(a.what).startsWith("asked") ? "asked computers" : String(a.what).split(" ")[0];
    verbs[verb] = (verbs[verb] ?? 0) + 1;
  }
  return {
    computers: {
      total: live.length,
      reported_this_week: live.filter((d) => seen(d) <= 7 * DAY_MS).length,
      not_seen_week: names(live.filter((d) => seen(d) > 7 * DAY_MS)),
      up_to_date: live.filter((d) => d.updates && Number(d.updates.waiting ?? 0) === 0).length,
      security_waiting: security.slice(0, LIST_MAX).map((d) => ({ hostname: d.hostname || "(no name)", count: Number(d.updates.security) })),
      restart_needed: names(live.filter((d) => d.updates?.restart_required === true)),
      encrypted: live.filter((d) => d.state?.encrypted === true).length,
      not_encrypted: names(live.filter((d) => d.state?.encrypted === false)),
      firewall_off: names(live.filter((d) => d.state?.firewall === false)),
      compliance_passing: live.filter((d) => d.compliance_total && Number(d.compliance_passed) === Number(d.compliance_total)).length,
      compliance_failing: failing.slice(0, LIST_MAX).map((d) => ({ hostname: d.hostname || "(no name)",
        failing: Number(d.compliance_total) - Number(d.compliance_passed) })),
      policy_not_met: notMet.slice(0, LIST_MAX),
      lost_mode: names(live.filter((d) => d.state?.lost_mode === true)),
      versions,
    },
    people: {
      total: active.length,
      disabled: active.filter((p) => !p.enabled).map((p) => p.username).slice(0, LIST_MAX),
      setup_code_unused: active.filter((p) => p.enabled && p.must_change &&
        now - Number(p.password_changed_at) * 1000 > 7 * DAY_MS).map((p) => p.username).slice(0, LIST_MAX),
      admins: active.filter((p) => p.role !== "employee").map((p) => p.username).slice(0, LIST_MAX),
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
  if (d.state?.lost_mode === true) out.push({ kind: "lost", text: "In lost mode: nobody can sign in" });
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

async function signedInEmail(req: Request): Promise<string | null> {
  const auth = req.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const res = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
    headers: { Authorization: auth, apikey: publishableKey() },
  });
  if (!res.ok) return null;
  const user = await res.json();
  if (!user?.email || !user?.email_confirmed_at) return null;
  return String(user.email).toLowerCase();
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
    const email = await signedInEmail(req);
    if (!email) return reply(401, { error: "please sign in" });
    const body = await req.json().catch(() => ({}));
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

    const canOrg = (id: string) => global || orgs.includes(id);
    const audit = (org: string | null, device: string | null, what: string, detail: unknown = null) =>
      sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
          values (${org}, ${device}, ${email}, ${what}, ${detail === null ? null : json(detail)})`;
    const deviceOrg = async (id: string | null) => {
      if (!id) return null;
      const rows = await sql`select organisation_id from fleet.devices where id = ${id}`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0].organisation_id : null;
    };
    const isOwner = async (org: string) => {
      if (global) return true;
      const rows = await sql`select owner_email from fleet.organisations where id = ${org}`;
      return rows.length === 1 && rows[0].owner_email === email;
    };
    // A person this admin may manage (not removed).
    const person = async (id: string | null) => {
      if (!id) return null;
      const rows = await sql`select id, organisation_id, username, full_name, email, role, enabled, all_computers,
                                    coalesce((select array_agg(pc.device_id::text order by pc.device_id)
                                              from fleet.person_computers pc where pc.person_id = fleet.people.id), '{}') as computers,
                                    coalesce((select array_agg(pg.group_id::text order by pg.group_id)
                                              from fleet.person_groups pg where pg.person_id = fleet.people.id), '{}') as groups
                             from fleet.people
                             where id = ${id} and removed_at is null`;
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

    switch (body.op) {
      case "overview": {
        const organisations = global
          ? await sql`select id, name, created_at, policy, owner_email, (${global} or owner_email = ${email}) as is_owner
                      from fleet.organisations order by name`
          : await sql`select id, name, created_at, policy, owner_email, owner_email = ${email} as is_owner
                      from fleet.organisations where id = any(${orgs}::uuid[]) order by name`;
        const ids = organisations.map((o) => o.id);
        const devices = await sql`
          select id, organisation_id, hostname, os, kulan_version, enrolled_at, last_seen, left_at,
                 last_report->'updates' as updates, last_report->'tpm' as tpm,
                 last_report->'compliance'->'passed' as compliance_passed,
                 last_report->'compliance'->'total' as compliance_total,
                 last_report->'compliance'->'mode' as compliance_mode,
                 last_report->'hardware' as hardware, last_report->'state' as state,
                 last_report->'policy_status' as policy_status, last_report->'sign_ins' as sign_ins, apps_at,
                 jsonb_array_length(coalesce(apps, '[]'::jsonb)) as app_count
          from fleet.devices where organisation_id = any(${ids}::uuid[]) order by left_at nulls first, hostname`;
        const actions = await sql`
          select a.id, a.device_id, a.type, a.status, a.detail, a.created_at, a.finished_at, a.requested_by
          from fleet.actions a join fleet.devices d on d.id = a.device_id
          where d.organisation_id = any(${ids}::uuid[]) order by a.created_at desc limit 50`;
        const policies = Object.fromEntries(organisations.map((o) => [o.id, o.policy ?? {}]));
        const now = Date.now();
        const alerts = devices.flatMap((d) => alertsFor(d, policies[d.organisation_id] ?? {}, now)
          .map((a) => ({ ...a, device_id: d.id, organisation_id: d.organisation_id, hostname: d.hostname })));
        // The admin-rights review, every 90 days (an alert for the organisation, not a computer).
        const reviews = await sql`select distinct on (organisation_id) organisation_id, reviewed_at
                                  from fleet.access_reviews where organisation_id = any(${ids}::uuid[])
                                  order by organisation_id, reviewed_at desc`;
        const lastReview = Object.fromEntries(reviews.map((r) => [r.organisation_id, r.reviewed_at]));
        for (const o of organisations) {
          const due = reviewDue(lastReview[o.id] ?? null, o.created_at, now);
          if (due.due) {
            alerts.push({ kind: "review", device_id: null, organisation_id: o.id, hostname: null, title: "Admin rights",
                          text: due.never ? "No admin-rights review yet: check who still needs administrator rights"
                                          : `Admin-rights review due: the last was ${due.days} days ago` } as any);
          }
        }
        return reply(200, { email, global, organisations, devices, actions, alerts });
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
        const day = (v: unknown) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v) && !isNaN(Date.parse(v)) ? v : null;
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
        if (!policy) return reply(400, { error: "something in that policy can't be used (an app name, or a part of Kulan OS that can't be blocked)" });
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
                 coalesce((select array_agg(pc.device_id) from fleet.person_computers pc where pc.person_id = p.id), '{}') as computers,
                 coalesce((select array_agg(pg.group_id) from fleet.person_groups pg where pg.person_id = p.id), '{}') as groups
          from fleet.people p where p.organisation_id = ${org} and p.removed_at is null order by p.username`;
        const devices = await sql`select d.id, d.hostname, d.left_at, d.last_report->'people_status' as people_status,
                                         d.last_report->'sign_ins' as sign_ins,
                                         coalesce((select array_agg(gd.group_id) from fleet.group_devices gd where gd.device_id = d.id), '{}') as groups
                                  from fleet.devices d where d.organisation_id = ${org} and d.left_at is null`;
        const signIns = signInsByUser(devices);
        return reply(200, {
          people: people.map((p) => ({
            ...p, password_changed_at: Number(p.password_changed_at), signed_in: signIns[p.username] ?? [],
            on: devices.filter((d) => p.all_computers || p.computers.includes(d.id)
                                     || d.groups.some((g: string) => p.groups.includes(g)))
              .map((d) => ({ device_id: d.id, hostname: d.hostname, status: d.people_status?.[p.username] ?? "not yet" })),
          })),
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
            ? "someone with that username was removed in the last 30 days: choose another username for now"
            : "someone already has that username" });
        }
        const [{ count }] = await sql`select count(*)::int as count from fleet.people where organisation_id = ${org}`;
        if (count >= MAX_PEOPLE) return reply(400, { error: "that's the most people an organisation can have" });
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
        if (!added) return reply(500, { error: "couldn't give them a user ID: try again" });
        await audit(org, null, `added ${body.username}`, { role, all_computers: all, computers: chosen.length, groups: chosenGroups.length });
        return reply(200, { id: added.id, uid: added.uid });
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
        const removeFiles = body.remove_files === true;
        await sql`update fleet.people set removed_at = now(), remove_files = ${removeFiles}, enabled = false
                  where id = ${p.id}`;
        await audit(p.organisation_id, null, `removed ${p.username}`, { files: removeFiles ? "deleted" : "kept" });
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
        const [row] = await sql`select fleet.new_enrolment_code(${org}, ${hours}, ${uses}) as code`;
        await audit(org, null, "made a join code", { hours, uses });
        return reply(200, { code: row.code, hours, uses });
      }
      case "action": {
        const id = uuid(body.device_id);
        if (!ACTIONS.has(body.type)) return reply(400, { error: "not an action Kulan OS allows" });
        const org = await deviceOrg(id);
        if (!org) return reply(404, { error: "no such computer" });
        if (body.type === "erase") {
          const [d] = await sql`select hostname, left_at, last_report->'state' as state from fleet.devices where id = ${id}`;
          if (d.left_at) return reply(400, { error: "that computer has left the organisation" });
          if (!d.hostname || typeof body.confirm !== "string" || body.confirm.trim() !== d.hostname) {
            return reply(400, { error: "type the computer's name to confirm" });
          }
          if (!d.state?.erase_allowed) return reply(400, { error: "the computer's user hasn't allowed erasing it from afar" });
          if (!d.state?.encrypted) return reply(400, { error: "only encrypted computers can be erased from afar" });
        }
        let payload: Record<string, unknown> | null = null;
        if (body.type === "install-app") {
          payload = cleanApp(body.payload);
          if (!payload) return reply(400, { error: "which app? (a Debian package or a Flathub app id)" });
        }
        const [row] = await sql`insert into fleet.actions (device_id, type, requested_by, payload)
                                values (${id}, ${body.type}, ${email}, ${payload === null ? null : json(payload)}) returning id`;
        await audit(org, id, `asked: ${body.type}`, payload);
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
                           where gp.group_id = g.id and p.removed_at is null), '{}') as people
          from fleet.groups g where g.organisation_id = ${org} order by lower(g.name)`;
        return reply(200, { groups });
      }
      case "add-group": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const name = typeof body.name === "string" ? body.name.trim().replace(/\s+/g, " ").slice(0, 60) : "";
        if (!name) return reply(400, { error: "a group needs a name" });
        const policy = body.policy === undefined ? {} : cleanPolicy(body.policy);
        if (!policy) return reply(400, { error: "something in that policy can't be used (an app name, or a part of Kulan OS that can't be blocked)" });
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
        if (!policy) return reply(400, { error: "something in that policy can't be used (an app name, or a part of Kulan OS that can't be blocked)" });
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
          return reply(400, { error: "a drive needs a name, and the vendor, product and serial its computer reported" });
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
        let payload: Record<string, unknown> | null = null;
        if (body.type === "install-app") {
          payload = cleanApp(body.payload);
          if (!payload) return reply(400, { error: "which app? (a Debian package or a Flathub app id)" });
        }
        const rows = await sql`
          insert into fleet.actions (device_id, type, requested_by, payload)
          select gd.device_id, ${body.type}, ${email}, ${payload === null ? null : json(payload)}
          from fleet.group_devices gd join fleet.devices d on d.id = gd.device_id
          where gd.group_id = ${g.id} and d.left_at is null
          returning id`;
        await audit(g.organisation_id, null, `asked group ${g.name}: ${body.type}`.slice(0, 80), { computers: rows.length, ...(payload ?? {}) });
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
        const people = await sql`select username, role, enabled, must_change, password_changed_at, removed_at
                                 from fleet.people where organisation_id = ${org} and removed_at is null order by username`;
        const activity = await sql`select what from fleet.audit where organisation_id = ${org} and at > now() - interval '7 days'`;
        const [last] = await sql`select reviewed_at, reviewed_by from fleet.access_reviews where organisation_id = ${org}
                                 order by reviewed_at desc limit 1`;
        const due = reviewDue(last?.reviewed_at ?? null, o?.created_at ?? null, now);
        const summary = weeklySummary(devices, people, activity,
          { at: last?.reviewed_at ?? null, by: last?.reviewed_by ?? null, due: due.due }, now);
        const week = mondayOf(new Date(now));
        await sql`insert into fleet.weekly_reports (organisation_id, week, summary) values (${org}, ${week}, ${json(summary)})
                  on conflict (organisation_id, week) do update set summary = excluded.summary, made_at = now()`;
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
        await audit(org, null, `reviewed admin rights (kept ${total - removed}, removed ${removed})`.slice(0, 80),
                    { admin_role_taken: people.map((p) => p!.username), fleet_access_taken: removeAdmins });
        return reply(200, { kept: total - removed, removed });
      }
      case "cancel-action": {
        const id = uuid(body.action_id);
        const rows = id ? await sql`select a.device_id, a.type, d.organisation_id from fleet.actions a
                                    join fleet.devices d on d.id = a.device_id where a.id = ${id}` : [];
        if (!rows.length || !canOrg(rows[0].organisation_id)) return reply(404, { error: "no such request" });
        const done = await sql`update fleet.actions set status = 'cancelled', finished_at = now()
                               where id = ${id} and status = 'pending' returning id`;
        if (!done.length) return reply(400, { error: "too late: the computer already has it" });
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
