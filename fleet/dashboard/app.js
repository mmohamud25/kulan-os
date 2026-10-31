// The Kulan Fleet dashboard (index.html's page), kept in its own file so the page's
// Content Security Policy can forbid scripts written into the page.
import { sha512crypt, newSalt, setupCode } from "./sha512crypt.js";
import { RemoteHelp } from "./remotehelp.js";
const { createClient } = window.supabase;

const SUPABASE_URL = "https://xegnwqxpegznsufulclx.supabase.co";
const PUBLISHABLE_KEY = "sb_publishable_Z7MrJQBPYz3_CmXviZuTJA_kWNAMOA1";  // public by design
const API = `${SUPABASE_URL}/functions/v1/fleet-admin`;
const FLEET_SERVER = `${SUPABASE_URL}/functions/v1/fleet`;
const ACTIONS = [["refresh", "Report now"], ["install-updates", "Install updates"],
                 ["compliance-on", "Turn compliance mode on"], ["lock-screen", "Lock the screen"],
                 ["restart", "Restart"], ["shutdown", "Switch off"]];
const LABEL = { ...Object.fromEntries(ACTIONS), erase: "Erase", "lost-mode-on": "Turn on lost mode",
                "lost-mode-off": "Release from lost mode", "install-app": "Install an app", "remote-help": "Remote help",
                "run-fix": "Run a fix", "run-script": "Run a script", message: "Message" };
const GROUP_ACTIONS = [["refresh", "Report now"], ["install-updates", "Install updates"], ["lock-screen", "Lock the screen"],
                       ["restart", "Restart"], ["shutdown", "Switch off"],
                       ["install-app", "Install an app…"], ["run-fix", "Run a fix…"], ["run-script", "Run a script…"],
                       ["message", "Send a message…"]];
// Kulan's fixes (2.9b), the same list as the computers' fixes.py.
const FIXES = [
  ["free-space", "Free up disk space", "Removes downloaded update files, old system files and logs older than two weeks."],
  ["reset-wifi", "Reset Wi-Fi", "Turns Wi-Fi off and on and restarts the network service. The computer is offline for a few seconds."],
  ["repair-updates", "Repair updates", "Finishes interrupted installs, fixes missing pieces and refreshes the update lists."],
  ["restart-printing", "Restart printing", "Restarts the printing service and turns stopped printers back on."],
  ["check-disk", "Check the disk", "Looks for disk errors and checks the disk's health. It changes nothing."]];
const FIX_NAME = Object.fromEntries(FIXES.map(([v, t]) => [v, t]));
const JOBS = new Set(["run-fix", "run-script", "message"]);
let scripts = [], scriptsAllowed = false, scriptKey = null;
const SCREEN_LOCK = [[0, "Not set"], [2, "After 2 minutes"], [5, "After 5 minutes"], [10, "After 10 minutes"],
                     [15, "After 15 minutes"], [30, "After 30 minutes"]];
const USB = [["", "Any USB drive works"], ["approved", "Approved drives only"], ["block", "Blocked"]];
const SOURCE = { debian: "Debian package", flatpak: "Flathub app" };
const DEBIAN_NAME = /^[a-z0-9][a-z0-9+.-]{0,61}[a-z0-9+.]$/;
const FLATPAK_ID = /^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$/;
// Never blocked (the server refuses these too): Kulan OS and what it can't run without.
const PROTECTED = /^(kulan|linux-|systemd|gnome-shell|gnome-session|gdm3|mutter|apt|dpkg|libc6|bash|coreutils|sudo|login|passwd|network-manager|flatpak|gnome-software|packagekit|firmware-|grub|shim|plymouth|xdg-|dbus|polkitd|udev|init)/;
let groups = [];
const ROLE = { owner: "Owner", admin: "Admin", employee: "Employee" };
const ROLE_HELP = { employee: "A standard account", admin: "Administrator rights on the computers",
                    owner: "The Owner's account, with administrator rights" };
const USERNAME = /^[a-z][a-z0-9_-]{0,31}$/;
const RESERVED = new Set(["root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail", "news", "uucp", "proxy",
                          "www-data", "backup", "list", "irc", "nobody", "admin", "sudo", "kulan", "gdm", "systemd"]);
let people = [], isOwner = false, removedWithMailbox = [];
// Google Workspace (2.8e)
const GOOGLE_SCOPE = "https://www.googleapis.com/auth/admin.directory.user";
const MAILBOX_ADDRESS = /^[a-z0-9](?:[a-z0-9._'-]{0,62}[a-z0-9])?@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;
const MAILBOX_TODO = { suspend: "still to suspend", unsuspend: "still to unsuspend", delete: "still to delete", rename: "name still to change" };
const MAILBOX_STATE = { active: ["working", "good"], suspended: ["suspended", "warn"], deleted: ["deleted", "dim"] };
const mailNote = (out) => out?.mailbox_error ? ` The mailbox wasn't updated (${out.mailbox_error}). Retry it from their page.` : "";
const orgNow = () => data?.organisations.find((o) => o.id === $("org").value);

// A sign-in link that didn't work comes back with the reason in the address: said plainly, then cleared.
const linkError = (() => {
  const p = new URLSearchParams(location.hash.slice(1) + "&" + location.search.slice(1));
  const code = p.get("error_code"), text = p.get("error_description");
  if (!code && !text) return null;
  history.replaceState(null, "", location.pathname);
  return code === "otp_expired" || /expired|invalid/i.test(text || "")
    ? "That sign-in link has expired or was already used. Each link works once, and asking for a new one cancels the earlier ones. Ask for a new link, then use the newest email."
    : `That sign-in link didn't work (${(text || code).replace(/\+/g, " ")}). Ask for a new one.`;
})();
const supabase = createClient(SUPABASE_URL, PUBLISHABLE_KEY, { auth: { flowType: "pkce", detectSessionInUrl: true } });
const $ = (id) => document.getElementById(id);
let data = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  const days = Math.round(s / 86400);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

// What to show for an error: Kulan Fleet's own messages as they are; a lost
// connection or a sign-in service message in plain words; never a browser's
// or library's own wording.
const SIGN_IN_WORDS = [
  [/invalid (totp|mfa)|code.*(invalid|expired)|otp_expired/i, "That code didn’t work. Check the code on your phone and try again."],
  [/invalid login credentials/i, "That email and password don’t match. Try again."],
  [/email not confirmed/i, "Confirm your email first: open the link Kulan Fleet sent you."],
  [/rate limit|too many/i, "Too many tries. Wait a minute, then try again."],
  [/jwt|session.*(expired|missing)|refresh token/i, "You were signed out. Sign in again."],
];
function friendly(e) {
  if (e && e.kulan) return e.message;
  const text = String((e && e.message) || e || "");
  if (e instanceof TypeError || /failed to fetch|networkerror|load failed|network request failed/i.test(text))
    return "Can’t reach Kulan Fleet. Check your internet connection and try again.";
  for (const [pattern, words] of SIGN_IN_WORDS) if (pattern.test(text)) return words;
  console.error("Kulan Fleet:", e);
  return "Something went wrong. Try again, and if it keeps happening, reload the page.";
}

let lastToken = null;  // for ending remote help as the page closes
async function api(body) {
  const { data: { session } } = await supabase.auth.getSession();
  if (!session) throw Object.assign(new Error("You were signed out. Sign in again."), { kulan: true });
  lastToken = session.access_token;
  const res = await fetch(API, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.access_token}` }, body: JSON.stringify(body) });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) {
    const text = /^unknown op/i.test(out.error || "")
      ? "This needs a newer Kulan Fleet server, which isn’t ready yet. Try again later."
      : out.error ? out.error[0].toUpperCase() + out.error.slice(1)  // starts in lower case
        : "Kulan Fleet’s server had a problem. Try again in a minute.";
    const err = new Error(text);
    err.kulan = true;
    err.twoStep = out.two_step;
    if (out.two_step) setTimeout(() => twoStepNeeded(out.two_step), 0);
    throw err;
  }
  return out;
}

// --- An authenticator app for Kulan Fleet admins (2.8d; Supabase MFA) -------------------------
let twoStepOpen = false;
function twoStepNeeded(kind) {
  if (twoStepOpen) return;
  twoStepOpen = true;
  (kind === "code" ? askAuthenticatorCode() : setUpAuthenticator(true)).finally(() => { twoStepOpen = false; });
}
function codeDialog(title, intro, extra, onCode) {
  return new Promise((resolve) => {
    const code = el("input", { inputmode: "numeric", autocomplete: "one-time-code", maxlength: "6", "aria-label": "6-digit code" });
    const err = errorLine();
    const go = el("button", { type: "button", class: "primary", onclick: async () => {
      if (!/^\d{6}$/.test(code.value.trim())) { err.textContent = "Enter the 6-digit code."; return; }
      go.disabled = true;
      try { await onCode(code.value.trim()); $("dlg").close(); await load(); resolve(); }
      catch (e) { err.textContent = friendly(e); go.disabled = false; }
    } }, "Continue");
    code.addEventListener("keydown", (ev) => { if (ev.key === "Enter") go.click(); });
    dialog(title, [el("p", { class: "note" }, intro), ...extra, field("6-digit code", code), err],
      [el("button", { type: "button", onclick: () => { $("dlg").close(); resolve(); } }, "Cancel"), go]);
    $("dlg").addEventListener("close", () => resolve(), { once: true });
    code.focus();
  });
}
async function askAuthenticatorCode() {
  const { data: f, error } = await supabase.auth.mfa.listFactors();
  const totp = (f?.totp || []).find((x) => x.status === "verified");
  if (error || !totp) { $("app-msg").textContent = error ? friendly(error) : "No authenticator app is set up for this email."; return; }
  return codeDialog("Your authenticator app's code", "Enter the 6-digit code from your authenticator app.", [],
    async (code) => {
      const { error: e } = await supabase.auth.mfa.challengeAndVerify({ factorId: totp.id, code });
      if (e) throw e;
    });
}
async function setUpAuthenticator(required) {
  const { data: f } = await supabase.auth.mfa.listFactors();
  for (const x of (f?.all || []).filter((x) => x.factor_type === "totp" && x.status !== "verified")) {
    await supabase.auth.mfa.unenroll({ factorId: x.id });  // a set-up that was never finished
  }
  const { data, error } = await supabase.auth.mfa.enroll({ factorType: "totp", issuer: "Kulan Fleet",
    friendlyName: `Kulan Fleet ${new Date().toISOString().slice(0, 16).replace("T", " ")}` });
  if (error) { $("app-msg").textContent = friendly(error); return; }
  const secret = data.totp.secret || "";
  const extra = [data.totp.qr_code ? el("img", { src: data.totp.qr_code, alt: "QR code to scan", width: "200", height: "200" }) : "",
                 el("p", { class: "small", style: "font-family: ui-monospace, monospace" }, secret.replace(/(.{4})/g, "$1 ").trim())];
  return codeDialog("Set up an authenticator app",
    (required ? "Your organisation requires an authenticator app for admins. " : "") +
    "Scan the QR code with an authenticator app on your phone, or type the key, then enter the code it shows. If you lose your phone, the Owner can reset it.",
    extra, async (code) => {
      const { error: e } = await supabase.auth.mfa.challengeAndVerify({ factorId: data.id, code });
      if (e) throw e;
    });
}
// Before something that needs the code (scripts): asked first, so nothing typed is lost.
async function codeFirst() {
  const level = async () => {
    const { data: aal, error } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
    if (error) throw error;
    return aal;
  };
  let now;
  try { now = await level(); } catch { return true; }  // can't tell here: the server asks for it if it's needed
  if (now?.currentLevel === "aal2") return true;
  twoStepOpen = true;
  try { await (now?.nextLevel === "aal2" ? askAuthenticatorCode() : setUpAuthenticator(false)); }
  finally { twoStepOpen = false; }
  try { return (await level())?.currentLevel === "aal2"; } catch { return true; }
}
async function myAuthenticator() {
  const { data: f } = await supabase.auth.mfa.listFactors();
  const verified = (f?.totp || []).filter((x) => x.status === "verified");
  if (!verified.length) return setUpAuthenticator(false);
  dialog("Your authenticator app", [el("p", {}, "Kulan Fleet asks for its code after your email code. Your organisation may require it, so remove it only to set it up again.")],
    [closeButton("Close"), el("button", { type: "button", class: "danger", onclick: async () => {
      for (const x of verified) await supabase.auth.mfa.unenroll({ factorId: x.id });
      $("dlg").close();
      $("app-msg").textContent = "Authenticator app removed.";
    } }, "Remove it")]);
}

function show(signedIn) {
  $("signin").classList.toggle("hidden", signedIn);
  $("app").classList.toggle("hidden", !signedIn);
}

// --- Sections (2.10): #computers, #people... in the address, so Back works and links can be shared.
const VIEWS = ["computers", "people", "groups", "policy", "scripts", "activity", "settings"];
let view = "computers";
function go(next, remember = true) {
  if (!VIEWS.includes(next)) next = "computers";
  view = next;
  for (const v of document.querySelectorAll("section.view")) v.classList.toggle("hidden", v.dataset.view !== next);
  for (const a of document.querySelectorAll(".nav[data-view]")) {
    const on = a.dataset.view === next;
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  if (remember && location.hash !== `#${next}`) history.pushState(null, "", `#${next}`);
  document.title = `${next[0].toUpperCase() + next.slice(1)} · Kulan Fleet`;
}
for (const a of document.querySelectorAll(".nav[data-view]")) {
  a.addEventListener("click", (ev) => { ev.preventDefault(); go(a.dataset.view); scrollTo(0, 0); });
}
addEventListener("popstate", () => go(location.hash.slice(1), false));

// Messages appear at the bottom for a few seconds (anything that sets #app-msg's text).
let msgTimer = null;
new MutationObserver(() => {
  clearTimeout(msgTimer);
  if ($("app-msg").textContent) msgTimer = setTimeout(() => { $("app-msg").textContent = ""; }, 8000);
}).observe($("app-msg"), { childList: true, characterData: true, subtree: true });

async function load() {
  $("app-msg").textContent = "";
  try {
    data = await api({ op: "overview" });
  } catch (e) {
    $("app-msg").textContent = friendly(e);
    return;
  }
  $("who").textContent = data.email;
  $("me-initials").textContent = (data.email.split("@")[0].match(/[a-z0-9]/gi) || ["?"]).slice(0, 2).join("").toUpperCase();
  $("new-org").classList.toggle("hidden", !data.global);
  const select = $("org"), keep = select.value;
  select.replaceChildren(...data.organisations.map((o) => el("option", { value: o.id }, o.name)));
  if (keep && data.organisations.some((o) => o.id === keep)) select.value = keep;
  render();
  await loadGroups();  // before people: their dialogs offer the groups
  await loadPeople();
  await loadScripts();
  helpFromLink();
}

async function loadPeople() {
  const org = $("org").value;
  people = [];
  isOwner = false;
  if (!org) { renderPeople(); renderAdmins(null); renderMail(null); return; }
  try {
    const out = await api({ op: "people", organisation_id: org });
    people = out.people || [];
    removedWithMailbox = out.removed_with_mailbox_todo || [];
    isOwner = !!out.is_owner;
    renderPeople();
    renderAdmins(await api({ op: "admins", organisation_id: org }));
    renderMail(await api({ op: "mail", organisation_id: org }));
  } catch (e) { $("app-msg").textContent = friendly(e); }
}

function signInTag(p) {
  if (!p.enabled) return el("span", { class: "tag bad" }, "Disabled");
  if (p.must_change) return el("span", { class: "tag dim" }, "Setup code not used");
  if ((p.two_step || []).length) return el("span", { class: "tag good" }, "Two-step on");
  return el("span", { class: "tag good" }, "Own password");
}
const TINTS = ["#c7d7f2", "#ccefe9", "#dbe7fb", "#fbe9c8", "#e6dcfb", "#fbdde2", "#d9f0d3"];
function avatar(name, key) {
  const parts = String(name || "?").trim().split(/\s+/);
  const initials = ((parts[0]?.[0] || "?") + (parts.length > 1 ? parts[parts.length - 1][0] : parts[0]?.[1] || "")).toUpperCase();
  let h = 0;
  for (const c of String(key || name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return el("span", { class: "avatar-sm", style: `background: ${TINTS[h % TINTS.length]}`, "aria-hidden": "true" }, initials);
}
function lastUsed(p) {
  const s = (p.signed_in || [])[0];
  return !s ? "Never" : s.now ? "Signed in now" : agoSeconds(s.last);
}
const PEOPLE_TABS = [["all", "Everyone", () => true], ["admins", "Admins", (p) => p.role !== "employee"],
                     ["new", "Haven't signed in yet", (p) => p.enabled && p.must_change], ["disabled", "Disabled", (p) => !p.enabled]];
let peopleTab = "all";
function renderPeople() {
  $("n-people").textContent = people.length ? String(people.length) : "";
  const find = $("find-people").value.trim().toLowerCase();
  const tabs = PEOPLE_TABS.map(([id, label, test]) => {
    const n = people.filter(test).length;
    const b = el("button", { type: "button", class: `tab${id === peopleTab ? " on" : ""}`, role: "tab", "aria-selected": String(id === peopleTab),
                             onclick: () => { peopleTab = id; renderPeople(); } }, label, el("span", { class: "n" }, String(n)));
    return id === "all" || n ? b : null;
  }).filter(Boolean);
  $("people-tabs").replaceChildren(...tabs);
  const test = (PEOPLE_TABS.find(([id]) => id === peopleTab) || PEOPLE_TABS[0])[2];
  const shown = people.filter(test).filter((p) => !find || [p.full_name, p.username, p.email].some((x) => (x || "").toLowerCase().includes(find)));
  const rows = shown.map((p) => {
    const n = (p.on || []).length;
    const open = (ev) => { ev?.preventDefault(); openPerson(p); };
    return el("tr", {},
      el("td", {}, el("div", { class: "who-cell" }, avatar(p.full_name || p.username, p.username),
        el("div", {}, el("div", { class: "name" }, el("a", { href: "#", onclick: open }, p.full_name || p.username)),
                      el("div", { class: "small" }, p.email || "No work email")))),
      el("td", { class: "mono" }, p.username),
      el("td", {}, el("span", { class: `tag ${p.role === "employee" ? "dim" : "navy"}` }, ROLE[p.role] || p.role)),
      el("td", {}, signInTag(p), p.mailbox_todo ? el("span", { class: "tag bad", style: "margin-left: 4px" }, `Mailbox ${MAILBOX_TODO[p.mailbox_todo] || "needs a retry"}`) : ""),
      el("td", { class: "hide-sm" }, p.all_computers ? "All computers" : [
        ...groups.filter((g) => (p.groups || []).includes(g.id)).map((g) => `${g.name} group`),
        ...((p.computers || []).length ? [`${p.computers.length} computer${p.computers.length === 1 ? "" : "s"}`] : [])].join(", ") || "None yet"),
      el("td", { class: "hide-sm small" }, lastUsed(p)),
      el("td", { style: "text-align: right" }, el("button", { onclick: open }, "Manage…")));
  });
  for (const p of peopleTab === "all" ? removedWithMailbox : []) {
    rows.push(el("tr", {}, el("td", { colspan: "6" }, `${p.username} (removed): mailbox ${p.mailbox} ${MAILBOX_TODO[p.mailbox_todo] || ""} · ${p.mailbox_error || ""}`),
      el("td", {}, mailboxButton(p, "retry", "Retry"))));
  }
  $("people").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "7", class: "empty" },
    people.length ? "Nobody matches." : "Nobody yet. Add someone to give them a setup code."))]));
  $("add-person").disabled = !$("org").value;
  if (data) renderComputers();  // "Used by" shows people's names
}
$("find-people").addEventListener("input", renderPeople);
$("to-admins").addEventListener("click", () => go("settings"));

