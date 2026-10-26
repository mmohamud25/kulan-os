// What admins may ask a computer to run (2.9b), checked the same way by the
// server (fleet-admin) and, again, by the computer (kulan-fleet's fixes.py).
// The same file is in fleet/ and fleet-admin/.

// Kulan's ready-made fixes: each does one well-known thing as root.
export const FIXES: Record<string, string> = {
  "free-space": "Free up disk space",
  "reset-wifi": "Reset Wi-Fi",
  "repair-updates": "Repair updates",
  "restart-printing": "Restart printing",
  "check-disk": "Check the disk",
};

export const SCRIPT_MAX = 65536;
export const OUTPUT_MAX = 65536;

// A message for people's screens: a short title and a few sentences.
export function cleanMessage(v: unknown): { title: string; body: string } | null {
  if (typeof v !== "object" || v === null) return null;
  const { title, body } = v as Record<string, unknown>;
  const t = typeof title === "string" ? title.replace(/[\p{Cc}]/gu, " ").replace(/\s+/g, " ").trim().slice(0, 80) : "";
  const b = typeof body === "string" ? body.replace(/[^\P{Cc}\n]/gu, " ").replace(/[ \t]+/g, " ").trim().slice(0, 500) : "";
  return t ? { title: t, body: b } : null;
}

// A script as an admin wrote it: text, at most 64 KB, no NUL bytes.
export function cleanScript(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const body = v.replace(/\r\n/g, "\n");
  if (!body.trim() || body.includes("\0") || new TextEncoder().encode(body).length > SCRIPT_MAX) return null;
  return body;
}

// A Wi-Fi network: its name (1-32 bytes), open or WPA2/WPA3 personal (8-63
// characters, or 64 hex digits).
export function cleanNetwork(v: unknown): { ssid: string; security: "open" | "wpa-psk"; password: string | null; hidden: boolean } | null {
  if (typeof v !== "object" || v === null) return null;
  const { ssid, security, password, hidden } = v as Record<string, unknown>;
  if (typeof ssid !== "string" || /[\p{Cc}]/u.test(ssid)) return null;
  const size = new TextEncoder().encode(ssid).length;
  if (size < 1 || size > 32) return null;
  if (security === "open") return { ssid, security, password: null, hidden: hidden === true };
  if (security !== "wpa-psk" || typeof password !== "string") return null;
  if (!(/^[\x20-\x7e]{8,63}$/.test(password) || /^[0-9a-fA-F]{64}$/.test(password))) return null;
  return { ssid, security, password, hidden: hidden === true };
}

// A printer: a name people see and a driverless (IPP Everywhere) address.
export const PRINTER_URI = /^(ipps?|https?):\/\/[A-Za-z0-9.:\[\]_-]+(:[0-9]{1,5})?(\/[A-Za-z0-9._~/%+-]*)?$/;
export function cleanPrinter(v: unknown): { name: string; uri: string } | null {
  if (typeof v !== "object" || v === null) return null;
  const { name, uri } = v as Record<string, unknown>;
  const n = typeof name === "string" ? name.replace(/[\p{Cc}]/gu, " ").replace(/\s+/g, " ").trim().slice(0, 60) : "";
  const u = typeof uri === "string" ? uri.trim() : "";
  if (!n || u.length > 300 || !PRINTER_URI.test(u)) return null;
  return { name: n, uri: u };
}

// How a computer's group Wi-Fi networks and printers went, as it reports it:
// {networks: {id: what happened}, printers: {id: what happened}}.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function cleanPushed(v: unknown): { networks: Record<string, string>; printers: Record<string, string> } | undefined {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return undefined;
  const side = (x: unknown): Record<string, string> => {
    const out: Record<string, string> = {};
    if (typeof x !== "object" || x === null || Array.isArray(x)) return out;
    for (const [id, what] of Object.entries(x).slice(0, 50)) {
      if (UUID.test(id) && typeof what === "string") out[id] = what.replace(/[\p{Cc}]/gu, " ").slice(0, 120);
    }
    return out;
  };
  const { networks, printers } = v as Record<string, unknown>;
  return { networks: side(networks), printers: side(printers) };
}

// Get help (2.9c): what someone at a computer wrote, and the screenshot they
// added (base64, a JPEG or PNG they saw first, at most 360 KB).
export const HELP_TEXT_MAX = 2000;
export const HELP_REPLY_MAX = 500;
export const SCREENSHOT_MAX = 368640;
const HELP_USER = /^[a-z_][a-z0-9_.-]{0,31}$/;

export function imageType(bytes: Uint8Array): "image/jpeg" | "image/png" | null {
  if (bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return "image/jpeg";
  if (bytes.length > 8 && [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a].every((b, i) => bytes[i] === b)) return "image/png";
  return null;
}

export function cleanHelpRequest(v: unknown):
    { username: string; body: string; screenshot: Uint8Array | null; type: string | null } | null {
  if (typeof v !== "object" || v === null) return null;
  const { username, text, screenshot } = v as Record<string, unknown>;
  if (typeof username !== "string" || !HELP_USER.test(username)) return null;
  const body = typeof text === "string" ? text.replace(/[^\P{Cc}\n]/gu, " ").replace(/[ \t]+/g, " ").trim().slice(0, HELP_TEXT_MAX) : "";
  if (!body) return null;
  if (screenshot === undefined || screenshot === null || screenshot === "") return { username, body, screenshot: null, type: null };
  if (typeof screenshot !== "string" || screenshot.length > Math.ceil(SCREENSHOT_MAX / 3) * 4 + 4 ||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(screenshot)) return null;
  let bytes: Uint8Array;
  try {
    bytes = Uint8Array.from(atob(screenshot), (c) => c.charCodeAt(0));
  } catch {
    return null;
  }
  const type = imageType(bytes);
  if (!type || bytes.length > SCREENSHOT_MAX) return null;
  return { username, body, screenshot: bytes, type };
}

// The admin's answer, shown to the person: plain text, a few sentences.
export function cleanReply(v: unknown): string | null {
  if (typeof v !== "string") return null;
  return v.replace(/[^\P{Cc}\n]/gu, " ").replace(/[ \t]+/g, " ").trim().slice(0, HELP_REPLY_MAX) || null;
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
