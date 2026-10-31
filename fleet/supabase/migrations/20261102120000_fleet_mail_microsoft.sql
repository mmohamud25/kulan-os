-- Kulan Fleet 2.12: Microsoft 365 mailboxes with People, as Google Workspace's
-- (2.8e). Same table: one connection per organisation, to one provider.
--   provider        'google' or now 'microsoft'
--   tenant_id       Microsoft Entra's Directory (tenant) ID
--   client_id       the app's Application (client) ID (Google: the service account's)
--   client_email    Microsoft: the client ID again (the column is required)
--   admin_email     Microsoft: the Owner who connected it (Kulan Fleet acts as the app)
--   private_key     Microsoft: the client secret, sealed as Google's key is
--   licence         the licence (SKU id) with Exchange Online new mailboxes get
--   licence_name    its name, for the dashboard (ENTERPRISEPACK...)
--   usage_location  the country mailboxes are used in (Microsoft needs it for a licence)
-- mailbox_google_id holds Microsoft's object id for Microsoft 365 mailboxes.
alter table fleet.mail_connections drop constraint if exists mail_connections_provider_check;
alter table fleet.mail_connections add constraint mail_connections_provider_check
  check (provider in ('google', 'microsoft'));
alter table fleet.mail_connections add column if not exists tenant_id text
  check (tenant_id is null or tenant_id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');
alter table fleet.mail_connections add column if not exists licence text
  check (licence is null or licence ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');
alter table fleet.mail_connections add column if not exists licence_name text
  check (licence_name is null or char_length(licence_name) <= 64);
alter table fleet.mail_connections add column if not exists usage_location text
  check (usage_location is null or usage_location ~ '^[A-Z]{2}$');
alter table fleet.mail_connections drop constraint if exists mail_connections_microsoft_check;
alter table fleet.mail_connections add constraint mail_connections_microsoft_check
  check (provider <> 'microsoft' or (tenant_id is not null and licence is not null and usage_location is not null));
