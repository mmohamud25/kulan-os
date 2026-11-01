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
// and GroupMember.Read.All to tell admin accounts apart (roles given,
// eligible, or through a group that can hold roles). The client secret is kept sealed, as Google's
// key is (google.ts sealText, bound to the organisation).
//
// A mailbox is a user whose licence includes Exchange Online: the licence is
// chosen when connecting (the first with one), and given when a mailbox is made.
//
// FLEET_MS_LOGIN and FLEET_MS_GRAPH point elsewhere only on the Kulan Fleet
// test server (its stand-in Microsoft); never set them otherwise.

import { GoogleError as MailError } from "./google.ts";

export const PERMISSIONS = ["User.ReadWrite.All", "User-PasswordProfile.ReadWrite.All", "Organization.Read.All",
                            "RoleManagement.Read.Directory", "GroupMember.Read.All"];
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
    if (/AADSTS90002|AADSTS900023/.test(said)) {
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

// The same shape as google.ts's GoogleUser. id: Microsoft's object id, which
// never changes. address: the mailbox's address (mail), else the sign-in name
// (userPrincipalName), which can differ (an onmicrosoft.com sign-in name).
export type MailUser = { id: string; address: string; name: string; suspended: boolean; admin: boolean };

const SELECT = "id,userPrincipalName,mail,displayName,accountEnabled";

function user(u: any, admin: boolean): MailUser {
  return {
    id: typeof u?.id === "string" ? u.id : "",
    address: String(u?.mail || u?.userPrincipalName || "").toLowerCase(),
    name: String(u?.displayName ?? "").slice(0, 120),
    suspended: u?.accountEnabled === false,
    admin,
  };
}

// By id only: every call that changes a user is made with Microsoft's id.
const at = (id: string) => `/users/${encodeURIComponent(id)}`;
const quoted = (v: string) => `'${v.replaceAll("'", "''")}'`;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
// Microsoft takes a few seconds to know a user it has just made (or changed):
// a 404 for an id is asked again, a little later.
// (FLEET_MS_SETTLE_MS, milliseconds between tries: only the tests set it.)
const settle = {
  get delays(): number[] {
    const v = Deno.env.get("FLEET_MS_SETTLE_MS");
    return v ? v.split(",").map(Number).filter((n) => n >= 0) : [1000, 3000];
  },
};

// Every page of a Graph list (up to 6 pages of 999).
async function all(c: Connection, first: string): Promise<{ status: number; body: any; value: any[] }> {
  const value: any[] = [];
  let next = first;
  for (let i = 0; i < 6 && next; i++) {
    const r = await call(c, "GET", next);
    if (r.status !== 200) return { ...r, value };
    value.push(...(Array.isArray(r.body?.value) ? r.body.value : []));
    next = typeof r.body?.["@odata.nextLink"] === "string" ? r.body["@odata.nextLink"] : "";
  }
  return { status: 200, body: null, value };
}

// Whether the user holds a Microsoft Entra admin role (Global, User, Exchange...)
// or could: a role given to them, one they're eligible to switch on (PIM), or
// membership of a group that can hold roles. Such accounts are changed only by
// the Owner, as Google admins are. Anything Microsoft doesn't answer counts as
// an admin.
async function isAdmin(c: Connection, id: string): Promise<boolean> {
  if (!GUID.test(id)) return true;
  const who = new URLSearchParams({ $filter: `principalId eq ${quoted(id)}` });
  const direct = await call(c, "GET", `/roleManagement/directory/roleAssignments?${who}`);
  if (direct.status !== 200 || (direct.body?.value ?? []).length) return true;
  const eligible = await call(c, "GET", `/roleManagement/directory/roleEligibilityScheduleInstances?${who}`);
  if (eligible.status !== 200 || (eligible.body?.value ?? []).length) return true;
  const groups = await all(c, `${at(id)}/transitiveMemberOf/microsoft.graph.group?$select=id,isAssignableToRole`);
  return groups.status !== 200 || groups.value.some((g) => g?.isAssignableToRole === true);
}

async function find(c: Connection, key: string): Promise<any | null> {
  if (GUID.test(key)) {
    for (let attempt = 0; ; attempt++) {
      const r = await call(c, "GET", `${at(key)}?$select=${SELECT}`);
      if (r.status === 200) return r.body;
      if (r.status !== 404) throw failed(r, "look up that mailbox");
      if (attempt >= settle.delays.length) return null;
      await sleep(settle.delays[attempt]);
    }
  }
  // An address: the mailbox's address or the sign-in name.
  const k = key.toLowerCase();
  const q = new URLSearchParams({ $filter: `userPrincipalName eq ${quoted(k)} or mail eq ${quoted(k)}`, $select: SELECT });
  const r = await call(c, "GET", `/users?${q}`);
  if (r.status !== 200) throw failed(r, "look up that mailbox");
  const hits = (r.body?.value ?? []) as any[];
  return hits.find((u) => String(u?.mail ?? "").toLowerCase() === k) ?? hits[0] ?? null;
}

// The mailbox, or null if Microsoft 365 has none with that address (or id).
export async function getUser(c: Connection, key: string): Promise<MailUser | null> {
  const u = await find(c, key);
  return u ? user(u, await isAdmin(c, String(u?.id ?? ""))) : null;
}

// The domain's users (up to 5000), for linking existing mailboxes. Who could be
// an admin, from every page of role assignments and eligibilities (unknown:
// everyone is shown as an admin).
export async function listUsers(c: Connection): Promise<MailUser[]> {
  const admins = new Set<string>();
  const direct = await all(c, "/roleManagement/directory/roleAssignments?$select=principalId");
  const eligible = await all(c, "/roleManagement/directory/roleEligibilityScheduleInstances?$select=principalId");
  for (const a of [...direct.value, ...eligible.value]) admins.add(String(a?.principalId));
  const unknown = direct.status !== 200 || eligible.status !== 200;
  const users = await all(c, `/users?${new URLSearchParams({ $select: SELECT, $top: "999" })}`);
  if (users.status !== 200) throw failed(users, "list your mailboxes");
  const out: MailUser[] = [];
  for (const u of users.value) {
    // A role given to a group: its members aren't listed here, so groups count by the user's own lookup.
    const x = user(u, unknown || admins.has(String(u?.id)));
    if (x.address.endsWith(`@${c.domain}`)) out.push(x);
  }
  return out.sort((a, b) => a.address.localeCompare(b.address));
}

// The tenant's licences that give a mailbox (an Exchange Online plan with one:
// Plan 1 or 2, Kiosk, Essentials, Business Basic and up; not the Exchange
// "foundation" every Power BI, Visio or Project licence carries), with how
// many are free.
export type Licence = { sku: string; name: string; free: number };
const MAILBOX_PLANS = /^(EXCHANGE_S_STANDARD|EXCHANGE_S_ENTERPRISE|EXCHANGE_S_DESKLESS|EXCHANGE_S_ESSENTIALS|EXCHANGE_B_STANDARD)(_[A-Z0-9]+)?$/;

export async function licences(c: Connection, fresh = false): Promise<Licence[]> {
  const r = await call(c, "GET", "/subscribedSkus?$select=skuId,skuPartNumber,capabilityStatus,prepaidUnits,consumedUnits,servicePlans",
                       undefined, fresh);
  if (r.status !== 200) throw failed(r, "see your Microsoft 365 licences");
  const out: Licence[] = [];
  for (const s of Array.isArray(r.body?.value) ? r.body.value : []) {
    const mail = (s?.servicePlans ?? []).some((p: any) => MAILBOX_PLANS.test(String(p?.servicePlanName ?? "")));
    if (!mail || typeof s?.skuId !== "string" || (s?.capabilityStatus && s.capabilityStatus !== "Enabled")) continue;
    const free = Number(s?.prepaidUnits?.enabled ?? 0) - Number(s?.consumedUnits ?? 0);
    out.push({ sku: s.skuId, name: String(s?.skuPartNumber ?? "").slice(0, 64), free: Math.max(0, free) });
  }
  return out;
}

// Checks the connection works: a token, the users of the domain, admin roles,
// and a licence with a mailbox. Returns the licence mailboxes are given: the
// one asked for (by name, ENTERPRISEPACK...), else the first with a free seat.
export async function check(c: Connection, wanted = ""): Promise<Licence> {
  const r = await call(c, "GET", `/users?${new URLSearchParams({ $select: "id", $top: "1" })}`, undefined, true);
  if (r.status !== 200) throw failed(r, `list the users of ${c.domain}`);
  // Without seeing admin roles, every mailbox would count as an admin's (Owner only): said now.
  const global = new URLSearchParams({ $filter: "roleDefinitionId eq '62e90394-69f5-4237-9190-012177145e10'" });
  for (const path of ["roleAssignments", "roleEligibilityScheduleInstances"]) {
    const roles = await call(c, "GET", `/roleManagement/directory/${path}?${global}`);
    if (roles.status !== 200) throw failed(roles, "see who has admin roles (RoleManagement.Read.Directory)");
  }
  const groups = await call(c, "GET", `/groups?${new URLSearchParams({ $select: "id", $top: "1" })}`);
  if (groups.status !== 200) throw failed(groups, "see groups that hold admin roles (GroupMember.Read.All)");
  const mine = await licences(c);
  const name = wanted.trim().toUpperCase();
  if (name) {
    const one = mine.find((l) => l.name === name || l.sku === wanted.trim().toLowerCase());
    if (!one) {
      throw new MailError(400, `there's no licence ${name} with a mailbox` +
        (mine.length ? `. You have: ${mine.map((l) => l.name).join(", ")}` : ""));
    }
    return one;
  }
  const chosen = mine.find((l) => l.sku === c.licence) ?? mine.find((l) => l.free > 0) ?? mine[0];
  if (!chosen) {
    throw new MailError(400, "your Microsoft 365 has no licence with a mailbox (Exchange Online). Buy or add one, then " +
      "connect again");
  }
  return chosen;
}

const mailNickname = (address: string) => address.split("@")[0].replace(/[^A-Za-z0-9._-]/g, "").slice(0, 64) || "user";

// Returns the new mailbox's Microsoft id. The user gets the connection's
// licence (that's what makes the mailbox); without a free one, nothing is made.
// If the licence isn't given, the user is taken away again: none is left
// without a mailbox, or with a licence Kulan Fleet doesn't know about.
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
  if (!GUID.test(id)) throw new MailError(502, "Microsoft made the user but didn't say its id. Check it in the Microsoft 365 admin center");
  let done = false;
  try {
    for (let attempt = 0; ; attempt++) {
      const lic = await call(c, "POST", `${at(id)}/assignLicense`, { addLicenses: [{ skuId: c.licence, disabledPlans: [] }],
                                                                    removeLicenses: [] });
      if (lic.status === 200) break;
      // Not known yet (made a moment ago): asked again.
      if (lic.status === 404 && attempt < settle.delays.length) {
        await sleep(settle.delays[attempt]);
        continue;
      }
      throw failed(lic, "give the new mailbox its licence");
    }
    done = true;
    return id;
  } finally {
    if (!done) await deleteUser(c, id).catch(() => null);
  }
}

