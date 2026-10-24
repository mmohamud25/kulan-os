// Kulan OS weekly count (the other end of /usr/lib/kulan/countme).
//
//   POST /countme/v1   {version: "2.6", arch: "amd64"|"arm64", age: 1-4, week: "2026-10-19"}  -> 204
//                      (week, the Monday being counted, from Kulan OS 2.7; older
//                      computers leave it out and are filed under this week)
//   GET  /countme/v1   totals for the last 12 weeks (anonymous already)
//
// This function stores nothing about the sender: not the address, not a
// time, not the request. (Supabase's own request logs, kept for a short
// time by the platform, can include the address; the privacy policy says
// so.) Each check-in only adds one to its week's total for that version,
// architecture and age. Anything else is refused, so nothing but those
// totals can end up in the database. The platform's JWT check is off: the
// computers have no account, and there's nothing private to protect here.
// Data lives in the private "stats" schema, over a direct Postgres connection.
import postgres from "npm:postgres@3.4.5";
import { acceptedWeek } from "./week.ts";

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 2, prepare: false });
const VERSION = /^[0-9]{1,2}\.[0-9]{1,2}$/;
const ARCHES = new Set(["amd64", "arm64"]);
const AGES = ["under a week", "under a month", "under six months", "longer"];
Deno.serve(async (req: Request) => {
  const path = new URL(req.url).pathname.replace(/^.*\/countme(?=\/v1)/, "");
  if (path !== "/v1") return reply(404, { error: "not found" });

  if (req.method === "GET") {
    const rows = await sql`
      select week, version, arch, age, computers from stats.weekly_computers
      where week > (date_trunc('week', now() at time zone 'utc'))::date - 84
      order by week desc, version desc, arch, age`;
    return reply(200, {
      about: "Kulan OS computers counted each week (anonymous totals)",
      ages: AGES,
      weeks: rows.map((r) => ({ ...r, week: new Date(r.week).toISOString().slice(0, 10) })),
    });
  }
  if (req.method !== "POST") return reply(405, { error: "GET or POST only" });

  if (Number(req.headers.get("Content-Length") ?? "0") > 512) return reply(413, { error: "too large" });
  const raw = await req.text();
  if (raw.length > 512) return reply(413, { error: "too large" });
  let body: Record<string, unknown>;
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    body = parsed;
  } catch {
    return reply(400, { error: "JSON object expected" });
  }
  const { version, arch, age } = body;
  const keys = Object.keys(body).sort().join(",");
  const week = acceptedWeek(body.week, new Date());
  if ((keys !== "age,arch,version" && keys !== "age,arch,version,week") || week === null ||
      typeof version !== "string" || !VERSION.test(version) ||
      typeof arch !== "string" || !ARCHES.has(arch) ||
      typeof age !== "number" || !Number.isInteger(age) || age < 1 || age > 4) {
    return reply(400, { error: "expected exactly {version, arch, age} and optionally week (this or last week's Monday)" });
  }
  await sql`
    insert into stats.weekly_computers (week, version, arch, age, computers)
    values (${week}::date, ${version}, ${arch}, ${age}, 1)
    on conflict (week, version, arch, age) do update
      set computers = stats.weekly_computers.computers + 1`;
  return reply(204);
});
