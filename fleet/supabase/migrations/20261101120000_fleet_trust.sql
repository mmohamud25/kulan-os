-- Kulan Fleet 2.10: from the security review.
--
-- New computers are trusted only when the Owner says so. A computer that
-- joins with a code the Owner made is approved at once; one that joins with a
-- code another admin made waits for the Owner's approval, and until then gets
-- no accounts (no password hashes, no phone codes), no Wi-Fi passwords, no
-- printers and no requests. So an admin can't make a computer of their own to
-- read everyone's passwords or set the Owner's.
--
-- Join codes are listed (never the code itself) and can be withdrawn. Codes
-- made before this (who made them is unknown) make computers that wait.

-- Every computer already in an organisation stays trusted (only when the
-- column is first added: running this again approves nothing).
do $$
begin
  if not exists (select 1 from information_schema.columns
                 where table_schema = 'fleet' and table_name = 'devices' and column_name = 'approved_at') then
    alter table fleet.devices add column approved_at timestamptz, add column approved_by text;
    update fleet.devices set approved_at = enrolled_at;
  end if;
end;
$$;

alter table fleet.enrolment_codes
  add column if not exists id uuid not null default gen_random_uuid(),
  add column if not exists trusted boolean not null default false,
  add column if not exists created_by text,
  add column if not exists uses integer;
create unique index if not exists enrolment_codes_id_idx on fleet.enrolment_codes (id);

-- A join code: trusted (the Owner's) or not; returns the code once, only its hash is kept.
create or replace function fleet.new_join_code(org uuid, valid_hours integer, max_uses integer, is_trusted boolean, made_by text)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  code text := upper(substr(translate(encode(extensions.gen_random_bytes(12), 'base64'), '+/=0O1Il', ''), 1, 4)
                || '-' || substr(translate(encode(extensions.gen_random_bytes(12), 'base64'), '+/=0O1Il', ''), 1, 4)
                || '-' || substr(translate(encode(extensions.gen_random_bytes(12), 'base64'), '+/=0O1Il', ''), 1, 4));
begin
  insert into fleet.enrolment_codes (code_hash, organisation_id, uses_left, uses, expires_at, trusted, created_by)
  values (encode(extensions.digest(code, 'sha256'), 'hex'), org, max_uses, max_uses,
          now() + make_interval(hours => valid_hours), is_trusted, made_by);
  return code;
end;
$$;
revoke all on function fleet.new_join_code(uuid, integer, integer, boolean, text) from public, anon, authenticated;
