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

// --- stand-in Supabase Auth (email + one-time code) -----------------------------------
type User = { email: string; email_confirmed_at: string };
const codes = new Map<string, { code: string; until: number }>();
const access = new Map<string, { user: User; until: number }>();
const refresh = new Map<string, User>();

function random(bytes = 32): string {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function session(user: User): Response {
  const a = random(), r = random();
  access.set(a, { user, until: Date.now() + 3600_000 });
  refresh.set(r, user);
  return json(200, { access_token: a, refresh_token: r, expires_in: 3600, token_type: "bearer", user });
}

async function auth(req: Request, path: string): Promise<Response> {
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
    return session({ email, email_confirmed_at: new Date().toISOString() });
  }
  if (path === "/auth/v1/token") {
    const user = refresh.get(String(body.refresh_token ?? ""));
    if (!user) return json(400, { msg: "Invalid refresh token" });
    refresh.delete(String(body.refresh_token));
    return session(user);
  }
  const token = (req.headers.get("Authorization") ?? "").replace(/^Bearer /, "");
  if (path === "/auth/v1/user") {
    const s = access.get(token);
    return s && s.until > Date.now() ? json(200, s.user) : json(401, { msg: "not signed in" });
  }
  if (path === "/auth/v1/logout") {
    access.delete(token);
    return new Response(null, { status: 204 });
  }
  return json(404, { msg: "not found" });
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
  } else {
    res = json(404, { error: "not found" });
  }
  console.log(`${new Date().toISOString().slice(11, 19)} ${req.method} ${path} ${res.status}`);
  return res;
});
