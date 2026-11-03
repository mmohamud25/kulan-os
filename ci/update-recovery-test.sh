#!/bin/bash
# Kulan OS 2.11: updates can never leave the computer broken. Every case with
# stand-ins for the system (no VM): installs as their own service that keeps
# going when whoever started it goes away, waiting for automatic updates,
# Software Update's status (running, interrupted), the start-up menu after a
# start-up that didn't reach the desktop (GRUB's scripts, kulan-boot-ok, the
# signed start-up files' boot counting), "Undo the last update" (power, from
# the start-up menu), the restore point before automatic updates, and the
# timers and time limits.
#
#   ci/update-recovery-test.sh [kulan-update-center's root]   (default /: installed)
#   ci/update-recovery-test.sh packages/kulan-update-center/root   (from the source)
# Prints "update recovery: every case passed", or ::error:: lines and exits 1.
set -uo pipefail

UC="$(cd "${1:-/}" && pwd)"
KULAN="${UC}/usr/lib/kulan"
UNITS="${UC}/usr/lib/systemd/system"
GRUBD="${UC}/etc/grub.d"
T="$(mktemp -d)"
trap 'rm -rf "${T}"' EXIT
FAILED=0

check() { # check <name> <command...>
	local name="$1"
	shift
	if "$@" >"${T}/out" 2>&1; then
		echo "ok: ${name}"
	else
		echo "::error::update recovery: ${name}: $(tail -n 8 "${T}/out" | tr '\n' ' ' | cut -c1-800)"
		FAILED=1
	fi
}

# --- update-run: long changes as their own service ------------------------------------
# Stand-ins: systemd-run starts the command in its own session (as the real
# service is outside the caller's), systemctl answers for it, systemd-inhibit
# just runs the command.
S="${T}/detach"
mkdir -p "${S}/bin" "${S}/run"
cat >"${S}/bin/systemd-run" <<'EOF'
#!/bin/bash
out=/dev/null err=/dev/null env=()
while [[ $# -gt 0 ]]; do
	case "$1" in
		--) shift; break ;;
		-p) shift
			case "$1" in
				StandardOutput=append:*) out="${1#StandardOutput=append:}" ;;
				StandardError=append:*) err="${1#StandardError=append:}" ;;
			esac ;;
		--setenv=*) env+=("${1#--setenv=}") ;;
	esac
	shift
done
printf '%s\n' "${env[*]}" >"${STUB}/env"
setsid env "${env[@]}" "$@" >>"${out}" 2>>"${err}" </dev/null &
echo $! >"${STUB}/pid"
EOF
cat >"${S}/bin/systemctl" <<'EOF'
#!/bin/bash
case "$*" in
	"show -P ActiveState kulan-update.service")
		if [[ -e "${STUB}/pid" ]] && kill -0 "$(cat "${STUB}/pid")" 2>/dev/null; then echo active; else echo inactive; fi ;;
	"show -P MainPID kulan-update.service") cat "${STUB}/pid" 2>/dev/null || echo 0 ;;
esac
exit 0
EOF
cat >"${S}/bin/systemd-inhibit" <<'EOF'
#!/bin/bash
[[ "$1" == --list ]] && exit 0
while [[ "$1" == --* ]]; do shift; done
exec "$@"
EOF
# The change itself: two progress lines a second apart, a message, exit 3.
cat >"${S}/change" <<'EOF'
#!/bin/bash
echo "pmstatus:x:10:Step one"
echo "apt says something" >&2
sleep 2
echo "pmstatus:x:90:Step two"
echo "say:Plain words"
echo "${KULAN_UPDATE_UNIT:-}" >"${STUB}/in-unit"
exit 3
EOF
chmod +x "${S}/bin/"* "${S}/change"
sed -e "s|^export PATH=|export PATH=${S}/bin:|" -e "s|^RUN_DIR=.*|RUN_DIR=${S}/run|" \
	-e "s|\[\[ -d /run/systemd/system \]\]|true|" -e "s|/run/kulan/update-run.rc|${S}/run/update-run.rc|" \
	-e "s|/usr/lib/kulan/update-status refresh >/dev/null 2>&1; echo|true; echo|" \
	-e "s|/usr/lib/kulan/update-run \"\$@\" 2>>|${S}/change \"\$@\" 2>>|" "${KULAN}/update-run" >"${S}/update-run"
export STUB="${S}"

