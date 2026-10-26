// Kulan Fleet test server: the real fleet and fleet-admin functions (the
// same files Supabase runs), on a computer of your own, for trying a Kulan
// Fleet release on test computers before it goes live. Never for real use.
//
//   https://<this computer>:8443/functions/v1/fleet        computers (kulan-fleet)
//   https://<this computer>:8443/functions/v1/fleet-admin  the Kulan Fleet app
//   https://<this computer>:8443/auth/v1/...               stand-in sign-in: the
//        6-digit code is printed here instead of emailed
//   https://<this computer>:8443/kulan-fleet-test-ca.crt   this server's test
//        certificate authority, for the test computers to trust
//   wss://<this computer>:8443/realtime/v1/websocket       stand-in Supabase
//        Realtime (2.9a): computers listen for "report now" here
//
// Started by run.sh (see README.md), with SUPABASE_DB_URL pointing at the
// local test database and SUPABASE_URL at this server.
const PORT = Number(Deno.env.get("KULAN_FLEET_TEST_PORT") ?? "8443");
const DIR = Deno.env.get("KULAN_FLEET_TEST_DIR") ?? "/opt/kulan-fleet-test";
const FUNCTIONS = new URL("../supabase/functions/", import.meta.url);

type Handler = (req: Request) => Response | Promise<Response>;
const handlers: Record<string, Handler> = {};

// Each function calls Deno.serve once when loaded: take its handler instead.
const realServe = Deno.serve.bind(Deno);
let loading = "";
(Deno as unknown as { serve: unknown }).serve = (h: Handler) => {
  handlers[loading] = h;
  return {} as Deno.HttpServer;
};
for (const name of ["fleet", "fleet-admin"]) {
  loading = name;
  await import(new URL(`${name}/index.ts`, FUNCTIONS).href);
}
(Deno as unknown as { serve: unknown }).serve = realServe;

// --- stand-in Supabase Auth (email + one-time code, authenticator apps) --------------------
// Access tokens look like Supabase's (header.claims.signature, unsigned here)
// so fleet-admin can read the session's assurance level: "aal2" after an
// authenticator app code. Only what Kulan Fleet uses is here.
import postgres from "npm:postgres@3.4.5";
const db = postgres(Deno.env.get("SUPABASE_DB_URL")!, { max: 2, prepare: false });
type Factor = { id: string; friendly_name: string; factor_type: "totp"; status: "verified" | "unverified";
                created_at: string; secret: Uint8Array<ArrayBuffer> };
type User = { id: string; email: string; email_confirmed_at: string };
const codes = new Map<string, { code: string; until: number }>();
const access = new Map<string, { user: User; aal: string; until: number }>();
const refresh = new Map<string, { user: User; aal: string }>();
const factors = new Map<string, Factor[]>();            // by user id
const challenges = new Map<string, { factor: string; until: number }>();

