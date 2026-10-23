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
//        "erase" needs confirm = the computer's name, and only works on a
//        computer that's encrypted and whose user allowed it there.
//
// Every change is written to fleet.audit with the admin's email.
import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ALLOWED_ORIGINS = (Deno.env.get("FLEET_DASHBOARD_ORIGINS") ?? "https://os.kulangroup.com,https://mmohamud25.github.io")
  .split(",").map((o) => o.trim()).filter(Boolean);
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart", "erase"]);
const STALE_DAYS = 3;

// A policy as the computers will read it: only known keys, sensible values.
function cleanPolicy(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  const policy: Record<string, unknown> = {};
  for (const key of ["auto_updates", "firewall", "require_encryption"]) {
    if (v[key] === true) policy[key] = true;
  }
  const minutes = Number(v.screen_lock_minutes);
  if (Number.isInteger(minutes) && minutes >= 1 && minutes <= 60) policy.screen_lock_minutes = minutes;
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
    const admins = await sql`select organisation_id from fleet.admins where email = ${email}`;
    if (admins.length === 0) return reply(403, { error: "this account isn't a fleet admin" });
    const global = admins.some((a) => a.organisation_id === null);
    const orgs: string[] = admins.map((a) => a.organisation_id).filter(Boolean);
    const body = await req.json().catch(() => ({}));

    const canOrg = (id: string) => global || orgs.includes(id);
    const audit = (org: string | null, device: string | null, what: string, detail: unknown = null) =>
      sql`insert into fleet.audit (organisation_id, device_id, email, what, detail)
          values (${org}, ${device}, ${email}, ${what}, ${detail === null ? null : sql.json(detail)})`;
    const deviceOrg = async (id: string | null) => {
      if (!id) return null;
      const rows = await sql`select organisation_id from fleet.devices where id = ${id}`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0].organisation_id : null;
    };

    switch (body.op) {
      case "overview": {
        const organisations = global
          ? await sql`select id, name, created_at, policy from fleet.organisations order by name`
          : await sql`select id, name, created_at, policy from fleet.organisations where id = any(${orgs}::uuid[]) order by name`;
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
        await sql`update fleet.organisations set policy = ${sql.json(policy)} where id = ${org}`;
        await audit(org, null, "changed the policy", policy);
        return reply(200, { policy });
      }
      case "new-organisation": {
        if (!global) return reply(403, { error: "only admins for all organisations can add one" });
        const name = typeof body.name === "string" ? body.name.trim().slice(0, 80) : "";
        if (!name) return reply(400, { error: "a name is needed" });
        const [org] = await sql`insert into fleet.organisations (name) values (${name}) returning id`;
        await audit(org.id, null, "added the organisation", { name });
        return reply(200, { id: org.id });
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
      default:
        return reply(400, { error: "unknown op" });
    }
  } catch (e) {
    console.error("fleet-admin:", e);
    return reply(500, { error: "server error" });
  }
});
