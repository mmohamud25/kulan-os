#!/bin/bash
# Start the Kulan Fleet test server (after setup.sh). Stop it with Ctrl+C.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run it with sudo"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR=/opt/kulan-fleet-test
[ -x "${DIR}/deno" ] || { echo "Run setup.sh first"; exit 1; }
IP="$(cat "${DIR}/address")"
export SUPABASE_DB_URL="postgres://kulanfleet:$(cat "${DIR}/db-password")@127.0.0.1:5432/kulanfleet"
export SUPABASE_URL="https://${IP}:8443"
export SUPABASE_ANON_KEY=test-anon-key-kulan-fleet-test-server
export SUPABASE_SERVICE_ROLE_KEY="test-service-$(openssl rand -hex 16)"
export FLEET_FACTOR_KEY="$(cat "${DIR}/factor-key")"
export KULAN_FLEET_TEST_DIR="${DIR}"
# Google Workspace mailboxes (2.8e): a stand-in Google on this server, unless
# KULAN_FLEET_REAL_GOOGLE=1 (then a real Workspace's service account key works).
if [ "${KULAN_FLEET_REAL_GOOGLE:-}" != 1 ]; then
  export FLEET_GOOGLE_TOKEN_URL="https://${IP}:8443/google/token"
  export FLEET_GOOGLE_API="https://${IP}:8443/google"
fi
# Microsoft 365 mailboxes (2.12): a stand-in Microsoft too, unless KULAN_FLEET_REAL_MICROSOFT=1.
if [ "${KULAN_FLEET_REAL_MICROSOFT:-}" != 1 ]; then
  export FLEET_MS_LOGIN="https://${IP}:8443/microsoft"
  export FLEET_MS_GRAPH="https://${IP}:8443/msgraph"
fi
export DENO_DIR="${DIR}/cache"
exec "${DIR}/deno" run --cert "${DIR}/ca.crt" --allow-net --allow-env --allow-read --allow-sys "${HERE}/serve.ts"
