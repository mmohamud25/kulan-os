#!/bin/bash
# Kulan Fleet test server: set it up on a Kulan OS (or Debian 13) computer.
#
#   sudo ./setup.sh ADMIN_EMAIL
#
# Installs PostgreSQL and Deno, makes a test database with every Fleet
# migration, a test certificate authority and this computer's certificate,
# trusts that authority here, makes ADMIN_EMAIL an admin for all
# organisations, and opens port 8443 in the firewall. Then: ./run.sh.
# Everything it adds is removed by teardown.sh. Never for real use.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run it with sudo"; exit 1; }
EMAIL="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"
[[ "${EMAIL}" == *@*.* ]] || { echo "Usage: sudo ./setup.sh you@example.com"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/kulan-fleet-test
DENO_VERSION=v2.5.4
mkdir -p "${DIR}"
chmod 700 "${DIR}"

echo "== Installing PostgreSQL"
# Noted, so teardown.sh removes PostgreSQL only if this put it here.
dpkg -s postgresql >/dev/null 2>&1 || touch /var/lib/kulan-fleet-test-installed-postgresql
apt-get update -q
apt-get install -y -q --no-install-recommends postgresql unzip curl openssl ca-certificates >/dev/null
systemctl enable --now postgresql >/dev/null

echo "== Installing Deno ${DENO_VERSION} (in ${DIR} only)"
case "$(uname -m)" in
	x86_64) ARCH=x86_64 ;;
	aarch64|arm64) ARCH=aarch64 ;;
	*) echo "Unknown processor $(uname -m)"; exit 1 ;;
esac
if [ ! -x "${DIR}/deno" ]; then
	curl -fsSL -o "${DIR}/deno.zip" "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-${ARCH}-unknown-linux-gnu.zip"
	unzip -q -o "${DIR}/deno.zip" -d "${DIR}"
	rm -f "${DIR}/deno.zip"
fi
"${DIR}/deno" --version | head -n 1

echo "== Test database"
if [ ! -s "${DIR}/db-password" ]; then
	openssl rand -hex 24 > "${DIR}/db-password"
	chmod 600 "${DIR}/db-password"
fi
PW="$(cat "${DIR}/db-password")"
sudo -u postgres psql -q -v ON_ERROR_STOP=1 <<SQL
do \$\$ begin create role anon; exception when duplicate_object then null; end \$\$;
do \$\$ begin create role authenticated; exception when duplicate_object then null; end \$\$;
do \$\$ begin create role kulanfleet login password '${PW}'; exception when duplicate_object then alter role kulanfleet password '${PW}'; end \$\$;
SQL
if ! sudo -u postgres psql -Atqc "select 1 from pg_database where datname = 'kulanfleet'" | grep -q 1; then
	sudo -u postgres createdb -O kulanfleet kulanfleet
	sudo -u postgres psql -q -d kulanfleet -v ON_ERROR_STOP=1 -c "create schema extensions authorization kulanfleet; create extension pgcrypto schema extensions;"
fi
# Supabase Auth's users, as far as Kulan Fleet reads them (the stand-in sign-in fills it).
sudo -u postgres psql -q -d kulanfleet -v ON_ERROR_STOP=1 \
	-c "create schema if not exists auth authorization kulanfleet" \
	-c "create table if not exists auth.users (id text primary key, email text unique)" -c "alter table auth.users owner to kulanfleet"
# Every migration, every time: they're safe to run again, and a newer
# Kulan Fleet's are added to a test server set up for an older one.
for f in "${HERE}"/../supabase/migrations/*.sql; do
	echo "   $(basename "$f")"
	PGPASSWORD="${PW}" psql -q -h 127.0.0.1 -U kulanfleet -d kulanfleet -v ON_ERROR_STOP=1 -f "$f" >/dev/null
done
# The key that seals people's phone-code secrets (FLEET_FACTOR_KEY), this test server's own.
if [ ! -s "${DIR}/factor-key" ]; then
	openssl rand -base64 32 > "${DIR}/factor-key"
	chmod 600 "${DIR}/factor-key"
fi
PGPASSWORD="${PW}" psql -q -h 127.0.0.1 -U kulanfleet -d kulanfleet -v ON_ERROR_STOP=1 \
	-c "insert into fleet.admins (email, organisation_id) values ('${EMAIL//\'/}', null) on conflict do nothing"

echo "== Test certificates"
IP="$(hostname -I | awk '{print $1}')"
if [ ! -s "${DIR}/ca.crt" ]; then
	# Python 3.13 checks certificates strictly: the authority needs its key
	# usage, and this computer's certificate its key usage and key ids.
	openssl req -x509 -newkey rsa:3072 -nodes -days 60 -subj "/CN=Kulan Fleet TEST authority (not for real use)" \
		-addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
		-addext "subjectKeyIdentifier=hash" -keyout "${DIR}/ca.key" -out "${DIR}/ca.crt" 2>/dev/null
fi
openssl req -newkey rsa:2048 -nodes -subj "/CN=${IP}" -keyout "${DIR}/server.key" -out "${DIR}/server.csr" 2>/dev/null
printf '%s\n' "subjectAltName=IP:${IP},IP:127.0.0.1,DNS:localhost,DNS:$(hostname)" "basicConstraints=critical,CA:FALSE" \
	"keyUsage=critical,digitalSignature,keyEncipherment" "extendedKeyUsage=serverAuth" \
	"subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid,issuer" > "${DIR}/san.ext"
openssl x509 -req -in "${DIR}/server.csr" -CA "${DIR}/ca.crt" -CAkey "${DIR}/ca.key" -CAcreateserial -days 30 \
	-extfile "${DIR}/san.ext" -out "${DIR}/server.crt" 2>/dev/null
chmod 600 "${DIR}"/*.key
cp "${DIR}/ca.crt" /usr/local/share/ca-certificates/kulan-fleet-test.crt
update-ca-certificates >/dev/null 2>&1
echo "${IP}" > "${DIR}/address"

echo "== Firewall: port 8443 open for the test computers"
if command -v ufw >/dev/null; then ufw allow 8443/tcp comment "Kulan Fleet test server" >/dev/null || true; fi

FP="$(openssl x509 -in "${DIR}/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)"
cat <<EOF

Done. Start the server with:   sudo ${HERE}/run.sh

Server address for the test computers:  https://${IP}:8443/functions/v1/fleet
Kulan Fleet app on this computer:      KULAN_FLEET_URL=https://${IP}:8443 kulan-fleet-admin
Test authority fingerprint (check it on the other computer): ${FP}
EOF
