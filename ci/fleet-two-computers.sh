#!/bin/bash
# People, groups and policies in Kulan Fleet across two computers, end to end, on a CI runner:
# the Kulan Fleet test server (fleet/test-server, the real functions and
# every migration) on the runner, and two Debian 13 containers with this
# build's kulan-base as laptop-a and laptop-b, joined over HTTPS.
#
# Signing in is checked with pamtester through the "login" PAM service: the
# same password, expiry and nologin checks GDM's sign-in uses (only the
# sign-in screen itself isn't here).
#
#   ci/fleet-two-computers.sh DEBS_DIR       (as a user with sudo, Docker on)
set -euo pipefail
DEBS="$(cd "${1:?folder with kulan-base_*.deb}" && pwd)"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LOG=/tmp/fleet-test-server.log
FAILS=0
step() { printf '\n== %s\n' "$*"; }
pass() { printf 'PASS %s\n' "$*"; }
fail() { printf 'FAIL %s\n' "$*"; echo "::error title=Fleet people::$*"; FAILS=$((FAILS + 1)); }
# Stopped by an error outside a check: say where (CI shows it as an annotation).
trap 'echo "::error title=Fleet people stopped::line ${LINENO}: ${BASH_COMMAND}"' ERR
expect() { local what="$1"; shift; if "$@"; then pass "${what}"; else fail "${what}"; fi; }

cleanup() {
	docker rm -f laptop-a laptop-b >/dev/null 2>&1 || true
	sudo pkill -f 'deno run --cert' 2>/dev/null || true
}
trap cleanup EXIT

step "Test server"
command -v oathtool >/dev/null || sudo apt-get install -y -q oathtool >/dev/null  # phone codes, as an app makes them
sudo "${HERE}/fleet/test-server/setup.sh" ci@kulan.example
sudo "${HERE}/fleet/test-server/run.sh" > "${LOG}" 2>&1 &
IP="$(sudo cat /opt/kulan-fleet-test/address)"
B="https://${IP}:8443"
SERVER="${B}/functions/v1/fleet"
for _ in $(seq 90); do curl -fs "${B}/kulan-fleet-test-ca.crt" -o /dev/null && break; sleep 2; done
curl -fsS -X POST "${B}/auth/v1/otp" -d '{"email":"ci@kulan.example"}' >/dev/null
CODE="$(grep -o 'ci@kulan.example: [0-9]*' "${LOG}" | tail -n 1 | awk '{print $2}')"
TOK="$(curl -fsS -X POST "${B}/auth/v1/verify" -d "{\"type\":\"email\",\"email\":\"ci@kulan.example\",\"token\":\"${CODE}\"}" | jq -r .access_token)"
adm() { curl -sS -X POST "${B}/functions/v1/fleet-admin" -H "Authorization: Bearer ${TOK}" -d "$1"; }
ORG="$(adm '{"op":"new-organisation","name":"Two computers"}' | jq -r .id)"
JOIN="$(adm "{\"op\":\"new-code\",\"organisation_id\":\"${ORG}\",\"uses\":2}" | jq -r .code)"

step "Two laptops with this build's kulan-base"
for host in laptop-a laptop-b; do
	docker run -d --name "${host}" --hostname "${host}" -v "${DEBS}:/debs:ro" debian:trixie sleep infinity >/dev/null
	sudo cat /opt/kulan-fleet-test/ca.crt | docker exec -i "${host}" tee /tmp/kulan-fleet-test.crt >/dev/null
done
for host in laptop-a laptop-b; do
	docker exec "${host}" bash -c 'export DEBIAN_FRONTEND=noninteractive
		apt-get update -q >/dev/null && apt-get install -y -q --no-install-recommends ca-certificates openssl pamtester zsh passwd login /debs/kulan-base_*_all.deb >/tmp/apt.log 2>&1 || { echo "::error title=Installing on $(hostname)::$(tail -n 8 /tmp/apt.log | tr "\n" " ")"; exit 1; }
		cp /tmp/kulan-fleet-test.crt /usr/local/share/ca-certificates/ && update-ca-certificates >/dev/null 2>&1' &
	INSTALLS="${INSTALLS:-} $!"
done
# Only the installs: a bare "wait" would also wait for the server, for ever.
for pid in ${INSTALLS}; do wait "${pid}"; done
on() { docker exec "$1" bash -c "$2"; }
signs_in() { docker exec -i "$1" pamtester login "$2" authenticate acct_mgmt <<<"$3" >/dev/null 2>&1; }
report() { on "$1" "kulan-fleet report 2>&1 | grep -v '^Report sent' || true"; }