function renderAdmins(out) {
  if (!out) { $("admins").replaceChildren(); return; }
  const owner = out.owner;
  $("admins-sub").textContent = "Admins manage computers and people. " +
    (out.is_owner ? "As the Owner, you choose who they are." : "Only the Owner can change who they are.");
  const items = [el("li", {}, el("span", { class: "grow-text" }, el("b", {}, owner || "No Owner yet")), el("span", { class: "tag navy" }, "Owner"))];
  for (const a of out.admins || []) {
    if (a.email === owner) continue;
    items.push(el("li", {}, el("span", { class: "grow-text" }, el("b", {}, a.email), el("span", { class: "small" }, ` · admin since ${ago(a.created_at)}`)),
      out.is_owner ? el("button", { onclick: () => changeAdmin("remove-admin", a.email) }, "Remove") : "",
      out.is_owner ? el("button", { onclick: async () => {
        if (!await askConfirm(`Reset ${a.email}'s authenticator app? They set it up again at their next sign-in. Make sure the request really comes from them.`)) return;
        try { await api({ op: "reset-admin-two-step", organisation_id: $("org").value, email: a.email }); $("app-msg").textContent = `${a.email} will set it up at their next sign-in.`; }
        catch (e) { $("app-msg").textContent = friendly(e); }
      } }, "Reset authenticator…") : ""));
  }
  const org = data?.organisations.find((o) => o.id === $("org").value);
  const require = el("input", { type: "checkbox", "aria-label": "Require an authenticator app for admins" });
  require.checked = !!org?.admin_two_step;
  require.disabled = !out.is_owner;
  require.addEventListener("change", async () => {
    try {
      await api({ op: "set-admin-two-step", organisation_id: org.id, required: require.checked });
      org.admin_two_step = require.checked;
      $("app-msg").textContent = require.checked ? "Authenticator app required for admins." : "An authenticator app is no longer required.";
    } catch (e) { require.checked = !require.checked; $("app-msg").textContent = friendly(e); }
  });
  items.push(el("li", {}, el("label", {}, require, " Require an authenticator app for admins. Set up yours first.")));
  $("admins").replaceChildren(...items);
  $("add-admin").classList.toggle("hidden", !out.is_owner);
  $("set-owner").classList.toggle("hidden", !data?.global);
}

async function changeAdmin(op, email) {
  try { await api({ op, organisation_id: $("org").value, email }); await loadPeople(); }
  catch (e) { $("app-msg").textContent = friendly(e); }
}

// --- dialogs -----------------------------------------------------------------------------
function dialog(title, body, buttons) {
  $("dlg-title").textContent = title;
  $("dlg-body").replaceChildren(...body);
  $("dlg-actions").replaceChildren(...buttons);
  if (!$("dlg").open) $("dlg").showModal();
}
const closeButton = (label = "Cancel") => el("button", { type: "button", onclick: () => $("dlg").close() }, label);
const field = (label, input) => el("label", { class: "field" }, el("span", {}, label), input);
const errorLine = () => el("p", { class: "small error", role: "status" });

// Asking before something happens: the page's own dialog, never the browser's
// confirm() or prompt() boxes. askConfirm resolves true or false; askText
// resolves the typed text, or null when cancelled.
const VERBS = { Reset: "Reset", Remove: "Remove", Disable: "Disable", Erase: "Erase", Disconnect: "Disconnect",
  Restart: "Restart", Stop: "Stop", Allow: "Allow", Switch: "Switch off", Turn: "Turn on", Lost: "Start again" };
const DANGER = /^(Reset|Remove|Disable|Erase|Disconnect|Switch off|Turn on|Start again)$/;
// Its own dialog, over any dialog already open (so that one keeps what's in it).
function ask(text, input, okLabel, fallback) {
  return new Promise((resolve) => {
    const d = $("ask-dlg");
    const label = okLabel || VERBS[text.split(/[\s’']/)[0]] || fallback;
    let answer = input ? null : false;
    const ok = el("button", { type: "button", class: DANGER.test(label) ? "danger" : "primary",
      onclick: () => { answer = input ? input.value : true; d.close(); } }, label);
    const cancel = el("button", { type: "button", onclick: () => d.close() }, "Cancel");
    if (input) input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ok.click(); } });
    $("ask-dlg-body").replaceChildren(el("p", {}, text), ...(input ? [input] : []));
    $("ask-dlg-actions").replaceChildren(cancel, ok);
    d.addEventListener("close", () => resolve(answer), { once: true });
    d.showModal();
    (input || ok).focus();
  });
}
const askConfirm = (text, okLabel) => ask(text, null, okLabel, "Continue");
const askText = (text, value = "", okLabel) =>
  ask(text, el("input", { type: "text", value, "aria-label": text }), okLabel, "OK");

function appProblem(source, id, blocking) {
  if (source === "debian" && !DEBIAN_NAME.test(id)) return "Use lowercase letters, numbers and + . - (for example vlc)";
  if (source === "flatpak" && (id.length > 255 || !FLATPAK_ID.test(id))) return "A Flathub app id has at least three parts (for example org.videolan.VLC)";
  if (blocking && source === "debian" && PROTECTED.test(id)) return "That's part of Kulan OS and can't be blocked";
  return null;
}

function policySummary(p) {
  const parts = [["auto_updates", "automatic updates"], ["firewall", "firewall"], ["require_encryption", "disk encryption"]]
    .filter(([k]) => p[k]).map(([, t]) => t);
  if (p.screen_lock_minutes) parts.push(`lock after ${p.screen_lock_minutes} min`);
  if (p.usb) parts.push(p.usb === "approved" ? "approved USB drives only" : "USB blocked");
  if (p.two_step) parts.push(`two-step sign-in for ${p.two_step === "everyone" ? "everyone" : "admins"}`);
  if (p.required_apps?.length) parts.push(`${p.required_apps.length} app${p.required_apps.length === 1 ? "" : "s"} required`);
  if (p.blocked_apps?.length) parts.push(`${p.blocked_apps.length} app${p.blocked_apps.length === 1 ? "" : "s"} blocked`);
  const text = parts.join(", ");
  return text ? text[0].toUpperCase() + text.slice(1) : "No policy of its own";
}

// A list of apps to add to and remove from: {node, value()}.
function appList(title, apps, blocking) {
  let list = (apps || []).map((a) => ({ ...a }));
  const items = el("ul", { class: "alerts" });
  const source = el("select", { "aria-label": `${title}: from` }, el("option", { value: "debian" }, SOURCE.debian), el("option", { value: "flatpak" }, SOURCE.flatpak));
  const input = el("input", { placeholder: "vlc, or org.videolan.VLC", "aria-label": `${title}: app`, autocomplete: "off", spellcheck: "false" });
  const err = el("span", { class: "small error" });
  const show = () => items.replaceChildren(...(list.length ? list.map((a) => el("li", {}, el("b", {}, a.id), ` · ${SOURCE[a.source]} `,
    el("button", { type: "button", onclick: () => { list = list.filter((x) => x !== a); show(); } }, "Remove")))
    : [el("li", { class: "small" }, "None")]));
  const add = () => {
    const id = input.value.trim();
    if (!id) return true;
    const problem = appProblem(source.value, id, blocking);
    err.textContent = problem || "";
    if (problem) return false;
    if (!list.some((a) => a.source === source.value && a.id === id)) list.push({ source: source.value, id });
    input.value = "";
    show();
    return true;
  };
  input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); add(); } });
  show();
  return { node: el("div", {}, el("h4", {}, title), el("p", { class: "note" }, blocking
             ? "Removed from the computers and kept off. A Debian package stays if removing it would remove other packages."
             : "Installed from Debian or Flathub, and reinstalled if someone removes it."),
           items, el("div", { class: "row" }, source, input, el("button", { type: "button", onclick: add }, "Add"), err)),
           value: () => (add() ? list : null) };
}

// The policy's fields: {node, value()} (value() is null while an app typed in isn't valid).
function policyForm(p) {
  const box = (key, label) => { const b = el("input", { type: "checkbox" }); b.checked = !!p[key]; return [key, b, el("label", {}, b, label)]; };
  const boxes = [box("auto_updates", "Automatic updates"), box("firewall", "Firewall"), box("require_encryption", "Require disk encryption")];
  const lock = el("select", { "aria-label": "Screen lock" }, ...SCREEN_LOCK.map(([m, t]) => el("option", { value: String(m) }, t)));
  lock.value = String(p.screen_lock_minutes || 0);
  const usb = el("select", { "aria-label": "USB drives" }, ...USB.map(([v, t]) => el("option", { value: v }, t)));
  usb.value = p.usb || "";
  const two = el("select", { "aria-label": "Two-step sign-in" }, el("option", { value: "" }, "Not required"),
    el("option", { value: "admins" }, "For admins and the Owner"), el("option", { value: "everyone" }, "For everyone"));
  two.value = p.two_step || "";
  const required = appList("Required apps", p.required_apps, false);
  const blocked = appList("Blocked apps", p.blocked_apps, true);
  return {
    node: el("div", {}, el("div", { class: "policy-switches" }, ...boxes.map(([, , l]) => l)),
             el("div", { class: "policy-fields" }, el("label", {}, "Screen lock", lock), el("label", {}, "USB drives", usb),
                el("label", {}, "Two-step sign-in", two)),
             el("p", { class: "note" }, "Keyboards, mice and other USB devices always work."), required.node, blocked.node),
    value: () => {
      const req = required.value(), blk = blocked.value();
      if (req === null || blk === null) return null;
      const out = Object.fromEntries(boxes.filter(([, b]) => b.checked).map(([k]) => [k, true]));
      if (Number(lock.value)) out.screen_lock_minutes = Number(lock.value);
      if (usb.value) out.usb = usb.value;
      if (two.value) out.two_step = two.value;
      if (req.length) out.required_apps = req;
      if (blk.length) out.blocked_apps = blk;
      return out;
    },
  };
}
let orgPolicy = null;

