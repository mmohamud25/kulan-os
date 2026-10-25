// Kulan Fleet policies (2.8b): what one policy may say, and how a computer's
// policy is worked out from its organisation's and its groups'. The same
// file is in fleet/ and fleet-admin/ (each Edge Function is deployed on its
// own); CI checks the two copies are identical.
//
//   auto_updates, firewall, require_encryption   true or left out
//   screen_lock_minutes                           1-60
//   usb                                           "block" | "approved" (approved drives only)
//   required_apps, blocked_apps                   [{source: "debian" | "flatpak", id}]

export type App = { source: "debian" | "flatpak"; id: string };
export type Policy = {
  auto_updates?: true;
  firewall?: true;
  require_encryption?: true;
  screen_lock_minutes?: number;
  usb?: "block" | "approved";
  required_apps?: App[];
  blocked_apps?: App[];
};

export const MAX_APPS = 50;
const DEBIAN = /^[a-z0-9][a-z0-9+.-]{1,62}$/;
const FLATPAK = /^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+){2,}$/;
// Never blocked: Kulan OS itself and what it can't run without. (Each
// computer also refuses to remove anything that would take more with it.)
const PROTECTED = /^(kulan|linux-|systemd|gnome-shell|gnome-session|gdm3|mutter|apt|dpkg|libc6|bash|coreutils|sudo|login|passwd|network-manager|flatpak|gnome-software|packagekit|firmware-|grub|shim|plymouth|xdg-|dbus|polkitd|udev|init)/;

function cleanApps(value: unknown, blocking: boolean): App[] | null {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > MAX_APPS) return null;
  const out: App[] = [];
  const seen = new Set<string>();
  for (const a of value) {
    if (typeof a !== "object" || a === null) return null;
    const { source, id } = a as Record<string, unknown>;
    if (typeof id !== "string" || id.length > 255) return null;
    if (source === "debian" ? !DEBIAN.test(id) : source === "flatpak" ? !FLATPAK.test(id) : true) return null;
    if (blocking && source === "debian" && PROTECTED.test(id)) return null;
    const key = `${source}:${id}`;
    if (!seen.has(key)) out.push({ source: source as App["source"], id });
    seen.add(key);
  }
  return out;
}

// A policy as given by an admin: only known keys and sensible values; null
// if something can't be accepted (an app name that isn't one, a protected
// package blocked...), so mistakes are refused rather than dropped quietly.
export function cleanPolicy(value: unknown): Policy | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  const policy: Policy = {};
  for (const key of ["auto_updates", "firewall", "require_encryption"] as const) {
    if (v[key] === true) policy[key] = true;
  }
  const minutes = v.screen_lock_minutes;
  if (typeof minutes === "number" && Number.isInteger(minutes) && minutes >= 1 && minutes <= 60) {
    policy.screen_lock_minutes = minutes;
  }
  if (v.usb === "block" || v.usb === "approved") policy.usb = v.usb;
  else if (v.usb !== undefined && v.usb !== null && v.usb !== "") return null;
  const required = cleanApps(v.required_apps, false);
  const blocked = cleanApps(v.blocked_apps, true);
  if (required === null || blocked === null) return null;
  if (required.length) policy.required_apps = required;
  if (blocked.length) policy.blocked_apps = blocked;
  return policy;
}

const USB_RANK = { approved: 1, block: 2 } as const;

// One computer's policy from its organisation's and its groups': the
// stricter rule always wins, so adding a computer to a group can only make
// it safer. An app both required and blocked is blocked, and listed in
// conflicts so the admin can be told.
export function mergePolicies(policies: unknown[]): { policy: Policy; conflicts: string[] } {
  const out: Policy = {};
  const required = new Map<string, App>();
  const blocked = new Map<string, App>();
  for (const raw of policies) {
    const p = cleanPolicy(raw) ?? {};
    for (const key of ["auto_updates", "firewall", "require_encryption"] as const) {
      if (p[key]) out[key] = true;
    }
    if (p.screen_lock_minutes && (!out.screen_lock_minutes || p.screen_lock_minutes < out.screen_lock_minutes)) {
      out.screen_lock_minutes = p.screen_lock_minutes;
    }
    if (p.usb && (!out.usb || USB_RANK[p.usb] > USB_RANK[out.usb])) out.usb = p.usb;
    for (const a of p.required_apps ?? []) required.set(`${a.source}:${a.id}`, a);
    for (const a of p.blocked_apps ?? []) blocked.set(`${a.source}:${a.id}`, a);
  }
  const conflicts = [...required.keys()].filter((k) => blocked.has(k));
  const req = [...required.entries()].filter(([k]) => !blocked.has(k)).map(([, a]) => a);
  if (req.length) out.required_apps = req.slice(0, MAX_APPS * 4);
  if (blocked.size) out.blocked_apps = [...blocked.values()].slice(0, MAX_APPS * 4);
  return { policy: out, conflicts };
}
