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