// --- Security and activity (2.8c) -----------------------------------------------------------
const agoSeconds = (t) => t ? ago(new Date(t * 1000).toISOString()) : "never";
const dayLabel = (iso) => iso ? new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }) : "";
const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + "s")}`;

// The weekly report as [heading, [[line, detail, good|bad|dim]]] (the same lines as the app's).
function weeklySections(s, prev) {
  const c = s.computers || {}, p = s.people || {}, a = s.activity || {}, rv = s.review || {};
  const pc = prev?.computers || {}, pp = prev?.people || {};
  const total = Number(c.total || 0);
  // How many: the server's count (its lists name at most 50), or the list's length.
  const n = (src, key) => key in (src.counts || {}) ? Number(src.counts[key] || 0)
    : Array.isArray(src[key]) ? src[key].length : Number(src[key] || 0);
  const since = (key, now, before) => {
    if (!prev || !(key in before || key in (before.counts || {}))) return "";
    const d = now - n(before, key);
    return d === 0 ? "same as last week" : `${d > 0 ? "+" : "−"}${Math.abs(d)} since last week`;
  };
  const names = (src, key) => {
    const lst = src[key] || [];
    const text = lst.map((x) => typeof x === "string" ? x :
      `${x.hostname || ""}${x.count ? ` (${x.count})` : ""}${x.failing ? ` (${x.failing} failing)` : ""}`).join(", ");
    const extra = n(src, key) - lst.length;
    return extra > 0 && text ? `${text} and ${extra} more` : text;
  };
  const detail = (...parts) => parts.filter(Boolean).join(" · ");
  const problem = (none, some, key, src, before) => {
    const k = n(src, key);
    return k === 0 ? [none, detail(since(key, k, before)), "good"] : [some(k), detail(names(src, key), since(key, k, before)), "bad"];
  };
  const comp = [
    [plural(total, "computer"), `${c.reported_this_week || 0} reported this week`, "dim"],
    [`Up to date: ${c.up_to_date || 0} of ${total}`, "", (c.up_to_date || 0) === total ? "good" : "dim"],
    problem("No security updates waiting", (k) => `Security updates waiting on ${plural(k, "computer")}`, "security_waiting", c, pc),
    problem("No restart needed", (k) => `Restart needed on ${plural(k, "computer")}`, "restart_needed", c, pc),
    [`Encrypted: ${c.encrypted || 0} of ${total}`, n(c, "not_encrypted") ? `Not encrypted: ${names(c, "not_encrypted")}` : "", n(c, "not_encrypted") ? "bad" : "good"],
    problem("Firewall on everywhere", (k) => `Firewall off on ${plural(k, "computer")}`, "firewall_off", c, pc),
    [`Compliance passing: ${c.compliance_passing || 0} of ${total}`, names(c, "compliance_failing"), n(c, "compliance_failing") ? "bad" : "good"],
    problem("Every policy met", (k) => `Policy not met on ${plural(k, "computer")}`, "policy_not_met", c, pc),
    problem("Every computer reported this week", (k) => `Not seen for a week: ${plural(k, "computer")}`, "not_seen_week", c, pc),
  ];
  if (n(c, "lost_mode")) comp.push([`In lost mode: ${plural(n(c, "lost_mode"), "computer")}`, names(c, "lost_mode"), "bad"]);
  if (c.versions && Object.keys(c.versions).length) comp.push(["Kulan OS versions", Object.entries(c.versions).sort().reverse().map(([v, k]) => `${v}: ${plural(k, "computer")}`).join(", "), "dim"]);
  const people = [
    [plural(Number(p.total || 0), "person", "people"), since("total", Number(p.total || 0), pp), "dim"],
    [`Administrator rights: ${n(p, "admins")}`, names(p, "admins"), "dim"],
    problem("Nobody disabled", (k) => `Disabled: ${k}`, "disabled", p, pp),
    problem("Every setup code used", (k) => `Setup code not used for a week: ${k}`, "setup_code_unused", p, pp),
  ];
  if (p.two_step) people.push(problem("Everyone required has two-step sign-in", (k) => `Two-step sign-in not set up: ${k}`, "two_step_missing", p, pp));
  if (n(p, "mailbox_problems")) people.push([`Mailboxes needing attention: ${n(p, "mailbox_problems")}`,
    (p.mailbox_problems || []).map((x) => `${x.username} (${MAILBOX_TODO[x.todo] || x.todo})`).join(", "), "bad"]);
  const activity = [[`${plural(Number(a.changes || 0), "change")} this week`, (a.by_kind || []).map((x) => `${x.what} ${x.count}`).join(", "), "dim"]];
  const review = [[rv.due ? "Admin-rights review due" : "Admin-rights review up to date",
                   rv.at ? `Last on ${dayLabel(rv.at)} by ${rv.by}` : "No review yet", rv.due ? "bad" : "good"]];
  return [["Computers", comp], ["People", people], ["Activity", activity], ["Admin rights", review]];
}

function sectionNodes(sections) {
  return sections.flatMap(([heading, rows]) => [el("h4", {}, heading), ...rows.map(([line, detail, kind]) =>
    el("div", { class: `report-line ${kind}` }, el("b", {}, `${{ good: "✓", bad: "!", dim: "·" }[kind] || "·"} ${line}`),
       detail ? el("span", { class: "detail" }, detail) : ""))]);
}

async function openWeekly() {
  const org = data.organisations.find((o) => o.id === $("org").value);
  if (!org) return;
  dialog(`Weekly security report for ${org.name}`, [el("p", { class: "small" }, "Loading…")], [closeButton("Close")]);
  try {
    const out = await api({ op: "weekly-report", organisation_id: org.id });
    const prev = out.previous?.summary || null;
    const sub = `Week of ${dayLabel(out.week)} · made ${dayLabel(out.made_at)}` + (prev ? ` · compared with the week of ${dayLabel(out.previous.week)}` : "");
    const nodes = sectionNodes(weeklySections(out.summary || {}, prev));
    const print = el("button", { type: "button", class: "primary", onclick: () => {
      $("print-area").replaceChildren(el("h2", {}, `Weekly security report for ${org.name}`), el("p", {}, sub),
        ...sectionNodes(weeklySections(out.summary || {}, prev)), el("p", { class: "small" }, "Made by Kulan Fleet from each computer's last report."));
      $("dlg").close();
      document.body.classList.add("printing");
      window.print();
      document.body.classList.remove("printing");
    } }, "Print or save as PDF");
    dialog(`Weekly security report for ${org.name}`, [el("p", { class: "note" }, sub), ...nodes], [closeButton("Close"), print]);
  } catch (e) {
    dialog("Weekly security report", [el("p", { class: "error" }, friendly(e))], [closeButton("Close")]);
  }
}

async function openReview() {
  const org = data.organisations.find((o) => o.id === $("org").value);
  if (!org) return;
  dialog("Admin-rights review", [el("p", { class: "small" }, "Loading…")], [closeButton("Close")]);
  let out;
  try { out = await api({ op: "access-review", organisation_id: org.id }); }
  catch (e) { dialog("Admin-rights review", [el("p", { class: "error" }, friendly(e))], [closeButton("Close")]); return; }
  const owner = !!out.is_owner;
  const keeps = [];
  const keepBox = (kind, id, can) => { const b = el("input", { type: "checkbox", "aria-label": "Keep" }); b.checked = true; b.disabled = !owner || !can; keeps.push([kind, id, b]); return b; };
  const people = (out.people || []).map((p) => {
    const si = (p.signed_in || [])[0];
    const used = si ? (si.now ? `signed in now on ${si.hostname}` : `last used ${agoSeconds(si.last)} on ${si.hostname}`) : "not used yet";
    const where = p.where === "all" ? "all computers" : (p.where || []).join(", ") || "no computers yet";
    return el("label", {}, keepBox("person", p.id, p.role !== "owner"), " Keep ", el("b", {}, `${p.full_name || p.username} (${p.username})`),
      el("span", { class: "small" }, ` · ${ROLE[p.role] || p.role} · ${where} · ${used}`));
  });
  const admins = (out.admins || []).map((a) => el("label", {}, keepBox("admin", a.email, !a.owner), " Keep ", el("b", {}, a.email),
    el("span", { class: "small" }, ` · ${a.owner ? "the Owner · " : ""}admin since ${dayLabel(a.created_at)}`)));
  const err = errorLine();
  const finish = el("button", { type: "button", class: "primary", onclick: async () => {
    const removePeople = keeps.filter(([k, , b]) => k === "person" && !b.checked).map(([, id]) => id);
    const removeAdmins = keeps.filter(([k, , b]) => k === "admin" && !b.checked).map(([, id]) => id);
    if ((removePeople.length || removeAdmins.length) && !await askConfirm(`Remove administrator rights from ${removePeople.length + removeAdmins.length}? People keep their accounts as employees. The review is saved in the activity log.`)) return;
    try {
      const r = await api({ op: "review-access", organisation_id: org.id, remove_people: removePeople, remove_admins: removeAdmins });
      $("dlg").close();
      $("app-msg").textContent = `Review saved: ${r.kept} kept, ${r.removed} removed.`;
      await load();
    } catch (e) { err.textContent = friendly(e); }
  } }, "Finish review");
  finish.disabled = !owner;
  const last = out.last_review;
  dialog("Admin-rights review", [
    el("p", { class: "note" }, `${out.due ? "Review due. " : ""}${last ? `Last reviewed ${dayLabel(last.reviewed_at)} by ${last.reviewed_by}.` : "Not reviewed yet."} Untick anyone who no longer needs administrator rights, then Finish review.${owner ? "" : " Only the Owner can finish a review."}`),
    el("h4", {}, "On the computers"), el("div", { class: "checks" }, ...(people.length ? people : [el("span", { class: "small" }, "Nobody")])),
    el("h4", {}, "In Kulan Fleet"), el("div", { class: "checks" }, ...admins),
    ...((out.all_organisations_admins || []).length ? [el("h4", {}, "Kulan Fleet admins for every organisation"),
      el("p", { class: "small" }, out.all_organisations_admins.map((a) => a.email).join(", "))] : []),
    err], [closeButton(), finish]);
}

// A cell of the CSV export; text a spreadsheet would read as a formula starts with '.
function csvCell(v) {
  let t = v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v);
  if (/^[=+\-@\t\r]/.test(t)) t = "'" + t;
  return /[",\n\r]/.test(t) ? `"${t.replaceAll('"', '""')}"` : t;
}
function logCsv(entries) {
  const rows = [["When (UTC)", "Who", "What", "Computer", "Details"],
    ...entries.map((e) => [e.at || "", e.email || "", e.what || "", e.hostname || "", e.detail ?? ""])];
  return rows.map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function askInstallApp(where, body) {
  const source = el("select", {}, el("option", { value: "debian" }, SOURCE.debian), el("option", { value: "flatpak" }, SOURCE.flatpak));
  const input = el("input", { placeholder: "vlc, or org.videolan.VLC", autocomplete: "off", spellcheck: "false" });
  const err = errorLine();
  dialog(`Install an app on ${where}`, [
    el("p", { class: "note" }, "Installs once, at the next report. To keep it installed, add it to a policy's required apps."),
    field("From", source), field("App", input), err],
    [closeButton(), el("button", { type: "button", class: "primary", onclick: async () => {
      const app = { source: source.value, id: input.value.trim() };
      const problem = appProblem(app.source, app.id, false);
      if (!app.id || problem) { err.textContent = problem || "Enter an app."; return; }
      try {
        const out = await api({ ...body, type: "install-app", payload: app });
        $("dlg").close();
        $("app-msg").textContent = `${app.id} will be installed on ${where}${out.count ? ` (${out.count} computers)` : ""}.`;
        await load();
      } catch (e) { err.textContent = friendly(e); }
    } }, "Install")]);
  input.focus();
}

async function loadGroups() {
  const org = $("org").value;
  groups = [];
  if (!org) { renderGroups(); $("drives").replaceChildren(); return; }
  try {
    groups = (await api({ op: "groups", organisation_id: org })).groups || [];
    renderGroups();
    renderDrives(await api({ op: "drives", organisation_id: org }));
  } catch (e) { $("app-msg").textContent = friendly(e); }
}

function orgDevices() { return data.devices.filter((d) => d.organisation_id === $("org").value && !d.left_at); }

function renderGroups() {
  const live = new Set(orgDevices().map((d) => d.id));
  const rows = groups.map((g) => el("tr", {},
    el("td", {}, el("a", { href: "#", onclick: (ev) => { ev.preventDefault(); openGroup(g); } }, g.name)),
    el("td", {}, String((g.devices || []).filter((i) => live.has(i)).length)),
    el("td", { class: "small" }, policySummary(g.policy || {})),
    el("td", {}, el("div", { class: "row" }, el("button", { onclick: () => openGroup(g) }, "Manage…"),
                    el("button", { onclick: () => openGroupNetwork(g) }, "Wi-Fi and printers…")))));
  $("groups").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "4", class: "empty" },
    "No groups yet. Add one, for example Office or Finance."))]));
  $("add-group").disabled = !$("org").value;
  $("n-groups").textContent = groups.length ? String(groups.length) : "";
  if (data) renderComputers();  // a computer's groups, in its details
}

function openGroup(g) {
  const name = el("input", { value: g.name, maxlength: "60" });
  const form = policyForm(g.policy || {});
  const boxes = orgDevices().map((d) => {
    const b = el("input", { type: "checkbox", value: d.id });
    b.checked = (g.devices || []).includes(d.id);
    return [b, el("label", {}, b, ` ${d.hostname || "(no name)"}`)];
  });
  const err = errorLine();
  const pick = el("select", { "aria-label": `Ask ${g.name}'s computers to` }, el("option", { value: "" }, "Choose…"),
    ...GROUP_ACTIONS.filter(([v]) => v !== "run-script" || orgNow()?.allow_scripts).map(([v, t]) => el("option", { value: v }, t)));
  const send = el("button", { type: "button", onclick: async () => {
    if (!pick.value) return;
    if (pick.value === "install-app") { askInstallApp(`${g.name}'s computers`, { op: "group-action", group_id: g.id }); return; }
    if (JOBS.has(pick.value)) { askJob(pick.value, `${g.name}'s computers`, { op: "group-action", group_id: g.id }); return; }
    if ((pick.value === "restart" || pick.value === "shutdown") &&
        !await askConfirm(`${LABEL[pick.value]} every computer in ${g.name}? Anyone using them gets 5 minutes' notice.`)) return;
    try {
      const out = await api({ op: "group-action", group_id: g.id, type: pick.value });
      err.textContent = `Asked ${out.count} computer${out.count === 1 ? "" : "s"}.`;
      await load();
    } catch (e) { err.textContent = friendly(e); }
  } }, "Send");
  const save = el("button", { type: "button", class: "primary", onclick: async () => {
    const policy = form.value();
    if (policy === null) { err.textContent = "One of the apps isn't valid. Fix it or remove it."; return; }
    try {
      await api({ op: "edit-group", group_id: g.id, name: name.value.trim(), policy,
                  devices: boxes.filter(([b]) => b.checked).map(([b]) => b.value) });
      $("dlg").close();
      $("app-msg").textContent = `${name.value.trim()} saved.`;
      await loadGroups();
    } catch (e) { err.textContent = friendly(e); }
  } }, "Save");
  const remove = el("button", { type: "button", class: "danger", onclick: async () => {
    const typed = await askText(`Remove ${g.name}? Its computers stop following its policy at their next report. Apps stay as they are, and USB drives work again unless another policy blocks them. Type the group's name to confirm.`);
    if (!typed || typed.trim() !== g.name) return;
    try { await api({ op: "remove-group", group_id: g.id, confirm: g.name }); $("dlg").close(); await loadGroups(); }
    catch (e) { err.textContent = friendly(e); }
  } }, "Remove…");
  dialog(g.name, [field("Name", name), el("h4", {}, "Policy"), el("p", { class: "note" }, "Adds to the organisation's policy. The stricter rule wins."),
    form.node, el("h4", {}, "Computers"), el("div", { class: "checks" }, ...(boxes.length ? boxes.map(([, l]) => l) : [el("span", { class: "small" }, "No computers yet")])),
    el("h4", {}, "Ask all its computers to"), el("div", { class: "row" }, pick, send), err],
    [remove, closeButton("Close"), save]);
}

function renderDrives(out) {
  const approved = (out.approved || []).map((d) => el("li", {}, el("b", {}, d.name), ` · ${d.vendor_id}:${d.product_id} · serial ${d.serial} · added by ${d.created_by || "–"} `,
    el("button", { onclick: async () => {
      if (!await askConfirm(`Stop approving ${d.name}?`)) return;
      try { await api({ op: "remove-drive", drive_id: d.id }); await loadGroups(); } catch (e) { $("app-msg").textContent = friendly(e); }
    } }, "Remove")));
  const seen = (out.seen || []).map((d) => el("li", {}, el("b", {}, d.name || "USB drive"), ` · ${d.vendor_id}:${d.product_id} · serial ${d.serial} · on ${(d.computers || []).join(", ")} `,
    el("button", { class: "primary", onclick: async () => {
      const name = await askText("A name you'll recognise for this drive, for example Finance backup drive. Other drives of the same model aren't approved.", d.name || "");
      if (!name || !name.trim()) return;
      try { await api({ op: "approve-drive", organisation_id: $("org").value, name: name.trim(), vendor_id: d.vendor_id, product_id: d.product_id, serial: d.serial }); await loadGroups(); }
      catch (e) { $("app-msg").textContent = friendly(e); }
    } }, "Approve…")));
  $("drives").replaceChildren(
    el("li", { class: "small" }, approved.length ? "Approved:" : "No approved drives yet."), ...approved,
    el("li", { class: "small" }, seen.length ? "Seen on computers, not approved:" : "No other drives seen."), ...seen);
}

