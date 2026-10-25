// Google Workspace for Kulan Fleet (2.8e): mailboxes made, suspended,
// renamed and deleted with People, through Google's Admin SDK Directory API.
//
// Kulan Fleet acts with a service account the organisation made, given
// domain-wide delegation for one scope (SCOPE) and acting as one of its
// Google super admins. Its private key is kept sealed (AES-GCM with
// FLEET_FACTOR_KEY, the key 2.8d's phone codes use) and opened only here.
//
// FLEET_GOOGLE_TOKEN_URL and FLEET_GOOGLE_API point elsewhere only on the
// Kulan Fleet test server (its stand-in Google); never set them otherwise.

export const SCOPE = "https://www.googleapis.com/auth/admin.directory.user";
const tokenUrl = () => Deno.env.get("FLEET_GOOGLE_TOKEN_URL") ?? "https://oauth2.googleapis.com/token";
const apiUrl = () => Deno.env.get("FLEET_GOOGLE_API") ?? "https://admin.googleapis.com";
const TIMEOUT_MS = 15_000;

// A mailbox address: lower-case, a plain local part, a domain with a dot.
export const ADDRESS = /^[a-z0-9](?:[a-z0-9._'-]{0,62}[a-z0-9])?@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const SERVICE_ACCOUNT = /^[a-z0-9-]{6,30}@[a-z0-9-]{4,63}\.iam\.gserviceaccount\.com$/;

export class GoogleError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// --- the sealed private key -------------------------------------------------------------------
// Its own AES key, derived from FLEET_FACTOR_KEY (HKDF, so it isn't the key
// that seals 2.8d's phone codes), and each sealed key is bound to its
// organisation (AES-GCM's associated data): copied to another organisation's
// row, it doesn't open.
let sealKey: CryptoKey | null | undefined;
async function mailKey(): Promise<CryptoKey | null> {
  if (sealKey !== undefined) return sealKey;
  try {
    const raw = Uint8Array.from(atob(Deno.env.get("FLEET_FACTOR_KEY") ?? ""), (c) => c.charCodeAt(0));
    if (raw.length !== 32) {
      sealKey = null;
    } else {
      const base = await crypto.subtle.importKey("raw", raw, "HKDF", false, ["deriveKey"]);
      sealKey = await crypto.subtle.deriveKey(
        { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: new TextEncoder().encode("kulan-fleet google key v1") },
        base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    }
  } catch {
    sealKey = null;
  }
  return sealKey;
}

const toB64 = (bytes: Uint8Array) => {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};
const bound = (org: string) => new TextEncoder().encode(`mail:${org}`);

// "m1:" + base64(12-byte IV + AES-GCM ciphertext); null when Kulan Fleet's key isn't set.
export async function sealText(text: string, org: string): Promise<string | null> {
  const key = await mailKey();
  if (!key) return null;
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: bound(org) }, key,
                                                        new TextEncoder().encode(text)));
  const all = new Uint8Array(12 + ct.length);
  all.set(iv);
  all.set(ct, 12);
  return "m1:" + toB64(all);
}

export async function openText(sealed: string, org: string): Promise<string | null> {
  const key = await mailKey();
  if (!key || !sealed.startsWith("m1:")) return null;
  try {
    const all = Uint8Array.from(atob(sealed.slice(3)), (c) => c.charCodeAt(0));
    return new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv: all.slice(0, 12), additionalData: bound(org) },
                                                                key, all.slice(12)));
  } catch {
    return null;
  }
}

// --- the service account key the Owner pastes ---------------------------------------------------
export type ServiceAccount = { client_email: string; client_id: string | null; private_key: string };

export function parseServiceAccount(value: unknown): ServiceAccount | null {
  let v = value;
  if (typeof v === "string") {
    if (v.length > 16384) return null;
    try {
      v = JSON.parse(v);
    } catch {
      return null;
    }
  }
  if (typeof v !== "object" || v === null || Array.isArray(v)) return null;
  const k = v as Record<string, unknown>;
  if (k.type !== "service_account" || typeof k.client_email !== "string" || typeof k.private_key !== "string") return null;
  const email = k.client_email.trim().toLowerCase();
  const pem = k.private_key.replace(/\r\n/g, "\n").trim();
  if (!SERVICE_ACCOUNT.test(email) || pem.length > 8000 ||
      !/^-----BEGIN PRIVATE KEY-----\n[A-Za-z0-9+/=\n]+\n-----END PRIVATE KEY-----$/.test(pem)) return null;
  const id = typeof k.client_id === "string" && /^\d{10,30}$/.test(k.client_id) ? k.client_id : null;
  return { client_email: email, client_id: id, private_key: pem + "\n" };
}