function random(bytes = 32): string {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
const b64url = (o: unknown) => btoa(JSON.stringify(o)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");

// The web dashboard (fleet/dashboard) signs in from its own address too.
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, content-type, apikey, x-client-info, x-supabase-api-version",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...CORS } });
}

function publicUser(user: User) {
  return { ...user, factors: (factors.get(user.id) ?? []).map(({ secret: _s, ...f }) => f) };
}

function session(user: User, aal = "aal1"): Response {
  const a = `${b64url({ alg: "none", typ: "JWT" })}.${b64url({ sub: user.id, email: user.email, aal,
                                                                exp: Math.floor(Date.now() / 1000) + 3600 })}.${random(16)}`;
  const r = random();
  access.set(a, { user, aal, until: Date.now() + 3600_000 });
  refresh.set(r, { user, aal });
  return json(200, { access_token: a, refresh_token: r, expires_in: 3600, token_type: "bearer", user: publicUser(user) });
}

const BASE32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
function base32(bytes: Uint8Array): string {
  let bits = 0, value = 0, out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += BASE32[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  return bits ? out + BASE32[(value << (5 - bits)) & 31] : out;
}

// RFC 6238 (30 seconds, 6 digits, SHA-1), one step either side.
async function totpOk(secret: Uint8Array<ArrayBuffer>, code: string): Promise<boolean> {
  const key = await crypto.subtle.importKey("raw", secret, { name: "HMAC", hash: "SHA-1" }, false, ["sign"]);
  const step = Math.floor(Date.now() / 30_000);
  for (const t of [step - 1, step, step + 1]) {
    const msg = new Uint8Array(8);
    new DataView(msg.buffer).setBigUint64(0, BigInt(t));
    const h = new Uint8Array(await crypto.subtle.sign("HMAC", key, msg));
    const o = h[19] & 15;
    const n = (((h[o] & 127) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3]) % 1_000_000;
    if (String(n).padStart(6, "0") === code) return true;
  }
  return false;
}

async function userFor(email: string): Promise<User> {
  const id = crypto.randomUUID();  // kept for the email: the same person signing in again keeps theirs
  const rows = await db`insert into auth.users (id, email) values (${id}, ${email})
                        on conflict (email) do update set email = excluded.email returning id`;
  return { id: rows[0].id, email, email_confirmed_at: new Date().toISOString() };
}

async function auth(req: Request, path: string): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  const body = req.method === "POST" ? await req.json().catch(() => ({})) : {};
  if (path === "/auth/v1/otp") {
    const email = String(body.email ?? "").trim().toLowerCase();
    if (!email.includes("@")) return json(400, { msg: "an email is needed" });
    const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1_000_000).padStart(6, "0");
    codes.set(email, { code, until: Date.now() + 15 * 60_000 });
    console.log(`\n*** Kulan Fleet sign-in code for ${email}: ${code} ***\n`);
    return json(200, {});
  }
  if (path === "/auth/v1/verify") {
    const email = String(body.email ?? "").trim().toLowerCase();
    const want = codes.get(email);
    if (!want || want.until < Date.now() || String(body.token ?? "") !== want.code) {
      return json(403, { msg: "That code isn't right or has expired. Ask for a new one." });
    }
    codes.delete(email);
    return session(await userFor(email));
  }
  if (path === "/auth/v1/token") {
    const old = refresh.get(String(body.refresh_token ?? ""));
    if (!old) return json(400, { msg: "Invalid refresh token" });
    refresh.delete(String(body.refresh_token));
    return session(old.user, old.aal);
  }
  // Kulan Fleet's own server (fleet-admin) resetting an admin's authenticator app.
  const admin = path.match(/^\/auth\/v1\/admin\/users\/([^/]+)\/factors(?:\/([^/]+))?$/);
  if (admin) {
    if (req.headers.get("Authorization") !== `Bearer ${Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")}`) {
      return json(401, { msg: "service key needed" });
    }
    const list = factors.get(admin[1]) ?? [];
    if (req.method === "DELETE") {
      factors.set(admin[1], list.filter((f) => f.id !== admin[2]));
      return json(200, {});
    }
    return json(200, list.map(({ secret: _s, ...f }) => f));
  }
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer /, "");
  const s = access.get(token);
  if (!s || s.until < Date.now()) return json(401, { msg: "not signed in" });
  if (path === "/auth/v1/user") return json(200, publicUser(s.user));
  if (path === "/auth/v1/logout") {
    access.delete(token);
    return new Response(null, { status: 204, headers: CORS });
  }
  // Authenticator apps (Supabase MFA, TOTP).
  if (path === "/auth/v1/factors" && req.method === "POST") {
    const secret = crypto.getRandomValues(new Uint8Array(20));
    const f: Factor = { id: crypto.randomUUID(), friendly_name: String(body.friendly_name ?? "Authenticator app").slice(0, 60),
                        factor_type: "totp", status: "unverified", created_at: new Date().toISOString(), secret };
    factors.set(s.user.id, [...(factors.get(s.user.id) ?? []).filter((x) => x.status === "verified"), f]);
    const uri = `otpauth://totp/Kulan%20Fleet%20(test):${encodeURIComponent(s.user.email)}?secret=${base32(secret)}&issuer=Kulan%20Fleet%20(test)`;
    return json(200, { id: f.id, type: "totp", friendly_name: f.friendly_name, totp: { secret: base32(secret), uri, qr_code: "" } });
  }
  const mfa = path.match(/^\/auth\/v1\/factors\/([^/]+)(\/challenge|\/verify)?$/);
  if (mfa) {
    const f = (factors.get(s.user.id) ?? []).find((x) => x.id === mfa[1]);
    if (!f) return json(404, { msg: "no such factor" });
    if (mfa[2] === "/challenge") {
      const id = crypto.randomUUID();
      challenges.set(id, { factor: f.id, until: Date.now() + 300_000 });
      return json(200, { id, type: "totp", expires_at: Math.floor(Date.now() / 1000) + 300 });
    }
    if (mfa[2] === "/verify") {
      const c = challenges.get(String(body.challenge_id ?? ""));
      if (!c || c.factor !== f.id || c.until < Date.now() || !(await totpOk(f.secret, String(body.code ?? "")))) {
        return json(422, { msg: "Invalid TOTP code entered" });
      }
      challenges.delete(String(body.challenge_id));
      f.status = "verified";
      return session(s.user, "aal2");
    }
    if (req.method === "DELETE") {
      if (f.status === "verified" && s.aal !== "aal2") return json(403, { msg: "AAL2 required" });
      factors.set(s.user.id, (factors.get(s.user.id) ?? []).filter((x) => x.id !== f.id));
      return json(200, { id: f.id });
    }
  }
  return json(404, { msg: "not found" });
}