function computerPicker(allOn, chosen, chosenGroups = []) {
  const devices = data.devices.filter((d) => d.organisation_id === $("org").value && !d.left_at);
  const all = el("input", { type: "checkbox" });
  all.checked = allOn;
  const boxes = devices.map((d) => {
    const box = el("input", { type: "checkbox", value: d.id });
    box.checked = chosen.includes(d.id);
    return [box, el("label", {}, box, ` ${d.hostname || "(no name)"}`)];
  });
  const groupBoxes = groups.map((g) => {
    const box = el("input", { type: "checkbox", value: g.id });
    box.checked = chosenGroups.includes(g.id);
    return [box, el("label", {}, box, ` Every computer in ${g.name}, including ones added later`)];
  });
  const list = el("div", { class: "checks" }, ...groupBoxes.map(([, l]) => l), ...boxes.map(([, l]) => l));
  list.classList.toggle("hidden", allOn);
  all.addEventListener("change", () => list.classList.toggle("hidden", all.checked));
  return { nodes: [el("label", {}, all, " All the organisation's computers, including ones that join later"), list],
           value: () => ({ all_computers: all.checked, computers: boxes.filter(([b]) => b.checked).map(([b]) => b.value),
                           groups: groupBoxes.filter(([b]) => b.checked).map(([b]) => b.value) }) };
}

function suggestUsername(name) {
  const first = (name.normalize("NFKD").replace(/[^\x00-\x7f]/g, "").toLowerCase().trim().split(/\s+/)[0] || "")
    .replace(/[^a-z0-9]/g, "").replace(/^[0-9]+/, "").slice(0, 32);
  return first && !RESERVED.has(first) ? first : "";
}
function usernameProblem(u) {
  if (!USERNAME.test(u)) return "Username: use lower-case letters, digits, - or _, starting with a letter.";
  return RESERVED.has(u) ? `Username: ${u} is used by the system. Choose another.` : null;
}

function showCode(name, code, first) {
  dialog(`Setup code for ${name}`, [
    el("p", {}, `Give this to ${name} in person or by phone, not by email. At the sign-in screen of ${first ? "any computer they can use" : "their computer"}, they choose their name, type this code with its capitals and dashes, then choose a password. It works from each computer's next report. It's shown only once.`),
    el("div", { class: "code" }, code)],
    [el("button", { type: "button", onclick: () => navigator.clipboard?.writeText(code) }, "Copy code"),
     el("button", { type: "button", class: "primary", onclick: () => $("dlg").close() }, "Done")]);
}

function showRecovery(name, code) {
  if (!code) return;
  dialog(`Recovery code for ${name}`, [
    el("p", {}, "Write this down and keep it safe, away from your computer. If the Owner loses their email account, sign in with another email and enter this code under Become the Owner, at the bottom of the page. It's shown only once. Using it makes a new one."),
    el("div", { class: "code" }, code)],
    [el("button", { type: "button", onclick: () => navigator.clipboard?.writeText(code) }, "Copy code"),
     el("button", { type: "button", class: "primary", onclick: () => $("dlg").close() }, "I wrote it down")]);
}

async function hashed(code) { return sha512crypt(code, newSalt()); }

function addPerson() {
  const full = el("input", { autocomplete: "off" });
  const user = el("input", { autocomplete: "off", spellcheck: "false" });
  const email = el("input", { type: "email", autocomplete: "off" });
  const roles = ["employee", ...(isOwner ? ["admin", "owner"] : [])];
  const role = el("select", {}, ...roles.map((r) => el("option", { value: r }, ROLE[r])));
  const roleHelp = el("span", { class: "note" }, isOwner ? ROLE_HELP.employee : "Only the Owner can add admins");
  role.addEventListener("change", () => { roleHelp.textContent = ROLE_HELP[role.value]; });
  const picker = computerPicker(true, []);
  const err = errorLine();
  const domain = orgNow()?.mail_domain;
  const makeMailbox = el("input", { type: "checkbox" });
  makeMailbox.checked = !!domain;
  const address = el("input", { autocomplete: "off", spellcheck: "false" });
  const mailboxField = field("Mailbox address", address);
  const emailField = field("Work email (optional)", email);
  const showMailbox = () => { mailboxField.classList.toggle("hidden", !makeMailbox.checked); emailField.classList.toggle("hidden", !!domain && makeMailbox.checked); };
  makeMailbox.addEventListener("change", showMailbox);
  let suggested = "", suggestedAddress = "";
  const problem = () => usernameProblem(user.value) ||
    (domain && makeMailbox.checked && !MAILBOX_ADDRESS.test(address.value.trim().toLowerCase()) ? "The mailbox address doesn't look right" : null);
  const userChanged = () => {
    if (user.value !== user.value.toLowerCase()) {  // capitals typed: lower case, as the computers need
      const at = user.selectionStart;
      user.value = user.value.toLowerCase();
      user.setSelectionRange(at, at);
    }
    if (domain && (address.value === "" || address.value === suggestedAddress)) { suggestedAddress = user.value ? `${user.value}@${domain}` : ""; address.value = suggestedAddress; }
    err.textContent = user.value ? problem() || "" : "";
  };
  full.addEventListener("input", () => {
    if (user.value === "" || user.value === suggested) { suggested = suggestUsername(full.value); user.value = suggested; }
    userChanged();
  });
  user.addEventListener("input", userChanged);
  address.addEventListener("input", () => { err.textContent = problem() || ""; });
  const add = el("button", { type: "button", class: "primary", onclick: async () => {
    if (problem()) { err.textContent = problem(); return; }
    add.disabled = true;
    const code = setupCode();
    try {
      const body = { op: "add-person", organisation_id: $("org").value, username: user.value, full_name: full.value.trim(),
                     role: role.value, ...picker.value(), password_hash: await hashed(code) };
      if (domain && makeMailbox.checked) {
        body.mailbox = { action: "create", address: address.value.trim().toLowerCase() };
        body.email = body.mailbox.address;
      } else if (email.value.trim()) body.email = email.value.trim();
      const out = await api(body);
      const name = full.value.trim() || user.value;
      showCode(name, code, true);
      if (out.mailbox_password) {
        // The setup code first; the Google password after it's closed.
        $("dlg").addEventListener("close", () => showMailboxPassword(name, out.mailbox, out.mailbox_password), { once: true });
      }
      if (out.mailbox_error) $("app-msg").textContent = `Added ${user.value}, but the mailbox wasn't made: ${out.mailbox_error}`;
      await loadPeople();
    } catch (e) { err.textContent = friendly(e); add.disabled = false; }
  } }, "Add");
  dialog("Add a person", [
    el("p", { class: "note" }, "They sign in with a setup code, then choose their own password."),
    field("Full name", full), field("Username", user),
    domain ? el("label", {}, makeMailbox, ` Make a Google mailbox on ${domain}. Google asks them to choose a password at first sign-in.`) : "",
    mailboxField, emailField,
    field("Role", role), roleHelp, el("h4", {}, "Computers they can use"), ...picker.nodes, err],
    [closeButton(), add]);
  showMailbox();
  full.focus();
}

function openPerson(p) {
  const manages = isOwner || p.role === "employee";
  const full = el("input", { value: p.full_name || "" });
  const email = el("input", { type: "email", value: p.email || "" });
  const role = el("select", {}, ...["employee", "admin", "owner"].map((r) => el("option", { value: r }, ROLE[r])));
  role.value = p.role;
  role.disabled = !isOwner;
  const picker = computerPicker(!!p.all_computers, p.computers || [], p.groups || []);
  const err = errorLine();
  const on = (p.on || []).map((c) => {
    const st = c.status || "not yet";
    const kind = st.startsWith("conflict") || st.startsWith("couldn") ? "bad" : st === "not yet" ? "dim" : "good";
    return el("li", {}, `${c.hostname || "(no name)"} `, el("span", { class: `tag ${kind}` }, st === "not yet" ? "waiting for its next report" : st));
  });
  const run = async (body, done) => {
    try {
      const out = await api(body);
      if (done) done(); else $("dlg").close();
      await loadPeople();
      if (out?.mailbox_error) $("app-msg").textContent = `Saved.${mailNote(out)}`;
    } catch (e) { err.textContent = friendly(e); }
  };
  const save = el("button", { type: "button", class: "primary", onclick: () => {
    const body = { op: "edit-person", person_id: p.id, full_name: full.value.trim(), email: email.value.trim(), ...picker.value() };
    if (role.value !== p.role) body.role = role.value;
    run(body);
  } }, "Save");
  const newCode = el("button", { type: "button", onclick: async () => {
    if (!await askConfirm(`A new setup code for ${p.username}? Their password stops working at each computer's next report.`)) return;
    const code = setupCode();
    run({ op: "new-setup-code", person_id: p.id, password_hash: await hashed(code) }, () => showCode(p.full_name || p.username, code, false));
  } }, "New setup code…");
  if (!manages) newCode.disabled = true;
  const toggle = el("button", { type: "button", onclick: () => {
    const mb = p.mailbox && p.mailbox_state !== "deleted" && orgNow()?.mail_domain ? ` Their mailbox ${p.mailbox} is suspended now and unsuspended when they're enabled.` : "";
    if (p.enabled && !await askConfirm(`Disable ${p.username}? At each computer's next report their account is locked and they're signed out. Their files stay.${mb}`)) return;
    run({ op: "set-person-enabled", person_id: p.id, enabled: !p.enabled });
  } }, p.enabled ? "Disable…" : "Enable again");
  if (!manages) toggle.disabled = true;
  const remove = el("button", { type: "button", class: "danger", onclick: () => removePerson(p) }, "Remove…");
  if (!manages) remove.disabled = true;
  const signedInNow = (p.signed_in || []).some((x) => x.now);
  const message = el("button", { type: "button", onclick: () => askJob("message", p.full_name || p.username, { op: "message-person", person_id: p.id }) },
                     "Send a message…");
  if (!signedInNow) { message.disabled = true; message.title = "They aren't signed in on any computer now"; }
  dialog(p.full_name || p.username, [
    el("p", { class: "note" }, `${p.username} · ${ROLE[p.role]} · `, signInTag(p), ` · added ${ago(p.created_at)} by ${p.created_by || "–"}`),
    el("h4", {}, "On these computers"), el("ul", { class: "alerts" }, ...(on.length ? on : [el("li", { class: "small" }, "No computers yet")])),
    el("h4", {}, "Two-step sign-in"), el("ul", { class: "alerts" }, ...((p.two_step || []).length ? p.two_step.map((f) =>
      el("li", {}, `${f.label || "Second step"} · ${f.kind === "key" ? "security key" : "phone code"}${f.created_on ? ` · set up on ${f.created_on}` : ""}`))
      : [el("li", { class: "small" }, "Not set up. They sign in with a password only, and can set it up in Kulan Settings.")])),
    (p.two_step || []).length && manages ? el("button", { type: "button", onclick: () => {
      if (!await askConfirm(`Reset ${p.username}'s two-step sign-in? Their keys and codes stop working at each computer's next report, and they set up new ones.`)) return;
      run({ op: "reset-two-step", person_id: p.id });
    } }, "Reset two-step sign-in…") : "",
    ...mailboxNodes(p, manages),
    el("h4", {}, "Last used"), el("ul", { class: "alerts" }, ...((p.signed_in || []).length ? p.signed_in.slice(0, 20).map((x) =>
      el("li", {}, `${x.hostname} · ${x.now ? "signed in now" : agoSeconds(x.last)}`)) : [el("li", { class: "small" }, "Not used on any computer yet")])),
    field("Full name", full), field("Work email", email),
    field(isOwner ? "Role" : "Role (only the Owner can change it)", role),
    el("h4", {}, "Computers they can use"), ...picker.nodes,
    manages ? "" : el("p", { class: "note" }, "Only the Owner can change an admin's or the Owner's sign-in."), err],
    [message, newCode, toggle, remove, closeButton("Close"), save]);
}

function removePerson(p) {
  const files = el("input", { type: "checkbox" });
  const typed = el("input", { autocomplete: "off", spellcheck: "false" });
  const err = errorLine();
  const mailbox = p.mailbox && p.mailbox_state !== "deleted" && orgNow()?.mail_domain ? p.mailbox : null;
  const choice = el("select", { "aria-label": "Their mailbox" },
    el("option", { value: "suspend" }, "Suspend it, keeping the mail (Google may still charge for it)"),
    el("option", { value: "delete" }, "Delete it (Google can restore it for 20 days)"));
  const go = el("button", { type: "button", class: "danger", disabled: "", onclick: async () => {
    try {
      const out = await api({ op: "remove-person", person_id: p.id, remove_files: files.checked, confirm: typed.value.trim(),
                              ...(mailbox ? { mailbox: choice.value } : {}) });
      $("dlg").close();
      await loadPeople();
      if (out?.mailbox_error) $("app-msg").textContent = `${p.username} removed.${mailNote(out)}`;
    } catch (e) { err.textContent = friendly(e); }
  } }, "Remove");
  typed.addEventListener("input", () => { go.disabled = typed.value.trim() !== p.username; });
  dialog(`Remove ${p.username}?`, [
    el("p", {}, "Their account is removed from every computer at its next report."),
    el("label", {}, files, " Delete their files too"),
    el("p", { class: "note" }, "If not ticked, each computer keeps their files in /home/.kulan-left for an administrator to hand over."),
    mailbox ? field(`Their mailbox ${mailbox}`, choice) : "",
    field(`Type ${p.username} to confirm`, typed), err],
    [closeButton(), go]);
}

// --- Google Workspace mailboxes (2.8e) -----------------------------------------------------
let mailOwner = false;
function renderMail(out) {
  const c = out?.connection;
  mailOwner = !!out?.is_owner;
  const org = orgNow();
  if (org) org.mail_domain = c?.domain || null;
  $("mail-sub").textContent = !out ? "" : c
    ? "Mailboxes are made, suspended and removed along with people's accounts."
    : "Connect Google Workspace to manage mailboxes with People. New people get a mailbox. Mailboxes are suspended when people are disabled, and suspended or deleted when they're removed.";
  $("mail").replaceChildren(...(c ? [
    el("li", {}, el("b", {}, c.domain), ` · acting as ${c.admin_email} · ${out.mailboxes || 0} mailbox${out.mailboxes === 1 ? "" : "es"} managed`),
    el("li", { class: "small" }, `Service account ${c.client_email} (client ID ${c.client_id || "?"}) · connected ${ago(c.connected_at)} by ${c.connected_by || "–"}`),
    ...(c.last_error ? [el("li", {}, el("span", { class: "tag bad" }, "problem"), ` ${c.last_error}`)] : [])] : []));
  $("mail-connect").textContent = c ? "Connect again with a new key…" : "Connect Google Workspace…";
  $("mail-connect").disabled = !mailOwner;
  $("mail-users").classList.toggle("hidden", !c);
  $("mail-users").disabled = !mailOwner;
  $("mail-disconnect").classList.toggle("hidden", !c);
  $("mail-disconnect").disabled = !mailOwner;
}

