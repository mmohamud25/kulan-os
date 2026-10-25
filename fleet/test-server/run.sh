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
export SUPABASE_ANON_KEY=test
export SUPABASE_SERVICE_ROLE_KEY="test-service-$(openssl rand -hex 16)"
export FLEET_FACTOR_KEY="$(cat "${DIR}/factor-key")"
export KULAN_FLEET_TEST_DIR="${DIR}"
export DENO_DIR="${DIR}/cache"
exec "${DIR}/deno" run --cert "${DIR}/ca.crt" --allow-net --allow-env --allow-read --allow-sys "${HERE}/serve.ts"
