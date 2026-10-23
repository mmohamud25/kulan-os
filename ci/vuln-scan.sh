#!/bin/bash
# Known vulnerabilities in an ISO's Debian packages, from Debian's own
# security tracker (debsecan). Kulan's own packages aren't in it; they're
# checked by their tests.
#
#   ci/vuln-scan.sh STATUS   (the ISO's /var/lib/dpkg/status)
#
# Reports how many known issues the packages have, how serious, and how
# many already have a fix in Debian. A serious issue (high urgency or open
# to the network) that already has a fix fails the build: the ISO should
# have been built with that fix, so something is out of date.
set -euo pipefail

STATUS="${1:?usage: ci/vuln-scan.sh STATUS}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../kulan.conf
. "${ROOT}/kulan.conf"
SUITE="${KULAN_DEBIAN_SUITE:-trixie}"
command -v debsecan >/dev/null 2>&1 || { echo "::error title=Vulnerabilities::debsecan isn't installed"; exit 1; }
[[ -s "${STATUS}" ]] || { echo "::error title=Vulnerabilities::no package list at ${STATUS}"; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
if ! debsecan --suite "${SUITE}" --status "${STATUS}" --format summary > "${WORK}/all" 2>"${WORK}/err"; then
	echo "::warning title=Vulnerabilities::couldn't reach Debian's security tracker: $(tail -n 1 "${WORK}/err")"
	exit 0
fi
debsecan --suite "${SUITE}" --status "${STATUS}" --format summary --only-fixed > "${WORK}/fixed" 2>/dev/null || true

# Lines look like: CVE-2025-1234 openssl (fixed, remotely exploitable, high urgency)
count() { grep -c -- "$1" "$2" || true; }
TOTAL="$(grep -c . "${WORK}/all" || true)"
HIGH="$(count 'high urgency' "${WORK}/all")"
MEDIUM="$(count 'medium urgency' "${WORK}/all")"
REMOTE="$(count 'remotely exploitable' "${WORK}/all")"
FIXED="$(grep -c . "${WORK}/fixed" || true)"
PACKAGES="$(awk '{print $2}' "${WORK}/all" | sort -u | grep -c . || true)"
echo "::notice title=Vulnerabilities::${TOTAL} known issue(s) in ${PACKAGES} Debian package(s): ${HIGH} high urgency, ${MEDIUM} medium, ${REMOTE} open to the network; ${FIXED} with a fix in Debian already"

SERIOUS="$(grep -E 'high urgency|remotely exploitable' "${WORK}/fixed" || true)"
if [[ -n "${SERIOUS}" ]]; then
	echo "::error title=Vulnerabilities::serious issues that Debian has already fixed, but this ISO doesn't have the fix: $(head -n 8 <<<"${SERIOUS}" | tr '\n' ';' | cut -c1-900)"
	exit 1
fi
if (( FIXED > 0 )); then
	echo "::warning title=Vulnerabilities::fixed in Debian but not in this ISO yet: $(head -n 8 "${WORK}/fixed" | tr '\n' ';' | cut -c1-900)"
fi