detach_follows() {
	out="$(bash "${S}/update-run" install 2>"${S}/stderr")"
	rc=$?
	[[ ${rc} -eq 3 ]] || { echo "exit status ${rc}, not the change's 3"; return 1; }
	[[ "${out}" == $'pmstatus:x:10:Step one\npmstatus:x:90:Step two\nsay:Plain words' ]] || { echo "stdout: ${out}"; return 1; }
	grep -qx "apt says something" "${S}/stderr" || { echo "stderr: $(cat "${S}/stderr")"; return 1; }
	[[ "$(cat "${S}/in-unit")" == 1 ]] && grep -q "KULAN_DEFER_STATUS=1" "${S}/env"
}
check "install runs as its own service; the caller gets its progress, messages and exit status" detach_follows

caller_goes_away() {
	rm -f "${S}/in-unit"
	timeout 1 bash "${S}/update-run" install >/dev/null 2>&1
	sleep 3
	[[ -e "${S}/in-unit" && "$(cat "${S}/run/update-run.rc")" == 3 ]] || { echo "the change stopped with its caller"; return 1; }
}
check "the install carries on when whoever started it goes away" caller_goes_away

one_at_a_time() {
	bash "${S}/update-run" install >/dev/null 2>&1 &
	sleep 0.5
	out="$(bash "${S}/update-run" addon-install x 2>/dev/null)"
	rc=$?
	wait
	[[ ${rc} -ne 0 && "${out}" == "say:Another change is still being made"* ]] || { echo "second change: ${rc} ${out}"; return 1; }
}
check "one change at a time, said in plain words" one_at_a_time

short_actions_inline() {
	rm -f "${S}/pid"
	bash "${S}/update-run" auto-nonsense >/dev/null 2>&1
	[[ ! -e "${S}/pid" ]] || { echo "a short action was started as a service"; return 1; }
}
check "short actions (and wrong ones) don't start a service" short_actions_inline

# --- update-run: waits for automatic updates instead of failing ---------------------------
waits_for_lock() {
	command -v dpkg >/dev/null 2>&1 && [[ -e /var/lib/dpkg/lock-frontend && ${EUID} -eq 0 ]] || { echo "skip (not root on Debian)"; return 0; }
	python3 -I -c 'import fcntl, os, time; fd = os.open("/var/lib/dpkg/lock-frontend", os.O_RDWR); fcntl.lockf(fd, fcntl.LOCK_EX); time.sleep(7)' &
	sleep 1
	out="$(timeout 60 "${KULAN}/update-run" repair 2>/dev/null)"
	rc=$?
	wait
	[[ ${rc} -eq 0 ]] && grep -q "^pmstatus:wait:0:Waiting for another installation to finish" <<<"${out}" || { echo "${rc}: ${out}"; return 1; }
}
check "update-run waits (and says so) while something else installs" waits_for_lock

# --- update-status: running, interrupted, never stuck on "Installing" ----------------------
status_states() {
	python3 -I -B - "${KULAN}/update-status" <<'PY'
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile
loader = importlib.machinery.SourceFileLoader("us", sys.argv[1])
spec = importlib.util.spec_from_loader("us", loader); m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.STATUS_FILE = os.path.join(tempfile.mkdtemp(), "updates.json")
os.environ["KULAN_STATUS_FORCE"] = "1"
os.environ.pop("KULAN_DEFER_STATUS", None)
m.available_updates = lambda: [{"name": "tzdata", "from": "1", "to": "2", "security": False}]
m.app_updates = m.firmware_updates = lambda: []
m.interrupted = lambda: False
st = lambda: json.load(open(m.STATUS_FILE))
m.main(["x", "running"]); s = st()
assert s["state"] == "running" and s["running_pid"] == m.installer_pid() and s["running_start"], s
m.main(["x", "refresh"]); s = st()
assert s["state"] == "idle" and "running_pid" not in s, ("the installer's own refresh ends it", s)
p = subprocess.Popen(["sleep", "30"])
json.dump({"state": "running", "running_pid": p.pid, "running_start": m.start_time(p.pid), "running_since": 1}, open(m.STATUS_FILE, "w"))
m.main(["x", "refresh"]); s = st()
assert s["state"] == "running" and s["count"] == 1, ("another live install stays running", s)
m.main(["x", "running"]); assert st()["running_pid"] == p.pid, "the first install is kept"
p.kill(); p.wait()
m.main(["x", "refresh"]); assert st()["state"] == "idle", "a finished (or crashed) install isn't running"
def fails():
    raise RuntimeError("E: dpkg was interrupted")
m.available_updates = fails
m.interrupted = lambda: True
json.dump({"state": "running", "running_pid": 999999, "running_start": 1, "checked": 5,
           "available": [{"name": "tzdata", "from": "1", "to": "2", "security": False}]}, open(m.STATUS_FILE, "w"))
m.main(["x", "refresh"]); s = st()
assert s["state"] == "interrupted" and s["interrupted"] and s["check_failed"] and s["checked"] == 5 \
    and s["available"][0]["name"] == "tzdata", ("apt failing: last list kept, interrupted, not running", s)
os.environ["KULAN_DEFER_STATUS"] = "1"
before = open(m.STATUS_FILE).read()
m.main(["x", "after-dpkg"]); assert open(m.STATUS_FILE).read() == before, "deferred during automatic updates"
del os.environ["KULAN_DEFER_STATUS"]
m.available_updates = lambda: []
m.interrupted = lambda: False
m.main(["x", "after-dpkg"]); assert st()["state"] == "idle"
assert m.dpkg_busy() in (True, False)
PY
}
check "Software Update's status: running only while the install runs; interrupted; never stuck" status_states