for host in laptop-a laptop-b; do
	expect "${host} joins over HTTPS" on "${host}" "echo '${JOIN}' | kulan-fleet join '${SERVER}' >/dev/null 2>&1"
done
DEV_B="$(adm '{"op":"overview"}' | jq -r '.devices[] | select(.hostname == "laptop-b") | .id')"

step "People"
SETUP="K7Q4-9XPA-M2RT"
SETUP_HASH="$(openssl passwd -6 -stdin <<<"${SETUP}")"
AMINA="$(adm "{\"op\":\"add-person\",\"organisation_id\":\"${ORG}\",\"username\":\"amina\",\"full_name\":\"Amina Yusuf\",\"password_hash\":\"${SETUP_HASH}\"}" | jq -r .id)"
OMAR_HASH="$(openssl passwd -6 -stdin <<<"OMAR-CODE-0001")"
OMAR="$(adm "{\"op\":\"add-person\",\"organisation_id\":\"${ORG}\",\"username\":\"omar\",\"full_name\":\"Omar Ali\",\"role\":\"admin\",\"all_computers\":false,\"computers\":[\"${DEV_B}\"],\"password_hash\":\"${OMAR_HASH}\"}" | jq -r .id)"
report laptop-a
report laptop-b
for host in laptop-a laptop-b; do
	expect "${host}: amina's account, uid 70000, name on it" on "${host}" 'getent passwd amina | grep -q "^amina:x:70000:.*:Amina Yusuf:/home/amina:"'
	expect "${host}: the setup code must be changed at first sign-in" on "${host}" 'test "$(getent shadow amina | cut -d: -f3)" = 0'
	expect "${host}: the setup code is accepted, then a new password is asked for" \
		bash -c "docker exec -i ${host} pamtester login amina authenticate acct_mgmt <<<'${SETUP}' 2>&1 | grep -q 'new one required'"
	expect "${host}: a wrong code is refused" bash -c "! docker exec -i ${host} pamtester login amina authenticate <<<'WRONG-CODE' >/dev/null 2>&1"
done
expect "laptop-a: no omar (only laptop-b was chosen)" on laptop-a '! getent passwd omar >/dev/null'
expect "laptop-b: omar, an administrator there" on laptop-b 'id -nG omar | grep -qw sudo'
expect "laptop-a: amina isn't an administrator" on laptop-a '! id -nG amina | grep -qw sudo'

step "Her own password follows her"
on laptop-a "echo 'amina:Amina-Own-Pass-1' | chpasswd"   # as passwd does when she chooses it
expect "laptop-a: she signs in with her own password" signs_in laptop-a amina Amina-Own-Pass-1
report laptop-a
password_taken() { adm "{\"op\":\"people\",\"organisation_id\":\"${ORG}\"}" | jq -e '.people[] | select(.username == "amina") | .must_change == false' >/dev/null; }
expect "server: her password changed, the setup code no longer asked for" password_taken
expect "laptop-b: the setup code still works before its next report" \
	bash -c "docker exec -i laptop-b pamtester login amina authenticate <<<'${SETUP}' >/dev/null 2>&1"
report laptop-b
expect "laptop-b: after its report, her own password works there" signs_in laptop-b amina Amina-Own-Pass-1
expect "laptop-b: and the setup code doesn't" bash -c "! docker exec -i laptop-b pamtester login amina authenticate <<<'${SETUP}' >/dev/null 2>&1"
report laptop-a
expect "laptop-a: her password isn't changed back" signs_in laptop-a amina Amina-Own-Pass-1

step "Disabled, then enabled"
adm "{\"op\":\"set-person-enabled\",\"person_id\":\"${AMINA}\",\"enabled\":false}" >/dev/null
report laptop-a
expect "laptop-a: disabled, she can't sign in" bash -c "! docker exec -i laptop-a pamtester login amina authenticate acct_mgmt <<<'Amina-Own-Pass-1' >/dev/null 2>&1"
adm "{\"op\":\"set-person-enabled\",\"person_id\":\"${AMINA}\",\"enabled\":true}" >/dev/null
report laptop-a
expect "laptop-a: enabled again, she signs in" signs_in laptop-a amina Amina-Own-Pass-1