function connectMail() {
  const file = el("input", { type: "file", accept: ".json,application/json", "aria-label": "The service account's JSON key file" });
  const admin = el("input", { type: "email", autocomplete: "off", placeholder: "it@yourdomain.com" });
  const keyNote = el("p", { class: "note" });
  const err = errorLine();
  let key = null;
  file.addEventListener("change", async () => {
    key = null;
    try {
      const k = JSON.parse((await file.files[0].text()).slice(0, 20000));
      if (k?.type !== "service_account" || !k.client_email || !k.private_key) throw new Error();
      key = k;
      keyNote.textContent = `${k.client_email} · client ID ${k.client_id || "?"}. Domain-wide delegation for that client ID, with the scope ${GOOGLE_SCOPE}, must be set up in the Google Admin console first.`;
    } catch { keyNote.textContent = "That isn't a service account's JSON key."; }
  });
  const go = el("button", { type: "button", class: "primary", onclick: async () => {
    if (!key || !admin.value.includes("@")) { err.textContent = "Choose the key file and enter a super admin's email."; return; }
    go.disabled = true;
    err.textContent = "Checking it with Google…";
    try {
      const out = await api({ op: "connect-mail", organisation_id: $("org").value, key: JSON.stringify(key), admin_email: admin.value.trim() });
      key = null;
      file.value = "";
      $("dlg").close();
      $("app-msg").textContent = `Connected to ${out.domain}.`;
      await load();
    } catch (e) { err.textContent = friendly(e); go.disabled = false; }
  } }, "Connect");
  dialog("Connect Google Workspace", [
    el("p", {}, "As a Google Workspace super admin, make a Google Cloud project, turn on the Admin SDK API and create a service account with a JSON key. Then, in the Google Admin console under Security › Access and data control › API controls › Domain-wide delegation, give its client ID the scope below."),
    el("p", { class: "small", style: "font-family: ui-monospace, monospace" }, GOOGLE_SCOPE),
    field("The service account's JSON key file", file), keyNote,
    field("Super admin's email", admin),
    el("p", { class: "note" }, "Kulan Fleet acts as that admin. The key is stored encrypted and can't be read back, so you can delete the file afterwards."), err],
    [closeButton(), go]);
}

async function mailUsers() {
  dialog("Existing mailboxes", [el("p", { class: "small" }, "Loading…")], [closeButton("Close")]);
  try {
    const out = await api({ op: "mail-users", organisation_id: $("org").value });
    const users = out.users || [];
    dialog("Existing mailboxes", [
      el("p", { class: "note" }, `${users.length} mailbox${users.length === 1 ? "" : "es"}. To link one, choose Manage… next to the person, then Link an existing mailbox.`),
      el("ul", { class: "alerts" }, ...users.slice(0, 500).map((u) => el("li", {}, el("b", {}, u.address),
        ` · ${u.name || ""} · ${u.username ? `in People as ${u.username}` : "not in People"}`,
        u.admin ? el("span", { class: "tag dim", style: "margin-left: 6px" }, "Google admin") : "",
        u.suspended ? el("span", { class: "tag warn", style: "margin-left: 6px" }, "suspended") : "")))],
      [closeButton("Close")]);
  } catch (e) { dialog("Existing mailboxes", [el("p", { class: "error" }, friendly(e))], [closeButton("Close")]); }
}

function showMailboxPassword(name, address, password) {
  dialog(`Google password for ${name}`, [
    el("p", {}, `Give this to ${name} in person or by phone, not by email. They use it to sign in to Google as ${address}, then choose their own password. It's shown only once.`),
    el("div", { class: "code" }, password)],
    [el("button", { type: "button", onclick: () => navigator.clipboard?.writeText(password) }, "Copy password"),
     el("button", { type: "button", class: "primary", onclick: () => $("dlg").close() }, "Done")]);
}

// A button for a mailbox action on someone (retry, forget, new-password, unlink, create, link).
function mailboxButton(p, action, label, address) {
  return el("button", { type: "button", style: "margin-left: 6px", onclick: async () => {
    if (action === "new-password" && !await askConfirm(`A new Google password for ${p.username}? Their current one stops working now. The new one is shown once.`)) return;
    if (action === "unlink" && !await askConfirm(`Stop managing ${p.mailbox}? It stays in Google Workspace but is no longer suspended or removed with ${p.username}'s account.`)) return;
    try {
      const out = await api({ op: "mailbox", person_id: p.id, action, ...(address ? { address: address() } : {}) });
      if (out.mailbox_error) { $("app-msg").textContent = `Still not done: ${out.mailbox_error}`; }
      else $("app-msg").textContent = { retry: "Done.", forget: "No longer retried.", "new-password": `New Google password for ${p.username}.`,
                                        unlink: "Kulan Fleet no longer manages that mailbox.", create: "Mailbox made.", link: "Mailbox linked." }[action];
      if (out.mailbox_password) showMailboxPassword(p.full_name || p.username, out.mailbox, out.mailbox_password);
      else if ($("dlg").open) $("dlg").close();
      await load();
    } catch (e) { $("app-msg").textContent = friendly(e); if ($("dlg").open) $("dlg").close(); }
  } }, label);
}

function mailboxNodes(p, manages) {
  const domain = orgNow()?.mail_domain;
  if (!domain && !p.mailbox) return [];
  const live = p.mailbox && p.mailbox_state !== "deleted";
  const nodes = [el("h4", {}, "Work email")];
  if (p.mailbox) {
    const [label, kind] = MAILBOX_STATE[p.mailbox_state] || [p.mailbox_state || "unknown", "dim"];
    nodes.push(el("p", {}, el("b", {}, p.mailbox), " ", el("span", { class: `tag ${kind}` }, label)));
  } else nodes.push(el("p", { class: "small" }, "No mailbox yet."));
  if (p.mailbox_todo) {
    nodes.push(el("p", {}, el("span", { class: "tag bad" }, `mailbox ${MAILBOX_TODO[p.mailbox_todo] || "needs a retry"}`), ` ${p.mailbox_error || ""} `,
      manages ? mailboxButton(p, "retry", "Retry") : "", manages ? mailboxButton(p, "forget", "Done by hand") : ""));
  }
  if (!domain || !manages) return nodes;
  if (live) {
    nodes.push(el("div", { class: "row" }, mailboxButton(p, "new-password", "New Google password…"), mailboxButton(p, "unlink", "Stop managing it…")));
  } else {
    const address = el("input", { value: `${p.username}@${domain}`, autocomplete: "off", spellcheck: "false", "aria-label": "Mailbox address" });
    const where = () => address.value.trim().toLowerCase();
    nodes.push(field("Mailbox address", address),
      el("div", { class: "row" }, mailboxButton(p, "create", "Make a mailbox", where), isOwner ? mailboxButton(p, "link", "Link an existing mailbox", where) : ""));
  }
  return nodes;
}

function render() {
  const org = $("org").value;
  const orgName = data.organisations.find((o) => o.id === org)?.name;
  $("org-title").textContent = orgName ? "Computers" : "No organisation yet";
  const role = orgNow()?.is_owner ? "Owner" : data.global ? "Kulan Fleet admin" : "Admin";
  $("me-role").textContent = orgName ? role : "";

  const alerts = (data.alerts || []).filter((a) => a.organisation_id === org);
  // Problems of the organisation itself; a computer's own are in its details (and its Health),
  // and help requests have their own list below.
  const general = alerts.filter((a) => !a.device_id);
  $("attention-card").classList.toggle("hidden", !general.length);
  $("alerts").replaceChildren(...general.slice(0, 50).map((a) => el("li", {},
    el("span", { class: "grow-text" }, el("b", {}, a.title || a.hostname || "Computer"), ` · ${a.text}`),
    a.kind === "review" ? el("button", { onclick: openReview }, "Review now…") : "",
    a.kind === "mailbox" && a.person_id ? mailboxButton({ id: a.person_id }, "retry", "Retry") : "",
    a.kind === "mail" ? el("button", { onclick: () => go("settings") }, "Open settings") : "",
    a.kind === "help" && a.request_id ? el("button", { onclick: () => openHelpRequest(a.request_id) }, "Open…") : "")));
  renderHelpRequests(org);
  orgPolicy = policyForm(data.organisations.find((o) => o.id === org)?.policy || {});
  $("policy").replaceChildren(orgPolicy.node);
  $("policy").classList.toggle("hidden", !org);
  $("p-save").disabled = !org;
  $("add-computers").disabled = !org;
  renderComputers();

  const devices = data.devices.filter((d) => d.organisation_id === org);
  const names = Object.fromEntries(data.devices.map((d) => [d.id, d.hostname]));
  const ids = new Set(devices.map((d) => d.id));
  const acts = data.actions.filter((a) => ids.has(a.device_id)).map((a) =>
    el("tr", {}, el("td", { class: "small" }, ago(a.created_at)), el("td", {}, names[a.device_id] || ""),
       el("td", {}, requestLabel(a)), el("td", {}, ...requestResult(a, names[a.device_id]))));
  $("actions").replaceChildren(...(acts.length ? acts : [el("tr", {}, el("td", { colspan: "4", class: "empty" }, "Nothing asked yet."))]));
}

// --- Computers (2.10: the Console look) -------------------------------------------------------
const DAY_MS = 86400000;
let selected = null, computerTab = "all";
const deviceAlerts = (d) => (data.alerts || []).filter((a) => a.device_id === d.id);
const offline = (d) => !d.last_seen || Date.now() - new Date(d.last_seen).getTime() > DAY_MS;
// [label, pill kind, which tab] for a computer.
function health(d) {
  if (d.left_at) return ["Left", "dim", "left"];
  if (d.approved === false) return ["Waiting for approval", "warn", "attention"];
  if (d.state?.lost_mode) return ["Lost mode", "bad", "lost"];
  if (offline(d)) return ["Offline", "dim", "offline"];
  const issues = deviceAlerts(d).filter((a) => a.kind !== "offline" && a.kind !== "help").length;
  return issues ? [`${issues} issue${issues === 1 ? "" : "s"}`, "warn", "attention"] : ["Healthy", "good", "healthy"];
}
function updatesTag(d) {
  if (d.updates == null) return el("span", { class: "tag dim" }, "Unknown");
  if (d.updates.security) return el("span", { class: "tag bad" }, `${d.updates.security} security`);
  if (d.updates.waiting) return el("span", { class: "tag warn" }, `${d.updates.waiting} waiting`);
  return el("span", { class: "tag good" }, "Up to date");
}
const model = (d) => [d.hardware?.vendor, d.hardware?.model].filter(Boolean).join(" ") || d.os || "";
const personName = (u) => people.find((p) => p.username === u)?.full_name || u;
function usedBy(d) {
  const ins = d.sign_ins || [];
  const now = ins.filter((x) => x.now);
  if (now.length) return now.map((x) => personName(x.username)).join(", ");
  return ins.length ? personName(ins[0].username) : "–";
}
const groupsOf = (d) => groups.filter((g) => (g.devices || []).includes(d.id)).map((g) => g.name);
const COMPUTER_TABS = [["all", "All", (d) => !d.left_at], ["attention", "Needs attention", (d) => ["attention", "lost"].includes(health(d)[2])],
                       ["offline", "Offline", (d) => health(d)[2] === "offline"], ["lost", "Lost mode", (d) => health(d)[2] === "lost"],
                       ["left", "Left", (d) => !!d.left_at]];
const RANK = { lost: 0, attention: 1, offline: 2, healthy: 3, left: 4 };

function renderComputers() {
  const org = $("org").value;
  const all = data.devices.filter((d) => d.organisation_id === org);
  const active = all.filter((d) => !d.left_at);
  $("n-computers").textContent = active.length ? String(active.length) : "";
  const latest = active.map((d) => d.last_seen).filter(Boolean).sort().pop();
  $("org-sub").textContent = !org ? "Make an organisation first, in Settings."
    : active.length ? `${active.length} computer${active.length === 1 ? "" : "s"} · last report ${ago(latest)}` : "No computers yet. Add one to get started.";
  renderHealth(active);

  const tabs = COMPUTER_TABS.map(([id, label, test]) => {
    const n = all.filter(test).length;
    if (id !== "all" && id !== "attention" && !n) return null;
    return el("button", { type: "button", class: `tab${id === computerTab ? " on" : ""}`, role: "tab", "aria-selected": String(id === computerTab),
                          onclick: () => { computerTab = id; renderComputers(); } }, label, el("span", { class: "n" }, String(n)));
  }).filter(Boolean);
  if (!tabs.some((t) => t.classList.contains("on"))) { computerTab = "all"; return renderComputers(); }
  $("computer-tabs").replaceChildren(...tabs);
  const find = $("find-computers").value.trim().toLowerCase();
  const test = COMPUTER_TABS.find(([id]) => id === computerTab)[2];
  const shown = all.filter(test)
    .filter((d) => !find || [d.hostname, model(d), usedBy(d), ...(d.sign_ins || []).map((x) => x.username)].some((x) => (x || "").toLowerCase().includes(find)))
    .sort((a, b) => RANK[health(a)[2]] - RANK[health(b)[2]] || (a.hostname || "").localeCompare(b.hostname || ""));
  if (!shown.some((d) => d.id === selected)) selected = shown[0]?.id || null;
  $("devices").replaceChildren(...(shown.length ? shown.map((d) => {
    const [label, kind] = health(d);
    const pick = () => { selected = d.id; renderComputers(); };
    return el("tr", { class: `pick${d.id === selected ? " on" : ""}${d.left_at ? " left" : ""}`, tabindex: "0", "aria-selected": String(d.id === selected),
                      onclick: pick, onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } } },
      el("td", {}, el("div", { class: "name" }, d.hostname || "(no name)"), el("div", { class: "small" }, model(d))),
      el("td", {}, usedBy(d)),
      el("td", {}, el("span", { class: `tag ${kind}` }, el("span", { class: "dot" }), label)),
      el("td", {}, d.left_at ? "" : updatesTag(d)),
      el("td", { class: "hide-sm" }, d.kulan_version || "–"),
      el("td", { class: "small" }, d.left_at ? `left ${ago(d.left_at)}` : ago(d.last_seen)));
  }) : [el("tr", {}, el("td", { colspan: "6", class: "empty" },
    !all.length ? "No computers yet. Choose Add computers to make a join code." : find ? "No computer matches." : "Nothing here.")) ]));
  renderDetail(all.find((d) => d.id === selected));
}
$("find-computers").addEventListener("input", renderComputers);

function renderHealth(active) {
  const by = { healthy: 0, attention: 0, offline: 0 };
  for (const d of active) { const k = health(d)[2]; by[k === "lost" ? "attention" : k]++; }
  $("health").classList.toggle("hidden", !active.length);
  const part = (n, colour) => n ? el("span", { style: `flex: ${n}; background: ${colour}` }) : null;
  const key = (n, colour, text) => el("span", {}, el("i", { style: `background: ${colour}` }), `${n} ${text}`);
  const passing = active.filter((d) => d.compliance_total && d.compliance_passed === d.compliance_total).length;
  $("health").replaceChildren(
    el("div", { class: "health-top" }, el("b", {}, "Fleet health"),
       el("span", { class: "small" }, `${passing} of ${active.length} meet the compliance baseline`)),
    el("div", { class: "bar", role: "img", "aria-label": `${by.healthy} healthy, ${by.attention} need attention, ${by.offline} offline` },
       ...[part(by.healthy, "var(--teal)"), part(by.attention, "var(--amber)"), part(by.offline, "var(--slate)")].filter(Boolean)),
    el("div", { class: "legend" }, key(by.healthy, "var(--teal)", "healthy"), key(by.attention, "var(--amber)", "need attention"),
       key(by.offline, "var(--slate)", "offline for over a day")));
}

// What a computer can be asked from its details: [value, label].
function actionChoices(d) {
  const out = [...ACTIONS];
  out.push(d.state?.lost_mode ? ["lost-mode-off", "Release from lost mode"] : ["lost-mode-on", "Turn on lost mode…"]);
  out.push(["install-app", "Install an app…"], ["remote-help", "Remote help…"], ["run-fix", "Run a fix…"]);
  if (orgNow()?.allow_scripts) out.push(["run-script", "Run a script…"]);
  out.push(["message", "Send a message…"]);
  if (d.state?.erase_allowed && d.state?.encrypted) out.push(["erase", "Erase…"]);
  return out;
}
async function sendAction(d, type) {
  const where = d.hostname || "this computer";
  if (type === "install-app") return askInstallApp(where, { op: "action", device_id: d.id });
  if (type === "remote-help") return askRemoteHelp(d);
  if (JOBS.has(type)) return askJob(type, where, { op: "action", device_id: d.id });
  if (type === "restart" && !await askConfirm(`Restart ${where}? Anyone using it gets 5 minutes' notice.`)) return;
  if (type === "shutdown" && !await askConfirm(`Switch off ${where}? Anyone using it gets 5 minutes' notice. It reports again when it's switched on.`)) return;
  if (type === "lost-mode-on" && !await askConfirm(`Turn on lost mode on ${where}? At its next report, nobody can sign in. Anyone using it is signed out after a 20-second warning, and unsaved work is lost. The sign-in screen asks for it to be returned to your organisation.`)) return;
  let confirmName;
  if (type === "erase") {
    confirmName = await askText(`Erase ${where}? At its next report, anyone using it gets a 20-second warning. Then everything on its disk is made unreadable and it switches off. This can't be undone. Type the computer's name to confirm.`);
    if (!confirmName || confirmName.trim() !== d.hostname) { $("app-msg").textContent = "Not erased. The name didn't match."; return; }
  }
  try {
    await api({ op: "action", device_id: d.id, type, confirm: confirmName });
    $("app-msg").textContent = `${LABEL[type] || type}: asked. ${where} gets it in a few seconds if it's online.`;
    await load();
  } catch (e) { $("app-msg").textContent = friendly(e); }
}

