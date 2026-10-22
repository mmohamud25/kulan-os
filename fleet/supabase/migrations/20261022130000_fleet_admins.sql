-- Who may use the fleet dashboard (by the email they sign in with). An admin
-- with no organisation sees and manages every organisation.
create table fleet.admins (
  email text not null check (email = lower(email) and position('@' in email) > 1),
  organisation_id uuid references fleet.organisations(id) on delete cascade,
  created_at timestamptz not null default now()
);
create unique index admins_email_org_idx on fleet.admins (email, coalesce(organisation_id, '00000000-0000-0000-0000-000000000000'::uuid));
create index admins_organisation_idx on fleet.admins (organisation_id);
alter table fleet.admins enable row level security;