step "Forgot her password: a new setup code"
NEW_CODE="R8WN-3HKD-PQ7Z"
adm "{\"op\":\"new-setup-code\",\"person_id\":\"${AMINA}\",\"password_hash\":\"$(openssl passwd -6 -stdin <<<"${NEW_CODE}")\"}" >/dev/null
report laptop-a
expect "laptop-a: the new code works and asks for a new password" \
	bash -c "docker exec -i laptop-a pamtester login amina authenticate acct_mgmt <<<'${NEW_CODE}' 2>&1 | grep -q 'new one required'"
expect "laptop-a: the forgotten password doesn't" bash -c "! docker exec -i laptop-a pamtester login amina authenticate <<<'Amina-Own-Pass-1' >/dev/null 2>&1"

step "Lost mode on laptop-b"
on laptop-b "echo 'omar:Omar-Own-Pass-1' | chpasswd"
report laptop-b
adm "{\"op\":\"action\",\"device_id\":\"${DEV_B}\",\"type\":\"lost-mode-on\"}" >/dev/null
report laptop-b
expect "laptop-b: in lost mode nobody signs in, not even an administrator" bash -c "! docker exec -i laptop-b pamtester login omar authenticate <<<'Omar-Own-Pass-1' >/dev/null 2>&1"
expect "laptop-b: the notice says whom to return it to" on laptop-b 'grep -q "please return it to Two computers" /etc/nologin'
lost_alert() { adm '{"op":"overview"}' | jq -e '.alerts[] | select(.kind == "lost")' >/dev/null; }
expect "server: it's an alert" lost_alert
adm "{\"op\":\"action\",\"device_id\":\"${DEV_B}\",\"type\":\"lost-mode-off\"}" >/dev/null
report laptop-b
expect "laptop-b: released, omar signs in again" signs_in laptop-b omar Omar-Own-Pass-1

step "Two-step sign-in (2.8d)"
signs_in2() { printf '%s\n%s\n' "$3" "$4" | docker exec -i "$1" pamtester login "$2" authenticate >/dev/null 2>&1; }
adm "{\"op\":\"set-policy\",\"organisation_id\":\"${ORG}\",\"policy\":{\"two_step\":\"everyone\"}}" >/dev/null
on laptop-a "echo 'amina:Amina-Pass-2' | chpasswd"
report laptop-a
report laptop-b
SECRET=3132333435363738393031323334353637383930
expect "laptop-a: amina sets up a phone code (checked first), through the helper" on laptop-a \
	"printf '%s\n%s\n' ${SECRET} \$(oathtool --totp ${SECRET}) | PKEXEC_UID=\$(id -u amina) /usr/lib/kulan/fleet-two-step add code Phone >/dev/null"
expect "laptop-a: a wrong code is refused when setting up" on laptop-a \
	"! printf '%s\n%s\n' ${SECRET} 000000 | PKEXEC_UID=\$(id -u amina) /usr/lib/kulan/fleet-two-step add code Other 2>/dev/null"
