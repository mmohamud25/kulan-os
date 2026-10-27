#!/bin/bash
# Build Debian 13's gnome-user-share (file sharing: the Public folder over
# WebDAV) with two small changes, inside debian:trixie, for the architecture
# of the machine it runs on. The packages go next to Kulan Files' in
# dist/files, and travel with them (the ISO, the update channel).
#
#   ci/build-user-share.sh            # output: dist/files/gnome-user-share_*.deb
#   ci/build-user-share.sh --version  # only print the Debian version it would build
#
# The changes (packages-src/gnome-user-share/kulan-user-share.py makes them,
# and refuses if Debian's source no longer looks as expected):
#   1. A fixed port when GNOME_USER_SHARE_PORT says one (Kulan sets 30301 in
#      a drop-in for its service), so the firewall can let it in from local
#      networks; upstream picks a new random port every time.
#   2. The password realm the password file is made with (upstream's fix of
#      April 2026, for versions without it), so "password to change files"
#      works at all.
# The version is Debian's plus "+kulanN" (N = REVISION), so it replaces
# Debian's, and Debian's own fixes are kept when it rebuilds on top of them.
set -euo pipefail

REVISION=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/files"
EDIT="${ROOT}/packages-src/gnome-user-share/kulan-user-share.py"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
export DEBIAN_FRONTEND=noninteractive

sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update -q >/dev/null

DEBIAN_VERSION="$(apt-cache showsrc gnome-user-share | awk '/^Version:/ {print $2}' | sort -V | tail -n 1)"
[[ -n "${DEBIAN_VERSION}" ]] || { echo "::error title=File sharing::no gnome-user-share source in Debian 13" >&2; exit 1; }
if [[ "${1:-}" == --version ]]; then
	echo "${DEBIAN_VERSION}"
	exit 0
fi
VERSION="${DEBIAN_VERSION}+kulan${REVISION}"
echo "Building gnome-user-share ${VERSION} for $(dpkg --print-architecture)"

apt-get install -y -q --no-install-recommends dpkg-dev devscripts fakeroot ca-certificates python3 >/dev/null
apt-get build-dep -y -q "gnome-user-share=${DEBIAN_VERSION}" >/dev/null

cd "${WORK}"
apt-get source -q "gnome-user-share=${DEBIAN_VERSION}" >/dev/null
SRC="$(find . -maxdepth 1 -type d -name 'gnome-user-share-*' | head -n 1)"
cd "${SRC}"
# Debian's own patches first, then Kulan's changes on top (a binary-only
# build: the changes needn't be a quilt patch).
dpkg-source --before-build . >/dev/null
if ! python3 "${EDIT}" . 2>"${WORK}/edit.err"; then
	echo "::error title=File sharing::Kulan's change doesn't fit Debian's gnome-user-share ${DEBIAN_VERSION}: $(tr '\n' ' ' < "${WORK}/edit.err")" >&2
	exit 1
fi

DEBEMAIL="noreply@kulangroup.com" DEBFULLNAME="Kulan OS" \
	dch --newversion "${VERSION}" --distribution trixie --force-distribution \
	"Kulan OS: a fixed port from GNOME_USER_SHARE_PORT, and the password realm the password file uses."

DEB_BUILD_OPTIONS="nocheck noautodbgsym parallel=$(nproc)"
export DEB_BUILD_OPTIONS
if ! dpkg-buildpackage -b -uc -us >"${WORK}/build.log" 2>&1; then
	echo "::error title=File sharing::build failed: $(grep -E 'error(\[|:)|Error' "${WORK}/build.log" | head -n 5 | tr '\n' ' ' | cut -c1-600)" >&2
	tail -n 60 "${WORK}/build.log" >&2
	exit 1
fi

mkdir -p "${OUT}"
rm -f "${OUT}"/gnome-user-share_*.deb
for DEB in ../gnome-user-share_*.deb; do
	case "$(basename "${DEB}")" in
		*dbgsym*) continue ;;
	esac
	cp "${DEB}" "${OUT}/"
done
CHECK="${WORK}/check"
mkdir -p "${CHECK}"
for DEB in "${OUT}"/gnome-user-share_*.deb; do dpkg-deb -x "${DEB}" "${CHECK}"; done
if ! grep -rlaq GNOME_USER_SHARE_PORT "${CHECK}"; then
	echo "::error title=File sharing::the built package doesn't read GNOME_USER_SHARE_PORT: $(find "${CHECK}" -type f -perm -u+x | tr '\n' ' ')" >&2
	exit 1
fi
echo "Reads GNOME_USER_SHARE_PORT: $(grep -rla GNOME_USER_SHARE_PORT "${CHECK}" | sed "s#${CHECK}##")"
ls -l "${OUT}"