# --- the start-up menu after a start-up that didn't reach the desktop --------------------
FAKE10="${T}/10_linux"
cat >"${FAKE10}" <<'EOF'
#!/bin/sh
echo "Found linux image: /boot/vmlinuz-6.12.48+deb13-amd64" >&2
cat <<'X'
function gfxmode {
	set gfxpayload="${1}"
}
menuentry 'Kulan OS GNU/Linux, with Linux 6.12.48+deb13-amd64' --class kulan --class gnu-linux --class os $menuentry_id_option 'gnulinux-6.12.48+deb13-amd64-advanced-1234' {
	load_video
	insmod ext2
	search --no-floppy --fs-uuid --set=root 1234
	echo	'Loading Linux 6.12.48+deb13-amd64 ...'
	linux	/vmlinuz-6.12.48+deb13-amd64 root=UUID=5678 ro  splash
	initrd	/initrd.img-6.12.48+deb13-amd64
}
menuentry 'Kulan OS GNU/Linux, with Linux 6.12.43+deb13-amd64' --class kulan $menuentry_id_option 'gnulinux-6.12.43' {
	linux	/vmlinuz-6.12.43+deb13-amd64 root=UUID=5678 ro
}
X
EOF
printf '#!/bin/sh\ncase "$1" in --target=fs) echo "${FAKE_FS:-ext2}" ;; esac\n' >"${T}/grub-probe"
chmod +x "${FAKE10}" "${T}/grub-probe"

grub_scripts() {
	[[ -x "${GRUBD}/01_kulan-boot-check" && -x "${GRUBD}/12_kulan-undo" ]] || { echo "not executable"; return 1; }
	sed "s|^\[ -x /usr/lib/kulan/boot-ok \] .*|:|" "${GRUBD}/01_kulan-boot-check" >"${T}/01"
	sed -e "s|^LINUX_ENTRIES=.*|LINUX_ENTRIES=${FAKE10}|" -e "s|^\[ -x /usr/lib/kulan/restore-points \].*|:|" \
		"${GRUBD}/12_kulan-undo" >"${T}/12"
	grub_probe="${T}/grub-probe" sh "${T}/01" >"${T}/menu.cfg" || return 1
	sh "${T}/12" >>"${T}/menu.cfg" || return 1
	grep -q 'if \[ "${kulan_boot_pending}" = "1" \]; then' "${T}/menu.cfg" &&
		grep -q "set timeout_style=menu" "${T}/menu.cfg" && grep -q "set timeout=10" "${T}/menu.cfg" &&
		grep -q "save_env kulan_boot_pending" "${T}/menu.cfg" || { cat "${T}/menu.cfg"; return 1; }
	grep -q "^menuentry 'Undo the last update' .*'kulan-undo-update' {" "${T}/menu.cfg" &&
		grep -q "linux	/vmlinuz-6.12.48+deb13-amd64 root=UUID=5678 ro  splash kulan.undo-update$" "${T}/menu.cfg" &&
		[[ "$(grep -c '^menuentry' "${T}/menu.cfg")" -eq 1 ]] || { cat "${T}/menu.cfg"; return 1; }
	# GRUB can't write its block on Btrfs: no save_env (it would show an error at every start).
	FAKE_FS=btrfs grub_probe="${T}/grub-probe" sh "${T}/01" | grep -q save_env && { echo "save_env on btrfs"; return 1; }
	# Without kulan-update-center, the file left in /etc/grub.d does nothing.
	if [[ ! -x /usr/lib/kulan/boot-ok ]]; then
		[[ -z "$(grub_probe="${T}/grub-probe" sh "${GRUBD}/01_kulan-boot-check")" ]] || { echo "does something without boot-ok"; return 1; }
	fi
	if command -v grub-script-check >/dev/null 2>&1; then
		grub-script-check "${T}/menu.cfg" || return 1
	else
		echo "(grub-script-check not here: syntax not checked)"
	fi
}
check "start-up menu: shown after a failed start-up, with Undo the last update" grub_scripts

