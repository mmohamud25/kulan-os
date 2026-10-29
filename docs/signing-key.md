# The update signing key

Every Kulan OS computer installs, as root, whatever the update channel offers
that this key signed. The key also signs the ISOs' SHA256SUMS, which Kulan USB
Maker checks before it writes a stick. So whoever holds it can update every
Kulan OS computer. This page says where it lives and how it's changed.

## Where it lives

- **The primary key**: only in a backup locked with a passphrase
  (`kulan-os-signing-key-BACKUP.asc.gpg`), kept offline: a password manager
  or an encrypted drive. Never on GitHub once signing moves to a subkey.
- **The signing subkey** (expires after a year): the secret
  `KULAN_APT_SIGNING_KEY` of the `github-pages` environment, which only the
  `main` branch can use. The jobs that sign (publish, release, the weekly
  re-sign) run in that environment; builds of other branches can't read it.
- **The public key**: `packages/kulan-archive-keyring.gpg`, shipped in
  kulan-base (apt trusts it for the Kulan channel only), served at
  `/apt/kulan-archive-keyring.gpg`, and built into Kulan USB Maker.

What the key can do is limited on the computers too: apt takes only Kulan's
own packages from the channel (`KULAN_CHANNEL_PACKAGES` in kulan.conf, pinned
in `/etc/apt/preferences.d/50-kulan-channel`), so a stolen key can't replace
Debian's packages such as sudo or ssh. Automatic updates take the channel only
from its own site (os.kulangroup.com).

## Once: from the 2.9 setup to this

`scripts/setup-update-channel.sh` (before 2.10) made one key with no
passphrase, stored it as a repository-wide secret, and left a plain backup in
your home folder. On your Mac, from the project folder:

1. `scripts/signing-key.sh protect ~/kulan-os-signing-key-BACKUP.asc`
   moves the secret to the `github-pages` environment (main only), deletes the
   repository-wide one, locks the backup with a passphrase you choose and
   deletes the plain file. If Time Machine has a copy of the plain file,
   delete it there too.
2. `scripts/signing-key.sh add-subkey ~/kulan-os-signing-key-BACKUP.asc.gpg`
   adds a signing subkey that expires in a year and writes the new public key
   to `packages/kulan-archive-keyring.gpg`. Commit it and release: computers
   take the new public key with that update. The channel is still signed with
   the primary key meanwhile.
3. A week after that release: `scripts/signing-key.sh use-subkey
   ~/kulan-os-signing-key-BACKUP.asc.gpg`. From then on GitHub holds only
   the subkey.

Also on GitHub, for the repository: protect `main` (a pull request with a
review, no force-push), and allow changing workflows only through it.

## Every year

Two months before the subkey expires (`gpg --show-keys
packages/kulan-archive-keyring.gpg` shows the date):
`scripts/signing-key.sh renew <backup>`, commit, release, and a week later
`scripts/signing-key.sh use-subkey <backup>`. Release a new Kulan USB Maker
too, since it carries the public key.

## If the subkey leaks

1. `scripts/signing-key.sh renew <backup>` and release at once; then
   `use-subkey`.
2. Revoke the leaked subkey: `gpg --edit-key <fingerprint>`, `key N`,
   `revkey`, then export the public key to the keyring file again and
   release. Computers stop trusting it with that update.

## If the primary key leaks or is lost

Installed computers can't be moved to a new primary key safely by an update
signed with a key an attacker may also hold. Make a new key
(`scripts/setup-update-channel.sh` after removing the old keyring file),
release kulan-base with both public keys signed by the old key while it's
still trusted, and tell people to check the new key's fingerprint on the
website. Plan this with care before doing it.
