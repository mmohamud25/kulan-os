#!/bin/bash
# Runs inside a privileged debian:trixie container on GitHub Actions.
# Usage: ci/build-in-container.sh <amd64|arm64>
set -euo pipefail

ARCH="${1:?usage: $0 <amd64|arm64>}"
cd "$(dirname "$0")/.."

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
	live-build debootstrap ca-certificates dpkg-dev

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