boot_ok() {
	local B="${T}/boot"
	mkdir -p "${B}/esp/EFI/Linux" "${B}/vars" "${B}/grub"
	: >"${B}/esp/EFI/Linux/kulan-6.12.48+deb13-amd64+2-1.efi"
	: >"${B}/esp/EFI/Linux/kulan-6.12.48+deb13-amd64~prev.efi"
	: >"${B}/esp/EFI/Linux/kulan-6.12.43+deb13-amd64.efi"
	{ printf '\007\000\000\000'; printf '\\EFI\\Linux\\kulan-6.12.48+deb13-amd64+2-1.efi' | iconv -f UTF-8 -t UTF-16LE; printf '\000\000'; } \
		>"${B}/vars/LoaderBootCountPath-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"
	export KULAN_ESP="${B}/esp" KULAN_EFIVARS="${B}/vars" KULAN_GRUBENV="${B}/grub/grubenv" KULAN_BOOT_OK_MARK="${B}/ok"
	[[ "$(sh "${KULAN}/boot-ok" status)" == pending ]] || return 1
	if [[ -x /usr/bin/grub-editenv ]]; then  # boot-ok looks only there
		: >"${B}/grub/grub.cfg"
		grub-editenv "${B}/grub/grubenv" create && grub-editenv "${B}/grub/grubenv" set kulan_boot_pending=1 || return 1
	fi
	sh "${KULAN}/boot-ok" pending || return 1
	[[ ! -e "${B}/vars/LoaderConfigTimeoutOneShot-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f" ]] || { echo "systemd-boot's menu asked for under GRUB"; return 1; }
	# Started by systemd-boot: its menu is asked for at the next start-up (10 seconds), once.
	{ printf '\007\000\000\000'; printf 'systemd-boot 257.9' | iconv -f UTF-8 -t UTF-16LE; printf '\000\000'; } \
		>"${B}/vars/LoaderInfo-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"
	local ONESHOT="${B}/vars/LoaderConfigTimeoutOneShot-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f"
	sh "${KULAN}/boot-ok" pending || return 1
	[[ "$(od -An -tx1 "${ONESHOT}" | tr -d ' \n')" == 0700000031003000"0000" ]] || { echo "one-shot timeout: $(od -An -tx1 "${ONESHOT}" 2>&1)"; return 1; }
	sh "${KULAN}/boot-ok" now || return 1
	[[ ! -e "${ONESHOT}" ]] || { echo "the menu is still asked for after a start-up that worked"; return 1; }
	[[ -e "${B}/esp/EFI/Linux/kulan-6.12.48+deb13-amd64.efi" && ! -e "${B}/esp/EFI/Linux/kulan-6.12.48+deb13-amd64+2-1.efi" &&
		! -e "${B}/esp/EFI/Linux/kulan-6.12.48+deb13-amd64~prev.efi" ]] ||
		{ ls "${B}/esp/EFI/Linux"; return 1; }
	[[ "$(sh "${KULAN}/boot-ok" status)" == ok ]] || return 1
	if [[ -x /usr/bin/grub-editenv ]]; then  # boot-ok looks only there
		grub-editenv "${B}/grub/grubenv" list | grep -q kulan_boot_pending && { echo "pending left in grubenv"; return 1; }
	fi
	unset KULAN_ESP KULAN_EFIVARS KULAN_GRUBENV KULAN_BOOT_OK_MARK
	return 0
}
check "a start-up that worked: GRUB's note cleared, the signed start-up file loses its try counter (and the one kept to fall back on goes), systemd-boot's menu not asked for" boot_ok

# --- restore points: undo needs power; the restore point before automatic updates -----------
RP="${T}/rp"
mkdir -p "${RP}/bin" "${RP}/snaps/2026-01-03_00-00-00" "${RP}/power/BAT0" "${RP}/power/AC"
printf '{"tags":"O","comments":"Before installing updates"}' >"${RP}/snaps/2026-01-03_00-00-00/info.json"
printf '#!/bin/sh\necho "$0 $*" >> "%s/calls"\n' "${RP}" >"${RP}/bin/timeshift"
cp "${RP}/bin/timeshift" "${RP}/bin/systemctl"
chmod +x "${RP}/bin/"*
echo "{}" >"${RP}/ts.json"
sed -e "s|^export PATH=|export PATH=${RP}/bin:|" -e "s|^CONFIG=.*|CONFIG=${RP}/ts.json|" \
	-e "s/^MIN_FREE_PERCENT=.*/MIN_FREE_PERCENT=0/" -e "s/^MIN_FREE_GIB=.*/MIN_FREE_GIB=0/" "${KULAN}/restore-points" >"${RP}/restore-points"
