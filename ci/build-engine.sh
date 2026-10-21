#!/bin/bash
# Build kulan-assistant-engine: llama.cpp's server, which runs Kulan
# Assistant's model on this computer. Runs inside debian:trixie, for the
# architecture of the machine it runs on (amd64 or arm64).
#
#   ci/build-engine.sh            # output: dist/engine/kulan-assistant-engine_<version>_<arch>.deb
#
# llama.cpp is pinned to a release commit. The CPU code is built once per
# processor generation (GGML_CPU_ALL_VARIANTS) and the best one is picked
# when it starts, so one package runs well on old and new PCs alike.
set -euo pipefail

LLAMA_TAG=b11796
LLAMA_COMMIT=0a1ef309cdbb9f2024f58a38fb4c42352c5db569
REVISION=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/engine"
SRC="${KULAN_ENGINE_SRC:-/tmp/llama.cpp}"
BUILD="${SRC}/build-kulan"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"

export DEBIAN_FRONTEND=noninteractive
if [[ "${KULAN_ENGINE_NO_APT:-0}" != 1 ]]; then
	apt-get update -q
	apt-get install -y -q --no-install-recommends build-essential cmake git ca-certificates dpkg-dev file binutils
fi

if [[ ! -d "${SRC}/.git" ]]; then
	git init -q "${SRC}"
	git -C "${SRC}" remote add origin https://github.com/ggml-org/llama.cpp.git
fi
if [[ "$(git -C "${SRC}" rev-parse HEAD 2>/dev/null || true)" != "${LLAMA_COMMIT}" ]]; then
	git -C "${SRC}" fetch -q --depth 1 origin "${LLAMA_COMMIT}"
	git -C "${SRC}" checkout -q --detach "${LLAMA_COMMIT}"
fi
test "$(git -C "${SRC}" rev-parse HEAD)" = "${LLAMA_COMMIT}"

cmake -S "${SRC}" -B "${BUILD}" -DCMAKE_BUILD_TYPE=Release \
	-DBUILD_SHARED_LIBS=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_NATIVE=OFF \
	-DLLAMA_OPENSSL=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_SERVER=ON \
	-DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON \
	-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON -DCMAKE_SKIP_INSTALL_RPATH=OFF
cmake --build "${BUILD}" --target llama-server -j "$(nproc)"

ARCH="$(dpkg --print-architecture)"
VERSION="0.0.${LLAMA_TAG#b}-${REVISION}"
PKG="${STAGE}/pkg"
DEST="${PKG}/usr/lib/kulan-assistant/engine"
install -d "${DEST}" "${PKG}/DEBIAN" "${PKG}/usr/share/doc/kulan-assistant-engine"
install -m 0755 "${BUILD}/bin/llama-server" "${DEST}/"
# Real library files only, with their soname links, as the loader expects.
for LIB in "${BUILD}"/bin/*.so*; do
	if [[ -L "${LIB}" ]]; then
		cp -P "${LIB}" "${DEST}/"
	else
		install -m 0644 "${LIB}" "${DEST}/"
	fi
done
strip --strip-unneeded "${DEST}/llama-server" "${DEST}"/*.so* 2>/dev/null || true
find "${DEST}" -type f -name '*.so*' -exec chmod 0644 {} +

# Everything must find its libraries next to itself.
for F in "${DEST}/llama-server" "${DEST}"/libllama.so.*; do
	[[ -L "${F}" ]] && continue
	# shellcheck disable=SC2016  # a literal $ORIGIN
	readelf -d "${F}" | grep -E 'R(UN)?PATH' | grep -qF '$ORIGIN' || { echo "E: ${F} does not look for its libraries next to itself" >&2; exit 1; }
	if readelf -d "${F}" | grep -E 'R(UN)?PATH' | grep -q "${BUILD}"; then
		echo "E: ${F} points at the build folder" >&2; exit 1
	fi
done
ls "${DEST}"/libggml-cpu*.so >/dev/null 2>&1 || { echo "E: no CPU backends built" >&2; exit 1; }

# Debian dependencies from what the binaries link against.
mkdir -p "${STAGE}/debian"
printf 'Source: kulan-assistant-engine\n\nPackage: kulan-assistant-engine\nArchitecture: any\n' > "${STAGE}/debian/control"
mapfile -t ELF < <(find "${DEST}" -type f \( -name '*.so*' -o -name llama-server \))
DEPENDS="$(cd "${STAGE}" && dpkg-shlibdeps -O --ignore-missing-info -l"${DEST}" "${ELF[@]/#/-e}" 2>/dev/null | sed -n 's/^shlibs:Depends=//p')"
[[ -n "${DEPENDS}" ]] || { echo "E: dpkg-shlibdeps found no dependencies" >&2; exit 1; }

cat > "${PKG}/usr/share/doc/kulan-assistant-engine/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: llama.cpp
Source: https://github.com/ggml-org/llama.cpp (release ${LLAMA_TAG}, commit ${LLAMA_COMMIT})

Files: *
Copyright: 2023-2026 The ggml authors
License: MIT

License: MIT
$(sed -e 's/^$/./' -e 's/^/ /' "${SRC}/LICENSE")
COPY
SIZE="$(du -sk "${PKG}/usr" | cut -f1)"
cat > "${PKG}/DEBIAN/control" <<CONTROL
Package: kulan-assistant-engine
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: ${KULAN_PUBLISHER} <${KULAN_MAINTAINER_EMAIL}>
Section: utils
Priority: optional
Installed-Size: ${SIZE}
Depends: ${DEPENDS}
Homepage: https://github.com/ggml-org/llama.cpp
Description: engine that runs ${KULAN_NAME} Assistant's model on this computer
 llama.cpp's server (release ${LLAMA_TAG}), built for ${KULAN_NAME} Assistant.
 It is started for one person when an assistant feature needs it, listens
 only on this computer and stops itself when idle. The processor code is
 built for each CPU generation and the best one is chosen when it starts.
CONTROL

mkdir -p "${OUT}"
rm -f "${OUT}"/kulan-assistant-engine_*_"${ARCH}".deb
dpkg-deb --root-owner-group -Zxz --build "${PKG}" "${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb"
echo "I: built ${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb ($(du -h "${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb" | cut -f1))"
dpkg-deb --field "${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb" Depends
