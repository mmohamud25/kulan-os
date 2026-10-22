#!/bin/bash
# Use the update channel's copy of a Kulan Files package whenever the channel
# already has that exact version.
#
#   ci/use-channel-files.sh DIR
#
# Kulan Files is built from a fixed Debian version plus a fixed patch
# (ci/build-nautilus.sh refuses a changed patch without a new revision), so
# the same version means the same program. The builds aren't byte for byte
# the same, though, and apt treats two different files of one version as a
# downgrade: a branch build (its own cached Kulan Files) would then fail next
# to the channel's copy, in the package test and in the ISO build. Swapping
# in the channel's file makes both agree. Files the channel doesn't have, or
# has at another version, are left alone.
#
# The channel's index is checked against Kulan's signing key before anything
# is used, and every downloaded file against the index's SHA-256.
set -euo pipefail

DIR="${1:?usage: ci/use-channel-files.sh DIR}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"
KEYRING="${ROOT}/packages/kulan-archive-keyring.gpg"
URL="${KULAN_APT_URL%/}"

shopt -s nullglob
DEBS=("${DIR}"/*.deb)
[[ ${#DEBS[@]} -gt 0 ]] || { echo "No packages in ${DIR}"; exit 0; }
[[ -s "${KEYRING}" ]] || { echo "::notice title=Kulan Files::no update channel key; using this build's files"; exit 0; }

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
if ! curl -fsSL --retry 3 -o "${WORK}/InRelease" "${URL}/InRelease"; then
	echo "::notice title=Kulan Files::update channel not reachable; using this build's files"
	exit 0
fi
if ! gpgv --keyring "${KEYRING}" --output "${WORK}/Release" "${WORK}/InRelease" 2>"${WORK}/gpgv.log"; then
	echo "::error title=Kulan Files::the update channel's index isn't signed by Kulan's key" >&2
	cat "${WORK}/gpgv.log" >&2
	exit 1
fi
curl -fsSL --retry 3 -o "${WORK}/Packages" "${URL}/Packages"
WANT="$(awk '/^SHA256:/{f=1; next} /^[A-Za-z]/{f=0} f && $3 == "Packages" {print $1}' "${WORK}/Release")"
GOT="$(sha256sum "${WORK}/Packages" | cut -d' ' -f1)"
if [[ -z "${WANT}" || "${WANT}" != "${GOT}" ]]; then
	echo "::error title=Kulan Files::the update channel's package list doesn't match its signed index" >&2
	exit 1
fi

swapped=0
for deb in "${DEBS[@]}"; do
	read -r name version arch < <(dpkg-deb --show --showformat='${Package} ${Version} ${Architecture}\n' "${deb}")
	# The channel's entry for exactly this package, version and architecture.
	entry="$(awk -v n="${name}" -v v="${version}" -v a="${arch}" '
		/^$/ { if (!done && p == n && ver == v && ar == a) { print file " " sum; done = 1 } p = ver = ar = file = sum = "" }
		/^Package: / { p = $2 } /^Version: / { ver = $2 } /^Architecture: / { ar = $2 }
		/^Filename: / { file = $2 } /^SHA256: / { sum = $2 }
		END { if (!done && p == n && ver == v && ar == a) print file " " sum }' "${WORK}/Packages")"
	[[ -n "${entry}" ]] || continue
	read -r file sum <<<"${entry}"
	[[ "$(sha256sum "${deb}" | cut -d' ' -f1)" == "${sum}" ]] && continue
	curl -fsSL --retry 3 -o "${WORK}/channel.deb" "${URL}/${file#./}"
	if [[ "$(sha256sum "${WORK}/channel.deb" | cut -d' ' -f1)" != "${sum}" ]]; then
		echo "::error title=Kulan Files::${file} from the update channel doesn't match its checksum" >&2
		exit 1
	fi
	mv "${WORK}/channel.deb" "${deb}"
	echo "Using the update channel's ${name} ${version} (${arch})"
	swapped=$((swapped + 1))
done
echo "::notice title=Kulan Files::${swapped} package(s) taken from the update channel (same version as this build)"
