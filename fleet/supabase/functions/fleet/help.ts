// Remote help and instant requests for Kulan Fleet (2.9a).
//
// Waking a computer: each computer listens on a Supabase Realtime broadcast
// topic of its own (fleet.devices.wake_topic); a "wake" there makes it
// report at once, so requests reach it in seconds rather than at its next
// hourly report. The wake carries nothing.
//
// The relay: Cloudflare Realtime TURN, with credentials made per remote help
// session (CLOUDFLARE_TURN_KEY_ID, CLOUDFLARE_TURN_TOKEN). Without them, only
// Cloudflare's free STUN is given: direct connections still work (the same
// network, most home routers), relayed ones don't.

export const STUN_ONLY = [{ urls: ["stun:stun.cloudflare.com:3478"] }];
const TURN_TTL_S = 62 * 60;  // a session lasts at most an hour

export type IceServer = { urls: string[]; username?: string; credential?: string };

// The ICE servers for one session: Cloudflare's relay when Kulan Fleet has a
// key for it, STUN only otherwise (and if Cloudflare doesn't answer).
export async function iceServers(): Promise<IceServer[]> {
  const id = Deno.env.get("CLOUDFLARE_TURN_KEY_ID"), token = Deno.env.get("CLOUDFLARE_TURN_TOKEN");
  if (!id || !token) return STUN_ONLY;
  try {
    const res = await fetch(`https://rtc.live.cloudflare.com/v1/turn/keys/${encodeURIComponent(id)}/credentials/generate-ice-servers`, {
      method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ ttl: TURN_TTL_S }), signal: AbortSignal.timeout(8000),
    });
    const body = await res.json().catch(() => ({}));
    const list = Array.isArray(body?.iceServers) ? body.iceServers : [];
    const out: IceServer[] = [];
    for (const s of list.slice(0, 4)) {
      const urls = (Array.isArray(s?.urls) ? s.urls : [s?.urls]).filter((u: unknown) =>
        typeof u === "string" && /^(stun|turns?):[a-z0-9.-]+(:\d+)?(\?transport=(udp|tcp))?$/i.test(u) && !/:53(\?|$)/.test(u));
      if (!urls.length) continue;
      out.push(typeof s.username === "string" && typeof s.credential === "string"
        ? { urls, username: s.username.slice(0, 512), credential: s.credential.slice(0, 512) } : { urls });
    }
    return out.length ? out : STUN_ONLY;
  } catch {
    return STUN_ONLY;
  }
}

// Wake computers (Realtime broadcast, Supabase's own API with the service key).
// Never in the way: a wake that doesn't arrive only means the next hourly report.
export async function wake(topics: string[]): Promise<number> {
  const list = topics.filter((t) => typeof t === "string" && /^wake-[0-9a-f]{48}$/.test(t)).slice(0, 1000);
  if (!list.length) return 0;
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  try {
    const res = await fetch(`${Deno.env.get("SUPABASE_URL")}/realtime/v1/api/broadcast`, {
      method: "POST",
      headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
      body: JSON.stringify({ messages: list.map((topic) => ({ topic, event: "wake", payload: {} })) }),
      signal: AbortSignal.timeout(5000),
    });
    return res.ok ? list.length : 0;
  } catch {
    return 0;
  }
}

// A session's own key (the computer's helper uses it for that session only).
export function sessionToken(): string {
  const b = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...b)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

// What the two sides send while connecting: an offer or answer, and network candidates.
export function cleanDescription(v: unknown, type: "offer" | "answer"): { type: string; sdp: string } | null {
  if (typeof v !== "object" || v === null) return null;
  const { type: t, sdp } = v as Record<string, unknown>;
  if (t !== type || typeof sdp !== "string" || sdp.length > 30000 || !sdp.startsWith("v=0")) return null;
  return { type, sdp };
}

export function cleanCandidates(v: unknown): Record<string, unknown>[] {
  if (!Array.isArray(v)) return [];
  return v.slice(0, 30).flatMap((c) => {
    if (typeof c !== "object" || c === null) return [];
    const { candidate, sdpMid, sdpMLineIndex } = c as Record<string, unknown>;
    if (typeof candidate !== "string" || candidate.length > 1000 || !(candidate === "" || candidate.startsWith("candidate:"))) return [];
    return [{ candidate, sdpMid: typeof sdpMid === "string" ? sdpMid.slice(0, 32) : null,
              sdpMLineIndex: Number.isInteger(sdpMLineIndex) && (sdpMLineIndex as number) >= 0 && (sdpMLineIndex as number) < 16
                ? sdpMLineIndex : null }];
  });
}

export const ACTIVE = ["requested", "asking", "allowed", "connected"];
export const MAX_CANDIDATES = 60;
