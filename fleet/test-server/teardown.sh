#!/bin/bash
# Remove the Kulan Fleet test server and everything setup.sh added: the test
# database and its role, Deno, the certificates and the trust in them, and
# the firewall rule, and PostgreSQL if setup.sh was what installed it.
#
# On the other test computers, remove the trust in the test authority:
#   sudo rm /usr/local/share/ca-certificates/kulan-fleet-test.crt && sudo update-ca-certificates --fresh
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "Run it with sudo"; exit 1; }
if command -v psql >/dev/null && systemctl is-active -q postgresql; then
	sudo -u postgres dropdb --if-exists kulanfleet
	sudo -u postgres psql -q -c "drop role if exists kulanfleet;"
	[ -e /var/lib/kulan-fleet-test-installed-postgresql ] && sudo -u postgres psql -q -c "drop role if exists anon; drop role if exists authenticated;"
fi
rm -rf /opt/kulan-fleet-test
rm -f /usr/local/share/ca-certificates/kulan-fleet-test.crt
update-ca-certificates --fresh >/dev/null 2>&1
if command -v ufw >/dev/null; then ufw delete allow 8443/tcp >/dev/null 2>&1 || true; fi
if [ -e /var/lib/kulan-fleet-test-installed-postgresql ]; then
	apt-get purge -y -q 'postgresql*' >/dev/null 2>&1
	apt-get autoremove -y -q >/dev/null 2>&1
	rm -rf /var/lib/postgresql /etc/postgresql
	rm -f /var/lib/kulan-fleet-test-installed-postgresql
fi
echo "Kulan Fleet test server removed"
