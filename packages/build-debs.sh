#!/bin/bash
# Build the Kulan OS packages (kulan-base, kulan-desktop-settings,
# kulan-welcome) into dist/debs/.
#
#   packages/build-debs.sh            # version from the last git commit time
#   KULAN_PKG_VERSION=1.0.1 packages/build-debs.sh
#
# Needs only dpkg-deb, so it runs on Debian, Ubuntu, or a GitHub runner.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/dist/debs"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"

# Versions must always increase so installed systems pick up updates:
# <KULAN_VERSION>.<UTC timestamp of the last commit>, e.g. 1.0.202610201905.
if [[ -z "${KULAN_PKG_VERSION:-}" ]]; then
	if STAMP="$(git -C "${ROOT}" log -1 --format=%ct 2>/dev/null)" && [[ -n "${STAMP}" ]]; then
		:
	else
		STAMP="$(date +%s)"
	fi
	KULAN_PKG_VERSION="${KULAN_VERSION}.$(date -u -d "@${STAMP}" +%Y%m%d%H%M)"
fi
KULAN_BUILD="${KULAN_BUILD:-local}"
if ! [[ "${KULAN_BUILD}" =~ ^[A-Za-z0-9]+$ ]]; then
	echo "E: invalid KULAN_BUILD '${KULAN_BUILD}'" >&2
	exit 1
fi
if ! [[ "${KULAN_PKG_VERSION}" =~ ^[0-9][A-Za-z0-9.+~-]*$ ]]; then
	echo "E: invalid package version '${KULAN_PKG_VERSION}'" >&2
	exit 1
fi

KEYRING="${ROOT}/packages/kulan-archive-keyring.gpg"

render_template() {
	sed \
		-e "s|@VERSION@|${KULAN_PKG_VERSION}|g" \
		-e "s|@NAME@|${KULAN_NAME}|g" \
		-e "s|@PUBLISHER@|${KULAN_PUBLISHER}|g" \
		-e "s|@MAINTAINER_EMAIL@|${KULAN_MAINTAINER_EMAIL}|g" \
		-e "s|@HOME_URL@|${KULAN_HOME_URL}|g" \
		"$1"
}

# Files generated from kulan.conf, so renaming the OS stays a one-line change.
generate_kulan_base() {
	local root="$1"
	install -d "${root}/usr/lib" "${root}/usr/share/kulan"
	cat > "${root}/usr/lib/os-release" <<EOF
PRETTY_NAME="${KULAN_NAME} ${KULAN_VERSION}"
NAME="${KULAN_NAME}"
VERSION_ID="${KULAN_VERSION}"
VERSION="${KULAN_VERSION} (based on Debian ${KULAN_DEBIAN_VERSION})"
VERSION_CODENAME=${KULAN_DEBIAN_SUITE}
DEBIAN_CODENAME=${KULAN_DEBIAN_SUITE}
ID=${KULAN_ID}
ID_LIKE=debian
LOGO=kulan-logo
BUILD_ID=${KULAN_BUILD:-local}
HOME_URL="${KULAN_HOME_URL}"
SUPPORT_URL="${KULAN_SUPPORT_URL}"
EOF
	install -m 0644 "${ROOT}/kulan.conf" "${root}/usr/share/kulan/kulan.conf"

	# Automatic updates, daily: Debian stable and security fixes plus updates
	# from the Kulan channel, matched by each repository's Origin. Debian's
	# stable archive must be trusted too: a Kulan update can need a new package
	# from it (2.0 needs Timeshift), and the updater installs nothing from an
	# untrusted origin. Debian's own default trusts it; this keeps it so.
	#
	# Debian's security sources are listed explicitly: unattended-upgrades
	# takes the distribution name from os-release (now Kulan), so rules that
	# use \${distro_id} would no longer match Debian's servers.
	# DevRelease "false": there is no Kulan entry in distro-info to check.
	install -d "${root}/etc/apt/apt.conf.d"
	cat > "${root}/etc/apt/apt.conf.d/52kulan-auto-upgrades" <<EOF
// ${KULAN_NAME}: install Debian stable and security updates and ${KULAN_NAME} updates automatically, daily.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
Unattended-Upgrade::Origins-Pattern:: "origin=Debian,codename=\${distro_codename},label=Debian";
Unattended-Upgrade::Origins-Pattern:: "origin=Debian,codename=\${distro_codename},label=Debian-Security";
Unattended-Upgrade::Origins-Pattern:: "origin=Debian,codename=\${distro_codename}-security,label=Debian-Security";
Unattended-Upgrade::Origins-Pattern:: "origin=${KULAN_NAME}";
Unattended-Upgrade::DevRelease "false";
EOF

	# The update channel is only switched on once its signing key exists
	# (scripts/setup-update-channel.sh). Without the key, apt would refuse
	# the repository and show an error on every update.
	if [[ -f "${KEYRING}" ]]; then
		install -D -m 0644 "${KEYRING}" "${root}/usr/share/keyrings/kulan-archive-keyring.gpg"
		install -d "${root}/etc/apt/sources.list.d"
		cat > "${root}/etc/apt/sources.list.d/kulan.sources" <<EOF
# ${KULAN_NAME} updates (wallpapers, settings, the welcome app and fixes).
Types: deb
URIs: ${KULAN_APT_URL}
Suites: ./
Signed-By: /usr/share/keyrings/kulan-archive-keyring.gpg
EOF
		echo "I: update channel included (${KULAN_APT_URL})"
	else
		echo "I: no signing key yet (packages/kulan-archive-keyring.gpg); update channel not included"
	fi
}

