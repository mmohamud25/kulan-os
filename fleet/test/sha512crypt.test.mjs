// The dashboard's SHA-512 crypt gives exactly what `openssl passwd -6` gives
// (the same as glibc's crypt and so /etc/shadow), for many passwords and
// salts, including the lengths where the algorithm branches.
//   node fleet/test/sha512crypt.test.mjs
import { execFileSync } from "node:child_process";
import { sha512crypt, newSalt, setupCode } from "../dashboard/sha512crypt.js";

// The specification's own example.
const known = await sha512crypt("Hello world!", "saltstring");
if (known !== "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1") {
  throw new Error(`the specification's example: ${known}`);
}
const passwords = ["a", "K7Q4-9XPA-M2RT", "x".repeat(63), "y".repeat(64), "z".repeat(65), "w".repeat(129),
                   "pässwörd ünïcode", "with space and $ and \\"];
let checked = 0;
for (const pw of passwords) {
  for (const salt of [newSalt(), "short", "exactlysixteen16"]) {
    const ours = await sha512crypt(pw, salt);
    const theirs = execFileSync("openssl", ["passwd", "-6", "-salt", salt, "-stdin"], { input: pw + "\n" }).toString().trim();
    if (ours !== theirs) throw new Error(`differs for ${JSON.stringify(pw)} / ${salt}:\n ${ours}\n ${theirs}`);
    checked++;
  }
}
const codes = new Set(Array.from({ length: 200 }, setupCode));
if (codes.size !== 200 || ![...codes].every((c) => /^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/.test(c))) {
  throw new Error("setup codes");
}
console.log(`sha512crypt test passed (${checked} hashes match openssl)`);