const requestLabel = (a) => el("span", {}, LABEL[a.type] || a.type,
  a.what ? el("span", { class: "small" }, ` · ${a.type === "run-fix" ? FIX_NAME[a.what] || a.what : a.what}`) : "");
function requestResult(a, hostname) {
  return [el("span", { class: `tag ${a.status === "done" ? "good" : a.status === "failed" ? "bad" : "dim"}` },
            { pending: "Waiting", sent: "Sent", done: "Done", failed: "Failed", cancelled: "Cancelled", expired: "Expired" }[a.status] || a.status),
          a.detail ? el("span", { class: "small" }, ` ${a.detail}`) : "",
          a.has_output ? el("button", { style: "margin-left: 8px", onclick: () => showOutput(a, hostname) }, "Output") : "",
          a.status === "pending" ? el("button", { style: "margin-left: 8px", onclick: async () => {
            try { await api({ op: "cancel-action", action_id: a.id }); await load(); }
            catch (e) { $("app-msg").textContent = friendly(e); }
          } }, "Cancel") : ""];
}

// The selected computer's details, beside the list.
function renderDetail(d) {
  const box = $("detail");
  box.classList.toggle("hidden", !d);
  if (!d) { box.replaceChildren(); return; }
  const [label, kind] = health(d);
  const where = [model(d), groupsOf(d).join(", ")].filter(Boolean).join(" · ");
  const parts = [el("div", { class: "detail-head" },
    el("div", {}, el("h2", {}, d.hostname || "(no name)"), where ? el("p", { class: "sub" }, where) : ""),
    el("span", { class: `tag ${kind}` }, el("span", { class: "dot" }), label))];
  if (d.left_at) {
    parts.push(el("p", { class: "note" }, `It left the organisation ${ago(d.left_at)}. It no longer reports or takes requests.`));
    box.replaceChildren(...parts);
    return;
  }
  const issues = deviceAlerts(d);
  if (issues.length) {
    const fix = (a) => {
      if (a.kind === "approve") return orgNow()?.is_owner ? el("button", { class: "primary", onclick: () => approveComputer(d) }, "Approve") : "";
      if (a.kind === "updates") return el("button", { onclick: () => sendAction(d, "install-updates") }, "Install now");
      if (a.kind === "help" && a.request_id) return el("button", { onclick: () => openHelpRequest(a.request_id) }, "Open…");
      if (a.kind === "lost") return el("button", { onclick: () => sendAction(d, "lost-mode-off") }, "Release");
      if (a.kind === "disk") return el("button", { onclick: () => askJob("run-fix", d.hostname || "this computer", { op: "action", device_id: d.id }) }, "Run a fix…");
      if (["firewall", "policy", "encryption"].includes(a.kind)) return el("button", { onclick: () => go("policy") }, "Open policy");
      if (a.kind === "compliance") return el("button", { onclick: () => openReport(d) }, "See checks");
      return "";
    };
    parts.push(el("div", { class: "stack" }, el("h3", {}, issues.every((a) => a.kind === "help") ? "Asked for help" : "Needs attention"),
      ...issues.map((a) => el("div", { class: `issue${["lost", "encryption"].includes(a.kind) ? " bad" : ""}` },
        el("span", { class: "grow-text" }, a.text), fix(a)))));
  }
  if (d.approved === false) {
    parts.push(el("p", { class: "note" }, orgNow()?.is_owner
      ? "It joined with a join code an admin made. Approve it if you know it's one of your organisation's computers."
      : "It joined with a join code an admin made, so the Owner approves it first."));
    parts.push(el("div", { class: "row" }, el("button", { class: "link danger", onclick: () => removeComputer(d) }, "Remove from the organisation…")));
    box.replaceChildren(...parts);
    return;
  }
  const more = el("select", { "aria-label": `Ask ${d.hostname || "this computer"} to` }, el("option", { value: "" }, "More…"),
    ...actionChoices(d).map(([v, t]) => el("option", { value: v }, t)));
  more.addEventListener("change", () => { const t = more.value; more.value = ""; if (t) sendAction(d, t); });
  parts.push(el("div", { class: "quick" },
    el("button", { class: "primary", onclick: () => askRemoteHelp(d) }, "Remote help"),
    el("button", { onclick: () => askJob("run-fix", d.hostname || "this computer", { op: "action", device_id: d.id }) }, "Run a fix"),
    el("button", { onclick: () => sendAction(d, "lock-screen") }, "Lock the screen"), more));
  const now = (d.sign_ins || []).filter((x) => x.now).map((x) => personName(x.username));
  const disk = d.hardware?.disk_total_gb ? `${d.hardware.disk_free_gb} GB free of ${d.hardware.disk_total_gb} GB` : "";
  const facts = [["Kulan OS", d.kulan_version || "–"], ["Last report", ago(d.last_seen)], ["Signed in now", now.join(", ") || "Nobody"],
    ["Compliance", d.compliance_total == null ? "Not reported" : `${d.compliance_passed} of ${d.compliance_total} checks pass${d.compliance_mode === "on" ? " · compliance mode on" : ""}`],
    ["Disk", disk], ["Encryption", d.state?.encrypted === true ? "On" : d.state?.encrypted === false ? "Off" : ""],
    ["TPM unlock", d.tpm == null ? "" : d.tpm.on ? "On" : d.tpm.possible ? "Off" : "Not possible"],
    ["Joined", d.enrolled_at ? dayLabel(d.enrolled_at) : ""]].filter(([, v]) => v);
  parts.push(el("div", { class: "facts" }, ...facts.map(([k, v]) => el("div", { class: "fact" }, el("span", {}, k), el("span", {}, v)))));
  const recent = data.actions.filter((a) => a.device_id === d.id).slice(0, 4);
  if (recent.length) {
    parts.push(el("div", { class: "stack" }, el("h3", {}, "Recent requests"), el("ul", { class: "list" }, ...recent.map((a) =>
      el("li", {}, el("span", { class: "grow-text" }, requestLabel(a), el("span", { class: "small" }, ` · ${ago(a.created_at)}`)), ...requestResult(a, d.hostname))))));
  }
  parts.push(el("div", { class: "row" }, el("button", { class: "link", onclick: () => openReport(d) }, "Full report, compliance checks and apps…"),
    el("button", { class: "link danger", onclick: () => removeComputer(d) }, "Remove from the organisation…")));
  box.replaceChildren(...parts);
}

async function approveComputer(d) {
  try {
    await api({ op: "approve-computer", device_id: d.id });
    $("app-msg").textContent = `${d.hostname || "The computer"} is approved. It gets its accounts, Wi-Fi and printers in a few seconds if it's online.`;
    await load();
  } catch (e) { $("app-msg").textContent = friendly(e); }
}
// Its key stops working at once, and it isn't told (a lost or stolen one shouldn't be warned).
async function removeComputer(d) {
  const where = d.hostname || "this computer";
  const name = await askText(`Remove ${where} from the organisation? It stops getting accounts, Wi-Fi, printers and requests at once, and its requests waiting are cancelled. Nothing on it is erased. To join again it needs a new join code. Type the computer's name to confirm.`);
  if (name === null) return;
  if (name.trim() !== (d.hostname || "")) { $("app-msg").textContent = "Not removed. The name didn't match."; return; }
  try {
    await api({ op: "remove-computer", device_id: d.id, confirm: name.trim() });
    $("app-msg").textContent = `${where} was removed from the organisation.`;
    await load();
  } catch (e) { $("app-msg").textContent = friendly(e); }
}

// --- Fixes, scripts and messages (2.9b) -----------------------------------------------------
// body: {op: "action", device_id}, {op: "group-action", group_id} or {op: "message-person", person_id}.
async function askJob(kind, where, body) {
  const err = errorLine();
  const go = el("button", { type: "button", class: "primary" }, kind === "message" ? "Send" : "Run");
  const send = async (payload, said) => {
    go.disabled = true;
    try {
      const out = await api(body.op === "message-person" ? { ...body, ...payload } : { ...body, type: kind, payload });
      $("dlg").close();
      const n = out.count;
      $("app-msg").textContent = `${said}${n === undefined ? "" : ` (${n} computer${n === 1 ? "" : "s"})`}. ` +
        (kind === "message" ? "It appears at their next report." : "The result appears under Recent requests.");
      await load();
    } catch (e) { err.textContent = friendly(e); go.disabled = false; }
  };
  if (kind === "run-fix") {
    const pick = el("select", {}, ...FIXES.map(([v, t]) => el("option", { value: v }, t)));
    const about = el("p", { class: "note" });
    const describe = () => { about.textContent = FIXES.find(([v]) => v === pick.value)[2]; };
    pick.addEventListener("change", describe);
    describe();
    go.addEventListener("click", () => send({ fix: pick.value }, `${FIX_NAME[pick.value]} will run on ${where}`));
    dialog(`Run a fix on ${where}`, [
      el("p", {}, "It runs at the next report. Whoever is using the computer sees which fix is running."),
      field("Fix", pick), about, err], [closeButton(), go]);
    return;
  }
  if (kind === "message") {
    const title = el("input", { maxlength: "80", placeholder: "Restart tonight" });
    const text = el("textarea", { rows: "4", maxlength: "500", placeholder: "Please save your work before 6 pm.", style: "font-family: inherit" });
    go.addEventListener("click", () => {
      if (!title.value.trim()) { err.textContent = "Give the message a title."; return; }
      send({ title: title.value.trim(), body: text.value.trim() }, `The message will show on ${where}`);
    });
    dialog(`Send a message to ${where}`, [
      el("p", { class: "note" }, "It shows as a notification on the screen of whoever is signed in, with your organisation's name."),
      field("Title", title), field("Message", text), err], [closeButton(), go]);
    title.focus();
    return;
  }
  // run-script
  if (!(await codeFirst())) return;
  if (scripts.some((x) => x.body === null)) await loadScripts();  // listed before the code: without what they say
  if (!scriptsAllowed) { $("app-msg").textContent = "The Owner hasn't allowed scripts for this organisation."; return; }
  if (!scripts.length) { $("app-msg").textContent = "No scripts yet. Add one under Scripts first."; return; }
  if (!scripts.some((x) => x.signed)) { $("app-msg").textContent = "No signed scripts yet. Sign one under Scripts first."; return; }
  const pick = el("select", {}, ...scripts.map((x) => el("option", { value: x.id, ...(x.signed ? {} : { disabled: true }) },
    x.signed ? x.name : `${x.name} (not signed)`)));
  pick.value = scripts.find((x) => x.signed).id;
  const about = el("p", { class: "note" });
  const shown = el("pre", { style: "max-height: 30vh" });
  const describe = () => {
    const x = scripts.find((y) => y.id === pick.value);
    about.textContent = `${x.description ? `${x.description} · ` : ""}changed ${ago(x.updated_at)} by ${x.updated_by}`;
    shown.textContent = x.body;
  };
  pick.addEventListener("change", describe);
  describe();
  go.addEventListener("click", () => send({ script_id: pick.value }, `${scripts.find((y) => y.id === pick.value).name} will run on ${where}`));
  dialog(`Run a script on ${where}`, [
    el("p", {}, "It runs as root at the next report, for up to 10 minutes. The computer checks that it's this exact script before running it, and whoever is using it sees the script's name."),
    field("Script", pick), about, shown, err], [closeButton(), go]);
}

async function showOutput(a, hostname) {
  try {
    const out = await api({ op: "action-output", action_id: a.id });
    const what = a.type === "run-fix" ? FIX_NAME[a.what] || a.what : a.what;
    const name = `${(hostname || "computer").replace(/[^A-Za-z0-9._-]+/g, "-")}-${a.created_at.slice(0, 10)}.txt`;
    dialog(`${what || LABEL[a.type]} on ${hostname || "a computer"}`, [
      el("p", { class: "note" }, `${ago(a.created_at)} · asked by ${a.requested_by}${out.exit_code === null ? "" : ` · exit code ${out.exit_code}`}`),
      el("pre", {}, out.output ?? "No output kept. Output is kept for 90 days.")],
      [el("button", { type: "button", onclick: () => download(name, out.output ?? "", "text/plain") }, "Download"), closeButton("Close")]);
  } catch (e) { $("app-msg").textContent = friendly(e); }
}

// --- Script signing (2.10) -------------------------------------------------------------------
// Computers run only scripts the Owner's script signing key signed. The key comes from a code
// only the Owner holds (made here, written down, never sent): SHA-256 of it is the Ed25519 seed.
// The server and computers get the public half. tweetnacl (public domain) does the signing.
const SIGN_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const normalSigningCode = (c) => String(c).toUpperCase().replace(/[^A-Z0-9]/g, "").replace(/(.{5})(?=.)/g, "$1-");
function newSigningCode() {
  const bytes = crypto.getRandomValues(new Uint8Array(20));
  return normalSigningCode(Array.from(bytes, (b) => SIGN_LETTERS[b % 32]).join(""));
}
const b64 = (bytes) => btoa(String.fromCharCode(...bytes));
const utf8 = (t) => new TextEncoder().encode(t);
const sha256Hex = async (t) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", typeof t === "string" ? utf8(t) : t)),
                                          (b) => b.toString(16).padStart(2, "0")).join("");
