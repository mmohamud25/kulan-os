-- Kulan Fleet 2.8d: stronger sign-in. Same private "fleet" schema (no Data
-- API access, RLS on with no policies): only the fleet and fleet-admin Edge
-- Functions use it.

-- The Owner can require an authenticator app code (Supabase MFA, "aal2")
-- for every Kulan Fleet admin of the organisation.
alter table fleet.organisations add column if not exists admin_two_step boolean not null default false;

-- A person's second step for signing in on the computers, set up on one of
-- them and sent to every computer they may use:
--   key   a security key's credential for pam_u2f (its public key: safe to share)
--   code  a phone code's secret for pam_oath, encrypted by the fleet
--         function with a key only it has (FLEET_FACTOR_KEY): never stored readable
create table if not exists fleet.person_factors (
  id uuid primary key,                -- made on the computer where it was set up
  person_id uuid not null references fleet.people(id) on delete cascade,
  kind text not null check (kind in ('key', 'code')),
  label text not null default '' check (char_length(label) <= 60),
  data text not null check (char_length(data) between 16 and 2048),
  created_at timestamptz not null default now(),
  created_on text check (created_on is null or char_length(created_on) <= 120)
);
create index if not exists person_factors_person_idx on fleet.person_factors (person_id);
alter table fleet.person_factors enable row level security;
revoke all on fleet.person_factors from public, anon, authenticated;
