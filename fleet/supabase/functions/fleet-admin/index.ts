// Kulan fleet dashboard API. Called by the dashboard page (GitHub Pages) with
// the signed-in person's Supabase session token. Only emails listed in
// fleet.admins get anything; an admin with an organisation sees only that
// organisation, one without sees all.
//
//   POST {op: "overview"}                                  -> organisations, computers, recent actions
//   POST {op: "report", device_id}                         -> a computer's latest full report
//   POST {op: "new-organisation", name}                    -> {id}            (admins for all organisations)
//   POST {op: "new-code", organisation_id, hours, uses}    -> {code}          (shown once)
//   POST {op: "action", device_id, type}                   -> {id}
import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const ALLOWED_ORIGINS = (Deno.env.get("FLEET_DASHBOARD_ORIGINS") ?? "https://mmohamud25.github.io")
  .split(",").map((o) => o.trim()).filter(Boolean);
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart"]);

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
    const deviceOrg = async (id: string | null) => {
      if (!id) return null;
      const rows = await sql`select organisation_id from fleet.devices where id = ${id}`;
      return rows.length && canOrg(rows[0].organisation_id) ? rows[0].organisation_id : null;
    };

    switch (body.op) {
      case "overview": {
        const organisations = global
          ? await sql`select id, name, created_at from fleet.organisations order by name`
          : await sql`select id, name, created_at from fleet.organisations where id = any(${orgs}::uuid[]) order by name`;
        const ids = organisations.map((o) => o.id);
        const devices = await sql`
          select id, organisation_id, hostname, os, kulan_version, enrolled_at, last_seen, left_at,
                 last_report->'updates' as updates, last_report->'tpm' as tpm,
                 last_report->'compliance'->'passed' as compliance_passed,
                 last_report->'compliance'->'total' as compliance_total,
                 last_report->'compliance'->'mode' as compliance_mode,
                 last_report->'hardware' as hardware
          from fleet.devices where organisation_id = any(${ids}::uuid[]) order by left_at nulls first, hostname`;
        const actions = await sql`
          select a.id, a.device_id, a.type, a.status, a.detail, a.created_at, a.finished_at
          from fleet.actions a join fleet.devices d on d.id = a.device_id
          where d.organisation_id = any(${ids}::uuid[]) order by a.created_at desc limit 50`;
        return reply(200, { email, global, organisations, devices, actions });
      }
      case "report": {
        const id = uuid(body.device_id);
        if (!(await deviceOrg(id))) return reply(404, { error: "no such computer" });
        const [row] = await sql`select last_report from fleet.devices where id = ${id}`;
        return reply(200, { report: row.last_report });
      }
      case "new-organisation": {
        if (!global) return reply(403, { error: "only admins for all organisations can add one" });
        const name = typeof body.name === "string" ? body.name.trim().slice(0, 80) : "";
        if (!name) return reply(400, { error: "a name is needed" });
        const [org] = await sql`insert into fleet.organisations (name) values (${name}) returning id`;
        return reply(200, { id: org.id });
      }
      case "new-code": {
        const org = uuid(body.organisation_id);
        if (!org || !canOrg(org)) return reply(404, { error: "no such organisation" });
        const hours = Math.min(720, Math.max(1, Number(body.hours) || 72));
        const uses = Math.min(500, Math.max(1, Number(body.uses) || 1));
        const [row] = await sql`select fleet.new_enrolment_code(${org}, ${hours}, ${uses}) as code`;
        return reply(200, { code: row.code, hours, uses });
      }
      case "action": {
        const id = uuid(body.device_id);
        if (!ACTIONS.has(body.type)) return reply(400, { error: "not an action Kulan OS allows" });
        if (!(await deviceOrg(id))) return reply(404, { error: "no such computer" });
        const [row] = await sql`insert into fleet.actions (device_id, type) values (${id}, ${body.type}) returning id`;
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
