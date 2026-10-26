#!/usr/bin/env bash
set -euo pipefail

MACHINE_NAME="${PODMAN_MACHINE_NAME:-podman-machine-default}"
MACHINE_CPUS="${PODMAN_MACHINE_CPUS:-12}"
MACHINE_MEMORY_MIB="${PODMAN_MACHINE_MEMORY_MIB:-45776}"
MACHINE_DISK_GIB="${PODMAN_MACHINE_DISK_GIB:-93}"

command -v podman >/dev/null 2>&1 || { echo "setup-podman-machine: 'podman' not found on PATH" >&2; exit 1; }

# Rosetta is read from the host's containers.conf at `podman machine init` time.
HOST_CONTAINERS_CONF="$HOME/.config/containers/containers.conf"
if ! grep -qE '^\s*rosetta\s*=\s*true' "$HOST_CONTAINERS_CONF" 2>/dev/null; then
    echo "setup-podman-machine: rosetta is not enabled in ${HOST_CONTAINERS_CONF}" >&2
    echo "  add:  [machine]" >&2
    echo "        rosetta = true" >&2
    exit 1
fi

if podman machine inspect "$MACHINE_NAME" >/dev/null 2>&1; then
    echo "setup-podman-machine: machine '${MACHINE_NAME}' already exists, skipping creation" >&2
    echo "  (cpus/memory/disk/rootful/rosetta are fixed at init; run 'podman machine rm ${MACHINE_NAME}' to recreate)" >&2
else
    echo "setup-podman-machine: creating machine '${MACHINE_NAME}'" >&2
    podman machine init "$MACHINE_NAME" \
        --cpus "$MACHINE_CPUS" \
        --memory "$MACHINE_MEMORY_MIB" \
        --disk-size "$MACHINE_DISK_GIB" \
        --rootful
fi

if [ "$(podman machine inspect "$MACHINE_NAME" --format '{{.State}}')" != "running" ]; then
    echo "setup-podman-machine: starting machine '${MACHINE_NAME}'" >&2
    podman machine start "$MACHINE_NAME"
fi

# `podman machine init` writes pids_limit=0 only for the rootless `core` user.
# This machine is rootful, so kind's node containers run as root and fall back
# to the 2048 default. systemd inside each node then caps every container at
# 15% of that (307 tasks), which starves the Hub JVMs of threads. Set it for
# root so new containers (the kind nodes) are created with no PID limit.
echo "setup-podman-machine: setting pids_limit=0 for root in the VM" >&2
podman machine ssh "$MACHINE_NAME" \
    "sudo mkdir -p /root/.config/containers && printf '[containers]\npids_limit=0\n' | sudo tee /root/.config/containers/containers.conf >/dev/null"
# The running API service caches containers.conf, so restart it to pick this up.
podman machine ssh "$MACHINE_NAME" "sudo systemctl restart podman.socket podman.service"

if podman ps -a --format '{{.Names}}' | grep -q -- '-control-plane$'; then
    echo "setup-podman-machine: existing kind nodes keep their old PID limit; recreate the cluster" >&2
    echo "  (teardown-cluster.sh, setup-cluster.sh) or fix in place, see README" >&2
fi

echo "setup-podman-machine: done" >&2