expect "laptop-a: it works at once: password and code" signs_in2 laptop-a amina Amina-Pass-2 "$(oathtool --totp ${SECRET})"
report laptop-a
report laptop-b
expect "laptop-b: her phone code arrived: the password alone isn't enough" bash -c "! printf 'Amina-Pass-2\n\n' | docker exec -i laptop-b pamtester login amina authenticate >/dev/null 2>&1"
expect "laptop-b: password and code" signs_in2 laptop-b amina Amina-Pass-2 "$(oathtool --totp ${SECRET})"
expect "laptop-b: only root can read the secret" on laptop-b 'test "$(stat -c %a /etc/kulan/two-step/users.oath)" = 600 && ! grep -q 3132333435 /var/lib/kulan/fleet-two-step-public.json'
two_step_listed() { adm "{\"op\":\"people\",\"organisation_id\":\"${ORG}\"}" | jq -e '.people[] | select(.username == "amina") | (.two_step | length == 1 and .[0].kind == "code" and .[0].created_on == "laptop-a")' >/dev/null; }
expect "server: amina's phone code is listed (never its secret)" two_step_listed
adm "{\"op\":\"reset-two-step\",\"person_id\":\"${AMINA}\"}" >/dev/null
report laptop-b
expect "laptop-b: after an admin's reset, the password alone again" bash -c "printf 'Amina-Pass-2\n' | docker exec -i laptop-b pamtester login amina authenticate >/dev/null 2>&1"
adm "{\"op\":\"set-policy\",\"organisation_id\":\"${ORG}\",\"policy\":{}}" >/dev/null
# Kulan Fleet admins: an authenticator app (Supabase MFA, the test server's stand-in).
FACTOR="$(curl -fsS -X POST "${B}/auth/v1/factors" -H "Authorization: Bearer ${TOK}" -d '{"factor_type":"totp","friendly_name":"ci"}')"
FID="$(jq -r .id <<<"${FACTOR}")"; B32="$(jq -r .totp.secret <<<"${FACTOR}")"
CH="$(curl -fsS -X POST "${B}/auth/v1/factors/${FID}/challenge" -H "Authorization: Bearer ${TOK}" -d '{}' | jq -r .id)"
TOK2="$(curl -fsS -X POST "${B}/auth/v1/factors/${FID}/verify" -H "Authorization: Bearer ${TOK}" -d "{\"challenge_id\":\"${CH}\",\"code\":\"$(oathtool --totp -b "${B32}")\"}" | jq -r .access_token)"
old_token_refused() { curl -sS -X POST "${B}/functions/v1/fleet-admin" -H "Authorization: Bearer ${TOK}" -d '{"op":"overview"}' | jq -e '.two_step == "code"' >/dev/null; }
expect "server: once set up, the email code alone isn't enough for Kulan Fleet" old_token_refused
TOK="${TOK2}"
aal2_in() { adm '{"op":"overview"}' | jq -e '.me.aal == "aal2"' >/dev/null; }
expect "server: with the authenticator app's code, in" aal2_in

step "Work email with Google Workspace (2.8e)"
# The test server's stand-in Google, its made-up service account key, acting as it@<domain>.
KEY="$(sudo cat /opt/kulan-fleet-test/google-test-key.json)"
connected() { adm "$(jq -n --arg o "${ORG}" --arg k "${KEY}" '{op: "connect-mail", organisation_id: $o, key: $k, admin_email: "it@kulan.example"}')" | jq -e '.domain == "kulan.example"' >/dev/null; }
expect "server: Google Workspace connected (the key checked first, kept sealed)" connected
# Geary would be a big download in these containers: blocked here, so the server doesn't ask for it.
adm "{\"op\":\"set-policy\",\"organisation_id\":\"${ORG}\",\"policy\":{\"blocked_apps\":[{\"source\":\"debian\",\"id\":\"geary\"}]}}" >/dev/null
made() { adm "{\"op\":\"mailbox\",\"person_id\":\"${AMINA}\",\"action\":\"create\",\"address\":\"amina@kulan.example\"}" | jq -e '.mailbox == "amina@kulan.example" and (.mailbox_password | test("^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3}$"))' >/dev/null; }
expect "server: a mailbox for amina, with a first Google password" made
grep -q "(stand-in Google) made amina@kulan.example" "${LOG}" && pass "stand-in Google: the mailbox was made" || fail "stand-in Google: the mailbox was made"
report laptop-a
report laptop-b
for host in laptop-a laptop-b; do
	expect "${host}: offers amina her work email (her own file, only she can read it)" on "${host}" \
		'python3 -c "import json; d = json.load(open(\"/var/lib/kulan/fleet-mail/amina\")); assert d == {\"address\": \"amina@kulan.example\", \"provider\": \"google\"}, d" && test "$(stat -c %a:%U /var/lib/kulan/fleet-mail/amina)" = 600:amina && test "$(ls /var/lib/kulan/fleet-mail)" = amina'
done
expect "laptop-a: Geary not asked for while a policy blocks it" on laptop-a \
	'! python3 -c "import json, sys; p = json.load(open(\"/var/lib/kulan/fleet-policy.json\"))[\"policy\"]; sys.exit(0 if any(a[\"id\"] == \"geary\" for a in p.get(\"required_apps\", [])) else 1)"'
adm "{\"op\":\"set-person-enabled\",\"person_id\":\"${AMINA}\",\"enabled\":false}" >/dev/null
grep -q "(stand-in Google) changed amina@kulan.example: suspended" "${LOG}" && pass "disabled: her mailbox suspended in Google" || fail "disabled: her mailbox suspended in Google"
report laptop-a
expect "laptop-a: disabled, her work email no longer offered" on laptop-a 'test ! -e /var/lib/kulan/fleet-mail/amina'
adm "{\"op\":\"set-person-enabled\",\"person_id\":\"${AMINA}\",\"enabled\":true}" >/dev/null
report laptop-a
expect "laptop-a: enabled again, offered again" on laptop-a 'grep -q amina@kulan.example /var/lib/kulan/fleet-mail/amina'
adm "{\"op\":\"set-policy\",\"organisation_id\":\"${ORG}\",\"policy\":{}}" >/dev/null

