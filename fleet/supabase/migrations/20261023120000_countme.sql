-- Counting Kulan OS computers, privately (the other end of /usr/lib/kulan/countme).
-- Once a week a computer says only: Kulan OS version, PC or Mac (amd64/arm64),
-- and a rough install age. No ID, no address, nothing that follows a computer,
-- so only totals are kept: one row per week, version, architecture and age.
--
-- A private schema, like fleet: not exposed through the Data API; only the
-- countme Edge Function (direct Postgres connection) uses it. RLS is on with
-- no policies on purpose: the API roles get nothing.
create schema if not exists stats;
revoke all on schema stats from public, anon, authenticated;

create table stats.weekly_computers (
  week date not null check (extract(isodow from week) = 1),        -- the Monday (UTC)
  version text not null check (version ~ '^[0-9]{1,2}\.[0-9]{1,2}$'),
  arch text not null check (arch in ('amd64', 'arm64')),
  -- How long since the computer first counted itself: under a week, a month,
  -- six months, or longer (Fedora's "countme" buckets).
  age smallint not null check (age between 1 and 4),
  computers integer not null default 0 check (computers >= 0),
  primary key (week, version, arch, age)
);
alter table stats.weekly_computers enable row level security;
revoke all on all tables in schema stats from public, anon, authenticated;
alter default privileges in schema stats revoke all on tables from public, anon, authenticated;
