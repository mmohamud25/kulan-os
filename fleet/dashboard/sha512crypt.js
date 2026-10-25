// SHA-512 crypt ("$6$", Ulrich Drepper's specification, as in glibc and
// /etc/shadow), with the browser's own SHA-512 (WebCrypto). The dashboard
// makes a person's setup code here and sends only this one-way hash: the
// code never leaves the admin's browser. Tested against `openssl passwd -6`
// (fleet/test/sha512crypt.test.mjs).
const ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
const ROUNDS = 5000; // the default, written without "rounds=" (as glibc does)

const enc = new TextEncoder();

async function sha512(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const all = new Uint8Array(total);
  let at = 0;
  for (const p of parts) { all.set(p, at); at += p.length; }
  return new Uint8Array(await crypto.subtle.digest("SHA-512", all));
}

// `bytes` repeated to exactly `length` bytes.
function stretch(bytes, length) {
  const out = new Uint8Array(length);
  for (let i = 0; i < length; i++) out[i] = bytes[i % bytes.length];
  return out;
}

function encode64(b) {
  const order = [[0, 21, 42], [22, 43, 1], [44, 2, 23], [3, 24, 45], [25, 46, 4], [47, 5, 26], [6, 27, 48],
                 [28, 49, 7], [50, 8, 29], [9, 30, 51], [31, 52, 10], [53, 11, 32], [12, 33, 54], [34, 55, 13],
                 [56, 14, 35], [15, 36, 57], [37, 58, 16], [59, 17, 38], [18, 39, 60], [40, 61, 19], [62, 20, 41]];
  let out = "";
  const put = (w, n) => { for (let i = 0; i < n; i++) { out += ITOA64[w & 63]; w >>= 6; } };
  for (const [x, y, z] of order) put((b[x] << 16) | (b[y] << 8) | b[z], 4);
  put(b[63], 2);
  return out;
}

export async function sha512crypt(password, salt) {
  const P = enc.encode(password);
  const S = enc.encode(salt).slice(0, 16);
  const B = await sha512(P, S, P);
  // A: the password, the salt, B for each byte of the password, then B or
  // the password for each bit of the password's length.
  const aParts = [P, S];
  let n = P.length;
  for (; n > 64; n -= 64) aParts.push(B);
  aParts.push(B.slice(0, n));
  for (let len = P.length; len > 0; len >>= 1) aParts.push(len & 1 ? B : P);
  const A = await sha512(...aParts);
  const DP = await sha512(...Array(P.length).fill(P));
  const Pseq = stretch(DP, P.length);
  const DS = await sha512(...Array(16 + A[0]).fill(S));
  const Sseq = stretch(DS, S.length);
  let C = A;
  for (let i = 0; i < ROUNDS; i++) {
    const parts = [i & 1 ? Pseq : C];
    if (i % 3) parts.push(Sseq);
    if (i % 7) parts.push(Pseq);
    parts.push(i & 1 ? C : Pseq);
    C = await sha512(...parts);
  }
  return `$6$${new TextDecoder().decode(S)}$${encode64(C)}`;
}

// A fresh 16-character salt from crypt's own alphabet.
export function newSalt() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => ITOA64[b & 63]).join("");
}

// 12 random characters (60 bits) in groups of four, without 0/O or 1/I/L:
// typed once at the sign-in screen, then replaced by the person's own password.
export function setupCode() {
  const letters = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  const chars = Array.from(bytes, (b) => letters[b & 31]).join("");
  return chars.match(/.{4}/g).join("-");
}
