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
// Every change is written to fleet.audit with the admin's email.
import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
// Any JSON-able value as jsonb (postgres.js's types want its own JSON type).
const json = (v: unknown) => sql.json(v as Parameters<typeof sql.json>[0]);
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ALLOWED_ORIGINS = (Deno.env.get("FLEET_DASHBOARD_ORIGINS") ?? "https://os.kulangroup.com,https://mmohamud25.github.io")
  .split(",").map((o) => o.trim()).filter(Boolean);
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart", "erase",
                         "shutdown", "lost-mode-on", "lost-mode-off"]);
const STALE_DAYS = 3;
const USERNAME = /^[a-z][a-z0-9_-]{0,31}$/;
const RESERVED = new Set(["root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
                          "www-data", "backup", "list", "irc", "nobody", "admin", "sudo", "kulan", "gdm", "systemd"]);
const HASH = /^\$(y|gy|6|5|2b|2y|7)\$[./A-Za-z0-9$=,]{8,250}$/;
const EMAIL = /^[^\s@]{1,64}@[^\s@]+\.[^\s@]+$/;
const ROLES = new Set(["owner", "admin", "employee"]);
const MAX_PEOPLE = 5000;

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

// A policy as the computers will read it: only known keys, sensible values.
function cleanPolicy(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  const policy: Record<string, unknown> = {};
  for (const key of ["auto_updates", "firewall", "require_encryption"]) {
    if (v[key] === true) policy[key] = true;
  }
  const minutes = v.screen_lock_minutes;
  if (typeof minutes === "number" && Number.isInteger(minutes) && minutes >= 1 && minutes <= 60) {
    policy.screen_lock_minutes = minutes;
  }
  return policy;
}

type Device = Record<string, any>;

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
    if (status !== "met" && status !== "n/a") out.push({ kind: "policy", text: `Policy not met: ${key.replaceAll("_", " ")}` });
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
      const rows = await sql`select id, organisation_id, username, role, enabled, all_computers from fleet.people
                             where id = ${id} and removed_at is null`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0] : null;
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
                 last_report->'policy_status' as policy_status, apps_at,
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
        const entries = await sql`
          select a.at, a.email, a.what, a.detail, a.device_id, d.hostname
          from fleet.audit a left join fleet.devices d on d.id = a.device_id
          where a.organisation_id = ${org} order by a.at desc limit 100`;
        return reply(200, { entries });
      }
      case "set-policy": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const policy = cleanPolicy(body.policy);
        if (!policy) return reply(400, { error: "a policy object is needed" });
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
                 coalesce((select array_agg(pc.device_id) from fleet.person_computers pc where pc.person_id = p.id), '{}') as computers
          from fleet.people p where p.organisation_id = ${org} and p.removed_at is null order by p.username`;
        const devices = await sql`select id, hostname, last_report->'people_status' as people_status
                                  from fleet.devices where organisation_id = ${org} and left_at is null`;
        return reply(200, {
          people: people.map((p) => ({
            ...p, password_changed_at: Number(p.password_changed_at),
            on: devices.filter((d) => p.all_computers || p.computers.includes(d.id))
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
        const chosen = all ? [] : await computersOf(org, body.computers);
        if (chosen === null) return reply(400, { error: "choose computers from this organisation" });
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
        await audit(org, null, `added ${body.username}`, { role, all_computers: all, computers: chosen.length });
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
        try {
          await sql.begin(async (tx) => {
            await tx`update fleet.people set full_name = coalesce(${name}, full_name),
                       email = ${body.email === undefined ? sql`email` : email2},
                       role = ${role}, all_computers = ${all}
                     where id = ${p.id}`;
            if (body.all_computers !== undefined || body.computers !== undefined) {
              await tx`delete from fleet.person_computers where person_id = ${p.id}`;
              for (const d of chosen) await tx`insert into fleet.person_computers (person_id, device_id) values (${p.id}, ${d})`;
            }
          });
        } catch (e) {
          if ((e as { code?: string }).code === "23505") return reply(409, { error: "the organisation already has an Owner's account" });
          throw e;
        }
        const changed = [name !== null && "name", body.email !== undefined && "email", role !== p.role && `role ${role}`,
                         (body.all_computers !== undefined || body.computers !== undefined) && "computers"].filter(Boolean);
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
        const [row] = await sql`insert into fleet.actions (device_id, type, requested_by)
                                values (${id}, ${body.type}, ${email}) returning id`;
        await audit(org, id, `asked: ${body.type}`);
        return reply(200, { id: row.id });
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