step "Who uses which computer (2.8c)"
sign_ins_sent() { adm '{"op":"overview"}' | jq -e '[.devices[] | select(.hostname == "laptop-a") | .sign_ins | type] == ["array"]' >/dev/null; }
expect "server: laptop-a reports who uses it (here, in a container, nobody)" sign_ins_sent
weekly() { adm "{\"op\":\"weekly-report\",\"organisation_id\":\"${ORG}\"}" | jq -e '.summary.computers.total == 2 and .summary.people.total >= 1' >/dev/null; }
expect "server: the weekly report counts both laptops" weekly

step "Groups and policies (2.8b)"
on laptop-a 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q cowsay >/dev/null 2>&1'   # an app the group will block
DEV_A="$(adm '{"op":"overview"}' | jq -r '.devices[] | select(.hostname == "laptop-a") | .id')"
OFFICE="$(adm "{\"op\":\"add-group\",\"organisation_id\":\"${ORG}\",\"name\":\"Office\",\"policy\":{\"usb\":\"approved\",\"required_apps\":[{\"source\":\"debian\",\"id\":\"sl\"}],\"blocked_apps\":[{\"source\":\"debian\",\"id\":\"cowsay\"}]}}" | jq -r .id)"
refused() { adm "{\"op\":\"add-group\",\"organisation_id\":\"${ORG}\",\"name\":\"Bad\",\"policy\":{\"blocked_apps\":[{\"source\":\"debian\",\"id\":\"kulan-base\"}]}}" | jq -e .error >/dev/null; }
expect "server: a group can't block Kulan OS itself" refused
adm "{\"op\":\"edit-group\",\"group_id\":\"${OFFICE}\",\"devices\":[\"${DEV_A}\"]}" >/dev/null
HODAN="$(adm "{\"op\":\"add-person\",\"organisation_id\":\"${ORG}\",\"username\":\"hodan\",\"full_name\":\"Hodan Ali\",\"all_computers\":false,\"groups\":[\"${OFFICE}\"],\"password_hash\":\"$(openssl passwd -6 -stdin <<<"HODA-N000-0001")\"}" | jq -r .id)"
report laptop-a
report laptop-b
expect "laptop-a: in Office, the required app is installed" on laptop-a 'dpkg -s sl 2>/dev/null | grep -qx "Status: install ok installed"'
expect "laptop-a: the blocked app removed and kept off" on laptop-a '! dpkg -s cowsay >/dev/null 2>&1 && grep -q "Package: cowsay" /etc/apt/preferences.d/60-kulan-fleet-blocked && ! apt-get install -s cowsay >/dev/null 2>&1'
expect "laptop-a: USB storage limited to approved drives (the udev rule)" on laptop-a 'grep -q "bInterfaceClass" /etc/udev/rules.d/60-kulan-fleet-usb.rules && python3 -c "import json; s = json.load(open(\"/var/lib/kulan/fleet-policy.json\"))[\"status\"]; assert s[\"usb\"] == \"met\" and s[\"app:install:debian:sl\"] == \"met\" and s[\"app:block:debian:cowsay\"] == \"met\", s"'
expect "laptop-b: not in Office, none of it" on laptop-b '! dpkg -s sl >/dev/null 2>&1 && test ! -e /etc/udev/rules.d/60-kulan-fleet-usb.rules'
expect "laptop-a: hodan, through the Office group" on laptop-a 'getent passwd hodan >/dev/null'
expect "laptop-b: no hodan" on laptop-b '! getent passwd hodan >/dev/null'
policy_of_a() { adm "{\"op\":\"device-policy\",\"device_id\":\"${DEV_A}\"}" | jq -e '.policy.usb == "approved" and (.groups | map(.name) == ["Office"])' >/dev/null; }
expect "server: laptop-a's policy comes from Office" policy_of_a
# laptop-b joins the group: hodan and the policy follow; then an app for the whole group, once.
adm "{\"op\":\"edit-group\",\"group_id\":\"${OFFICE}\",\"devices\":[\"${DEV_A}\",\"${DEV_B}\"]}" >/dev/null
COUNT="$(adm "{\"op\":\"group-action\",\"group_id\":\"${OFFICE}\",\"type\":\"install-app\",\"payload\":{\"source\":\"debian\",\"id\":\"hello\"}}" | jq -r .count)"
expect "server: install-app asked of both computers" test "${COUNT}" = 2
report laptop-a
report laptop-b
for host in laptop-a laptop-b; do
	expect "${host}: hello installed once, as the group asked" on "${host}" 'dpkg -s hello 2>/dev/null | grep -qx "Status: install ok installed"'
