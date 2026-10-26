#!/usr/bin/env bash
# Removes the pf redirect installed by enable-port-443-redirect.sh.
set -euo pipefail

ANCHOR_NAME="com.apple/hub-cluster-443-redirect"
ANCHOR_FILE="/etc/pf.anchors/hub-cluster-443-redirect"

sudo pfctl -a "$ANCHOR_NAME" -F all 2>/dev/null || true
sudo pfctl -a "hub-cluster-443-redirect" -F all 2>/dev/null || true  # clean up older unwired anchor name
sudo rm -f "$ANCHOR_FILE"

echo "disable-port-443-redirect: removed" >&2
