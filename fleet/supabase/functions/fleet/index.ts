// Kulan OS fleet server, protocol v1 (the other end of /usr/bin/kulan-fleet).
//
//   POST /fleet/v1/enrol          {code, hostname, os, kulan_version} -> {device_id, token, organisation}
//   POST /fleet/v1/report         Bearer token, {device_id, ...status, apps?}
//                                 -> {actions: [{id, type}], policy: {...}}
//   POST /fleet/v1/actions/{id}   Bearer token, {device_id, status: done|failed, detail}
//   POST /fleet/v1/leave          Bearer token, {device_id}
//
// Computers authenticate with their own key (made here at enrolment; only its
// SHA-256 is stored), so the platform's JWT check is off for this function.
// Data lives in the private "fleet" schema, reached over a direct Postgres
// connection, never through the public Data API.
import postgres from "npm:postgres@3.4.5";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 3, prepare: false });
const MAX_BODY = 512 * 1024; // a report with a full app list
const ACTIONS = new Set(["refresh", "install-updates", "compliance-on", "lock-screen", "restart", "erase"]);
const MAX_APPS = 3000;

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

async function device(req: Request, body: Record<string, unknown>) {
  const auth = req.headers.get("Authorization") ?? "";
  if (!auth.startsWith("Bearer ")) return null;
  const hash = await sha256(auth.slice(7).trim());
  const rows = await sql`select id, organisation_id from fleet.devices
                         where token_hash = ${hash} and left_at is null`;
  if (rows.length !== 1 || rows[0].id !== body.device_id) return null;
  return rows[0];
}

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") return reply(405, { error: "POST only" });
  const length = Number(req.headers.get("Content-Length") ?? "0");
  if (length > MAX_BODY) return reply(413, { error: "too large" });
  const raw = await req.text();
  if (raw.length > MAX_BODY) return reply(413, { error: "too large" });
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
    if (path === "/v1/enrol") {
      const code = text(body.code, 64)?.trim().toUpperCase();
      if (!code) return reply(400, { error: "code missing" });
      const codeHash = await sha256(code);
      const token = newToken();
      const result = await sql.begin(async (tx) => {
        const codes = await tx`update fleet.enrolment_codes set uses_left = uses_left - 1
                               where code_hash = ${codeHash} and uses_left > 0 and expires_at > now()
                               returning organisation_id`;
        if (codes.length !== 1) return null;
        const org = codes[0].organisation_id;
        const [dev] = await tx`insert into fleet.devices (organisation_id, token_hash, hostname, os, kulan_version, last_seen)
                               values (${org}, ${await sha256(token)}, ${text(body.hostname, 120)},
                                       ${text(body.os, 120)}, ${text(body.kulan_version, 60)}, now())
                               returning id`;
        const [o] = await tx`select name from fleet.organisations where id = ${org}`;
        return { device_id: dev.id, organisation: o.name };
      });
      if (!result) return reply(403, { error: "that code isn't valid (used, expired or mistyped)" });
      return reply(200, { ...result, token });
    }

    const dev = await device(req, body);
    if (!dev) return reply(401, { error: "unknown computer or key" });

    if (path === "/v1/report") {
      const apps = cleanApps(body.apps);
      delete body.apps; // kept on the computer's row, not in every hourly report
      if (apps) await sql`update fleet.devices set apps = ${sql.json(apps)}, apps_at = now() where id = ${dev.id}`;
      await sql`insert into fleet.reports (device_id, report) values (${dev.id}, ${sql.json(body)})`;
      if (Math.random() < 0.01) await sql`select fleet.prune_reports()`;
      await sql`update fleet.devices set last_seen = now(), last_report = ${sql.json(body)},
                  hostname = coalesce(${text(body.hostname, 120)}, hostname),
                  os = coalesce(${text(body.os, 120)}, os),
                  kulan_version = coalesce(${text(body.kulan_version, 60)}, kulan_version)
                where id = ${dev.id}`;
      // An erase still waiting after 30 days expires instead (the computer was found, say).
      await sql`update fleet.actions set status = 'expired', finished_at = now()
                where device_id = ${dev.id} and status = 'pending' and type = 'erase'
                  and created_at < now() - interval '30 days'`;
      const actions = await sql`update fleet.actions set status = 'sent', sent_at = now()
                                where id in (select id from fleet.actions where device_id = ${dev.id}
                                             and status = 'pending' order by created_at limit 10)
                                returning id, type`;
      const [org] = await sql`select policy from fleet.organisations where id = ${dev.organisation_id}`;
      return reply(200, {
        actions: actions.filter((a) => ACTIONS.has(a.type)).map((a) => ({ id: a.id, type: a.type })),
        policy: org?.policy ?? {},
      });
    }

    const done = path.match(/^\/v1\/actions\/([0-9a-f-]{36})$/);
    if (done) {
      const status = body.status === "done" ? "done" : "failed";
      await sql`update fleet.actions set status = ${status}, detail = ${text(body.detail, 300)}, finished_at = now()
                where id = ${done[1]} and device_id = ${dev.id} and status = 'sent'`;
      return reply(200, {});
    }

    if (path === "/v1/leave") {
      await sql`update fleet.devices set left_at = now(), token_hash = null where id = ${dev.id}`;
      return reply(200, {});
    }
    return reply(404, { error: "unknown path" });
  } catch (e) {
    console.error("fleet:", e);
    return reply(500, { error: "server error" });
  }
});
