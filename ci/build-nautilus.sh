#!/bin/bash
# Build Kulan Files: Debian 13's own Nautilus source with Kulan's patch
# (packages-src/nautilus/kulan-files.patch: status bar, preview pane,
# Finder-style sidebar). Runs inside debian:trixie, for the architecture of
# the machine it runs on (amd64 or arm64).
#
#   ci/build-nautilus.sh            # output: dist/files/*.deb
#   ci/build-nautilus.sh --version  # only print the Debian version it would build
#
# The version is Debian's plus "+kulanN" (N = REVISION below), so it is newer
# than Debian's and replaces it. When Debian updates Nautilus (a security fix,
# a point release), the CI cache key changes and this rebuilds on top of the
# new version, so Kulan Files keeps every Debian fix.
set -euo pipefail

REVISION=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/files"
PATCH="${ROOT}/packages-src/nautilus/kulan-files.patch"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
export DEBIAN_FRONTEND=noninteractive

# Debian's sources too (deb822 file in the debian:trixie image).
sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/debian.sources
apt-get update -q >/dev/null

DEBIAN_VERSION="$(apt-cache showsrc nautilus | awk '/^Version:/ {print $2}' | sort -V | tail -n 1)"
[[ -n "${DEBIAN_VERSION}" ]] || { echo "::error title=Kulan Files::no Nautilus source in Debian 13" >&2; exit 1; }
if [[ "${1:-}" == --version ]]; then
	echo "${DEBIAN_VERSION}"
	exit 0
fi
VERSION="${DEBIAN_VERSION}+kulan${REVISION}"
echo "Building Kulan Files ${VERSION} for $(dpkg --print-architecture)"

apt-get install -y -q --no-install-recommends dpkg-dev devscripts quilt fakeroot ca-certificates >/dev/null
apt-get build-dep -y -q "nautilus=${DEBIAN_VERSION}" >/dev/null

cd "${WORK}"
apt-get source -q "nautilus=${DEBIAN_VERSION}" >/dev/null
SRC="$(find . -maxdepth 1 -type d -name 'nautilus-*' | head -n 1)"
cd "${SRC}"

# Kulan's patch goes last, after Debian's own.
cp "${PATCH}" debian/patches/kulan-files.patch
echo kulan-files.patch >> debian/patches/series
export QUILT_PATCHES=debian/patches
if ! quilt push -a -q >/dev/null 2>"${WORK}/quilt.err"; then
	echo "::error title=Kulan Files::the patch doesn't apply to Debian's Nautilus ${DEBIAN_VERSION}: $(tail -n 5 "${WORK}/quilt.err" | tr '\n' ' ')" >&2
	quilt push -a 2>&1 | tail -n 30 >&2 || true
	exit 1
fi
quilt pop -a -q >/dev/null

DEBEMAIL="noreply@kulangroup.com" DEBFULLNAME="Kulan OS" \
	dch --newversion "${VERSION}" --distribution trixie --force-distribution \
	"Kulan Files: status bar, preview pane and Finder-style sidebar (kulan-files.patch)."

DEB_BUILD_OPTIONS="nocheck noautodbgsym parallel=$(nproc)"
export DEB_BUILD_OPTIONS
if ! dpkg-buildpackage -b -uc -us >"${WORK}/build.log" 2>&1; then
	echo "::error title=Kulan Files::build failed: $(grep -E 'error:|Error' "${WORK}/build.log" | head -n 5 | tr '\n' ' ' | cut -c1-600)" >&2
	tail -n 60 "${WORK}/build.log" >&2
	exit 1
fi

# The packages a desktop uses (not -dev, -doc or debug symbols).
mkdir -p "${OUT}"
rm -f "${OUT}"/*.deb
for DEB in ../*.deb; do
	case "$(basename "${DEB}")" in
		*-dev_*|*-doc_*|*dbgsym*) continue ;;
	esac
	cp "${DEB}" "${OUT}/"
done
echo "${VERSION}" > "${OUT}/VERSION"
ls -l "${OUT}"