async function signingKey(pem: string): Promise<CryptoKey> {
  const body = pem.replace(/-----(BEGIN|END) PRIVATE KEY-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  return await crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
}

const b64url = (bytes: Uint8Array) => toB64(bytes).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
const jsonPart = (o: unknown) => b64url(new TextEncoder().encode(JSON.stringify(o)));

// --- talking to Google --------------------------------------------------------------------------
export type Connection = { organisation_id: string; domain: string; admin_email: string; client_email: string; pem: string };

const tokens = new Map<string, { token: string; until: number }>();

// Kept per organisation and per exact key: one organisation's token can never
// be another's, even for the same service account and super admin.
async function cacheKeyOf(c: Connection): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${c.organisation_id}\n${c.client_email}\n${c.admin_email}\n${c.pem}`));
  return toB64(new Uint8Array(d));
}

// An access token for the service account acting as the super admin (OAuth
// 2.0 JWT bearer grant). fresh: always ask Google (connecting checks the key).
export async function accessToken(c: Connection, fresh = false): Promise<string> {
  const cacheKey = await cacheKeyOf(c);
  const cached = tokens.get(cacheKey);
  if (!fresh && cached && cached.until > Date.now()) return cached.token;
  let key: CryptoKey;
  try {
    key = await signingKey(c.pem);
  } catch {
    throw new GoogleError(400, "that isn't a private key Kulan Fleet can use: download a new JSON key for the service account");
  }
  const now = Math.floor(Date.now() / 1000);
  const unsigned = `${jsonPart({ alg: "RS256", typ: "JWT" })}.${jsonPart({
    iss: c.client_email, sub: c.admin_email, scope: SCOPE, aud: tokenUrl(), iat: now, exp: now + 3600 })}`;
  const sig = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(unsigned)));
  let res: Response;
  try {
    res = await fetch(tokenUrl(), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: `${unsigned}.${b64url(sig)}` }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new GoogleError(0, "Google couldn't be reached: try again in a minute");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok || typeof body?.access_token !== "string") {
    const why = `${body?.error ?? ""} ${body?.error_description ?? ""}`.toLowerCase();
    if (why.includes("unauthorized_client") || why.includes("not authorized")) {
      throw new GoogleError(403, "Google hasn't allowed this service account to act for your domain: in the Google Admin " +
        "console, give its client ID domain-wide delegation for " + SCOPE);
    }
    if (why.includes("invalid_grant")) {
      throw new GoogleError(403, `Google didn't accept it: check ${c.admin_email} is a Google super admin of your domain ` +
        "and the service account's key hasn't been deleted");
    }
    throw new GoogleError(res.status || 502, `Google said no (${String(body?.error ?? res.status).slice(0, 60)})`);
  }
  const life = Number(body.expires_in) > 120 ? Number(body.expires_in) : 3600;
  tokens.set(cacheKey, { token: body.access_token, until: Date.now() + (life - 120) * 1000 });
  return body.access_token;
}

async function call(c: Connection, method: string, path: string, body?: unknown, fresh = false): Promise<{ status: number; body: any }> {
  const token = await accessToken(c, fresh);
  let res: Response;
  try {
    res = await fetch(`${apiUrl()}/admin/directory/v1${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new GoogleError(0, "Google couldn't be reached: try again in a minute");
  }
  const text = await res.text();
  let parsed: any = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch { /* not JSON */ }
  return { status: res.status, body: parsed };
}

function failed(r: { status: number; body: any }, c: Connection, what: string): GoogleError {
  const said = String(r.body?.error?.message ?? "").slice(0, 160);
  if (r.status === 401 || r.status === 403) {
    return new GoogleError(403, `Google won't let Kulan Fleet ${what}: check ${c.admin_email} is a super admin` +
      (said ? ` (Google: ${said})` : ""));
  }
  if (r.status === 409) return new GoogleError(409, "that address is already in use in Google Workspace");
  if (r.status === 429) return new GoogleError(429, "Google is busy: try again in a minute");
  return new GoogleError(r.status >= 500 ? 502 : 400, `Google couldn't ${what}` + (said ? `: ${said}` : ""));
}

// id: Google's own, which never changes (an address can be renamed, or become another user's alias).
export type GoogleUser = { id: string; address: string; name: string; suspended: boolean; admin: boolean };

