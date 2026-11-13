#!/usr/bin/env bash
# Refreshes the registrysecret image-pull secret in the tanzusm namespace, which
# kapp-controller/kubelet use to resolve Hub's packages from the Broadcom registry.
#
# Same username/token as run-install.sh (the token expires after ~2 days) — rerun
# this instead of a full teardown/reinstall when package resolution starts failing
# with 401/403s on an already-installed cluster.
set -euo pipefail

NAMESPACE="${NAMESPACE:-tanzusm}"
SECRET_NAME="${SECRET_NAME:-registrysecret}"
REGISTRY="${REGISTRY:-tanzu-platform-sm.packages.broadcom.com}"
USERNAME="${TANZU_HUB_USERNAME:-}"

command -v kubectl >/dev/null 2>&1 || { echo "update-registry-secret: 'kubectl' not found on PATH" >&2; exit 1; }

if [ -z "$USERNAME" ]; then
    read -r -p "Tanzu Hub username (Broadcom support portal email): " USERNAME
fi

if [ -z "${TANZU_HUB_TOKEN:-}" ]; then
    read -r -s -p "Tanzu Hub registry token for ${USERNAME}: " TANZU_HUB_TOKEN
    echo >&2
fi

kubectl -n "$NAMESPACE" create secret docker-registry "$SECRET_NAME" \
    --docker-server="$REGISTRY" \
    --docker-username="$USERNAME" \
    --docker-password="$TANZU_HUB_TOKEN" \
    --dry-run=client -o yaml \
  | kubectl label --local -f - app=registry-secret -o yaml --dry-run=client \
  | kubectl apply -f -

unset TANZU_HUB_TOKEN
echo "update-registry-secret: updated ${SECRET_NAME} in ${NAMESPACE} for registry ${REGISTRY}" >&2
