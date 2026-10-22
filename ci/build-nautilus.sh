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

# 2: the update channel now carries one nautilus-data, built on PCs (see build-iso.yml).
REVISION=2
# The patch this revision was made with. A changed patch with the same
# version would never reach installed systems (apt sees nothing new), so
# changing the patch means: REVISION+1 and this hash updated.
PATCH_SHA256=a2bb19f28502a4545ee775021ba7bc9ee2067f218528d18b1cc21b0f4f4befa2
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
ACTUAL_SHA256="$(sha256sum "${PATCH}" | cut -d" " -f1)"
if [[ "${ACTUAL_SHA256}" != "${PATCH_SHA256}" ]]; then
	echo "::error title=Kulan Files::kulan-files.patch changed: raise REVISION in ci/build-nautilus.sh and set PATCH_SHA256=${ACTUAL_SHA256}" >&2
	exit 1
fi
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
# --fuzz=0: exactly as dpkg-source applies it during the build.
if ! quilt push -a -q --fuzz=0 >/dev/null 2>"${WORK}/quilt.err"; then
	echo "::error title=Kulan Files::the patch doesn't apply to Debian's Nautilus ${DEBIAN_VERSION}: $(tail -n 5 "${WORK}/quilt.err" | tr '\n' ' ')" >&2
	quilt push -a --fuzz=0 2>&1 | tail -n 30 >&2 || true
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
