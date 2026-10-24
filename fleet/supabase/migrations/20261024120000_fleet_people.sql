-- Kulan Fleet 2.8: People (the organisation's accounts on its computers), the
-- Owner, and two more actions. Same private "fleet" schema (no Data API
-- access, RLS on with no policies): only the fleet and fleet-admin Edge
-- Functions use it.

-- The Owner of each organisation (by the email they sign in to Kulan Fleet
-- with): only the Owner adds or removes Kulan Fleet admins. A recovery code
-- (only its SHA-256 kept) lets the Owner move ownership to a new email if
-- they lose the old one.
alter table fleet.organisations
  add column if not exists owner_email text check (owner_email is null or (owner_email = lower(owner_email) and position('@' in owner_email) > 1)),
  add column if not exists recovery_hash text check (recovery_hash is null or recovery_hash ~ '^[0-9a-f]{64}$');

-- The people who use the organisation's computers. Passwords are never
-- stored: only a one-way crypt(3) hash (yescrypt or SHA-512), made on the
-- admin's computer for a setup code or on a person's computer when they
-- choose their own password.
create table if not exists fleet.people (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  username text not null check (username ~ '^[a-z][a-z0-9_-]{0,31}$'
    and username not in ('root', 'daemon', 'bin', 'sys', 'sync', 'games', 'man', 'lp', 'mail', 'news', 'uucp',
                         'proxy', 'www-data', 'backup', 'list', 'irc', 'nobody', 'admin', 'sudo', 'kulan', 'gdm', 'systemd')),
  full_name text not null default '' check (char_length(full_name) <= 80 and full_name !~ '[:,=\n\r\t\\]'),
  email text check (email is null or (email = lower(email) and position('@' in email) > 1 and char_length(email) <= 254)),
  uid integer not null check (uid between 61000 and 69999),
  role text not null default 'employee' check (role in ('owner', 'admin', 'employee')),
  enabled boolean not null default true,
  password_hash text check (password_hash is null or password_hash ~ '^\$(y|gy|6|5|2b|2y|7)\$[./A-Za-z0-9$=,]{8,250}$'),
  must_change boolean not null default true,
  password_changed_at bigint not null default 0,      -- unix seconds; the newest password wins
  all_computers boolean not null default true,
  removed_at timestamptz,                             -- still sent (as removed) for 30 days
  remove_files boolean not null default false,
  created_at timestamptz not null default now(),
  created_by text not null
);
create unique index if not exists people_org_username_idx on fleet.people (organisation_id, username);
create unique index if not exists people_org_uid_idx on fleet.people (organisation_id, uid);
-- One Owner's account per organisation (the Owner's own computer account).
create unique index if not exists people_one_owner_idx on fleet.people (organisation_id)
  where role = 'owner' and removed_at is null;
alter table fleet.people enable row level security;
revoke all on fleet.people from public, anon, authenticated;

-- The computers a person may use when not all of them.
create table if not exists fleet.person_computers (
  person_id uuid not null references fleet.people(id) on delete cascade,
  device_id uuid not null references fleet.devices(id) on delete cascade,
  primary key (person_id, device_id)
);
create index if not exists person_computers_device_idx on fleet.person_computers (device_id);
alter table fleet.person_computers enable row level security;
revoke all on fleet.person_computers from public, anon, authenticated;

-- The uid for a new person (the same uid on every computer): one above the
-- highest so far, so a uid isn't given to someone else while an old computer
-- might still have the account; only when the range is used up, the lowest
-- free one. 0 when none is left.
create or replace function fleet.next_uid(org uuid)
returns integer
language sql
security invoker
set search_path = ''
as $$
  select coalesce(
    (select max(uid) + 1 from fleet.people where organisation_id = org having max(uid) < 69999),
    (select min(u) from generate_series(61000, 69999) u
     where not exists (select 1 from fleet.people p where p.organisation_id = org and p.uid = u)),
    0);
$$;
revoke all on function fleet.next_uid(uuid) from public, anon, authenticated;

-- People removed more than 30 days ago are forgotten (every computer that
-- reported since has removed their account).
create or replace function fleet.prune_people()
returns void
language sql
security invoker
set search_path = ''
as $$
  delete from fleet.people where removed_at < now() - interval '30 days';
$$;
revoke all on function fleet.prune_people() from public, anon, authenticated;

-- Shut down (5 minutes' notice, like restart) and Lost mode (nobody can use
-- the computer until an admin releases it).
alter table fleet.actions drop constraint if exists actions_type_check;
alter table fleet.actions add constraint actions_type_check
  check (type in ('refresh', 'install-updates', 'compliance-on', 'lock-screen', 'restart', 'erase',
                  'shutdown', 'lost-mode-on', 'lost-mode-off'));
