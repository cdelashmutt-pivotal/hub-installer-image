#!/usr/bin/env bash
set -euo pipefail

SOURCE_KUBECONFIG="${SOURCE_KUBECONFIG:-/host-kube/config}"
TARGET_KUBECONFIG="${KUBECONFIG:-/root/.kube/config}"
REPLACEMENT_HOST="${REPLACEMENT_HOST:-host.docker.internal}"

mkdir -p "$(dirname "$TARGET_KUBECONFIG")"

if [ -f "$SOURCE_KUBECONFIG" ]; then
    KUBECONFIG="$SOURCE_KUBECONFIG" kubectl config view --raw -o json | jq --arg host "$REPLACEMENT_HOST" '
        .clusters = ((.clusters // []) | map(
            if ((.cluster.server // "") | test("127\\.0\\.0\\.1")) then
                .cluster.server |= sub("127\\.0\\.0\\.1"; $host)
                | .cluster["insecure-skip-tls-verify"] = true
                | del(.cluster["certificate-authority-data"])
                | del(.cluster["certificate-authority"])
            else
                .
            end
        ))
    ' > "$TARGET_KUBECONFIG"
    chmod 600 "$TARGET_KUBECONFIG"
    echo "entrypoint: loaded kubeconfig from $SOURCE_KUBECONFIG, rewrote 127.0.0.1 -> $REPLACEMENT_HOST (insecure-skip-tls-verify)" >&2
else
    echo "entrypoint: no kubeconfig found at $SOURCE_KUBECONFIG, skipping" >&2
fi

exec "$@"
