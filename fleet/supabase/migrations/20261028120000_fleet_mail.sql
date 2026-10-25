-- Kulan Fleet 2.8e: Google Workspace mailboxes with People. Same private
-- "fleet" schema (no Data API access, RLS on with no policies): only the
-- fleet and fleet-admin Edge Functions use it.

-- An organisation's connection to its mail provider: Google Workspace now,
-- through a service account with domain-wide delegation acting as one of
-- the organisation's Google super admins. The service account's private
-- key is kept sealed by fleet-admin (AES-GCM, FLEET_FACTOR_KEY): never
-- stored readable, never shown again.
create table if not exists fleet.mail_connections (
  organisation_id uuid primary key references fleet.organisations(id) on delete cascade,
  provider text not null default 'google' check (provider in ('google')),
  domain text not null check (char_length(domain) between 3 and 253),
  admin_email text not null check (char_length(admin_email) <= 254),
  client_email text not null check (char_length(client_email) <= 254),
  client_id text check (client_id is null or char_length(client_id) <= 64),
  private_key text not null check (char_length(private_key) between 16 and 8192),
  connected_by text,
  connected_at timestamptz not null default now(),
  checked_at timestamptz,
  last_error text check (last_error is null or char_length(last_error) <= 300)
);
alter table fleet.mail_connections enable row level security;
revoke all on fleet.mail_connections from public, anon, authenticated;

-- A person's mailbox, managed with them:
--   mailbox        the address (null: none, or not managed by Kulan Fleet)
--   mailbox_state  active | suspended | deleted, as Kulan Fleet last left it
--   mailbox_todo   what still has to be done in Google (suspend, unsuspend,
--                  delete, rename) because Google refused or couldn't be reached
--   mailbox_error  why, in a sentence
alter table fleet.people add column if not exists mailbox text
  check (mailbox is null or (char_length(mailbox) <= 254 and mailbox = lower(mailbox)));
alter table fleet.people add column if not exists mailbox_state text
  check (mailbox_state is null or mailbox_state in ('active', 'suspended', 'deleted'));
alter table fleet.people add column if not exists mailbox_todo text
  check (mailbox_todo is null or mailbox_todo in ('suspend', 'unsuspend', 'delete', 'rename'));
alter table fleet.people add column if not exists mailbox_error text
  check (mailbox_error is null or char_length(mailbox_error) <= 300);
-- One person per mailbox in an organisation (removed people's included
-- while they're kept, so a mailbox being suspended or deleted isn't reused).
create unique index if not exists people_org_mailbox_idx on fleet.people (organisation_id, mailbox)
  where mailbox is not null and mailbox_state is distinct from 'deleted';

-- Removed people are forgotten after 30 days, except while something still
-- has to be done to their mailbox (it stays an alert until it's done).
create or replace function fleet.prune_people()
returns void
language sql
security invoker
set search_path = ''
as $$
  delete from fleet.people where removed_at < now() - interval '30 days' and mailbox_todo is null;
$$;
revoke all on function fleet.prune_people() from public, anon, authenticated;
