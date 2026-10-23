#!/bin/bash
# Turn dist/debs/*.deb into a signed apt repository in site/apt/, ready for
# GitHub Pages (or, with --from-channel, re-sign what the channel serves). Installed Kulan OS systems read it via
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
# How long a signed index is good for. apt refuses an older one, so nobody
# can keep a computer on an old (but genuinely signed) channel to hold back
# fixes. The channel is re-signed every week (resign-channel.yml), so three
# weeks leaves room for a missed run.
VALID_S=$((21 * 24 * 3600))
[[ -f "${KEYRING}" ]] || { echo "::error::${KEYRING} missing; run scripts/setup-update-channel.sh" >&2; exit 1; }

# --from-channel: re-sign what the update channel serves now (the weekly
# re-sign, so the index's Valid-Until never runs out between releases).
# Every package is checked against the signed index before it's used.
if [[ "${1:-}" == "--from-channel" ]]; then
	DEBS="$(mktemp -d)"
	URL="${KULAN_APT_URL%/}"
	curl -fsSL --retry 3 -o "${DEBS}/InRelease" "${URL}/InRelease"
	gpgv --keyring "${KEYRING}" --output "${DEBS}/Release" "${DEBS}/InRelease"
	curl -fsSL --retry 3 -o "${DEBS}/Packages" "${URL}/Packages"
	WANT="$(awk '$1 ~ /^[0-9a-f]{64}$/ && $3 == "Packages" {print $1}' "${DEBS}/Release" | head -n 1)"
	[[ "$(sha256sum "${DEBS}/Packages" | cut -d' ' -f1)" == "${WANT}" ]] ||
		{ echo "::error::the channel's Packages doesn't match its signed index" >&2; exit 1; }
	awk '/^Filename: /{f=$2} /^SHA256: /{print $2, f}' "${DEBS}/Packages" | while read -r SUM FILE; do
		NAME="$(basename "${FILE}")"
		[[ "${NAME}" =~ ^[A-Za-z0-9.+~_-]+\.deb$ ]] || { echo "::error::unexpected file name ${FILE}" >&2; exit 1; }
		curl -fsSL --retry 3 -o "${DEBS}/${NAME}" "${URL}/${FILE#./}"
		echo "${SUM}  ${DEBS}/${NAME}" | sha256sum -c --quiet - || { echo "::error::${NAME} doesn't match the index" >&2; exit 1; }
	done
	rm -f "${DEBS}/InRelease" "${DEBS}/Release" "${DEBS}/Packages"
fi

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
	-o "APT::FTPArchive::Release::ValidTime=${VALID_S}" \
	release . > Release
gpg --batch --yes --local-user "${FPR}" --clearsign --output InRelease Release
gpg --batch --yes --local-user "${FPR}" --armor --detach-sign --output Release.gpg Release

# Check the signatures with only the public key, exactly as apt will.
gpgv --keyring "${KEYRING}" InRelease
gpgv --keyring "${KEYRING}" Release.gpg Release

install -m 0644 "${KEYRING}" "${REPO}/kulan-archive-keyring.gpg"
# The website (os.kulangroup.com) around the update channel.
python3 "${ROOT}/ci/build-website.py" "${SITE}"
touch "${SITE}/.nojekyll"
# Every version's notes, read by Software Update when it offers a new version
# (the installed notes only cover the installed one) and by the website.
install -m 0644 "${ROOT}/packages/kulan-update-center/root/usr/share/kulan/release-notes.json" \
	"${SITE}/release-notes.json"

echo "Published $(grep -c '^Package:' Packages) packages, signed by ${FPR}:"
grep -E '^(Package|Version):' Packages | paste - -
