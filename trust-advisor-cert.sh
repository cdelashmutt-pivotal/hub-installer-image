#!/usr/bin/env bash
# Extracts Hub's self-signed ingress cert for the Application Advisor CLI.
#
# The Advisor CLI is a GraalVM native-image binary, not launched via `java`,
# so it doesn't honor JDK_JAVA_OPTIONS or the -Djavax.net.ssl.trustStore=...
# approach documented at
# https://techdocs.broadcom.com/us/en/vmware-tanzu/spring/application-advisor/1-6/app-advisor/portfolio-analysis-with-tanzu-platform.html#-connecting-with-self-signed-certificates
# (confirmed by testing: JDK_JAVA_OPTIONS is silently ignored). Instead it
# reads an undocumented env var, APP_ADVISOR_OPTIONS_CERT_PATH, directly off
# a PEM file — confirmed by pointing it at a missing file and getting
# "Unable to use the client certificate at '...' (APP_ADVISOR_OPTIONS_CERT_PATH)".
# No JKS truststore needed.
#
# Hub's cert is regenerated every time the kind cluster is recreated, so rerun
# this after every ./teardown-cluster.sh + ./setup-cluster.sh cycle. Safe to
# rerun any time — overwrites the cert file in place.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-hub.local}"
PORT="${2:-443}"
CERT_FILE="$SCRIPT_DIR/server-cert.pem"

command -v openssl >/dev/null 2>&1 || { echo "trust-advisor-cert: 'openssl' not found on PATH" >&2; exit 1; }

echo "trust-advisor-cert: fetching cert from ${HOST}:${PORT}" >&2
openssl s_client -showcerts -connect "${HOST}:${PORT}" -servername "$HOST" </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > "$CERT_FILE"

if [ ! -s "$CERT_FILE" ]; then
    echo "trust-advisor-cert: no cert received from ${HOST}:${PORT} — is Hub up and reachable there?" >&2
    exit 1
fi
echo "trust-advisor-cert: wrote ${CERT_FILE}" >&2

echo "trust-advisor-cert: run advisor commands with:" >&2
echo "  APP_ADVISOR_OPTIONS_CERT_PATH=${CERT_FILE} advisor ..." >&2
echo "trust-advisor-cert: or export it once for the rest of the session:" >&2
echo "  export APP_ADVISOR_OPTIONS_CERT_PATH=${CERT_FILE}" >&2
echo "trust-advisor-cert: (see .envrc.example to have direnv set this automatically)" >&2