done
expect "laptop-b: added to Office, hodan and the required app follow" on laptop-b 'getent passwd hodan >/dev/null && dpkg -s sl >/dev/null 2>&1'
# Taken out of the group: the rule and the pin go, apps stay as they are.
adm "{\"op\":\"edit-group\",\"group_id\":\"${OFFICE}\",\"devices\":[\"${DEV_A}\"]}" >/dev/null
report laptop-b
expect "laptop-b: out of Office, USB and the pin back to normal, apps kept" on laptop-b 'test ! -e /etc/udev/rules.d/60-kulan-fleet-usb.rules && test ! -e /etc/apt/preferences.d/60-kulan-fleet-blocked && dpkg -s hello >/dev/null 2>&1'
adm "{\"op\":\"remove-person\",\"person_id\":\"${HODAN}\",\"remove_files\":true,\"confirm\":\"hodan\"}" >/dev/null

step "Leaving the organisation's people"
on laptop-a "echo handover > /home/amina/report.txt"
adm "{\"op\":\"remove-person\",\"person_id\":\"${AMINA}\",\"remove_files\":false,\"confirm\":\"amina\",\"mailbox\":\"delete\"}" >/dev/null
adm "{\"op\":\"remove-person\",\"person_id\":\"${OMAR}\",\"remove_files\":true,\"confirm\":\"omar\"}" >/dev/null
report laptop-a
report laptop-b
grep -q "(stand-in Google) deleted amina@kulan.example" "${LOG}" && pass "removed with 'delete': her mailbox deleted in Google" || fail "removed with 'delete': her mailbox deleted in Google"
expect "laptop-a: amina's account removed, her files kept for handover" on laptop-a '! getent passwd amina >/dev/null && grep -q handover /home/.kulan-left/amina-*/report.txt'
expect "laptop-b: omar removed with his files" on laptop-b '! getent passwd omar >/dev/null && test ! -e /home/omar'

step "Activity log"
LOGS="$(adm "{\"op\":\"log\",\"organisation_id\":\"${ORG}\"}" | jq -r '.entries[] | "\(.email): \(.what)"')"
for want in "ci@kulan.example: added amina" "amina: changed their password on a computer" "ci@kulan.example: disabled amina" \
	"ci@kulan.example: removed amina" "ci@kulan.example: asked: lost-mode-on" "ci@kulan.example: added group Office" \
	"ci@kulan.example: asked group Office: install-app" "ci@kulan.example: connected Google Workspace" \
	"ci@kulan.example: made a mailbox for amina" "ci@kulan.example: suspended amina's mailbox" \
	"ci@kulan.example: deleted amina's mailbox"; do
	expect "in the log: ${want}" grep -qxF "${want}" <<<"${LOGS}"
done

step "Teardown"
for host in laptop-a laptop-b; do on "${host}" "kulan-fleet leave >/dev/null 2>&1" || true; done
expect "laptop-a: leaving takes the USB rule and the pin away, apps stay" on laptop-a 'test ! -e /etc/udev/rules.d/60-kulan-fleet-usb.rules && test ! -e /etc/apt/preferences.d/60-kulan-fleet-blocked && dpkg -s sl >/dev/null 2>&1'
expect "laptop-a: no work email offered after leaving" on laptop-a 'test ! -e /var/lib/kulan/fleet-mail'
cleanup
sudo "${HERE}/fleet/test-server/teardown.sh"
expect "the test server leaves nothing" test ! -e /opt/kulan-fleet-test

if [ "${FAILS}" -gt 0 ]; then
	echo; echo "${FAILS} check(s) failed. Server log:"; tail -n 60 "${LOG}"
	echo "::error title=Server log::$(grep -vE ' (200|204) *$' "${LOG}" | tail -n 15 | tr '\n' ' ')"
	exit 1
fi
echo; echo "People across two computers: every check passed"
