#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CONFIG="$SCRIPT_DIR/hub-cluster.yaml"
HUB_CONFIG_OUT="$SCRIPT_DIR/hub-install-config.yaml"

CLUSTER_NAME="$(awk -F'[: ]+' '/^name:/{print $2; exit}' "$KIND_CONFIG")"

command -v kind >/dev/null 2>&1 || { echo "teardown-cluster: 'kind' not found on PATH" >&2; exit 1; }

echo "teardown-cluster: deleting kind cluster '${CLUSTER_NAME}'" >&2
kind delete cluster --name "$CLUSTER_NAME"

if [ -f "$HUB_CONFIG_OUT" ]; then
    rm -f "$HUB_CONFIG_OUT"
    echo "teardown-cluster: removed stale ${HUB_CONFIG_OUT} (load balancer IP no longer valid once the cluster is gone)" >&2
fi