async function signerFrom(code) {
  const seed = new Uint8Array(await crypto.subtle.digest("SHA-256", utf8(`kulan-fleet-script-key v1\n${normalSigningCode(code)}`)));
  const pair = window.nacl.sign.keyPair.fromSeed(seed);
  const sign = (message) => b64(window.nacl.sign.detached(utf8(message), pair.secretKey));
  return { pub: b64(pair.publicKey), sign,
           script: async (text) => sign(`kulan-fleet-script v1\n${await sha256Hex(text)}\n`),
           endorse: (key) => sign(`kulan-fleet-script-key v1\n${key}\n`) };
}
// A short way to compare keys by eye (the same as kulan-fleet trust-script-key prints).
async function keyFingerprint(key) {
  const hex = (await sha256Hex(Uint8Array.from(atob(key), (c) => c.charCodeAt(0)))).slice(0, 16).toUpperCase();
  return hex.match(/.{4}/g).join(" ");
}
// Kept only in this page, until it's closed: typed once per visit.
let signer = null;
const normalScript = (t) => t.replace(/\r\n/g, "\n");
// The organisation's signer, asking for the code if this page doesn't have it.
function askSigningCode(why) {
  if (signer && signer.pub === scriptKey) return Promise.resolve(signer);
  return new Promise((resolve) => {
    const code = el("input", { type: "password", autocomplete: "off", spellcheck: "false", "aria-label": "Script signing code",
                               placeholder: "XXXXX-XXXXX-XXXXX-XXXXX" });
    const err = errorLine();
    const ok = el("button", { type: "button", class: "primary", onclick: async () => {
      const s2 = await signerFrom(code.value);
      if (s2.pub !== scriptKey) { err.textContent = "That isn't your organisation's script signing code."; return; }
      signer = s2;
      $("dlg").close();
      resolve(signer);
    } }, "Continue");
    dialog("Script signing code", [el("p", {}, `${why} Type the script signing code the Owner wrote down when they set up script signing.`),
      field("Script signing code", code), err], [el("button", { type: "button", onclick: () => { $("dlg").close(); resolve(null); } }, "Cancel"), ok]);
    code.focus();
  });
}
async function setUpSigning(change) {
  if (!(await codeFirst())) return;
  let endorsement, reset = false;
  if (change) {
    const old = await askSigningCode("To change the code, the current one signs the new one, so the computers move to it by themselves.");
    if (!old) {
      if (!await askConfirm("Lost the current code? You can start again with a new one, but every computer then refuses scripts until someone with its administrator password runs \"sudo kulan-fleet trust-script-key\" on it. Start again?")) return;
      reset = true;
    } else endorsement = old;
  }
  const code = newSigningCode();
  const next = await signerFrom(code);
  const written = el("input", { type: "checkbox" });
  const err = errorLine();
  const save = el("button", { type: "button", class: "primary", disabled: true, onclick: async () => {
    try {
      const out = await api({ op: "set-script-key", organisation_id: $("org").value, public_key: next.pub,
                              ...(endorsement ? { endorsement: endorsement.endorse(next.pub) } : {}), ...(reset ? { reset: true } : {}) });
      signer = next;
      $("dlg").close();
      $("app-msg").textContent = out.reset ? "New script signing code set up. Sign your scripts again; each computer needs \"sudo kulan-fleet trust-script-key\" before it runs them."
        : change ? "Script signing code changed. Sign your scripts again." : "Script signing is set up.";
      await loadScripts();
    } catch (e) { err.textContent = friendly(e); }
  } }, "Save");
  written.addEventListener("change", () => { save.disabled = !written.checked; });
  dialog(change ? "A new script signing code" : "Set up script signing", [
    el("p", {}, "Computers run only scripts signed with this code. Write it down and keep it with your recovery code. Kulan doesn't keep it, so nobody, not even someone who breaks into Kulan Fleet, can run a script on your computers without it."),
    el("div", { class: "code" }, code),
    el("label", { class: "row" }, written, " I've written it down"), err], [closeButton(), save]);
}

async function loadScripts() {
  const org = $("org").value;
  scripts = [];
  scriptsAllowed = false;
  scriptKey = null;
  let owner = false;
  if (org) {
    try {
      const out = await api({ op: "scripts", organisation_id: org });
      scripts = out.scripts || [];
      scriptsAllowed = out.allowed;
      scriptKey = out.script_key || null;
      owner = out.is_owner;
    } catch (e) { $("app-msg").textContent = friendly(e); }
  }
  const fp = scriptKey ? await keyFingerprint(scriptKey) : "";
  $("signing").replaceChildren(org ? el("li", {},
    el("span", { class: "grow-text" }, el("b", {}, "Script signing"), scriptKey
      ? ` · key ${fp}. Computers run only scripts signed with the Owner's script signing code.`
      : " · not set up. Computers run only scripts signed with the Owner's script signing code, so scripts need it first."),
    owner ? el("button", { onclick: () => setUpSigning(!!scriptKey) }, scriptKey ? "Change the code…" : "Set up…") : "") : "");
  $("scripts-sub").textContent = scriptsAllowed
    ? "Your organisation's own scripts, run as root on the computers you choose, with their output shown here. Adding, changing and running them needs your authenticator app's code; computers run only those signed with the Owner's script signing code."
    : `Off. Only the Owner can allow your organisation's own scripts on its computers. Kulan's fixes work either way.${owner ? "" : " Ask the Owner if you need them."}`;
  $("scripts-allow-row").classList.toggle("hidden", !owner);
  $("scripts-allow").checked = scriptsAllowed;
  $("add-script").classList.toggle("hidden", !scriptsAllowed);
  $("scripts").replaceChildren(...(scriptsAllowed ? (scripts.length ? scripts.map((x) => el("li", {},
    el("b", {}, x.name), x.description ? ` · ${x.description}` : "",
    el("span", { class: "small" }, ` · changed ${ago(x.updated_at)} by ${x.updated_by} `),
    x.signed ? "" : el("span", { class: "tag warn" }, "Not signed"), " ",
    x.signed ? "" : el("button", { onclick: () => signScript(x) }, "Sign…"), " ",
    el("button", { onclick: () => editScript(x) }, "Edit…"), " ",
    el("button", { class: "danger", onclick: async () => {
      if (!await askConfirm(`Remove the script ${x.name}? Requests already sent still run if they haven't yet.`)) return;
      try { await api({ op: "remove-script", script_id: x.id }); await loadScripts(); } catch (e) { $("app-msg").textContent = friendly(e); }
    } }, "Remove…")))
    : [el("li", { class: "small" }, "No scripts yet.")]) : []));
}

async function editScript(x) {
  if (!(await codeFirst())) return;
  if (x && x.body === null) {
    await loadScripts();
    x = scripts.find((y) => y.id === x.id);
    if (!x || x.body === null) return;
  }
  const name = el("input", { maxlength: "80", value: x?.name || "", placeholder: "Clear the print queue" });
  const about = el("input", { maxlength: "300", value: x?.description || "", placeholder: "What it does, for other admins" });
  const text = el("textarea", { rows: "14", spellcheck: "false", placeholder: "#!/bin/bash\nset -e\n…" });
  text.value = x?.body || "";
  const err = errorLine();
  // Signed when it's saved: the Owner's script signing code, unless this page has it already.
  const needCode = !(signer && signer.pub === scriptKey);
  const code = el("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: "XXXXX-XXXXX-XXXXX-XXXXX" });
  dialog(x ? `Edit ${x.name}` : "Add a script", [
    el("p", { class: "note" }, "Bash, run as root with a 10-minute limit. Only the first 64 KB of what it prints is kept. Computers run it only once it's signed with the Owner's script signing code."),
    field("Name", name), field("What it does", about), field("Script", text), needCode && scriptKey ? field("Script signing code", code) : "", err],
    [closeButton(), el("button", { type: "button", class: "primary", onclick: async () => {
      if (!name.value.trim() || !text.value.trim()) { err.textContent = "A script needs a name and some commands."; return; }
      if (!scriptKey) { err.textContent = "The Owner sets up script signing first (above the list)."; return; }
      if (needCode) {
        const s2 = await signerFrom(code.value);
        if (s2.pub !== scriptKey) { err.textContent = "That isn't your organisation's script signing code."; return; }
        signer = s2;
      }
      const body = normalScript(text.value);
      try {
        await api({ op: x ? "edit-script" : "add-script", ...(x ? { script_id: x.id } : { organisation_id: $("org").value }),
                    name: name.value.trim(), description: about.value.trim(), body, signature: await signer.script(body) });
        $("dlg").close();
        await loadScripts();
      } catch (e) { err.textContent = friendly(e); }
    } }, "Save")]);
  name.focus();
}
async function signScript(x) {
  if (!(await codeFirst())) return;
  if (x.body === null) {
    await loadScripts();
    x = scripts.find((y) => y.id === x.id);
    if (!x || x.body === null) return;
  }
  const s2 = await askSigningCode(`Sign ${x.name} so the computers run it. Check what it says first.`);
  if (!s2) return;
  try {
    await api({ op: "sign-script", script_id: x.id, signature: await s2.script(x.body) });
    $("app-msg").textContent = `${x.name} is signed.`;
    await loadScripts();
  } catch (e) { $("app-msg").textContent = friendly(e); }
}
$("add-script").addEventListener("click", () => editScript(null));
$("scripts-allow").addEventListener("change", async () => {
  const want = $("scripts-allow").checked;
  if (want && !scriptKey) { $("scripts-allow").checked = false; $("app-msg").textContent = "Set up script signing first."; return; }
  if (want && !await askConfirm("Allow scripts? Admins who sign in with their authenticator app can then run the scripts signed with your script signing code, as root, on this organisation's computers. Every script and every run is in the activity log.")) {
    $("scripts-allow").checked = false;
    return;
  }
  if (want && !(await codeFirst())) { $("scripts-allow").checked = false; return; }
  try { await api({ op: "set-scripts-allowed", organisation_id: $("org").value, allowed: want }); await load(); }
  catch (e) { $("scripts-allow").checked = !want; $("app-msg").textContent = friendly(e); }
});

// How a group's Wi-Fi network or printer went on its computers (their reports say so).
function pushedLine(kind, id, g) {
  const ds = orgDevices().filter((d) => (g.devices || []).includes(d.id));
  if (!ds.length) return "no computers in the group yet";
  let added = 0, waiting = 0;
  const problems = [];
  for (const d of ds) {
    const st = d.pushed?.[kind]?.[id];
    if (st === "added") added++;
    else if (!st) waiting++;
    else problems.push(`${d.hostname || "(no name)"}: ${st}`);
  }
  return [`added on ${added} of ${ds.length}`, waiting ? `${waiting} waiting for their next report` : "", ...problems].filter(Boolean).join(" · ");
}

function openGroupNetwork(g) {
  const err = errorLine();
  const remove = (op, key, id, label) => el("button", { class: "danger", onclick: async () => {
    if (!await askConfirm(`Remove ${label}? It's taken off the group's computers at their next report.`)) return;
    try { await api({ op, [key]: id }); await loadGroups(); openGroupNetwork(groups.find((x) => x.id === g.id)); }
    catch (e) { err.textContent = friendly(e); }
  } }, "Remove…");
  const nets = (g.networks || []).map((n) => el("li", {}, el("b", {}, n.ssid),
    ` · ${n.security === "open" ? "open" : "password"}${n.hidden ? " · hidden" : ""} `,
    el("span", { class: "small" }, pushedLine("networks", n.id, g)), " ", remove("remove-network", "network_id", n.id, n.ssid)));
  const printers = (g.printers || []).map((p) => el("li", {}, el("b", {}, p.name), el("span", { class: "small" }, ` · ${p.uri} · ${pushedLine("printers", p.id, g)} `),
    remove("remove-printer", "printer_id", p.id, p.name)));
  const ssid = el("input", { maxlength: "32", placeholder: "Office Wi-Fi", autocomplete: "off" });
  const security = el("select", {}, el("option", { value: "wpa-psk" }, "Password (WPA2 or WPA3 personal)"), el("option", { value: "open" }, "Open, no password"));
  const password = el("input", { type: "password", maxlength: "64", autocomplete: "new-password" });
  const hidden = el("input", { type: "checkbox" });
  security.addEventListener("change", () => { password.disabled = security.value === "open"; });
  const pname = el("input", { maxlength: "60", placeholder: "Front desk" });
  const uri = el("input", { maxlength: "300", placeholder: "ipp://192.168.1.20/ipp/print", autocomplete: "off", spellcheck: "false" });
  const add = async (body) => {
    try { await api(body); await loadGroups(); openGroupNetwork(groups.find((x) => x.id === g.id)); }
    catch (e) { err.textContent = friendly(e); }
  };
  dialog(`${g.name}: Wi-Fi and printers`, [
    el("p", { class: "note" }, "The group's computers connect to these networks and get these printers at their next report. Taking one away here takes it off them too. Networks and printers people add themselves stay."),
    el("h4", {}, "Wi-Fi networks"), el("ul", { class: "alerts" }, ...(nets.length ? nets : [el("li", { class: "small" }, "None yet")])),
    field("Network name", ssid), field("Security", security), field("Password", password),
    el("label", { class: "row" }, hidden, " It's hidden (doesn't show its name)"),
    el("p", { class: "note" }, "The password is kept encrypted and sent only to the group's computers. It isn't shown in Kulan Fleet again. People using those computers may be able to see it, as with any shared Wi-Fi password."),
    el("button", { type: "button", onclick: () => {
      if (!ssid.value) { err.textContent = "Enter the network's name."; return; }
      add({ op: "add-network", group_id: g.id, ssid: ssid.value, security: security.value,
            ...(security.value === "open" ? {} : { password: password.value }), hidden: hidden.checked });
    } }, "Add network"),
    el("h4", {}, "Printers"), el("ul", { class: "alerts" }, ...(printers.length ? printers : [el("li", { class: "small" }, "None yet")])),
    field("Name people see", pname), field("Printer address", uri),
    el("p", { class: "note" }, "Most office printers made since 2015 work without a driver. Their address is usually ipp://, the printer's IP address, then /ipp/print."),
    el("button", { type: "button", onclick: () => add({ op: "add-printer", group_id: g.id, name: pname.value.trim(), uri: uri.value.trim() }) }, "Add printer"),
    err], [closeButton("Close")]);
}

// --- Help requests (2.9c) --------------------------------------------------------------------
function renderHelpRequests(org) {
  const list = (data.help_requests || []).filter((h) => h.organisation_id === org);
  $("help-card").classList.toggle("hidden", !list.length);
  $("help-requests").replaceChildren(...list.slice(0, 50).map((h) => el("li", {},
    avatar(h.full_name || h.username, h.username),
    el("span", { class: "grow-text" }, el("b", {}, h.full_name || h.username), el("span", { class: "small" }, ` on ${h.hostname || "a computer"} · ${ago(h.created_at)}`),
       el("div", { class: "note", style: "white-space: pre-wrap" }, String(h.body).slice(0, 300) + (h.body.length > 300 ? "…" : ""))),
    h.state === "done" ? el("span", { class: "tag good" }, "Done") : el("span", { class: "tag warn" }, "Waiting"),
    h.has_screenshot ? el("span", { class: "tag dim" }, "Screenshot") : "",
    el("button", { onclick: () => openHelpRequest(h.id) }, "Open…"))));
}

async function openHelpRequest(id) {
  let h;
  try { h = (await api({ op: "help-request", request_id: id })).request; }
  catch (e) { $("app-msg").textContent = friendly(e); return; }
  const device = data.devices.find((d) => d.id === h.device_id);
  const who = h.full_name ? `${h.full_name} (${h.username})` : h.username;
  const parts = [
    el("p", { class: "note" }, `${who} on ${h.hostname || "a computer"} · ${ago(h.created_at)}`),
    el("pre", { style: "font: inherit; font-size: 14px" }, h.body)];
  if (h.screenshot) {
    const src = `data:${h.screenshot_type};base64,${h.screenshot}`;
    parts.push(el("a", { href: "#", "aria-label": "Their screenshot, full size", onclick: (ev) => {
      ev.preventDefault();
      const bytes = Uint8Array.from(atob(h.screenshot), (c) => c.charCodeAt(0));
      window.open(URL.createObjectURL(new Blob([bytes], { type: h.screenshot_type })), "_blank", "noopener");
    } }, el("img", { src, alt: "The screenshot they added", style: "max-width: 100%; max-height: 50vh; border-radius: 10px; border: 1px solid var(--line)" })));
    parts.push(el("p", { class: "note" }, "Screenshots are kept for 30 days."));
  }
  const buttons = [];
  if (device && !device.left_at) {
    buttons.push(el("button", { type: "button", onclick: () => askRemoteHelp(device) }, "Start remote help…"));
  }
  if (h.state === "done") {
    parts.push(el("p", {}, el("b", {}, "Done"), ` · ${ago(h.done_at)} by ${h.done_by}`), h.reply ? el("pre", { style: "font: inherit; font-size: 14px" }, h.reply) : el("p", { class: "note" }, "No answer was written."));
    buttons.push(closeButton("Close"));
  } else {
    const answer = el("textarea", { rows: "3", maxlength: "500", placeholder: "For example: I restarted the printer. Try again now.",
                                    style: "width: 100%; box-sizing: border-box; font: inherit" });
    const err = errorLine();
    parts.push(field("Answer (optional). It appears on their screen when you mark it done", answer), err);
    const done = el("button", { type: "button", class: "primary", onclick: async () => {
      done.disabled = true;
      try {
        await api({ op: "close-help", request_id: h.id, ...(answer.value.trim() ? { reply: answer.value.trim() } : {}) });
        $("dlg").close();
        await load();
      } catch (e) { err.textContent = friendly(e); done.disabled = false; }
    } }, "Mark as done");
    buttons.push(closeButton("Close"), done);
  }
  dialog(`Help request from ${h.full_name || h.username}`, parts, buttons);
}

// --- Remote help (2.9a) ----------------------------------------------------------------------
let help = null;
function askRemoteHelp(d) {
  const here = (d.sign_ins || []).filter((x) => x.now).map((x) => x.username);
  const control = el("input", { type: "checkbox" });
  const err = errorLine();
  const start = el("button", { type: "button", class: "primary", onclick: async () => {
    start.disabled = true;
    try { $("dlg").close(); await openHelp(d, control.checked); }
    catch (e) { err.textContent = friendly(e); start.disabled = false; }
  } }, "Ask to see the screen");
  dialog(`Remote help on ${d.hostname || "this computer"}`, [
    el("p", {}, here.length ? `${here.join(", ")} ${here.length === 1 ? "is" : "are"} signed in there. ` : "Nobody was signed in at its last report. ",
      "The person at the computer is asked first and sees your email. Nothing is shared unless they press Allow. They can stop it at any time, and it ends after an hour."),
    el("label", { class: "row" }, control, " Also ask to use their mouse and keyboard"),
    el("p", { class: "note" }, "The screen goes directly from the computer to you, or through Kulan Fleet's relay, and isn't recorded. Every request is saved in the activity log."),
    err], [closeButton(), start]);
}
async function openHelp(d, control) {
  if (help) await help.end();
  $("help-title").textContent = `Remote help · ${d.hostname || "computer"}`;
  $("help-status").textContent = "Asking…";
  $("help-control-wrap").classList.add("hidden");
  $("help-video").classList.remove("control");
  $("help-end").textContent = "End";
  $("help-dialog").showModal();
  const mine = help = new RemoteHelp({ api, deviceId: d.id, hostname: d.hostname, control, video: $("help-video"),
    onStatus: (t) => { if (help === mine) $("help-status").textContent = t; },
    onControl: (on) => {
      if (help !== mine) return;
      $("help-control-wrap").classList.toggle("hidden", !on);
      $("help-video").classList.toggle("control", on && $("help-control").checked);
    },
    onEnd: (t) => {
      if (help !== mine) return;
      help = null;
      $("help-status").textContent = t;
      $("help-control-wrap").classList.add("hidden");
      $("help-end").textContent = "Close";
      load();
    } });
  try { await mine.start(); }
  catch (e) { help = null; $("help-dialog").close(); throw e; }
}
$("help-end").addEventListener("click", async () => { if (help) await help.end(); else $("help-dialog").close(); });
$("help-dialog").addEventListener("cancel", async (ev) => {  // Esc: ends it
  ev.preventDefault();
  if (help) await help.end(); else $("help-dialog").close();
});
$("help-control").addEventListener("change", () => {
  help?.setUseControl($("help-control").checked);
  $("help-video").classList.toggle("control", $("help-control").checked);
});
$("help-full").addEventListener("click", () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else $("help-dialog").requestFullscreen?.().catch(() => {});
});
// Leaving the page ends it (the computer stops sharing at once, not after a timeout).
addEventListener("pagehide", () => {
  if (!help?.sid || !lastToken) return;
  fetch(API, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", Authorization: `Bearer ${lastToken}` },
               body: JSON.stringify({ op: "help-end", session_id: help.sid }) });
});
// The Kulan Fleet app opens this page at #help=<computer> to start remote help there.
const helpLink = /^#help=([0-9a-f-]{36})$/.exec(location.hash);
if (helpLink) { sessionStorage.setItem("kulan-help", helpLink[1]); history.replaceState(null, "", location.pathname); }
function helpFromLink() {
  const id = sessionStorage.getItem("kulan-help");
  if (!id || !data) return;
  sessionStorage.removeItem("kulan-help");
  const d = data.devices.find((x) => x.id === id && !x.left_at);
  if (!d) { $("app-msg").textContent = "That computer isn't in your organisations, or it has left."; return; }
  if ($("org").value !== d.organisation_id) { $("org").value = d.organisation_id; render(); }
  askRemoteHelp(d);
}

