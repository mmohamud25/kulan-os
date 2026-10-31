// Microsoft 365 for Kulan Fleet (2.12): mailboxes made, suspended, renamed
// and deleted with People, through Microsoft Graph, as google.ts does for
// Google Workspace (the same functions, so fleet-admin uses either through
// mail.ts).
//
// Kulan Fleet acts as an app the organisation registered in Microsoft Entra
// (its tenant ID, the app's client ID and a client secret), with these
// application permissions, admin consent given: User.ReadWrite.All (make,
// rename, disable and delete users, give them a licence),
// User-PasswordProfile.ReadWrite.All (first passwords) and
// Organization.Read.All (the licences there are); RoleManagement.Read.Directory
// to tell admin accounts apart. The client secret is kept sealed, as Google's
// key is (google.ts sealText, bound to the organisation).
//
// A mailbox is a user whose licence includes Exchange Online: the licence is
// chosen when connecting (the first with one), and given when a mailbox is made.
//
// FLEET_MS_LOGIN and FLEET_MS_GRAPH point elsewhere only on the Kulan Fleet
// test server (its stand-in Microsoft); never set them otherwise.

import { GoogleError as MailError } from "./google.ts";

export const PERMISSIONS = ["User.ReadWrite.All", "User-PasswordProfile.ReadWrite.All", "Organization.Read.All",
                            "RoleManagement.Read.Directory"];
const loginUrl = () => Deno.env.get("FLEET_MS_LOGIN") ?? "https://login.microsoftonline.com";
const graphUrl = () => Deno.env.get("FLEET_MS_GRAPH") ?? "https://graph.microsoft.com";
const TIMEOUT_MS = 15_000;
export const GUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// Where people's mailboxes live (Microsoft needs it before it gives a licence).
export const COUNTRY = /^[A-Z]{2}$/;

export type Connection = {
  organisation_id: string; domain: string; tenant_id: string; client_id: string; secret: string;
  licence: string | null; usage_location: string;
};

// The app registration's details as the Owner types them, checked.
export function parseApp(v: Record<string, unknown>): { tenant_id: string; client_id: string; secret: string } | null {
  const tenant = typeof v.tenant_id === "string" ? v.tenant_id.trim().toLowerCase() : "";
  const client = typeof v.client_id === "string" ? v.client_id.trim().toLowerCase() : "";
  const secret = typeof v.client_secret === "string" ? v.client_secret.trim() : "";
  if (!GUID.test(tenant) || !GUID.test(client) || secret.length < 16 || secret.length > 512 || /\s/.test(secret)) return null;
  return { tenant_id: tenant, client_id: client, secret };
}

const tokens = new Map<string, { token: string; until: number }>();
const toB64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes));

// Kept per organisation and per exact secret: one organisation's token is never another's.
async function cacheKeyOf(c: Connection): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(
    `${c.organisation_id}\n${c.tenant_id}\n${c.client_id}\n${c.secret}`));
  return toB64(new Uint8Array(d));
}

