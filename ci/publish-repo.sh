#!/bin/bash
# Turn dist/debs/*.deb into a signed apt repository in site/apt/, ready for
# GitHub Pages. Installed Kulan OS systems read it via
# /etc/apt/sources.list.d/kulan.sources (a "flat" repository: Suites: ./).
#
# Needs: apt-utils (apt-ftparchive), gnupg; the armored private signing key
# in $KULAN_APT_SIGNING_KEY.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEBS="${ROOT}/dist/debs"
SITE="${ROOT}/site"
REPO="${SITE}/apt"

# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"

for TOOL in apt-ftparchive gpg gpgv xz gzip; do
	command -v "${TOOL}" >/dev/null 2>&1 || { echo "::error::${TOOL} not found (install apt-utils, gnupg, xz-utils)" >&2; exit 1; }
done
: "${KULAN_APT_SIGNING_KEY:?KULAN_APT_SIGNING_KEY is not set}"
KEYRING="${ROOT}/packages/kulan-archive-keyring.gpg"
[[ -f "${KEYRING}" ]] || { echo "::error::${KEYRING} missing; run scripts/setup-update-channel.sh" >&2; exit 1; }

shopt -s nullglob
FILES=("${DEBS}"/*.deb)
[[ ${#FILES[@]} -gt 0 ]] || { echo "::error::no packages in ${DEBS}" >&2; exit 1; }

rm -rf "${SITE}"
mkdir -p "${REPO}"
cp "${FILES[@]}" "${REPO}/"

export GNUPGHOME
GNUPGHOME="$(mktemp -d)"
trap 'rm -rf "${GNUPGHOME}"' EXIT
chmod 700 "${GNUPGHOME}"
printf '%s\n' "${KULAN_APT_SIGNING_KEY}" | gpg --batch --quiet --import
FPR="$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr:/ {print $10; exit}')"
[[ -n "${FPR}" ]] || { echo "::error::signing key did not import" >&2; exit 1; }

# The secret key must match the public key that systems trust.
TRUSTED="$(gpg --batch --with-colons --show-keys "${KEYRING}" | awk -F: '/^fpr:/ {print $10; exit}')"
if [[ "${FPR}" != "${TRUSTED}" ]]; then
	echo "::error::KULAN_APT_SIGNING_KEY (${FPR}) does not match packages/kulan-archive-keyring.gpg (${TRUSTED})" >&2
	exit 1
fi

cd "${REPO}"
apt-ftparchive packages . > Packages
gzip -9nk Packages
xz -9k Packages
apt-ftparchive \
	-o "APT::FTPArchive::Release::Origin=${KULAN_NAME}" \
	-o "APT::FTPArchive::Release::Label=${KULAN_NAME}" \
	-o "APT::FTPArchive::Release::Suite=${KULAN_DEBIAN_SUITE}" \
	-o "APT::FTPArchive::Release::Codename=${KULAN_DEBIAN_SUITE}" \
	-o "APT::FTPArchive::Release::Description=${KULAN_NAME} updates" \
	release . > Release
gpg --batch --yes --local-user "${FPR}" --clearsign --output InRelease Release
gpg --batch --yes --local-user "${FPR}" --armor --detach-sign --output Release.gpg Release

# Check the signatures with only the public key, exactly as apt will.
gpgv --keyring "${KEYRING}" InRelease
gpgv --keyring "${KEYRING}" Release.gpg Release

install -m 0644 "${KEYRING}" "${REPO}/kulan-archive-keyring.gpg"
cat > "${SITE}/index.html" <<EOF
<!doctype html><meta charset="utf-8"><title>${KULAN_NAME} updates</title>
<p>This is the ${KULAN_NAME} update channel. It is used automatically by installed systems.</p>
EOF
touch "${SITE}/.nojekyll"
# Every version's notes, read by Software Update when it offers a new version
# (the installed notes only cover the installed one) and by the website.
install -m 0644 "${ROOT}/packages/kulan-update-center/root/usr/share/kulan/release-notes.json" \
	"${SITE}/release-notes.json"

echo "Published $(grep -c '^Package:' Packages) packages, signed by ${FPR}:"
grep -E '^(Package|Version):' Packages | paste - -