async function openReport(d) {
  $("report-title").textContent = d.hostname || "Computer";
  $("report-body").replaceChildren(el("p", { class: "small" }, "Loading…"));
  $("report-dialog").showModal();
  try {
    const { report } = await api({ op: "report", device_id: d.id });
    const parts = [];
    parts.push(el("h4", {}, "Who uses it"), el("ul", { class: "alerts" }, ...((d.sign_ins || []).length
      ? d.sign_ins.slice(0, 50).map((x) => el("li", {}, el("b", {}, x.username), ` · ${x.now ? "signed in now" : `last used ${agoSeconds(x.last)}`}`))
      : [el("li", { class: "small" }, "Not reported yet. Needs Kulan OS 2.8c or later.")])));
    if (report?.compliance?.controls) {
      parts.push(el("h4", {}, `Compliance: ${report.compliance.passed} of ${report.compliance.total} (${report.compliance.baseline || ""})`));
      parts.push(el("table", {}, ...report.compliance.controls.map((c) => el("tr", {},
        el("td", {}, c.id), el("td", {}, c.title),
        el("td", {}, el("span", { class: `tag ${c.status === "pass" ? "good" : c.status === "fail" ? "bad" : "dim"}` }, c.status)),
        el("td", { class: "small" }, `CIS ${c.cis} · NIST ${c.nist}`)))));
    }
    if (d.apps_at) {
      const { apps } = await api({ op: "apps", device_id: d.id });
      parts.push(el("h4", {}, `Apps (${apps.length}, sent ${ago(d.apps_at)})`),
        el("table", {}, ...apps.slice(0, 1000).map((a) => el("tr", {}, el("td", {}, a.name), el("td", { class: "small" }, a.version),
          el("td", {}, el("span", { class: "tag dim" }, a.source === "flatpak" ? "Flatpak" : "Debian"))))));
    }
    parts.push(el("h4", {}, "Full report"), el("pre", {}, JSON.stringify(report, null, 2)));
    $("report-body").replaceChildren(...parts);
  } catch (e) {
    $("report-body").replaceChildren(el("p", { class: "error" }, friendly(e)));
  }
}

$("report-close").addEventListener("click", () => $("report-dialog").close());
$("p-save").addEventListener("click", async () => {
  const policy = orgPolicy?.value();
  if (!policy) { $("app-msg").textContent = "One of the apps isn't valid. Fix it or remove it."; return; }
  try { await api({ op: "set-policy", organisation_id: $("org").value, policy }); $("app-msg").textContent = "Policy saved."; await load(); }
  catch (e) { $("app-msg").textContent = friendly(e); }
});
let logBefore = null, logSearch = null;
const logQuery = (extra) => {
  const q = { op: "log", organisation_id: $("org").value, ...extra };
  for (const k of ["from", "to", "who", "what"]) if ($(`log-${k}`).value.trim()) q[k] = $(`log-${k}`).value.trim();
  return q;
};
const logWhat = (e) => e.what.startsWith("asked: ") ? `asked ${e.hostname || "a computer"}: ${LABEL[e.what.slice(7)] || e.what.slice(7)}` : e.what;
async function loadLog(more) {
  if (!more) logSearch = logQuery({});  // "Show more" carries on with the same search
  try {
    const out = await api({ ...logSearch, limit: 100, ...(more && logBefore ? { before: logBefore } : {}) });
    const rows = out.entries.map((e) => el("tr", {}, el("td", {}, `${dayLabel(e.at)} · ${ago(e.at)}`), el("td", {}, e.email), el("td", {}, logWhat(e))));
    if (more) $("log").append(...rows);
    else $("log").replaceChildren(...(rows.length ? rows : [el("tr", {}, el("td", { colspan: "3", class: "empty" }, "Nothing found."))]));
    logBefore = out.entries.length ? out.entries[out.entries.length - 1].id : null;
    $("log-more").classList.toggle("hidden", !out.more);
    $("log-wrap").classList.remove("hidden");
  } catch (e) { $("app-msg").textContent = friendly(e); }
}
$("log-find").addEventListener("submit", (ev) => { ev.preventDefault(); loadLog(false); });
$("log-more").addEventListener("click", () => loadLog(true));
$("log-csv").addEventListener("click", async () => {
  $("log-csv").disabled = true;
  try {
    let all = [], before = null;
    for (let i = 0; i < 20; i++) {
      const out = await api(logQuery({ limit: 10000, ...(before ? { before } : {}) }));
      all = all.concat(out.entries);
      if (!out.more || !out.entries.length) break;
      before = out.entries[out.entries.length - 1].id;
    }
    const org = data.organisations.find((o) => o.id === $("org").value)?.name || "organisation";
    download(`Kulan Fleet activity ${org} ${new Date().toISOString().slice(0, 10)}.csv`, logCsv(all), "text/csv");
    $("app-msg").textContent = `Exported ${all.length} entr${all.length === 1 ? "y" : "ies"}.`;
  } catch (e) { $("app-msg").textContent = friendly(e); }
  $("log-csv").disabled = false;
});
$("weekly").addEventListener("click", openWeekly);
$("review").addEventListener("click", openReview);
function resetLog() {
  logBefore = null;
  logSearch = null;
  $("log").replaceChildren();
  $("log-more").classList.add("hidden");
  $("log-wrap").classList.add("hidden");
}
$("org").addEventListener("change", () => { selected = null; resetLog(); render(); loadPeople(); loadGroups(); loadScripts(); });
$("add-group").addEventListener("click", () => {
  const name = el("input", { maxlength: "60" });
  const err = errorLine();
  dialog("Add a group", [field("Name", name), el("p", { class: "note" }, "Then choose its computers and its policy."), err],
    [closeButton(), el("button", { type: "button", class: "primary", onclick: async () => {
      const n = name.value.trim().replace(/\s+/g, " ");
      if (!n) { err.textContent = "A group needs a name."; return; }
      try {
        const out = await api({ op: "add-group", organisation_id: $("org").value, name: n });
        await loadGroups();
        openGroup(groups.find((g) => g.id === out.id) || { id: out.id, name: n, policy: {}, devices: [] });
      } catch (e) { err.textContent = friendly(e); }
    } }, "Add")]);
  name.focus();
});
$("add-person").addEventListener("click", addPerson);
$("mail-connect").addEventListener("click", connectMail);
$("mail-users").addEventListener("click", mailUsers);
$("mail-disconnect").addEventListener("click", async () => {
  if (!await askConfirm("Disconnect Google Workspace? Kulan Fleet deletes its copy of the key. Mailboxes stay in Google but are no longer managed with People, and computers stop offering them. Unfinished suspensions or deletions are left for you to do by hand. To revoke the key, delete it in Google Cloud.")) return;
  try { await api({ op: "disconnect-mail", organisation_id: $("org").value }); $("app-msg").textContent = "Google Workspace disconnected."; await load(); }
  catch (e) { $("app-msg").textContent = friendly(e); }
});
$("add-admin").addEventListener("submit", (ev) => { ev.preventDefault(); changeAdmin("add-admin", $("admin-email").value.trim()).then(() => { $("admin-email").value = ""; }); });
$("set-owner").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const org = data.organisations.find((o) => o.id === $("org").value);
  try {
    const out = await api({ op: "set-owner", organisation_id: org.id, email: $("owner-email").value.trim() });
    $("owner-email").value = "";
    await load();
    showRecovery(org.name, out.recovery_code);
  } catch (e) { $("app-msg").textContent = friendly(e); }
});
$("recover").addEventListener("click", (ev) => {
  ev.preventDefault();
  const code = el("input", { autocomplete: "off", spellcheck: "false" });
  const err = errorLine();
  dialog("Become the Owner", [
    el("p", {}, "Enter the organisation's recovery code. The email you're signed in with becomes the Owner."),
    field("Recovery code", code), err],
    [closeButton(), el("button", { type: "button", class: "primary", onclick: async () => {
      try {
        const out = await api({ op: "recover-owner", recovery_code: code.value.trim() });
        await load();
        if (out.organisation?.id) { $("org").value = out.organisation.id; render(); await loadPeople(); }
        showRecovery(out.organisation?.name || "your organisation", out.recovery_code);
      } catch (e) { err.textContent = friendly(e); }
    } }, "Become the Owner")]);
});
$("refresh").addEventListener("click", load);
$("my-auth").addEventListener("click", myAuthenticator);
$("signout").addEventListener("click", async () => { await supabase.auth.signOut(); show(false); });
$("new-org").addEventListener("click", async () => {
  const name = await askText("Name of the organisation");
  if (!name) return;
  const owner = await askText("The Owner's email. The Owner decides who the admins are. Leave it empty to be the Owner yourself.", "");
  if (owner === null) return;
  try {
    const out = await api({ op: "new-organisation", name, ...(owner.trim() ? { owner_email: owner.trim() } : {}) });
    await load();
    $("org").value = out.id;
    render();
    await loadPeople();
    showRecovery(name, out.recovery_code);
  } catch (e) { $("app-msg").textContent = friendly(e); }
});
// The join codes still usable (never the codes themselves), each with Withdraw.
async function loadCodes() {
  const list = $("codes-list");
  try {
    const { codes } = await api({ op: "codes", organisation_id: $("org").value });
    $("codes-box").classList.toggle("hidden", !codes.length);
    list.replaceChildren(...codes.map((c) => el("li", {},
      el("span", { class: "grow-text" }, `Made ${ago(c.created_at)} by ${c.created_by || "an admin"} · ${c.uses_left} of ${c.uses ?? c.uses_left} left · `,
        `until ${dayLabel(c.expires_at)}`, c.trusted ? "" : el("span", { class: "small" }, " · computers wait for approval")),
      el("button", { onclick: async () => {
        try { await api({ op: "revoke-code", organisation_id: $("org").value, code_id: c.id }); await loadCodes(); }
        catch (e) { $("app-msg").textContent = friendly(e); }
      } }, "Withdraw"))));
  } catch (e) { $("codes-box").classList.add("hidden"); }
}
$("add-computers").addEventListener("click", () => { $("code-box").classList.add("hidden"); $("add-dialog").showModal(); loadCodes(); });
$("add-close").addEventListener("click", () => $("add-dialog").close());
$("make-code").addEventListener("click", async () => {
  try {
    const out = await api({ op: "new-code", organisation_id: $("org").value, hours: Number($("hours").value), uses: Number($("uses").value) });
    $("server").textContent = FLEET_SERVER;
    $("code").textContent = out.code;
    $("code-note").textContent = `Works for ${out.uses} computer${out.uses === 1 ? "" : "s"} within ${out.hours} hours.` +
      (out.approved === false ? " Computers that join with it wait for the Owner's approval." : "");
    $("code-box").classList.remove("hidden");
    await loadCodes();
  } catch (e) { $("app-msg").textContent = friendly(e); }
});
$("signin-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const email = $("email").value.trim();
  const { error } = await supabase.auth.signInWithOtp({ email, options: { emailRedirectTo: location.origin + location.pathname, shouldCreateUser: true } });
  $("signin-msg").textContent = !error
    ? "Check your email and open the sign-in link in this browser. If you ask again, only the newest email's link works."
    : error.status === 429 || /rate limit|only request this after/i.test(error.message)
      ? "Too many sign-in emails for now. Wait a few minutes (up to an hour if you asked several times), then try again."
      : friendly(error);
});

if (linkError) $("signin-msg").textContent = linkError;

go(location.hash.slice(1), false);

// Fires once at start (INITIAL_SESSION) and on every sign-in or sign-out.
supabase.auth.onAuthStateChange((event, session) => {
  show(!!session);
  if (session && (event === "INITIAL_SESSION" || event === "SIGNED_IN")) setTimeout(load, 0);
});
