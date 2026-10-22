#!/bin/bash
# Build kulan-assistant-engine: llama.cpp's server, which runs Kulan
# Assistant's model on this computer, and whisper.cpp, which turns speech
# into text for Dictation. Runs inside debian:trixie, for the
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
WHISPER_TAG=v1.9.5
WHISPER_COMMIT=d1be6fde11ac6e0407606b4e42fe72d34add8037
REVISION=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/engine"
SRC="${KULAN_ENGINE_SRC:-/tmp/llama.cpp}"
BUILD="${SRC}/build-kulan"
WSRC="${KULAN_WHISPER_SRC:-/tmp/whisper.cpp}"
WBUILD="${WSRC}/build-kulan"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"

export DEBIAN_FRONTEND=noninteractive
if [[ "${KULAN_ENGINE_NO_APT:-0}" != 1 ]]; then
	apt-get update -q
	apt-get install -y -q --no-install-recommends build-essential cmake git ca-certificates dpkg-dev file binutils
fi

fetch() { # fetch <dir> <url> <commit>
	if [[ ! -d "$1/.git" ]]; then
		git init -q "$1"
		git -C "$1" remote add origin "$2"
	fi
	if [[ "$(git -C "$1" rev-parse HEAD 2>/dev/null || true)" != "$3" ]]; then
		git -C "$1" fetch -q --depth 1 origin "$3"
		git -C "$1" checkout -q --detach "$3"
	fi
	test "$(git -C "$1" rev-parse HEAD)" = "$3"
}
fetch "${SRC}" https://github.com/ggml-org/llama.cpp.git "${LLAMA_COMMIT}"
fetch "${WSRC}" https://github.com/ggml-org/whisper.cpp.git "${WHISPER_COMMIT}"

cmake -S "${SRC}" -B "${BUILD}" -DCMAKE_BUILD_TYPE=Release \
	-DBUILD_SHARED_LIBS=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_NATIVE=OFF \
	-DLLAMA_OPENSSL=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_SERVER=ON \
	-DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON \
	-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON -DCMAKE_SKIP_INSTALL_RPATH=OFF
cmake --build "${BUILD}" --target llama-server -j "$(nproc)"

# whisper.cpp with its own copy of ggml, kept in its own folder so the two
# never load each other's libraries.
cmake -S "${WSRC}" -B "${WBUILD}" -DCMAKE_BUILD_TYPE=Release \
	-DBUILD_SHARED_LIBS=ON -DGGML_BACKEND_DL=ON -DGGML_CPU_ALL_VARIANTS=ON -DGGML_NATIVE=OFF \
	-DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
	-DWHISPER_SDL2=OFF -DWHISPER_CURL=OFF -DWHISPER_BUILD_IS_DEV=OFF \
	-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON -DCMAKE_SKIP_INSTALL_RPATH=OFF \
	-DCMAKE_LIBRARY_OUTPUT_DIRECTORY="${WBUILD}/bin" -DCMAKE_RUNTIME_OUTPUT_DIRECTORY="${WBUILD}/bin"