// An app-only access token (OAuth 2.0 client credentials). fresh: always ask
// Microsoft (connecting checks the secret).
export async function accessToken(c: Connection, fresh = false): Promise<string> {
  const cacheKey = await cacheKeyOf(c);
  const cached = tokens.get(cacheKey);
  if (!fresh && cached && cached.until > Date.now()) return cached.token;
  let res: Response;
  try {
    res = await fetch(`${loginUrl()}/${encodeURIComponent(c.tenant_id)}/oauth2/v2.0/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ grant_type: "client_credentials", client_id: c.client_id, client_secret: c.secret,
                                  scope: `${graphUrl()}/.default` }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new MailError(0, "Microsoft couldn't be reached. Try again in a minute");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok || typeof body?.access_token !== "string") {
    const code = String(body?.error ?? "");
    const said = String(body?.error_description ?? "");
    // AADSTS codes: 7000215 wrong secret, 7000222 expired secret, 700016 no such app, 90002 no such tenant.
    if (/AADSTS7000222/.test(said)) {
      throw new MailError(403, "the client secret has expired. Make a new one for the app in Microsoft Entra, and connect again");
    }
    if (code === "invalid_client" || /AADSTS7000215/.test(said)) {
      throw new MailError(403, "Microsoft didn't accept the client secret. Copy the secret's Value (not its ID) from the app's " +
        "Certificates & secrets page");
    }
    if (code === "unauthorized_client" || /AADSTS700016/.test(said)) {
      throw new MailError(403, "Microsoft has no app with that client ID in your tenant. Copy the Application (client) ID " +
        "from the app's Overview page");
    }
    if (/AADSTS90002|AADSTS900023/.test(said) || code === "invalid_request") {
      throw new MailError(400, "Microsoft has no tenant with that ID. Copy the Directory (tenant) ID from the app's Overview page");
    }
    throw new MailError(res.status >= 500 ? 502 : 400, `Microsoft refused the request (${code.slice(0, 60) || res.status})`);
  }
  const life = Number(body.expires_in) > 120 ? Number(body.expires_in) : 3600;
  tokens.set(cacheKey, { token: body.access_token, until: Date.now() + (life - 120) * 1000 });
  return body.access_token;
}

async function call(c: Connection, method: string, path: string, body?: unknown, fresh = false):
    Promise<{ status: number; body: any }> {
  const token = await accessToken(c, fresh);
  let res: Response;
  try {
    res = await fetch(path.startsWith("https://") || path.startsWith("http://") ? path : `${graphUrl()}/v1.0${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new MailError(0, "Microsoft couldn't be reached. Try again in a minute");
  }
  const text = await res.text();
  let parsed: any = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch { /* not JSON */ }
  return { status: res.status, body: parsed };
}

function failed(r: { status: number; body: any }, what: string): MailError {
  const said = String(r.body?.error?.message ?? "").slice(0, 160);
  if (r.status === 401 || r.status === 403) {
    return new MailError(403, `Microsoft won't let Kulan Fleet ${what}. Check that the app has the permissions ` +
      `${PERMISSIONS.join(", ")} with admin consent` + (said ? ` (${said})` : ""));
  }
  if (r.status === 409 || /already exists/i.test(said)) return new MailError(409, "that address is already in use in Microsoft 365");
  if (r.status === 429) return new MailError(429, "Microsoft is busy. Try again in a minute");
  return new MailError(r.status >= 500 ? 502 : 400, `Microsoft couldn't ${what}` + (said ? `: ${said}` : ""));
}

// The same shape as google.ts's GoogleUser. id: Microsoft's object id, which never changes.
export type MailUser = { id: string; address: string; name: string; suspended: boolean; admin: boolean };

const SELECT = "id,userPrincipalName,displayName,accountEnabled";

function user(u: any, admin: boolean): MailUser {
  return {
    id: typeof u?.id === "string" ? u.id : "",
    address: String(u?.userPrincipalName ?? "").toLowerCase(),
    name: String(u?.displayName ?? "").slice(0, 120),
    suspended: u?.accountEnabled === false,
    admin,
  };
}

const at = (key: string) => `/users/${encodeURIComponent(key)}`;

// Whether the user holds a Microsoft Entra admin role (Global, User, Exchange...):
// such accounts are changed only by the Owner, as Google admins are. Unknown
// counts as an admin.
async function isAdmin(c: Connection, id: string): Promise<boolean> {
  const q = new URLSearchParams({ $filter: `principalId eq '${id}'`, $select: "id" });
  const r = await call(c, "GET", `/roleManagement/directory/roleAssignments?${q}`);
  if (r.status !== 200) return true;
  return Array.isArray(r.body?.value) && r.body.value.length > 0;
}

// The mailbox, or null if Microsoft 365 has none with that address (or id).
export async function getUser(c: Connection, key: string): Promise<MailUser | null> {
  const r = await call(c, "GET", `${at(key)}?$select=${SELECT}`);
  if (r.status === 404) return null;
  if (r.status !== 200) throw failed(r, "look up that mailbox");
  return user(r.body, await isAdmin(c, String(r.body?.id ?? "")));
}

// The domain's users (up to 5000), for linking existing mailboxes.
export async function listUsers(c: Connection): Promise<MailUser[]> {
  const out: MailUser[] = [];
  const admins = new Set<string>();
  const roles = await call(c, "GET", "/roleManagement/directory/roleAssignments?$select=principalId&$top=999");
  for (const a of roles.status === 200 && Array.isArray(roles.body?.value) ? roles.body.value : []) admins.add(String(a.principalId));
  let next: string = `/users?${new URLSearchParams({ $select: SELECT, $top: "999" })}`;
  for (let i = 0; i < 6 && next; i++) {
    const r = await call(c, "GET", next);
    if (r.status !== 200) throw failed(r, "list your mailboxes");
    for (const u of Array.isArray(r.body?.value) ? r.body.value : []) {
      const x = user(u, roles.status !== 200 || admins.has(String(u?.id)));
      if (x.address.endsWith(`@${c.domain}`)) out.push(x);
    }
    next = typeof r.body?.["@odata.nextLink"] === "string" ? r.body["@odata.nextLink"] : "";
  }
  return out.sort((a, b) => a.address.localeCompare(b.address));
}

// The tenant's licences that include Exchange Online (mail), with how many are free.
export type Licence = { sku: string; name: string; free: number };

export async function licences(c: Connection, fresh = false): Promise<Licence[]> {
  const r = await call(c, "GET", "/subscribedSkus?$select=skuId,skuPartNumber,prepaidUnits,consumedUnits,servicePlans",
                       undefined, fresh);
  if (r.status !== 200) throw failed(r, "see your Microsoft 365 licences");
  const out: Licence[] = [];
  for (const s of Array.isArray(r.body?.value) ? r.body.value : []) {
    const mail = (s?.servicePlans ?? []).some((p: any) => /^EXCHANGE_S_/.test(String(p?.servicePlanName ?? "")));
    if (!mail || typeof s?.skuId !== "string") continue;
    const free = Number(s?.prepaidUnits?.enabled ?? 0) - Number(s?.consumedUnits ?? 0);
    out.push({ sku: s.skuId, name: String(s?.skuPartNumber ?? "").slice(0, 64), free: Math.max(0, free) });
  }
  return out;
}

// Checks the connection works: a token, the users of the domain, and a licence
// with mail. Returns the licence mailboxes are given.
export async function check(c: Connection): Promise<Licence> {
  const r = await call(c, "GET", `/users?${new URLSearchParams({ $select: "id", $top: "1" })}`, undefined, true);
  if (r.status !== 200) throw failed(r, `list the users of ${c.domain}`);
  // Without seeing admin roles, every mailbox would count as an admin's (Owner only): said now.
  const roles = await call(c, "GET", "/roleManagement/directory/roleAssignments?$select=id&$top=1");
  if (roles.status !== 200) throw failed(roles, "see who has admin roles (RoleManagement.Read.Directory)");
  const all = await licences(c);
  const chosen = all.find((l) => l.sku === c.licence) ?? all.find((l) => l.free > 0) ?? all[0];
  if (!chosen) {
    throw new MailError(400, "your Microsoft 365 has no licence with email (Exchange Online). Buy or add one, then connect again");
  }
  return chosen;
}

const mailNickname = (address: string) => address.split("@")[0].replace(/[^A-Za-z0-9._-]/g, "").slice(0, 64) || "user";

// Returns the new mailbox's Microsoft id. The user gets the connection's
// licence (that's what makes the mailbox); without a free one, nothing is made.
export async function createUser(c: Connection, address: string, name: { given: string; family: string }, password: string):
    Promise<string> {
  const free = (await licences(c)).find((l) => l.sku === c.licence);
  if (!free) throw new MailError(400, "the Microsoft 365 licence Kulan Fleet gives isn't there any more. Connect Microsoft 365 again");
  if (free.free < 1) {
    throw new MailError(400, `there's no free ${free.name || "Microsoft 365"} licence for a new mailbox. Add one in the ` +
      "Microsoft 365 admin center, then try again");
  }
  const r = await call(c, "POST", "/users", {
    accountEnabled: true, userPrincipalName: address, mailNickname: mailNickname(address),
    displayName: `${name.given} ${name.family}`.trim().slice(0, 256), givenName: name.given, surname: name.family,
    usageLocation: c.usage_location,
    passwordProfile: { password, forceChangePasswordNextSignIn: true },
  });
  if (r.status !== 201 && r.status !== 200) throw failed(r, "make that mailbox");
  const id = typeof r.body?.id === "string" ? r.body.id : "";
  const lic = await call(c, "POST", `${at(id || address)}/assignLicense`, { addLicenses: [{ skuId: c.licence, disabledPlans: [] }],
                                                                            removeLicenses: [] });
  if (lic.status !== 200) {
    // A user without the licence has no mailbox: taken away again, so a retry starts clean.
    await call(c, "DELETE", at(id || address)).catch(() => null);
    throw failed(lic, "give the new mailbox its licence");
  }
  return id;
}

export async function setSuspended(c: Connection, key: string, suspended: boolean) {
  const r = await call(c, "PATCH", at(key), { accountEnabled: !suspended });
  if (r.status !== 204 && r.status !== 200) throw failed(r, suspended ? "suspend that mailbox" : "unsuspend that mailbox");
  // Signed out everywhere at once, as Google does when suspending (best effort).
  if (suspended) await call(c, "POST", `${at(key)}/revokeSignInSessions`).catch(() => null);
}

export async function rename(c: Connection, key: string, name: { given: string; family: string }) {
  const r = await call(c, "PATCH", at(key), { givenName: name.given, surname: name.family,
                                              displayName: `${name.given} ${name.family}`.trim().slice(0, 256) });
  if (r.status !== 204 && r.status !== 200) throw failed(r, "change the name on that mailbox");
}

export async function setPassword(c: Connection, key: string, password: string) {
  const r = await call(c, "PATCH", at(key), { passwordProfile: { password, forceChangePasswordNextSignIn: true } });
  if (r.status !== 204 && r.status !== 200) throw failed(r, "give that mailbox a new password");
}

// Microsoft keeps a deleted user 30 days (Deleted users in the admin center), then removes it.
export async function deleteUser(c: Connection, key: string) {
  const r = await call(c, "DELETE", at(key));
  if (r.status !== 204 && r.status !== 200 && r.status !== 404) throw failed(r, "delete that mailbox");
}
