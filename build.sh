#!/bin/bash
# Build a Kulan OS ISO locally on a Debian 13 machine (or a Debian 13 VM).
#
#   sudo ./build.sh            # build for this machine's architecture
#   sudo ./build.sh arm64      # Apple Silicon Mac VMs (UTM)
#   sudo ./build.sh amd64      # regular PCs
#
# Most people never run this: pushing to GitHub builds both ISOs for you
# (see README.md). The finished ISO lands in this folder.
set -euo pipefail

cd "$(dirname "$0")"

ARCH="${1:-$(dpkg --print-architecture)}"
case "${ARCH}" in
	amd64|arm64) ;;
	*) echo "Usage: sudo $0 [amd64|arm64]" >&2; exit 2 ;;
esac

if [[ ${EUID} -ne 0 ]]; then
	echo "This build needs root (it creates a full system image). Run: sudo $0 ${ARCH}" >&2
	exit 1
fi

HOST_ARCH="$(dpkg --print-architecture)"
if [[ "${ARCH}" != "${HOST_ARCH}" ]]; then
	echo "Cannot build ${ARCH} on a ${HOST_ARCH} machine. Build on matching hardware," >&2
	echo "or let GitHub Actions build it (it has both kinds of machine)." >&2
	exit 1
fi

if ! command -v lb >/dev/null 2>&1; then
	echo "Installing live-build..."
	apt-get update
	DEBIAN_FRONTEND=noninteractive apt-get install -y live-build debootstrap ca-certificates
fi

echo "==> Building Kulan OS for ${ARCH}"
lb clean --purge
KULAN_ARCH="${ARCH}" lb config
lb build

shopt -s nullglob
ISOS=(./*.iso)
if [[ ${#ISOS[@]} -eq 0 ]]; then
	echo "Build finished but no ISO was produced. See build.log." >&2
	exit 1
fi
for iso in "${ISOS[@]}"; do
	sha256sum "${iso}" > "${iso}.sha256"
	echo "==> Done: ${iso} ($(du -h "${iso}" | cut -f1))"
done
