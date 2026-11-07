#!/bin/bash
# Fails when an ISO has a Debian package older than ci/min-versions.txt
# allows (a version with a known serious flaw).
#
#   ci/min-versions.sh STATUS [LIST]   (STATUS: the ISO's /var/lib/dpkg/status)
set -euo pipefail

STATUS="${1:?usage: ci/min-versions.sh STATUS [LIST]}"
LIST="${2:-$(dirname "$0")/min-versions.txt}"
[[ -s "${STATUS}" ]] || { echo "::error title=Oldest allowed versions::no package list at ${STATUS}"; exit 1; }

# "source version package" for every installed package (a package without a
# Source: line is its own source; "Source: linux (6.12.115-2)" is linux).
INSTALLED="$(awk '
	/^Package:/ {p = $2; s = $2; v = ""; ok = 0}
	/^Source:/ {s = $2}
	/^Version:/ {v = $2}
	/^Status:/ {ok = ($4 == "installed")}
	/^$/ {if (p != "" && ok) print s, v, p; p = ""}
	END {if (p != "" && ok) print s, v, p}' "${STATUS}")"

FAILS=0
while IFS=$'\t' read -r sources min why; do
	[[ -z "${sources}" || "${sources}" == \#* ]] && continue
	found=0 old=""
	while read -r s v p; do
		[[ "|${sources}|" == *"|${s}|"* ]] || continue
		found=1
		dpkg --compare-versions "${v}" lt "${min}" && old="${old}${p} ${v}; "
	done <<<"${INSTALLED}"
	if ((found == 0)); then
		echo "::error title=Oldest allowed versions::${sources%%|*} isn't in the ISO, so it can't be checked (${why})"
		FAILS=$((FAILS + 1))
	elif [[ -n "${old}" ]]; then
		echo "::error title=Oldest allowed versions::${old}older than ${min} - ${why}"
		FAILS=$((FAILS + 1))
	else
		echo "::notice title=Oldest allowed versions::${sources%%|*} ${min} or newer (${why%%:*})"
	fi
done <"${LIST}"
exit $((FAILS > 0))
