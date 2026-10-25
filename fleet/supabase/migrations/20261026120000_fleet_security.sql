-- Kulan Fleet 2.8c: the weekly security report's snapshots and the
-- admin-rights reviews. Same private "fleet" schema (no Data API access, RLS
-- on with no policies): only the fleet-admin Edge Function uses them.

-- One snapshot a week per organisation (the Monday the week starts, UTC),
-- written when the report is opened, so the next week's report can show
-- what changed. Kept about a year.
create table if not exists fleet.weekly_reports (
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  week date not null,
  summary jsonb not null check (jsonb_typeof(summary) = 'object' and pg_column_size(summary) < 65536),
  made_at timestamptz not null default now(),
  primary key (organisation_id, week)
);
alter table fleet.weekly_reports enable row level security;
revoke all on fleet.weekly_reports from public, anon, authenticated;

-- An admin-rights review: who went through the list of administrators,
-- when, and what they kept and took away (due every 90 days).
create table if not exists fleet.access_reviews (
  id bigint generated always as identity primary key,
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  reviewed_by text not null,
  reviewed_at timestamptz not null default now(),
  kept integer not null default 0 check (kept >= 0),
  removed integer not null default 0 check (removed >= 0),
  detail jsonb check (detail is null or (jsonb_typeof(detail) = 'object' and pg_column_size(detail) < 65536))
);
create index if not exists access_reviews_org_idx on fleet.access_reviews (organisation_id, reviewed_at desc);
alter table fleet.access_reviews enable row level security;
revoke all on fleet.access_reviews from public, anon, authenticated;

create or replace function fleet.prune_weekly_reports()
returns void
language sql
security invoker
set search_path = ''
as $$
  delete from fleet.weekly_reports where week < (now() - interval '400 days')::date;
$$;
revoke all on function fleet.prune_weekly_reports() from public, anon, authenticated;