cmake --build "${WBUILD}" --target whisper-cli -j "$(nproc)"
# The CPU variants are loaded at run time, so nothing links to them: build them by name.
mapfile -t WCPU < <(cmake --build "${WBUILD}" --target help | sed -n 's/^\.\.\. \(ggml-cpu-[a-z0-9_]*\)$/\1/p')
if [[ ${#WCPU[@]} -gt 0 ]]; then
	cmake --build "${WBUILD}" --target "${WCPU[@]}" -j "$(nproc)"
fi

ARCH="$(dpkg --print-architecture)"
# The Kulan version is part of it: the engine is rebuilt whenever kulan.conf
# changes (its build cache key), and the rebuilt files differ from the ones
# already on the update channel. With the same version number apt can't tell
# them apart and refuses to build the ISO ("Packages were downgraded").
VERSION="0.0.${LLAMA_TAG#b}+whisper${WHISPER_TAG#v}-${REVISION}+kulan${KULAN_VERSION}"
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

WDEST="${PKG}/usr/lib/kulan-assistant/whisper"
install -d "${WDEST}"
install -m 0755 "${WBUILD}/bin/whisper-cli" "${WDEST}/"
for LIB in "${WBUILD}"/bin/*.so*; do
	if [[ -L "${LIB}" ]]; then
		cp -P "${LIB}" "${WDEST}/"
	else
		install -m 0644 "${LIB}" "${WDEST}/"
	fi
done
strip --strip-unneeded "${WDEST}/whisper-cli" "${WDEST}"/*.so* 2>/dev/null || true
find "${WDEST}" -type f -name '*.so*' -exec chmod 0644 {} +
ls "${WDEST}"/libwhisper.so* >/dev/null 2>&1 || { echo "E: libwhisper missing" >&2; exit 1; }
ls "${WDEST}"/libggml-cpu*.so >/dev/null 2>&1 || { echo "E: no CPU backends for whisper" >&2; exit 1; }

# Everything must find its libraries next to itself.
for F in "${DEST}/llama-server" "${DEST}"/libllama.so.* "${WDEST}/whisper-cli" "${WDEST}"/libwhisper.so.*; do
	[[ -L "${F}" ]] && continue
	# shellcheck disable=SC2016  # a literal $ORIGIN
	readelf -d "${F}" | grep -E 'R(UN)?PATH' | grep -qF '$ORIGIN' || { echo "E: ${F} does not look for its libraries next to itself" >&2; exit 1; }
	if readelf -d "${F}" | grep -E 'R(UN)?PATH' | grep -qE "${BUILD}|${WBUILD}"; then
		echo "E: ${F} points at the build folder" >&2; exit 1
	fi
done
ls "${DEST}"/libggml-cpu*.so >/dev/null 2>&1 || { echo "E: no CPU backends built" >&2; exit 1; }

# Debian dependencies from what the binaries link against.
mkdir -p "${STAGE}/debian"
printf 'Source: kulan-assistant-engine\n\nPackage: kulan-assistant-engine\nArchitecture: any\n' > "${STAGE}/debian/control"
mapfile -t ELF < <(find "${DEST}" "${WDEST}" -type f \( -name '*.so*' -o -name llama-server -o -name whisper-cli \))
DEPENDS="$(cd "${STAGE}" && dpkg-shlibdeps -O --ignore-missing-info -l"${DEST}" -l"${WDEST}" "${ELF[@]/#/-e}" 2>/dev/null | sed -n 's/^shlibs:Depends=//p')"
[[ -n "${DEPENDS}" ]] || { echo "E: dpkg-shlibdeps found no dependencies" >&2; exit 1; }

cat > "${PKG}/usr/share/doc/kulan-assistant-engine/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: llama.cpp and whisper.cpp
Source: https://github.com/ggml-org/llama.cpp (release ${LLAMA_TAG}, commit ${LLAMA_COMMIT})
 https://github.com/ggml-org/whisper.cpp (release ${WHISPER_TAG}, commit ${WHISPER_COMMIT})

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
Description: engines that run ${KULAN_NAME} Assistant's models on this computer
 llama.cpp's server (release ${LLAMA_TAG}) for writing help, summaries and
 search, and whisper.cpp (${WHISPER_TAG}) for Dictation, built for
 ${KULAN_NAME} Assistant.
 It is started for one person when an assistant feature needs it, listens
 only on this computer and stops itself when idle. The processor code is
 built for each CPU generation and the best one is chosen when it starts.
CONTROL

mkdir -p "${OUT}"
rm -f "${OUT}"/kulan-assistant-engine_*_"${ARCH}".deb
dpkg-deb --root-owner-group -Zxz --build "${PKG}" "${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb"
echo "I: built ${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb ($(du -h "${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb" | cut -f1))"
dpkg-deb --field "${OUT}/kulan-assistant-engine_${VERSION}_${ARCH}.deb" Depends
