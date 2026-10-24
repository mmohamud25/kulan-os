// Which week a count goes under (used by index.ts; tested by week.test.ts).
const DAY_MS = 86_400_000;

// This week's Monday (UTC), as YYYY-MM-DD.
export function mondayOf(now: Date): string {
  const day = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const sinceMonday = (new Date(day).getUTCDay() + 6) % 7;
  return new Date(day - sinceMonday * DAY_MS).toISOString().slice(0, 10);
}

// The week a computer says it's counting: this week, or last week (a count
// sent just before midnight on Sunday that arrives on Monday). Anything else
// is refused (null), so totals can't be added to weeks long gone or not yet
// here. No week (Kulan OS before 2.7): this week.
export function acceptedWeek(week: unknown, now: Date): string | null {
  const thisWeek = mondayOf(now);
  if (week === undefined) return thisWeek;
  if (typeof week !== "string" || !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(week)) return null;
  const lastWeek = new Date(Date.parse(thisWeek + "T00:00:00Z") - 7 * DAY_MS).toISOString().slice(0, 10);
  return week === thisWeek || week === lastWeek ? week : null;
}
