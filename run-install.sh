#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

INSTALL_VERSION="${INSTALL_VERSION:-11.0.0-350-europa+va99b428}"
REPOSITORY="tanzu-platform-sm.packages.broadcom.com/hub-self-managed/${INSTALL_VERSION//+/--}/packages"
CONFIG_FILE="${CONFIG_FILE:-hub-install-config.yaml}"
KUBECONFIG_PATH="${KUBECONFIG:-/root/.kube/config}"
USERNAME="${TANZU_HUB_USERNAME:-}"

if [ -z "$USERNAME" ]; then
    read -r -p "Tanzu Hub username (Broadcom support portal email): " USERNAME
fi

if [ -z "${TANZU_HUB_TOKEN:-}" ]; then
    read -r -s -p "Tanzu Hub registry token for ${USERNAME}: " TANZU_HUB_TOKEN
    echo >&2
fi

./tanzu-sm-installer install \
    --repository "$REPOSITORY" \
    --install-version "$INSTALL_VERSION" \
    --authentication "${USERNAME}:${TANZU_HUB_TOKEN}" \
    --config-file "$CONFIG_FILE" \
    --kubeconfig "$KUBECONFIG_PATH" \
    --install-local-path

unset TANZU_HUB_TOKEN
