-- Kulan Fleet 2.9a: remote help, and requests that reach computers in
-- seconds. Same private "fleet" schema (no Data API access, RLS on with no
-- policies): only the fleet and fleet-admin Edge Functions use it.

-- Each computer listens on a Realtime broadcast topic of its own (a long
-- random name, given in its report replies). fleet-admin sends "wake" there
-- when it asks the computer something, and the computer reports at once
-- instead of at its next hourly report. A wake carries nothing: whoever
-- learns the name can only make the computer report early.
alter table fleet.devices add column if not exists wake_topic text
  check (wake_topic is null or wake_topic ~ '^wake-[0-9a-f]{48}$');

alter table fleet.actions drop constraint if exists actions_type_check;
alter table fleet.actions add constraint actions_type_check
  check (type in ('refresh', 'install-updates', 'compliance-on', 'lock-screen', 'restart', 'erase',
                  'shutdown', 'lost-mode-on', 'lost-mode-off', 'install-app', 'remote-help'));

-- A remote help session: an admin asks to see a computer's screen; the
-- person signed in there allows or declines; then the admin's browser or app
-- and the computer connect directly (WebRTC), relayed by Cloudflare when
-- they can't. Only the setting-up messages pass through here; the screen
-- itself never does, and nothing of it is kept.
--   state      requested (waiting for the computer) | asking (the person sees the question)
--              | allowed | connected | declined | ended | expired | failed
--   token_hash SHA-256 of the session's own key, made when the computer picks
--              the request up: its helper in the person's session uses it for
--              this session only (never the computer's own key)
--   control    the admin may use the mouse and keyboard (the person allowed it)
create table if not exists fleet.help_sessions (
  id uuid primary key default gen_random_uuid(),
  organisation_id uuid not null references fleet.organisations(id) on delete cascade,
  device_id uuid not null references fleet.devices(id) on delete cascade,
  requested_by text not null,
  token_hash text,
  state text not null default 'requested'
    check (state in ('requested', 'asking', 'allowed', 'connected', 'declined', 'ended', 'expired', 'failed')),
  control_asked boolean not null default false,
  control boolean not null default false,
  answered_by text check (answered_by is null or char_length(answered_by) <= 32),
  detail text check (detail is null or char_length(detail) <= 300),
  ice jsonb not null default '[]'::jsonb,           -- the relay's addresses and this session's credentials
  offer jsonb,                                      -- the computer's side: {type, sdp}
  answer jsonb,                                     -- the admin's side
  admin_candidates jsonb not null default '[]'::jsonb,
  device_candidates jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  ended_at timestamptz
);
create index if not exists help_sessions_device_idx on fleet.help_sessions (device_id, created_at desc);
-- One live session per computer (two admins asking at once: the second is told no).
create unique index if not exists help_sessions_live_idx on fleet.help_sessions (device_id)
  where state in ('requested', 'asking', 'allowed', 'connected');
-- Expiring looks only at live sessions.
create index if not exists help_sessions_active_idx on fleet.help_sessions (state, updated_at)
  where state in ('requested', 'asking', 'allowed', 'connected');
alter table fleet.help_sessions enable row level security;
revoke all on fleet.help_sessions from public, anon, authenticated;

-- Sessions are kept 30 days (their record is in the activity log for good).
create or replace function fleet.prune_help_sessions()
returns void
language sql
security invoker
set search_path = ''
as $$
  delete from fleet.help_sessions where created_at < now() - interval '30 days';
$$;
revoke all on function fleet.prune_help_sessions() from public, anon, authenticated;

-- Sessions that ran out: never picked up by the computer (10 minutes: it's
-- off or offline), nobody answered the question (2 minutes), or an hour
-- passed. Run by the functions before they read a session.
create or replace function fleet.expire_help_sessions()
returns void
language sql
security invoker
set search_path = ''
as $$
  update fleet.help_sessions
     set state = 'expired', ended_at = now(), updated_at = now(),
         detail = case state when 'requested' then 'the computer didn''t pick it up (off or offline?)'
                             else 'nobody answered on the computer' end
   where (state = 'requested' and created_at < now() - interval '10 minutes')
      or (state = 'asking' and updated_at < now() - interval '2 minutes');
  -- A request the computer never picked up doesn't wait in its queue.
  update fleet.actions a set status = 'expired', finished_at = now()
   where a.type = 'remote-help' and a.status = 'pending'
     and not exists (select 1 from fleet.help_sessions s where s.id = (a.payload->>'session_id')::uuid
                     and s.state in ('requested', 'asking', 'allowed', 'connected'));
  update fleet.help_sessions
     set state = 'ended', ended_at = now(), updated_at = now(), detail = 'an hour is the most a session lasts'
   where state in ('allowed', 'connected') and created_at < now() - interval '61 minutes';
$$;
revoke all on function fleet.expire_help_sessions() from public, anon, authenticated;
