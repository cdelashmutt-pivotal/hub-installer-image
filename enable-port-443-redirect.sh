#!/usr/bin/env bash
# Redirects macOS host port 443 -> 8443 via pf, since podman's host-side
# port-forwarder (gvproxy/vfkit) runs as your user and can't bind <1024
# directly (see README "Known issue: binding host port 443"). This lets
# kind keep its hostPort at 8443 (unprivileged) while the browser still
# reaches Hub at https://localhost with no port suffix.
#
# Not persistent across reboots. Rerun after every reboot, before
# setup-cluster.sh, if you want plain https://localhost to work.
set -euo pipefail

# Loaded under "com.apple/" because /etc/pf.conf only auto-evaluates
# anchors at that wildcard point (rdr-anchor "com.apple/*") — it's how
# Internet Sharing/VPN tools inject rules at runtime without editing
# /etc/pf.conf. An anchor loaded under any other name is never consulted.
ANCHOR_NAME="com.apple/hub-cluster-443-redirect"
ANCHOR_FILE="/etc/pf.anchors/hub-cluster-443-redirect"

sudo tee "$ANCHOR_FILE" >/dev/null <<'EOF'
rdr pass on lo0 inet proto tcp from any to 127.0.0.1 port 443 -> 127.0.0.1 port 8443
EOF

sudo pfctl -a "$ANCHOR_NAME" -f "$ANCHOR_FILE"
sudo pfctl -e 2>/dev/null || true

echo "enable-port-443-redirect: active — https://localhost:443 now forwards to 127.0.0.1:8443" >&2
echo "enable-port-443-redirect: run ./disable-port-443-redirect.sh to remove" >&2
