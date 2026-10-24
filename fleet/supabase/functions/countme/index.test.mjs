// node --experimental-strip-types index.test.mjs   (in this folder)
// Runs index.ts with a stand-in Deno.serve and database: old (2.6) and new
// (2.7) computers' counts, refused weeks and fields, the totals page, 404.
// Runs index.ts with a stand-in Deno.serve and database.
import fs from "node:fs";
const inserts = [];
let handler;
globalThis.Deno = { env: { get: () => "postgres://x" }, serve: (h) => { handler = h; } };
const src = fs.readFileSync("index.ts", "utf8")
  .replace('import postgres from "npm:postgres@3.4.5";', 'const postgres = () => (strings, ...values) => { globalThis.__ins.push(values); return Promise.resolve([]); };')
  .replace('./week.ts', './week.ts');
fs.writeFileSync("index.run.ts", src.replace("const sql =", "export const _inserts = globalThis.__ins; const sql ="));
globalThis.__ins = inserts;
await import("./index.run.ts");
const post = (b) => handler(new Request("https://x/functions/v1/countme/v1", { method: "POST", body: JSON.stringify(b) }));
const ok = (cond, msg) => { if (!cond) { throw new Error(msg); } };
const monday = (() => { const n = new Date(); const d = Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate()); return new Date(d - ((new Date(d).getUTCDay() + 6) % 7) * 86400000).toISOString().slice(0, 10); })();
let r = await post({ version: "2.6", arch: "amd64", age: 1 });
ok(r.status === 204 && inserts.at(-1)[0] === monday, `old computer: ${r.status} ${inserts.at(-1)}`);
r = await post({ version: "2.7", arch: "arm64", age: 2, week: monday });
ok(r.status === 204 && inserts.at(-1)[0] === monday && inserts.at(-1)[1] === "2.7", `new computer: ${r.status}`);
const n = inserts.length;
r = await post({ version: "2.7", arch: "arm64", age: 2, week: "2020-01-06" });
ok(r.status === 400 && inserts.length === n && (await r.json()).error.includes("optionally week"), "old week refused");
r = await post({ version: "2.7", arch: "arm64", age: 2, id: "x" });
ok(r.status === 400 && inserts.length === n, "extra field refused");
r = await handler(new Request("https://x/functions/v1/countme/v1", { method: "GET" }));
ok(r.status === 200, "GET");
r = await handler(new Request("https://x/functions/v1/countme/nope", { method: "GET" }));
ok(r.status === 404, "404");
fs.unlinkSync("index.run.ts");
console.log("countme function test passed");