# The same with a free-space limit (1 GiB), for the "no room" cases.
sed -e "s/^MIN_FREE_GIB=.*/MIN_FREE_GIB=1/" "${RP}/restore-points" >"${RP}/restore-points-strict"
undo_env() {
	export KULAN_RESTORE_FORCE=1 KULAN_RESTORE_SNAPSHOTS="${RP}/snaps" KULAN_RESTORE_USAGE="${RP}/u.json" \
		KULAN_PAUSE_FILE="${RP}/pause" KULAN_POWER_SUPPLIES="${RP}/power" KULAN_INHIBITED=1 \
		KULAN_LAST_UPDATE="${RP}/root/var/lib/kulan/last-update.json" KULAN_KEEP_ROOT="${RP}/root" KULAN_KEPT="${RP}/kept"
	mkdir -p "${RP}/root/var/lib/kulan"
	# Free space as FAKE_AVAIL says (plenty when unset).
	cat >"${RP}/bin/df" <<'DF'
#!/bin/sh
case "$*" in
	*--output=avail*) printf 'Avail\n%s\n' "${FAKE_AVAIL:-999999999999}" ;;
	*) printf 'Size Avail\n%s %s\n' 999999999999 "${FAKE_AVAIL:-999999999999}" ;;
esac
DF
	chmod +x "${RP}/bin/df"
}
# A Timeshift stand-in: --create makes a restore point (with its note),
# --restore runs ${RP}/on-restore (the restore point's older files going
# back) and, with FAKE_RESTORE_FAIL=1, fails the first time.
fake_timeshift() {
	cat >"${RP}/bin/timeshift" <<TS
#!/bin/bash
echo "\$0 \$*" >> "${RP}/calls"
case "\$*" in
	*--create*)
		n=2026-11-0\$(( \$(ls "${RP}/snaps" | wc -l) + 1 ))_10-00-00
		all="\$*"
		note="\${all##*--comments }"
		mkdir -p "${RP}/snaps/\${n}"
		printf '{"tags":"O","comments":"%s"}' "\${note}" >"${RP}/snaps/\${n}/info.json" ;;
	*--restore*)
		if [[ -x "${RP}/on-restore" ]]; then "${RP}/on-restore"; fi
		if [[ "\${FAKE_RESTORE_FAIL:-0}" == 1 && ! -e "${RP}/failed-once" ]]; then touch "${RP}/failed-once"; exit 1; fi ;;
esac
exit 0
TS
	chmod +x "${RP}/bin/timeshift"
}
reset_points() { # one point, taken before the last update, and the record of it
	rm -rf "${RP}/snaps" "${RP}/calls" "${RP}/kept" "${RP}/failed-once" "${RP}/pause"
	mkdir -p "${RP}/snaps/2026-01-03_00-00-00"
	printf '{"tags":"O","comments":"Before installing updates"}' >"${RP}/snaps/2026-01-03_00-00-00/info.json"
	printf '{"point": "2026-01-03_00-00-00", "undone": "", "time": 1}\n' >"${KULAN_LAST_UPDATE}"
}
undo_power() {
	undo_env
	fake_timeshift
	reset_points
	echo Battery >"${RP}/power/BAT0/type"
	echo 12 >"${RP}/power/BAT0/capacity"
	echo Mains >"${RP}/power/AC/type"
	echo 0 >"${RP}/power/AC/online"
	out="$(bash "${RP}/restore-points" undo-update 2>/dev/null)" && { echo "undid on 12% battery"; return 1; }
	grep -q "^say:Plug in the charger first" <<<"${out}" && [[ ! -e "${RP}/calls" ]] || { echo "${out}"; return 1; }
	echo 1 >"${RP}/power/AC/online"
	bash "${RP}/restore-points" undo-update >/dev/null 2>&1 || return 1
	grep -q -- "--create --scripted --comments Before undoing an update" "${RP}/calls" &&
		grep -q -- "--restore --snapshot 2026-01-03_00-00-00 --scripted --yes --skip-grub" "${RP}/calls" &&
		grep -q "systemctl reboot --check-inhibitors=no" "${RP}/calls" || { cat "${RP}/calls"; return 1; }
	# Done once: never again for that update (a start-up that comes back with
	# the menu's option still set does nothing).
	grep -q '"point": "", "undone": "2026-01-03_00-00-00"' "${KULAN_LAST_UPDATE}" || { cat "${KULAN_LAST_UPDATE}"; return 1; }
	rm -f "${RP}/calls"
	bash "${RP}/restore-points" undo-at-start >/dev/null 2>&1 || { echo "start-up stopped"; return 1; }
	! grep -q -- "--restore" "${RP}/calls" 2>/dev/null || { echo "undid twice"; return 1; }
	# At start-up, on a flat battery: says so, and start-up carries on (exit 0, nothing restored).
	reset_points
	echo 0 >"${RP}/power/AC/online"
	bash "${RP}/restore-points" undo-at-start >/dev/null 2>&1 && [[ ! -e "${RP}/calls" ]] || return 1
	echo 80 >"${RP}/power/BAT0/capacity"
	bash "${RP}/restore-points" undo-at-start >/dev/null 2>&1 && grep -q -- "--restore --snapshot" "${RP}/calls"
}
check "undo the last update: only with the charger or a charged battery; also from the start-up menu; never twice" undo_power

