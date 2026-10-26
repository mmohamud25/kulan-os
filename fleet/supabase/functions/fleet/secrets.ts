// Secrets Kulan Fleet keeps for organisations (2.9b: Wi-Fi passwords), sealed
// with AES-GCM under a key derived from FLEET_FACTOR_KEY (HKDF, one key per
// purpose) and bound to the row they belong to (AES-GCM's associated data):
// copied elsewhere, a sealed secret doesn't open. The same file is in fleet/
// and fleet-admin/ (CI checks the two copies are identical).

const keys = new Map<string, CryptoKey | null>();

async function keyFor(purpose: string): Promise<CryptoKey | null> {
  if (keys.has(purpose)) return keys.get(purpose)!;
  let key: CryptoKey | null = null;
  try {
    const raw = Uint8Array.from(atob(Deno.env.get("FLEET_FACTOR_KEY") ?? ""), (c) => c.charCodeAt(0));
    if (raw.length === 32) {
      const base = await crypto.subtle.importKey("raw", raw, "HKDF", false, ["deriveKey"]);
      key = await crypto.subtle.deriveKey(
        { name: "HKDF", hash: "SHA-256", salt: new Uint8Array(32), info: new TextEncoder().encode(`kulan-fleet ${purpose} key v1`) },
        base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    }
  } catch {
    key = null;
  }
  keys.set(purpose, key);
  return key;
}

const toB64 = (bytes: Uint8Array) => {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
};

// "s1:" + base64(12-byte IV + AES-GCM ciphertext); null when Kulan Fleet's key isn't set.
export async function seal(text: string, purpose: string, bound: string): Promise<string | null> {
  const key = await keyFor(purpose);
  if (!key) return null;
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: new TextEncoder().encode(bound) },
                                                        key, new TextEncoder().encode(text)));
  const all = new Uint8Array(12 + ct.length);
  all.set(iv);
  all.set(ct, 12);
  return "s1:" + toB64(all);
}

export async function open(sealed: string, purpose: string, bound: string): Promise<string | null> {
  const key = await keyFor(purpose);
  if (!key || !sealed.startsWith("s1:")) return null;
  try {
    const all = Uint8Array.from(atob(sealed.slice(3)), (c) => c.charCodeAt(0));
    return new TextDecoder().decode(await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: all.slice(0, 12), additionalData: new TextEncoder().encode(bound) }, key, all.slice(12)));
  } catch {
    return null;
  }
}