// --- stand-in Google Workspace (2.8e) -------------------------------------------------------
// Unless run.sh is told to use the real Google (KULAN_FLEET_REAL_GOOGLE=1),
// fleet-admin's Google calls come here: any service account key is accepted
// (for super admin it@<domain>), mailboxes live in memory. Enough to try
// mailboxes with People on test computers without a real Workspace.
type GUser = { id: string; primaryEmail: string; name: { givenName: string; familyName: string }; suspended: boolean;
               isAdmin: boolean; changePasswordAtNextLogin?: boolean };
const gusers = new Map<string, GUser>();
async function google(req: Request, path: string): Promise<Response> {
  if (path === "/google/token") {
    const form = new URLSearchParams(await req.text());
    const [, claims] = String(form.get("assertion") ?? "").split(".");
    let sub = "";
    try {
      sub = JSON.parse(atob(claims.replaceAll("-", "+").replaceAll("_", "/") + "==".slice(0, (4 - claims.length % 4) % 4))).sub;
    } catch { /* below */ }
    if (!/^it@/.test(sub)) return json(400, { error: "invalid_grant", error_description: "act as it@<your domain> on the test server" });
    return json(200, { access_token: "test-google-token", expires_in: 3600, token_type: "Bearer" });
  }
  if (req.headers.get("Authorization") !== "Bearer test-google-token") return json(401, { error: { message: "Login Required" } });
  const m = path.match(/^\/google\/admin\/directory\/v1\/users(?:\/(.+))?$/);
  if (!m) return json(404, { error: { message: "not found" } });
  const who = m[1] ? decodeURIComponent(m[1]) : null;
  const body = req.method === "POST" || req.method === "PATCH" ? await req.json().catch(() => ({})) : {};
  const show = (u: GUser) => ({ ...u, name: { ...u.name, fullName: `${u.name.givenName} ${u.name.familyName}` }, isDelegatedAdmin: false });
  if (!who && req.method === "GET") {
    const domain = new URL(req.url).searchParams.get("domain") ?? "";
    return json(200, { users: [...gusers.values()].filter((u) => u.primaryEmail.endsWith("@" + domain)).map(show) });
  }
  if (!who && req.method === "POST") {
    if (gusers.has(body.primaryEmail)) return json(409, { error: { message: "Entity already exists." } });
    gusers.set(body.primaryEmail, { id: String(100000 + gusers.size), primaryEmail: body.primaryEmail, name: body.name,
                                    suspended: false, isAdmin: false, changePasswordAtNextLogin: true });
    console.log(`(stand-in Google) made ${body.primaryEmail}`);
    return json(200, show(gusers.get(body.primaryEmail)!));
  }
  const u = who ? gusers.get(who) ?? [...gusers.values()].find((x) => x.id === who) : undefined;
  if (!u) return json(404, { error: { message: "Resource Not Found: userKey" } });
  if (req.method === "GET") return json(200, show(u));
  if (req.method === "PATCH") {
    if (typeof body.suspended === "boolean") u.suspended = body.suspended;
    if (body.name) u.name = { ...u.name, ...body.name };
    console.log(`(stand-in Google) changed ${u.primaryEmail}: ${Object.keys(body).filter((k) => k !== "password").join(", ")}`);
    return json(200, show(u));
  }
  if (req.method === "DELETE") {
    gusers.delete(u.primaryEmail);
    console.log(`(stand-in Google) deleted ${u.primaryEmail}`);
    return new Response(null, { status: 204 });
  }
  return json(400, { error: { message: "bad request" } });
}