// A change Microsoft refuses for an admin account: the app itself needs an
// admin role for those. Not a broken connection (status 400, not 403).
async function refused(c: Connection, r: { status: number; body: any }, id: string, what: string): Promise<MailError> {
  if ((r.status === 403 || r.status === 401) && await isAdmin(c, id)) {
    return new MailError(400, `Microsoft only lets Kulan Fleet ${what} of an admin account if the Kulan Fleet app has an ` +
      "admin role itself (Privileged Authentication Administrator). Do it in the Microsoft 365 admin center instead");
  }
  return failed(r, what);
}

export async function setSuspended(c: Connection, id: string, suspended: boolean) {
  const r = await call(c, "PATCH", at(id), { accountEnabled: !suspended });
  if (r.status !== 204 && r.status !== 200) throw await refused(c, r, id, suspended ? "block sign-in" : "allow sign-in");
  // Signed out everywhere at once, as Google does when suspending (best effort).
  if (suspended) await call(c, "POST", `${at(id)}/revokeSignInSessions`).catch(() => null);
}

export async function rename(c: Connection, id: string, name: { given: string; family: string }) {
  const r = await call(c, "PATCH", at(id), { givenName: name.given, surname: name.family,
                                             displayName: `${name.given} ${name.family}`.trim().slice(0, 256) });
  if (r.status !== 204 && r.status !== 200) throw await refused(c, r, id, "change the name");
}

export async function setPassword(c: Connection, id: string, password: string) {
  const r = await call(c, "PATCH", at(id), { passwordProfile: { password, forceChangePasswordNextSignIn: true } });
  if (r.status !== 204 && r.status !== 200) throw await refused(c, r, id, "change the password");
}

// Microsoft keeps a deleted user 30 days (Deleted users in the admin center), then removes it.
export async function deleteUser(c: Connection, id: string) {
  const r = await call(c, "DELETE", at(id));
  if (r.status !== 204 && r.status !== 200 && r.status !== 404) throw await refused(c, r, id, "delete the account");
}
