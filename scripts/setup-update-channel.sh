#!/usr/bin/env bash
# One-time setup for the Kulan OS update channel. Run this on your Mac from
# the project folder:
#
#     scripts/setup-update-channel.sh
#
# It:
#   1. creates the signing key that proves updates really come from you,
#   2. stores the private half as a GitHub secret (it never goes in the repo),
#   3. saves the public half in packages/ so every Kulan OS trusts it,
#   4. turns on GitHub Pages, which hosts the updates,
#   5. saves a backup of the private key for you to keep somewhere safe.
#
# Needs: gpg (brew install gnupg) and the GitHub CLI (brew install gh, then
# gh auth login). Works with the bash that ships with macOS.
set -euo pipefail

cd "$(dirname "$0")/.."
KEYRING="packages/kulan-archive-keyring.gpg"
BACKUP="${HOME}/kulan-os-signing-key-BACKUP.asc.gpg"
SECRET_NAME="KULAN_APT_SIGNING_KEY"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

command -v gpg >/dev/null 2>&1 || die "gpg not found. Install it with: brew install gnupg"
command -v gh >/dev/null 2>&1 || die "GitHub CLI not found. Install it with: brew install gh"
gh auth status >/dev/null 2>&1 || die "Not signed in to GitHub. Run: gh auth login"
[ -f kulan.conf ] || die "Run this from the kulan-os project folder."

# shellcheck source=../kulan.conf
. ./kulan.conf

REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)" ||
	die "This folder isn't linked to a GitHub repository."

if [ -f "${KEYRING}" ]; then
	die "${KEYRING} already exists, so the update channel is already set up.
Delete it first only if you mean to replace the key - installed systems would
then need the new key before they accept updates again."
fi

say "Creating the signing key for ${KULAN_NAME} updates"
GNUPGHOME="$(mktemp -d)"
export GNUPGHOME
trap 'rm -rf "${GNUPGHOME}"' EXIT
chmod 700 "${GNUPGHOME}"

UID_TEXT="${KULAN_NAME} Archive Signing Key <${KULAN_MAINTAINER_EMAIL}>"
# No passphrase: GitHub's build machines sign updates unattended. The key is
# protected by GitHub's encrypted secrets instead.
gpg --batch --quiet --passphrase '' --quick-generate-key "${UID_TEXT}" ed25519 sign never
FPR="$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr:/ {print $10; exit}')"
[ -n "${FPR}" ] || die "Key creation failed."

say "Saving the public key to ${KEYRING}"
gpg --batch --export "${FPR}" > "${KEYRING}"

say "Making the github-pages environment, for main only"
scripts/signing-key.sh environment "${REPO}"

say "Storing the private key as a secret of the github-pages environment (main only)"
gpg --batch --armor --export-secret-keys "${FPR}" | gh secret set "${SECRET_NAME}" --repo "${REPO}" --env github-pages

say "Saving a backup of the private key to ${BACKUP}, locked with a passphrase you choose now"
umask 077
gpg --batch --armor --export-secret-keys "${FPR}" | gpg --symmetric --cipher-algo AES256 --armor --output "${BACKUP}"

say "Turning on GitHub Pages (it hosts the updates)"
if gh api "repos/${REPO}/pages" >/dev/null 2>&1; then
	gh api -X PUT "repos/${REPO}/pages" -f build_type=workflow >/dev/null
else
	gh api -X POST "repos/${REPO}/pages" -f build_type=workflow >/dev/null
fi

cat <<EOF

Done. The update channel is ready. Key fingerprint:
  ${FPR}

Next:
  1. Commit and push the public key, which switches the channel on:
       git add ${KEYRING}
       git commit -m "Add the Kulan OS update signing key"
       git push
  2. Keep the backup safe, then delete it from this Mac:
       ${BACKUP}
     Store it in a password manager (or an encrypted drive). Without it, a
     lost GitHub secret means installed systems need a new key to keep
     getting updates. Delete the file with:
       rm "${BACKUP}"
EOF
