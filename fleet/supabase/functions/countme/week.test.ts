// node --experimental-strip-types week.test.ts   (or: deno run week.test.ts)
import { acceptedWeek, mondayOf } from "./week.ts";

function eq(a: unknown, b: unknown): void {
  if (a !== b) throw new Error(`expected ${String(b)}, got ${String(a)}`);
}

const mondayMorning = new Date("2026-10-26T00:00:01Z");
const sundayNight = new Date("2026-10-25T23:59:59Z");
eq(mondayOf(mondayMorning), "2026-10-26");
eq(mondayOf(sundayNight), "2026-10-19");
eq(mondayOf(new Date("2027-01-01T12:00:00Z")), "2026-12-28");
// Sent at 23:59:59 on Sunday, arrived on Monday: filed under the week it counted.
eq(acceptedWeek("2026-10-19", mondayMorning), "2026-10-19");
eq(acceptedWeek("2026-10-26", mondayMorning), "2026-10-26");
// Older computers send no week: this week, as before.
eq(acceptedWeek(undefined, mondayMorning), "2026-10-26");
// Anything else is refused.
for (const bad of ["2026-10-12", "2026-11-02", "2026-10-20", "26-10-19", "2026-10-19; drop", 20261019, null, {}]) {
  eq(acceptedWeek(bad, mondayMorning), null);
}
console.log("countme week test passed");