mkdir -p "${OUT}"
rm -f "${OUT}"/*.deb

for SRC in "${ROOT}"/packages/*/; do
	[[ -f "${SRC}/DEBIAN/control.in" ]] || continue
	NAME="$(basename "${SRC}")"
	STAGE="${WORK}/${NAME}"
	mkdir -p "${STAGE}/DEBIAN"
	if [[ -d "${SRC}/root" ]]; then
		cp -a "${SRC}/root/." "${STAGE}/"
	fi
	# Tool caches from local development never belong in a package.
	find "${STAGE}" \( -name __pycache__ -o -name .ruff_cache -o -name .mypy_cache -o -name '*.pyc' \) \
		-prune -exec rm -rf {} +
	if [[ "${NAME}" == kulan-base ]]; then
		generate_kulan_base "${STAGE}"
	fi

	render_template "${SRC}/DEBIAN/control.in" > "${STAGE}/DEBIAN/control"
	for SCRIPT in preinst postinst prerm postrm triggers; do
		if [[ -f "${SRC}/DEBIAN/${SCRIPT}" ]]; then
			install -m 0755 "${SRC}/DEBIAN/${SCRIPT}" "${STAGE}/DEBIAN/${SCRIPT}"
		fi
	done
	[[ -f "${STAGE}/DEBIAN/triggers" ]] && chmod 0644 "${STAGE}/DEBIAN/triggers"

	# Everything under /etc is a configuration file: dpkg keeps a user's
	# edits on upgrade instead of silently overwriting them.
	if [[ -d "${STAGE}/etc" ]]; then
		(cd "${STAGE}" && find etc -type f | sort | sed 's|^|/|') > "${STAGE}/DEBIAN/conffiles"
		[[ -s "${STAGE}/DEBIAN/conffiles" ]] || rm -f "${STAGE}/DEBIAN/conffiles"
	fi

	# Normalise permissions so the package never depends on the build machine.
	find "${STAGE}" -path "${STAGE}/DEBIAN" -prune -o -type d -exec chmod 0755 {} +
	find "${STAGE}" -path "${STAGE}/DEBIAN" -prune -o -type f -exec chmod 0644 {} +
	for EXEC in usr/bin usr/lib/kulan; do
		[[ -d "${STAGE}/${EXEC}" ]] && find "${STAGE}/${EXEC}" -type f -exec chmod 0755 {} +
	done

	# Debian policy: every package documents its copyright and changes.
	DOC="${STAGE}/usr/share/doc/${NAME}"
	install -d "${DOC}"
	cat > "${DOC}/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ${NAME}
Upstream-Contact: ${KULAN_PUBLISHER} <${KULAN_MAINTAINER_EMAIL}>
Source: ${KULAN_HOME_URL}

Files: *
Copyright: $(date -u +%Y) ${KULAN_PUBLISHER}
License: All-rights-reserved
 All rights reserved. ${KULAN_NAME} is built on Debian; Debian's own packages
 keep their own licenses, listed in /usr/share/doc/<package>/copyright.
EOF
	COMMIT="$(git -C "${ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
	{
		echo "${NAME} (${KULAN_PKG_VERSION}) ${KULAN_DEBIAN_SUITE}; urgency=medium"
		echo
		echo "  * ${KULAN_NAME} build from commit ${COMMIT}."
		echo
		echo " -- ${KULAN_PUBLISHER} <${KULAN_MAINTAINER_EMAIL}>  $(date -u -R -d "@${STAMP:-$(date +%s)}")"
	} | gzip -9n > "${DOC}/changelog.gz"

	# Manual pages must be compressed.
	if [[ -d "${STAGE}/usr/share/man" ]]; then
		find "${STAGE}/usr/share/man" -type f ! -name '*.gz' -exec gzip -9n {} +
	fi

	SIZE="$(du -sk --exclude=DEBIAN "${STAGE}" | cut -f1)"
	echo "Installed-Size: ${SIZE}" >> "${STAGE}/DEBIAN/control"

	DEB="${OUT}/${NAME}_${KULAN_PKG_VERSION}_all.deb"
	dpkg-deb --root-owner-group -Zxz --build "${STAGE}" "${DEB}" >/dev/null
	echo "I: built $(basename "${DEB}")"
done

echo "${KULAN_PKG_VERSION}" > "${OUT}/VERSION"
