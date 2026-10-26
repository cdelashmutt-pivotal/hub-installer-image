#!/usr/bin/env bash
# Extracts Hub's self-signed ingress cert and imports it into a JKS truststore
# for the Application Advisor CLI, per:
# https://techdocs.broadcom.com/us/en/vmware-tanzu/spring/application-advisor/1-6/app-advisor/portfolio-analysis-with-tanzu-platform.html#-connecting-with-self-signed-certificates
#
# Hub's cert is regenerated every time the kind cluster is recreated, so rerun
# this after every ./teardown-cluster.sh + ./setup-cluster.sh cycle. Safe to
# rerun any time — overwrites the cert file and replaces the truststore alias.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-hub.local}"
PORT="${2:-443}"
CERT_FILE="$SCRIPT_DIR/server-cert.pem"
TRUSTSTORE="$SCRIPT_DIR/myTrustStore.jks"
TRUSTSTORE_PASSWORD="${TRUSTSTORE_PASSWORD:-changeit}"
ALIAS="hub-local"

for bin in openssl keytool; do
    command -v "$bin" >/dev/null 2>&1 || { echo "trust-advisor-cert: '$bin' not found on PATH" >&2; exit 1; }
done

echo "trust-advisor-cert: fetching cert from ${HOST}:${PORT}" >&2
openssl s_client -showcerts -connect "${HOST}:${PORT}" -servername "$HOST" </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > "$CERT_FILE"

if [ ! -s "$CERT_FILE" ]; then
    echo "trust-advisor-cert: no cert received from ${HOST}:${PORT} — is Hub up and reachable there?" >&2
    exit 1
fi
echo "trust-advisor-cert: wrote ${CERT_FILE}" >&2

# Drop any existing alias first so rerunning after a cluster recreate (new
# cert) doesn't fail with "alias already exists".
if [ -f "$TRUSTSTORE" ]; then
    keytool -delete -alias "$ALIAS" -keystore "$TRUSTSTORE" -storepass "$TRUSTSTORE_PASSWORD" 2>/dev/null || true
fi

keytool -import -trustcacerts -noprompt \
  -keystore "$TRUSTSTORE" -storepass "$TRUSTSTORE_PASSWORD" \
  -file "$CERT_FILE" -alias "$ALIAS"

echo "trust-advisor-cert: imported into ${TRUSTSTORE} (alias '${ALIAS}')" >&2
echo "trust-advisor-cert: run advisor commands with:" >&2
echo "  -Djavax.net.ssl.trustStore=${TRUSTSTORE} -Djavax.net.ssl.trustStorePassword=${TRUSTSTORE_PASSWORD}" >&2
echo "trust-advisor-cert: or, to avoid passing those on every command, export:" >&2
echo "  export JDK_JAVA_OPTIONS=\"-Djavax.net.ssl.trustStore=${TRUSTSTORE} -Djavax.net.ssl.trustStorePassword=${TRUSTSTORE_PASSWORD}\"" >&2
echo "trust-advisor-cert: (see .envrc.example to have direnv set this automatically)" >&2