function user(u: any): GoogleUser {
  return {
    id: typeof u?.id === "string" ? u.id : "",
    address: String(u?.primaryEmail ?? "").toLowerCase(),
    name: String(u?.name?.fullName ?? "").slice(0, 120),
    suspended: u?.suspended === true,
    admin: u?.isAdmin === true || u?.isDelegatedAdmin === true,
  };
}

// A user by Google id or address (Google takes either).
const at = (key: string) => `/users/${encodeURIComponent(key)}`;

// The mailbox, or null if Google has none with that address.
export async function getUser(c: Connection, key: string): Promise<GoogleUser | null> {
  const r = await call(c, "GET", at(key));
  if (r.status === 404) return null;
  if (r.status !== 200) throw failed(r, c, "look up that mailbox");
  return user(r.body);
}

// The domain's mailboxes (up to 5000), for linking existing ones.
export async function listUsers(c: Connection): Promise<GoogleUser[]> {
  const out: GoogleUser[] = [];
  let page = "";
  for (let i = 0; i < 10; i++) {
    const q = new URLSearchParams({ domain: c.domain, maxResults: "500", orderBy: "email", ...(page ? { pageToken: page } : {}) });
    const r = await call(c, "GET", `/users?${q}`);
    if (r.status !== 200) throw failed(r, c, "list your mailboxes");
    for (const u of Array.isArray(r.body?.users) ? r.body.users : []) out.push(user(u));
    page = typeof r.body?.nextPageToken === "string" ? r.body.nextPageToken : "";
    if (!page) break;
  }
  return out;
}

// Checks the connection works: a token, then one mailbox of the domain.
export async function check(c: Connection): Promise<void> {
  const r = await call(c, "GET", `/users?${new URLSearchParams({ domain: c.domain, maxResults: "1" })}`, undefined, true);
  if (r.status !== 200) throw failed(r, c, `list the mailboxes of ${c.domain}`);
}

// Returns the new mailbox's Google id.
export async function createUser(c: Connection, address: string, name: { given: string; family: string }, password: string): Promise<string> {
  const r = await call(c, "POST", "/users", {
    primaryEmail: address, name: { givenName: name.given, familyName: name.family }, password,
    changePasswordAtNextLogin: true });
  if (r.status !== 200 && r.status !== 201) throw failed(r, c, "make that mailbox");
  return typeof r.body?.id === "string" ? r.body.id : "";
}

export async function setSuspended(c: Connection, key: string, suspended: boolean) {
  const r = await call(c, "PATCH", at(key), { suspended });
  if (r.status !== 200) throw failed(r, c, suspended ? "suspend that mailbox" : "unsuspend that mailbox");
}

export async function rename(c: Connection, key: string, name: { given: string; family: string }) {
  const r = await call(c, "PATCH", at(key), { name: { givenName: name.given, familyName: name.family } });
  if (r.status !== 200) throw failed(r, c, "change the name on that mailbox");
}

export async function setPassword(c: Connection, key: string, password: string) {
  const r = await call(c, "PATCH", at(key), { password, changePasswordAtNextLogin: true });
  if (r.status !== 200) throw failed(r, c, "give that mailbox a new password");
}

export async function deleteUser(c: Connection, key: string) {
  const r = await call(c, "DELETE", at(key));
  if (r.status !== 204 && r.status !== 200 && r.status !== 404) throw failed(r, c, "delete that mailbox");
}

// A first password for Google, shown to the admin once (Google asks for a
// new one at the first sign-in): 16 letters and digits that can't be
// mistaken for each other, in groups of four.
export function tempPassword(): string {
  const letters = "abcdefghjkmnpqrstuvwxyzACDEFGHJKLMNPQRTUVWXYZ2346789";
  const out: string[] = [];
  for (const b of crypto.getRandomValues(new Uint32Array(16))) out.push(letters[b % letters.length]);
  return out.join("").match(/.{4}/g)!.join("-");
}

// A full name as Google wants it: a first and a last name, each 1-60 characters.
export function splitName(fullName: string, username: string): { given: string; family: string } {
  const clean = fullName.replace(/[<>=\p{Cc}]/gu, " ").replace(/\s+/g, " ").trim();
  const words = (clean || username).split(" ");
  const family = (words.length > 1 ? words.pop()! : words[0]).slice(0, 60);
  const given = (words.join(" ") || family).slice(0, 60);
  return { given, family };
}
