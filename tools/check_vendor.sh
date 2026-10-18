#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
find_first() {
  local label="$1"; shift
  local candidate
  for candidate in "$@"; do
    if [[ -f "$ROOT/$candidate" ]]; then
      printf 'OK      %-26s %s\n' "$label" "$candidate"
      return 0
    fi
  done
  printf 'MISSING %-26s tried: %s\n' "$label" "$*"
  return 1
}
missing=0
find_first 'Three.js ESM' \
  vendor/three/three.module.js vendor/three/build/three.module.js vendor/three/build/three.module.min.js || missing=1
find_first 'GLTFLoader.js' \
  vendor/three/addons/loaders/GLTFLoader.js vendor/three/examples/jsm/loaders/GLTFLoader.js || missing=1
find_first 'three-vrm ESM' \
  vendor/three-vrm/lib/three-vrm.module.min.js vendor/three-vrm/lib/three-vrm.module.js \
  vendor/three-vrm/dist/three-vrm.module.min.js vendor/three-vrm/dist/three-vrm.module.js \
  vendor/three-vrm/three-vrm.module.min.js vendor/three-vrm/three-vrm.module.js || missing=1
find_first 'three-vrm-animation ESM' \
  vendor/three-vrm-animation/lib/three-vrm-animation.module.min.js \
  vendor/three-vrm-animation/lib/three-vrm-animation.module.js \
  vendor/three-vrm-animation/dist/three-vrm-animation.module.min.js \
  vendor/three-vrm-animation/dist/three-vrm-animation.module.js \
  vendor/three-vrm-animation/three-vrm-animation.module.min.js \
  vendor/three-vrm-animation/three-vrm-animation.module.js || missing=1
if (( missing )); then
  echo
  echo 'Some modules were not found. Locate the copies with: find vendor -type f | sort'
  exit 1
fi
printf '\nAll required local module entry points were found.\n'