undo_keeps() {
	# Accounts, passwords, Fleet, lost mode, signed start-up and its key, Wi-Fi:
	# as they are now, not as the restore point had them. A file that isn't
	# there now isn't brought back.
	undo_env
	fake_timeshift
	reset_points
	local R="${RP}/root" ok=0
	mkdir -p "${R}/etc/kulan" "${R}/var/lib/kulan/signed-start" "${R}/etc/NetworkManager/system-connections"
	echo 'mo:$y$new' >"${R}/etc/shadow"
	chmod 0640 "${R}/etc/shadow"
	echo current-key >"${R}/var/lib/kulan/signed-start/kulan.key"
	echo '{"phase": "on"}' >"${R}/var/lib/kulan/signed-start.json"
	echo '{"server": "now"}' >"${R}/etc/kulan/fleet.json"
	echo wifi >"${R}/etc/NetworkManager/system-connections/Home.nmconnection"
	rm -f "${R}/etc/nologin"
	# The restore point's (older) files going back.
	cat >"${RP}/on-restore" <<OR
#!/bin/sh
echo 'mo:\$y\$old' >"${R}/etc/shadow"
echo old-key >"${R}/var/lib/kulan/signed-start/kulan.key"
echo '{"phase": "enroll"}' >"${R}/var/lib/kulan/signed-start.json"
echo '{"server": "then"}' >"${R}/etc/kulan/fleet.json"
echo lost >"${R}/etc/nologin"
rm -f "${R}/etc/NetworkManager/system-connections/Home.nmconnection"
OR
	chmod +x "${RP}/on-restore"
	printf '#!/bin/sh\necho "kulan-signed-start $* key=$(cat %s)" >> "%s/calls"\n' "${R}/var/lib/kulan/signed-start/kulan.key" "${RP}" >"${RP}/bin/kulan-signed-start"
	chmod +x "${RP}/bin/kulan-signed-start"
	echo 1 >"${RP}/power/AC/online"
	bash "${RP}/restore-points" undo-update >/dev/null 2>"${RP}/err" || { echo "undo failed: $(cat "${RP}/err")"; return 1; }
	grep -qx 'mo:$y$new' "${R}/etc/shadow" || { echo "the old password came back"; ok=1; }
	[[ "$(stat -c %a "${R}/etc/shadow")" == 640 ]] || { echo "shadow's permissions changed"; ok=1; }
	[[ "$(cat "${R}/var/lib/kulan/signed-start/kulan.key")" == current-key ]] || { echo "the key went back"; ok=1; }
	grep -q '"on"' "${R}/var/lib/kulan/signed-start.json" || { echo "signed start-up's state went back (GRUB would return)"; ok=1; }
	grep -q '"now"' "${R}/etc/kulan/fleet.json" || { echo "Fleet went back"; ok=1; }
	[[ ! -e "${R}/etc/nologin" ]] || { echo "an old lost mode came back"; ok=1; }
	[[ -e "${R}/etc/NetworkManager/system-connections/Home.nmconnection" ]] || { echo "a Wi-Fi network was lost"; ok=1; }
	grep -q "^kulan-signed-start rebuild --after-undo key=current-key$" "${RP}/calls" || { echo "start-up files not signed again: $(cat "${RP}/calls")"; ok=1; }
	[[ ! -e "${RP}/kept" ]] || { echo "kept copy left behind"; ok=1; }
	rm -f "${RP}/on-restore" "${RP}/bin/kulan-signed-start"
	return "${ok}"
}
check "undo keeps accounts, passwords, Fleet, lost mode, signed start-up and Wi-Fi as they are now" undo_keeps

