#!/usr/bin/env bash
# Applies every ytt overlay in ./overlays to the Hub install.
#
# Hub's child PackageInstalls (ensemble-helm, contour, ...) are owned by the
# parent `sm` PackageInstall, which re-renders and overwrites them on every
# reconcile, so annotating a child directly doesn't stick. Instead this script:
#   1. creates a Secret per overlays/*.yaml (custom-<name>-overlay),
#   2. generates one "wiring" overlay Secret that adds the
#      `ext.packaging.carvel.dev/ytt-paths-from-secret-name.N` annotation for
#      each of those onto the child PackageInstall named on the overlay's
#      `#! target-packageinstall: <name>` line,
#   3. attaches the wiring Secret to the `sm` PackageInstall, so the child
#      annotations are re-applied each time sm renders.
#
# Add a new overlay by dropping a file in overlays/; no script changes needed.
#
# run-install.sh rewrites the `sm` PackageInstall and will likely drop the
# annotation on it, so rerun this after every install. Safe to rerun any time.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-tanzusm}"
PARENT_PACKAGEINSTALL="sm"
WIRING_SECRET="custom-overlays-wiring"
ANNOTATION_PREFIX="ext.packaging.carvel.dev/ytt-paths-from-secret-name"
# Installer-managed overlays use low indexes (0-21 at time of writing).
FIRST_CUSTOM_INDEX=100

command -v kubectl >/dev/null 2>&1 || { echo "apply-overlays: 'kubectl' not found on PATH" >&2; exit 1; }

shopt -s nullglob
overlay_files=("$SCRIPT_DIR"/overlays/*.yaml)
[ "${#overlay_files[@]}" -gt 0 ] || { echo "apply-overlays: no overlays found in $SCRIPT_DIR/overlays" >&2; exit 1; }

wiring_overlay='#@ load("@ytt:overlay", "overlay")'
index="$FIRST_CUSTOM_INDEX"

for overlay_file in "${overlay_files[@]}"; do
  overlay_name="$(basename "$overlay_file" .yaml)"
  target="$(sed -n 's/^#! target-packageinstall: *//p' "$overlay_file" | head -1)"
  [ -n "$target" ] || { echo "apply-overlays: $overlay_file has no '#! target-packageinstall: <name>' line" >&2; exit 1; }
  secret_name="custom-${overlay_name}-overlay"

  echo "apply-overlays: $overlay_name -> packageinstall/$target (secret $secret_name, index $index)" >&2
  kubectl -n "$NAMESPACE" create secret generic "$secret_name" \
    --from-file="${overlay_name}.yaml=$overlay_file" --dry-run=client -o yaml \
    | kubectl -n "$NAMESPACE" apply -f - >/dev/null

  wiring_overlay+=$'\n'"#@overlay/match by=overlay.subset({\"kind\": \"PackageInstall\", \"metadata\": {\"name\": \"${target}\"}})"
  wiring_overlay+=$'\n---\nmetadata:\n  #@overlay/match missing_ok=True\n  annotations:\n    #@overlay/match missing_ok=True\n'
  wiring_overlay+="    ${ANNOTATION_PREFIX}.${index}: ${secret_name}"$'\n'
  index=$((index + 1))
done

kubectl -n "$NAMESPACE" create secret generic "$WIRING_SECRET" \
  --from-literal="custom-overlays-wiring.yaml=${wiring_overlay}" --dry-run=client -o yaml \
  | kubectl -n "$NAMESPACE" apply -f - >/dev/null

# The timestamp annotation changes on every run, forcing sm to reconcile even
# when only overlay Secret content changed.
kubectl -n "$NAMESPACE" annotate packageinstall "$PARENT_PACKAGEINSTALL" --overwrite \
  "${ANNOTATION_PREFIX}.${FIRST_CUSTOM_INDEX}=${WIRING_SECRET}" \
  "custom-overlays/applied-at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >/dev/null

echo "apply-overlays: done. Watch with: kubectl -n $NAMESPACE get packageinstall -w" >&2
