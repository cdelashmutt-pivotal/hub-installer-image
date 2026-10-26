#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIND_CONFIG="$SCRIPT_DIR/hub-cluster.yaml"
STORAGE_CLASS="$SCRIPT_DIR/sc.yaml"
HUB_CONFIG_TEMPLATE="$SCRIPT_DIR/hub-install-config.yaml.tmpl"
HUB_CONFIG_OUT="$SCRIPT_DIR/hub-install-config.yaml"

CLUSTER_NAME="$(awk -F'[: ]+' '/^name:/{print $2; exit}' "$KIND_CONFIG")"
CONTAINER_PORT="$(grep -m1 'containerPort:' "$KIND_CONFIG" | grep -oE '[0-9]+')"

for bin in kind kubectl docker; do
    command -v "$bin" >/dev/null 2>&1 || { echo "setup-cluster: '$bin' not found on PATH" >&2; exit 1; }
done

# Warn and ask before continuing if the nodes are (or will be) capped at a
# finite PID limit, which starves the Hub JVMs of threads. See the README
# section on `fork: Resource temporarily unavailable`.
confirm_pid_limit() {
    local problem="$1"
    echo "setup-cluster: WARNING: ${problem}" >&2
    echo "  Hub will be unreliable. Run ./setup-podman-machine.sh (and recreate the cluster)," >&2
    echo "  or see the README for the in-place fix." >&2
    if [ ! -t 0 ]; then
        echo "setup-cluster: not a terminal, refusing to continue" >&2
        exit 1
    fi
    read -r -p "Continue anyway? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || exit 1
}

check_pid_limit() {
    local node limit capped=()
    if docker ps -a --format '{{.Names}}' | grep -qx "${CLUSTER_NAME}-control-plane"; then
        for node in $(docker ps -a --format '{{.Names}}' | grep "^${CLUSTER_NAME}-"); do
            limit="$(docker inspect "$node" --format '{{.HostConfig.PidsLimit}}')"
            [ "$limit" -gt 0 ] && capped+=("${node}=${limit}")
        done
        [ "${#capped[@]}" -gt 0 ] && confirm_pid_limit "node containers have a PID limit: ${capped[*]}"
    elif command -v podman >/dev/null 2>&1 \
        && ! podman machine ssh 'sudo grep -qE "^pids_limit *= *(0|-1)$" /root/.config/containers/containers.conf' 2>/dev/null; then
        confirm_pid_limit "the Podman VM does not set pids_limit=0 for root, so new nodes will get the 2048 default"
    fi
    return 0
}

check_pid_limit

# kind's own `get clusters` can fail against some podman versions (a podman
# `ps --format` template bug indexing .Labels), so check for the
# control-plane container directly instead of trusting `kind get clusters`.
if docker ps -a --format '{{.Names}}' | grep -qx "${CLUSTER_NAME}-control-plane"; then
    echo "setup-cluster: kind cluster '${CLUSTER_NAME}' already exists, skipping creation" >&2
else
    echo "setup-cluster: creating kind cluster '${CLUSTER_NAME}'" >&2
    kind create cluster --config "$KIND_CONFIG"
fi

echo "setup-cluster: applying storage class" >&2
kubectl apply -f "$STORAGE_CLASS"

echo "setup-cluster: resolving load balancer IP (node exposing container port ${CONTAINER_PORT})" >&2
LB_NODE="$(docker ps --filter "name=${CLUSTER_NAME}-worker" --format '{{.Names}}\t{{.Ports}}' | grep "$CONTAINER_PORT" | cut -f1 || true)"
if [ -z "$LB_NODE" ]; then
    echo "setup-cluster: no worker container exposing port ${CONTAINER_PORT} found" >&2
    exit 1
fi
LB_IP="$(docker inspect "$LB_NODE" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
echo "setup-cluster: load balancer node '${LB_NODE}' -> ${LB_IP}" >&2

sed "s/__LOAD_BALANCER_IP__/${LB_IP}/" "$HUB_CONFIG_TEMPLATE" > "$HUB_CONFIG_OUT"
echo "setup-cluster: wrote ${HUB_CONFIG_OUT}" >&2