undo_safely() {
	undo_env
	fake_timeshift
	local ok=0 out
	echo 1 >"${RP}/power/AC/online"
	# No room for the safety point: no undo at all.
	reset_points
	out="$(FAKE_AVAIL=1000 bash "${RP}/restore-points-strict" undo-update 2>/dev/null)" && { echo "undid without a safety point"; ok=1; }
	grep -q "^say:There isn't enough free space" <<<"${out}" || { echo "no room: ${out}"; ok=1; }
	grep -q -- "--restore" "${RP}/calls" 2>/dev/null && { echo "restored with no safety point"; ok=1; }
	# The restore stops half-way: back to the safety point, no restart, record untouched.
	reset_points
	out="$(FAKE_RESTORE_FAIL=1 bash "${RP}/restore-points" undo-update 2>/dev/null)" && { echo "a failed restore reported success"; ok=1; }
	grep -q "^say:The update couldn't be undone" <<<"${out}" || { echo "failed restore: ${out}"; ok=1; }
	grep -q -- "--restore --snapshot 2026-11-02_10-00-00" "${RP}/calls" || { echo "didn't go back to the safety point: $(cat "${RP}/calls")"; ok=1; }
	grep -q "systemctl reboot" "${RP}/calls" && { echo "restarted after a failed undo"; ok=1; }
	grep -q '"point": "2026-01-03_00-00-00"' "${KULAN_LAST_UPDATE}" || { echo "record changed"; ok=1; }
	# Lost mode or an erase waiting: no undo, from Software Update or the menu.
	reset_points
	touch "${RP}/root/var/lib/kulan/fleet-lost"
	bash "${RP}/restore-points" undo-update >/dev/null 2>&1 && { echo "undid in lost mode"; ok=1; }
	bash "${RP}/restore-points" undo-at-start >/dev/null 2>&1 || { echo "start-up stopped in lost mode"; ok=1; }
	grep -q -- "--restore" "${RP}/calls" 2>/dev/null && { echo "restored in lost mode"; ok=1; }
	rm -f "${RP}/root/var/lib/kulan/fleet-lost"
	# The last update's point was skipped: nothing to undo (not an older one).
	reset_points
	printf '{"point": "", "undone": "", "time": 1}\n' >"${KULAN_LAST_UPDATE}"
	out="$(bash "${RP}/restore-points" undo-update 2>/dev/null)" && { echo "went back to an older point"; ok=1; }
	grep -q "^say:There's no restore point from the last update" <<<"${out}" || { echo "skipped point: ${out}"; ok=1; }
	# From before 2.13 (no record): the newest point, only if under a week old.
	reset_points
	rm -f "${KULAN_LAST_UPDATE}"
	bash "${RP}/restore-points" undo-update >/dev/null 2>&1 && { echo "went back to a 10-month-old point"; ok=1; }
	return "${ok}"
}
check "undo: never without a safety point, back to it if the restore stops, not in lost mode, never further than the last update" undo_safely

update_point() {
	undo_env
	fake_timeshift
	rm -rf "${RP}/snaps" "${RP}/calls"
	mkdir -p "${RP}/snaps"
	rm -f "${KULAN_LAST_UPDATE}"
	local ok=0
	bash "${RP}/restore-points" create "Before installing updates" >/dev/null 2>&1 || { echo "create failed"; ok=1; }
	grep -q '"point": "2026-11-01_10-00-00"' "${KULAN_LAST_UPDATE}" || { echo "not recorded: $(cat "${KULAN_LAST_UPDATE}" 2>&1)"; ok=1; }
	# No room: no point, and the record says so (no undo for this update).
	FAKE_AVAIL=1000 bash "${RP}/restore-points-strict" create "Before installing updates" >/dev/null 2>&1 && { echo "made a point with no room"; ok=1; }
	grep -q '"point": ""' "${KULAN_LAST_UPDATE}" || { echo "skipped point still offered: $(cat "${KULAN_LAST_UPDATE}")"; ok=1; }
	return "${ok}"
}
check "the restore point before installing updates is recorded; a skipped one isn't offered" update_point

