#!/bin/bash
# Runs inside a privileged debian:trixie container on GitHub Actions.
# Usage: ci/build-in-container.sh <amd64|arm64>
set -euo pipefail

ARCH="${1:?usage: $0 <amd64|arm64>}"
cd "$(dirname "$0")/.."

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
	live-build debootstrap ca-certificates dpkg-dev apt-utils

# The Kulan packages are built by the "packages" job and downloaded here.
shopt -s nullglob
DEBS=(config/packages.chroot/kulan-*.deb)
if [[ ${#DEBS[@]} -lt 3 ]]; then
	echo "::error::Expected the 3 Kulan packages in config/packages.chroot, found ${#DEBS[@]}" >&2
	exit 1
fi
# kulan-devkit is an optional install (Welcome app); live-build installs every
# package in packages.chroot, so keep it out of the image.
rm -f config/packages.chroot/kulan-devkit_*.deb
DEBS=(config/packages.chroot/kulan-*.deb)
printf 'Kulan package: %s\n' "${DEBS[@]}"
shopt -u nullglob

HOST_ARCH="$(dpkg --print-architecture)"
if [[ "${HOST_ARCH}" != "${ARCH}" ]]; then
	echo "::error::Runner is ${HOST_ARCH} but ${ARCH} was requested" >&2
	exit 1
fi

lb --version
KULAN_ARCH="${ARCH}" lb config
lb build

shopt -s nullglob
ISOS=(./*.iso)
if [[ ${#ISOS[@]} -ne 1 ]]; then
	echo "::error::Expected exactly one ISO, found ${#ISOS[@]}" >&2
	ls -la
	exit 1
fi

ISO="${ISOS[0]}"
mkdir -p out
mv "${ISO}" out/
( cd out && sha256sum "$(basename "${ISO}")" > "$(basename "${ISO}").sha256" )
cp build.log "out/build-${ARCH}.log"
chmod -R a+rX out
ls -lh out
