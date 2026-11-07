#!/bin/bash
# Tests /usr/lib/kulan/apt-https (2.14: Debian's package servers over HTTPS)
# on made-up source files, then puts this computer's own back as they were.
# Usage: ci/apt-https-test.sh [/usr/lib/kulan/apt-https]   (as root)
set -uo pipefail
TOOL="${1:-/usr/lib/kulan/apt-https}"
D=/etc/apt/sources.list.d
BACKUP="$(mktemp -d)"
cp -a /etc/apt/sources.list "${BACKUP}/" 2>/dev/null
cp -a "${D}" "${BACKUP}/d"
restore() {
	rm -f /etc/apt/sources.list /etc/apt/apt.conf.d/99kulan-test-proxy
	cp -a "${BACKUP}/sources.list" /etc/apt/ 2>/dev/null
	rm -rf "${D}" && cp -a "${BACKUP}/d" "${D}"
	rm -rf "${BACKUP}"
}
trap restore EXIT
FAILS=0
expect() { # expect <file> <line>
	grep -qxF -- "$2" "$1" || { echo "FAIL: $1 should have: $2"; cat "$1"; FAILS=$((FAILS + 1)); }
}

rm -f "${D}"/*.list "${D}"/*.sources
# What live-build wrote before 2.14.
cat >/etc/apt/sources.list <<'EOF'
deb http://deb.debian.org/debian/ trixie main contrib non-free non-free-firmware
deb http://security.debian.org/ trixie-security main contrib non-free non-free-firmware
deb http://deb.debian.org/debian/ trixie-updates main
# deb-src http://deb.debian.org/debian/ trixie main
deb http://ftp.us.debian.org/debian/ trixie main
deb http://deb.debian.org.example.com/debian/ trixie main
deb [signed-by=/usr/share/keyrings/x.gpg] http://security.debian.org trixie-security main
EOF
# Debian's own newer format, and Kulan's channel (left alone).
printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie\nComponents: main\n\nTypes: deb\nURIs: http://security.debian.org/debian-security\nSuites: trixie-security\nComponents: main\n' >"${D}/debian.sources"
chmod 640 "${D}/debian.sources"
printf 'Types: deb\nURIs: https://os.kulangroup.com/apt/\nSuites: ./\n' >"${D}/kulan.sources"
cp "${D}/kulan.sources" "${BACKUP}/kulan-before"

"${TOOL}" || { echo "FAIL: exit $?"; FAILS=$((FAILS + 1)); }
L=/etc/apt/sources.list
expect $L "deb https://deb.debian.org/debian/ trixie main contrib non-free non-free-firmware"
expect $L "deb https://security.debian.org/debian-security trixie-security main contrib non-free non-free-firmware"
expect $L "deb https://deb.debian.org/debian/ trixie-updates main"
expect $L "# deb-src https://deb.debian.org/debian/ trixie main"
expect $L "deb http://ftp.us.debian.org/debian/ trixie main"
expect $L "deb http://deb.debian.org.example.com/debian/ trixie main"
expect $L "deb [signed-by=/usr/share/keyrings/x.gpg] https://security.debian.org/debian-security trixie-security main"
expect "${D}/debian.sources" "URIs: https://deb.debian.org/debian"
expect "${D}/debian.sources" "URIs: https://security.debian.org/debian-security"
[[ "$(stat -c %a "${D}/debian.sources")" == 640 ]] || { echo "FAIL: permissions changed"; FAILS=$((FAILS + 1)); }
cmp -s "${D}/kulan.sources" "${BACKUP}/kulan-before" || { echo "FAIL: kulan.sources changed"; FAILS=$((FAILS + 1)); }
ls "${D}"/*.kulan-new /etc/apt/*.kulan-new 2>/dev/null && { echo "FAIL: left a temporary file"; FAILS=$((FAILS + 1)); }

# A second time changes nothing.
cp $L "${BACKUP}/once"
if ! "${TOOL}" || ! cmp -s $L "${BACKUP}/once"; then
	echo "FAIL: a second run changed something"
	FAILS=$((FAILS + 1))
fi

# With a package cache for APT (HTTP only), nothing changes.
echo 'deb http://deb.debian.org/debian/ trixie main' >$L
echo 'Acquire::http::Proxy "http://127.0.0.1:3142";' >/etc/apt/apt.conf.d/99kulan-test-proxy
"${TOOL}" && expect $L "deb http://deb.debian.org/debian/ trixie main"

if ((FAILS == 0)); then
	echo "apt-https: every case passed"
else
	exit 1
fi