before_auto_update() {
	export KULAN_RESTORE_FORCE=1 KULAN_RESTORE_SNAPSHOTS="${RP}/snaps" KULAN_RESTORE_USAGE="${RP}/u.json"
	local stamp=/var/lib/kulan/auto-update-restore-point
	[[ ${EUID} -eq 0 ]] || { echo "skip (not root)"; return 0; }
	rm -f "${stamp}" "${RP}/calls"
	# Automatic updates off: nothing.
	printf '#!/bin/sh\necho "D='"'"'0'"'"'"\n' >"${RP}/bin/apt-config"
	chmod +x "${RP}/bin/apt-config"
	bash "${RP}/restore-points" before-auto-update || return 1
	[[ ! -e "${RP}/calls" && ! -e "${stamp}" ]] || { echo "a restore point with automatic updates off"; return 1; }
	# On, due, updates waiting, the disk nearly full after it: restore point, stamp, then 75.
	printf '#!/bin/sh\necho "D='"'"'1'"'"'"\n' >"${RP}/bin/apt-config"
	cat >"${RP}/bin/apt-get" <<'EOF'
#!/bin/sh
case "$*" in
	*-s*) echo "Inst tzdata [1] (2 Debian:13/stable [all])" ;;
	*) echo "Need to get 50.0 MB of archives."; echo "After this operation, 100 MB of additional disk space will be used." ;;
esac
EOF
	cat >"${RP}/bin/df" <<'EOF'
#!/bin/sh
case "$*" in
	*--output=avail*) printf 'Avail\n%s\n' "${FAKE_AVAIL:-999999999999}" ;;
	*) printf 'Size Avail\n%s %s\n' 999999999999 "${FAKE_AVAIL:-999999999999}" ;;
esac
EOF
	chmod +x "${RP}/bin/apt-get" "${RP}/bin/df"
	rm -f /var/lib/apt/periodic/unattended-upgrades-stamp.kulan-test
	FAKE_AVAIL=100000000 bash "${RP}/restore-points" before-auto-update
	rc=$?
	[[ ${rc} -eq 75 ]] || { echo "exit ${rc} with no room left (want 75)"; return 1; }
	grep -q -- "--create --scripted --comments Before installing updates" "${RP}/calls" && [[ -e "${stamp}" ]] || return 1
	# Once a day.
	rm -f "${RP}/calls"
	bash "${RP}/restore-points" before-auto-update && [[ ! -e "${RP}/calls" ]] || return 1
	# A failed restore point leaves no stamp (tried again next time), and never stops the updates.
	rm -f "${stamp}"
	printf '#!/bin/sh\nexit 1\n' >"${RP}/bin/timeshift"
	bash "${RP}/restore-points" before-auto-update && [[ ! -e "${stamp}" ]] || return 1
	rm -f "${stamp}"
}
check "restore point before automatic updates: before their lock, once a day, stamp only when made, room re-checked" before_auto_update

# --- units: timers, time limits, ordering -------------------------------------------------
units() {
	local u
	for u in kulan-restore-points.timer kulan-app-updates.timer; do
		grep -q "^Persistent=true" "${UNITS}/${u}" && { echo "${u}: catches up right after start-up"; return 1; }
		grep -q "^OnBootSec=" "${UNITS}/${u}" && grep -q "^OnUnitActiveSec=" "${UNITS}/${u}" || { echo "${u}"; return 1; }
	done
	for u in kulan-app-updates.service kulan-restore-points.service kulan-restore-points-tidy.service \
		kulan-update-status.service kulan-update-repair.service kulan-undo-at-start.service; do
		grep -q "^TimeoutStartSec=" "${UNITS}/${u}" || { echo "${u}: no time limit"; return 1; }
	done
	grep -q "^ExecCondition=.*restore-points before-auto-update; \[ \$\$? -ne 75 \]" \
		"${UNITS}/apt-daily-upgrade.service.d/60-kulan-restore-point.conf" || { echo "apt-daily-upgrade drop-in"; return 1; }
	grep -q "^Persistent=false" "${UNITS}/apt-daily-upgrade.timer.d/60-kulan-midday.conf" || return 1
	grep -q "^Before=.*display-manager.service" "${UNITS}/kulan-undo-at-start.service" || return 1
	grep -q "display-manager" "${UNITS}/kulan-update-repair.service" && { echo "repair in the way of the sign-in screen"; return 1; }
	grep -q "restore-points before-auto-update" "${UC}/etc/apt/apt.conf.d/60kulan-update-status" && { echo "still an apt hook"; return 1; }
	if command -v systemd-analyze >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
		for u in kulan-update-repair.service kulan-boot-ok.service kulan-undo-at-start.service; do
			systemd-analyze verify "${UNITS}/${u}" 2>&1 | grep -v "Cannot add dependency job\|not found\|Unit is bound to inactive" | grep . && return 1
		done
	fi
	return 0
}
check "timers start mid-session, every job has a time limit, nothing waits on repair" units

if [[ ${FAILED} -eq 0 ]]; then
	echo "update recovery: every case passed"
fi
exit "${FAILED}"
