#!/bin/bash
# Publish a main build's ISOs as a GitHub Release, so os.kulangroup.com can
# link to them with no login and no expiry:
#   kulan-os-amd64.iso, kulan-os-arm64.iso, SHA256SUMS, SHA256SUMS.sig
# The asset names stay the same from release to release, so
#   https://github.com/<repo>/releases/latest/download/kulan-os-amd64.iso
# is always the newest. SHA256SUMS is signed with the update channel's key.
#
#   ci/make-release.sh ISO_DIR   (ISO_DIR/amd64/*.iso, ISO_DIR/arm64/*.iso)
#
# Needs GH_TOKEN (contents: write), GITHUB_REPOSITORY, GITHUB_RUN_NUMBER and,
# for the signature, KULAN_APT_SIGNING_KEY.
set -euo pipefail

IN="${1:?usage: ci/make-release.sh ISO_DIR}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"
NOTES="${ROOT}/packages/kulan-update-center/root/usr/share/kulan/release-notes.json"
# GitHub refuses release files of 2 GiB or more.
LIMIT=$((2 * 1024 * 1024 * 1024))
WARN=$((1900 * 1024 * 1024))

# Beside the downloaded ISOs, so they're moved rather than copied (2 GB each).
OUT="${IN}/release"
rm -rf "${OUT}"
mkdir -p "${OUT}"
for arch in amd64 arm64; do
	iso="$(find "${IN}/${arch}" -maxdepth 1 -name '*.iso' | head -n 1)"
	[[ -n "${iso}" ]] || { echo "::error title=Release::no ${arch} ISO in ${IN}/${arch}"; exit 1; }
	size="$(stat -c %s "${iso}")"
	if (( size >= LIMIT )); then
		echo "::error title=Release::the ${arch} ISO is $((size / 1048576)) MB; GitHub Releases only take files under 2048 MB"
		exit 1
	elif (( size >= WARN )); then
		echo "::warning title=Release::the ${arch} ISO is $((size / 1048576)) MB, close to GitHub's 2048 MB limit"
	fi
	mv "${iso}" "${OUT}/kulan-os-${arch}.iso"
done
(cd "${OUT}" && sha256sum kulan-os-amd64.iso kulan-os-arm64.iso > SHA256SUMS)
cat "${OUT}/SHA256SUMS"

ASSETS=("${OUT}/kulan-os-amd64.iso" "${OUT}/kulan-os-arm64.iso" "${OUT}/SHA256SUMS")
if [[ -n "${KULAN_APT_SIGNING_KEY:-}" ]]; then
	GNUPGHOME="$(mktemp -d)"
	export GNUPGHOME
	trap 'rm -rf "${GNUPGHOME}"' EXIT
	chmod 700 "${GNUPGHOME}"
	printf '%s\n' "${KULAN_APT_SIGNING_KEY}" | gpg --batch --quiet --import
	FPR="$(gpg --batch --list-secret-keys --with-colons | awk -F: '/^fpr:/ {print $10; exit}')"
	gpg --batch --yes --local-user "${FPR}" --detach-sign --output "${OUT}/SHA256SUMS.sig" "${OUT}/SHA256SUMS"
	gpgv --keyring "${ROOT}/packages/kulan-archive-keyring.gpg" "${OUT}/SHA256SUMS.sig" "${OUT}/SHA256SUMS"
	ASSETS+=("${OUT}/SHA256SUMS.sig")
else
	echo "::warning title=Release::no signing key; SHA256SUMS is not signed"
fi

TAG="v${KULAN_VERSION}-build${GITHUB_RUN_NUMBER}"
TITLE="${KULAN_NAME} ${KULAN_VERSION_LABEL:-${KULAN_VERSION}} (build ${GITHUB_RUN_NUMBER})"
python3 - "${NOTES}" "${KULAN_VERSION}" > "${OUT}/notes.md" <<'PY'
import json, sys
notes = json.load(open(sys.argv[1]))
note = next((n for n in notes if n.get("version") == sys.argv[2]), None)
print("Download from https://os.kulangroup.com/download/ (PC: kulan-os-amd64.iso, Apple Silicon Macs and ARM: kulan-os-arm64.iso).")
print("Check a download with `sha256sum -c SHA256SUMS --ignore-missing`; SHA256SUMS.sig is signed with the update channel's key (https://os.kulangroup.com/apt/kulan-archive-keyring.gpg).")
print()
if note:
    print(f"## {note.get('title', '')}")
    for item in note.get("items", []):
        print(f"- {item}")
print()
print(f"Full notes for every version: https://os.kulangroup.com/whats-new/#v{sys.argv[2]}")
PY

gh release create "${TAG}" "${ASSETS[@]}" --repo "${GITHUB_REPOSITORY}" --target "${GITHUB_SHA}" \
	--title "${TITLE}" --notes-file "${OUT}/notes.md" --latest
echo "::notice title=Release::${TITLE} published: https://github.com/${GITHUB_REPOSITORY}/releases/tag/${TAG}"
