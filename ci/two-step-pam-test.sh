#!/bin/bash
# Kulan Fleet two-step sign-in through the real PAM stack (common-auth, as
# pam-auth-update wrote it from kulan-base's profile), in a throwaway
# system: a person with a phone code signs in with the password and the
# code, not without it and not with a used code; sudo asks the password
# only; everyone else as before. Run as root (CI's package-test container).
set -euo pipefail
FAILS=0
t() { local svc=$1 user=$2 want=$3 what=$4; shift 4
	if printf '%s\n' "$@" | pamtester "$svc" "$user" authenticate >/dev/null 2>&1; then got=in; else got=refused; fi
	if [ "$got" = "$want" ]; then echo "ok: $what"; else echo "::error::two-step PAM: $what (got $got)"; FAILS=$((FAILS + 1)); fi; }
command -v pamtester >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q pamtester >/dev/null
grep -q "pam_succeed_if.so quiet user ingroup kulan-two-step" /etc/pam.d/common-auth || { echo "::error::two-step PAM: not in common-auth"; exit 1; }
getent group kulan-two-step >/dev/null || { echo "::error::two-step PAM: no kulan-two-step group"; exit 1; }
for u in zztwostep zzplain; do id "$u" >/dev/null 2>&1 || useradd -m "$u"; echo "$u:Pass-Word-1" | chpasswd; done
SECRET=3132333435363738393031323334353637383930
python3 -I -B - <<PY
import subprocess, sys
sys.path.insert(0, "/usr/share/kulan-fleet")
import twostep
run = lambda a, timeout=60: subprocess.run(a, capture_output=True, text=True)
accounts = [{"username": "zztwostep", "two_step": {"required": True, "factors": [
    {"id": "00000000-0000-4000-8000-000000000001", "kind": "code", "label": "Phone", "data": "${SECRET}"}]}}]
twostep.apply(run, accounts, {"zztwostep"})
PY
t login zztwostep in "password and the phone code" Pass-Word-1 "$(oathtool --totp "$SECRET")"
t login zztwostep refused "password and a wrong code" Pass-Word-1 000000
t login zztwostep refused "a wrong password and the code" Wrong-1 "$(oathtool --totp "$SECRET")"
t sudo zztwostep in "sudo: the password only (they passed two-step to sign in)" Pass-Word-1
# su and polkit's prompts (another person's password) use common-auth like this:
printf 'auth include common-auth\naccount include common-account\n' > /etc/pam.d/kulan-two-step-check
t kulan-two-step-check zztwostep refused "like su or polkit (another person's password): the password alone isn't enough" Pass-Word-1 ""
t kulan-two-step-check zztwostep in "like su or polkit: their password and their code" Pass-Word-1 "$(sleep 31; oathtool --totp "$SECRET")"
rm -f /etc/pam.d/kulan-two-step-check
t login zzplain in "someone without two-step: the password, as before" Pass-Word-1
t login zzplain refused "someone without two-step: a wrong password" Wrong-1
python3 -I -B -c 'import subprocess, sys; sys.path.insert(0, "/usr/share/kulan-fleet"); import twostep; twostep.remove_all(lambda a, timeout=60: subprocess.run(a, capture_output=True))'
t login zztwostep in "after leaving the organisation: the password alone" Pass-Word-1
userdel -r zztwostep >/dev/null 2>&1 || true
userdel -r zzplain >/dev/null 2>&1 || true
[ "$FAILS" = 0 ] && echo "two-step PAM: every case passed"
exit "$FAILS"
