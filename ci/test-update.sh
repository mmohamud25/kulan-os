#!/bin/sh
# Update this Kulan OS to a test version (a GitHub pre-release), for testing
# before a release. Downloads into a temporary folder, checks every file,
# installs only packages that are already installed here (plus what they
# need), and deletes the downloads afterwards.
set -eu
BASE="https://github.com/@REPO@/releases/download/@TAG@"
ARCH="$(dpkg --print-architecture)"
TMP="$(mktemp -d)"
chmod 755 "${TMP}"  # readable by apt's own user
trap 'rm -rf "${TMP}"' EXIT
cd "${TMP}"
echo "Getting Kulan OS test packages (@TAG@) for ${ARCH}..."
curl -fsSL "${BASE}/SHA256SUMS" -o SHA256SUMS
for FILE in $(awk '{print $2}' SHA256SUMS | grep -E "_(all|${ARCH})\.deb$"); do
	curl -fsSL "${BASE}/${FILE}" -o "${FILE}"
done
grep -E "_(all|${ARCH})\.deb$" SHA256SUMS | sha256sum -c --quiet -
WANT=""
for DEB in ./*.deb; do
	PKG="$(dpkg-deb -f "${DEB}" Package)"
	# Kulan Files replaces Files (nautilus and its parts); Kulan's own
	# packages only where they're installed now.
	case "${PKG}" in
		nautilus|nautilus-data|libnautilus-extension4|gir1.2-nautilus-4.0) WANT="${WANT} ${DEB}" ;;
		*) dpkg-query -W -f '${Status}' "${PKG}" 2>/dev/null | grep -q "install ok installed" && WANT="${WANT} ${DEB}" ;;
	esac
done
echo "Installing:$(for D in ${WANT}; do printf ' %s' "$(dpkg-deb -f "${D}" Package)"; done)"
# No questions (this runs from a pipe): settings files someone edited are
# kept, as Software Update does.
# shellcheck disable=SC2086  # one argument per package file
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
	-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold ${WANT}
echo
echo "Done. Restart the computer (top-right menu, Restart) to finish."