// --- stand-in Supabase Realtime (2.9a): broadcast topics, as computers use them ------------
// Phoenix channels over WebSocket: phx_join and heartbeat answered; the
// functions' broadcasts (REST, with the service key) go to whoever joined.
const listeners = new Map<WebSocket, Set<string>>();
function realtime(req: Request, path: string): Response {
  if (path === "/realtime/v1/websocket") {
    if (req.headers.get("upgrade")?.toLowerCase() !== "websocket") return json(400, { error: "WebSocket only" });
    const { socket, response } = Deno.upgradeWebSocket(req);
    listeners.set(socket, new Set());
    socket.onmessage = (ev) => {
      let msg: Record<string, any>;
      try { msg = JSON.parse(String(ev.data)); } catch { return; }
      const ok = (topic: string) => socket.send(JSON.stringify({ topic, event: "phx_reply", ref: msg.ref ?? null,
                                                                 payload: { status: "ok", response: {} } }));
      if (msg.event === "phx_join" && typeof msg.topic === "string" && msg.topic.startsWith("realtime:")) {
        listeners.get(socket)?.add(msg.topic);
        ok(msg.topic);
      } else if (msg.event === "heartbeat") {
        ok("phoenix");
      }
    };
    socket.onclose = () => listeners.delete(socket);
    return response;
  }
  return json(404, { error: "not found" });
}
async function broadcast(req: Request): Promise<Response> {
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (req.headers.get("authorization") !== `Bearer ${key}`) return json(401, { error: "service key needed" });
  const body = await req.json().catch(() => ({}));
  let sent = 0;
  for (const m of Array.isArray(body?.messages) ? body.messages : []) {
    const topic = `realtime:${m?.topic}`;
    for (const [socket, topics] of listeners) {
      if (topics.has(topic) && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ topic, event: "broadcast", ref: null, payload: { event: m.event, payload: m.payload ?? {} } }));
        sent++;
      }
    }
  }
  console.log(`  wake: ${sent} computer${sent === 1 ? "" : "s"} listening`);
  return new Response(null, { status: 202 });
}

// --- the server ---------------------------------------------------------------------------
const ca = await Deno.readTextFile(`${DIR}/ca.crt`);
realServe({
  port: PORT,
  hostname: "0.0.0.0",
  cert: await Deno.readTextFile(`${DIR}/server.crt`),
  key: await Deno.readTextFile(`${DIR}/server.key`),
  onListen: ({ port }) => console.log(`Kulan Fleet test server on port ${port}. Stop it with Ctrl+C.`),
}, async (req) => {
  const path = new URL(req.url).pathname;
  let res: Response;
  if (path === "/kulan-fleet-test-ca.crt") {
    res = new Response(ca, { headers: { "Content-Type": "application/x-pem-file" } });
  } else if (path.startsWith("/functions/v1/fleet-admin")) {
    res = await handlers["fleet-admin"](req);
  } else if (path.startsWith("/functions/v1/fleet/")) {
    res = await handlers["fleet"](req);
  } else if (path.startsWith("/auth/v1/")) {
    res = await auth(req, path);
  } else if (path === "/realtime/v1/api/broadcast") {
    res = await broadcast(req);
  } else if (path.startsWith("/realtime/v1/")) {
    return realtime(req, path);
  } else if (path.startsWith("/google/") && Deno.env.get("FLEET_GOOGLE_API")) {
    res = await google(req, path);
  } else {
    res = json(404, { error: "not found" });
  }
  console.log(`${new Date().toISOString().slice(11, 19)} ${req.method} ${path} ${res.status}`);
  return res;
});
