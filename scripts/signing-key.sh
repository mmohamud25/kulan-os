#!/usr/bin/env bash
# The update signing key: keep it where only main's release jobs can read it,
# lock its backup, and move signing to a subkey that expires (and renew it).
# Run on your Mac from the project folder; see docs/signing-key.md for when.
#
#   scripts/signing-key.sh protect BACKUP.asc     1. the key only for main, the backup locked
#   scripts/signing-key.sh add-subkey BACKUP      2. a signing subkey (1 year); commit the new public key
#   scripts/signing-key.sh use-subkey BACKUP      3. once computers have it: CI holds only the subkey
#   scripts/signing-key.sh renew BACKUP           every year, two months before the subkey expires
#                                                 (a new subkey; then use-subkey a week after the release)
#   scripts/signing-key.sh environment [REPO]     (used by the above) github-pages: main only
#
# BACKUP is the private key's backup: the plain .asc from setup-update-channel.sh
# (protect locks it) or the locked .asc.gpg. The primary key never goes to
# GitHub after step 3: it stays in the locked backup, offline.
#
# Needs: gpg (brew install gnupg) and the GitHub CLI (gh auth login).
set -euo pipefail

cd "$(dirname "$0")/.."
KEYRING="packages/kulan-archive-keyring.gpg"
SECRET_NAME="KULAN_APT_SIGNING_KEY"
ENVIRONMENT="github-pages"
say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }
command -v gpg >/dev/null 2>&1 || die "gpg not found. Install it with: brew install gnupg"
command -v gh >/dev/null 2>&1 || die "GitHub CLI not found. Install it with: brew install gh"

repo() { gh repo view --json nameWithOwner --jq .nameWithOwner; }

# The environment the release jobs use, deployable from main only.
environment() {
	local r="${1:-$(repo)}"
	gh api -X PUT "repos/${r}/environments/${ENVIRONMENT}" --input - >/dev/null <<JSON
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
	gh api "repos/${r}/environments/${ENVIRONMENT}/deployment-branch-policies" --jq '.branch_policies[].name' | grep -qx main ||
		gh api -X POST "repos/${r}/environments/${ENVIRONMENT}/deployment-branch-policies" -f name=main -f type=branch >/dev/null
	say "${ENVIRONMENT}: deployable from main only"
}

# The key from BACKUP into a fresh, temporary GnuPG home.
load() {
	local backup="$1"
	[ -f "${backup}" ] || die "No backup at ${backup}"
	GNUPGHOME="$(mktemp -d)"
	export GNUPGHOME
	trap 'rm -rf "${GNUPGHOME}"' EXIT
	chmod 700 "${GNUPGHOME}"
	case "${backup}" in
		*.gpg) gpg --quiet --decrypt "${backup}" | gpg --batch --quiet --import ;;
		*) gpg --batch --quiet --import "${backup}" ;;
	esac
	FPR="$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr:/ {print $10; exit}')"
	[ -n "${FPR}" ] || die "The backup has no secret key"
	TRUSTED="$(gpg --batch --with-colons --show-keys "${KEYRING}" | awk -F: '/^fpr:/ {print $10; exit}')"
	[ "${FPR}" = "${TRUSTED}" ] || die "The backup's key (${FPR}) isn't the one Kulan OS trusts (${TRUSTED})"
}

# A locked backup of everything in the temporary home (primary and subkeys).
save_locked() {
	local out="$1"
	umask 077
	say "Locking the backup with a passphrase you choose now: ${out}"
	gpg --batch --armor --export-secret-keys "${FPR}" | gpg --symmetric --cipher-algo AES256 --armor --output "${out}.new"
	mv "${out}.new" "${out}"
}

subkey_secret_to_github() {
	say "Giving CI the signing subkey only (the primary key stays in your backup)"
	gpg --batch --armor --export-secret-subkeys "${FPR}" | gh secret set "${SECRET_NAME}" --repo "$(repo)" --env "${ENVIRONMENT}"
}

case "${1:-}" in
	environment)
		environment "${2:-}"
		;;
	protect)
		[ $# -eq 2 ] || die "usage: $0 protect BACKUP.asc"
		load "$2"
		environment
		say "Storing the key as a secret of ${ENVIRONMENT} (main only), and removing the repository-wide one"
		gpg --batch --armor --export-secret-keys "${FPR}" | gh secret set "${SECRET_NAME}" --repo "$(repo)" --env "${ENVIRONMENT}"
		gh secret delete "${SECRET_NAME}" --repo "$(repo)" 2>/dev/null || true
		case "$2" in
			*.gpg) ;;
			*)
				save_locked "$2.gpg"
				rm -P "$2" 2>/dev/null || rm "$2"
				say "The plain backup is deleted. If Time Machine backed it up before, delete it there too."
				;;
		esac
		;;
	add-subkey | renew)
		[ $# -eq 2 ] || die "usage: $0 $1 BACKUP"
		load "$2"
		say "Adding a signing subkey that expires in a year"
		gpg --batch --passphrase '' --quick-add-key "${FPR}" ed25519 sign 1y
		gpg --batch --export "${FPR}" > "${KEYRING}"
		case "$2" in *.gpg) save_locked "$2" ;; *) save_locked "$2.gpg" ;; esac
		cat <<EOF

Next: commit and push ${KEYRING} (it now lists the new subkey), and release.
Computers take the new public key with that update. A week after the release,
when they have it, run: $0 use-subkey <the backup>
Until then the update channel is signed as before.
EOF
		;;
	use-subkey)
		[ $# -eq 2 ] || die "usage: $0 use-subkey BACKUP"
		load "$2"
		gpg --batch --with-colons --list-keys "${FPR}" | grep -q '^sub:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:[^:]*:s' ||
			die "The backup has no signing subkey yet. Run: $0 add-subkey <backup>"
		subkey_secret_to_github
		;;
	*)
		sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
		exit 64
		;;
esac
