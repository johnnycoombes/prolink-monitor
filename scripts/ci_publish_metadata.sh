#!/usr/bin/env bash
# Sign update metadata for a bundle that is already on disk, and upload it
# to the "updates" GitHub release. Used by the tag publish job and by a
# workflow_dispatch re-run that does not rebuild the Windows zip.
#
# Usage: ci_publish_metadata.sh VERSION BUNDLE_DIR
# Reads GH_TOKEN, TUFUP_KEYS_B64, and TUFUP_KEY_PASSWORD from the environment.
set -euo pipefail

ver="${1:?version required}"
bundle="${2:?bundle directory required}"

if [[ ! -d "$bundle" ]]; then
  echo "Bundle folder not found: $bundle" >&2
  exit 1
fi

python -m pip install --upgrade pip "tufup>=0.10,<0.11" packaging
mkdir -p previous-metadata tuf-out
require_flag=()
if gh release view updates >/dev/null 2>&1; then
  echo "Downloading previous update metadata"
  gh release download updates --dir previous-metadata --pattern "*.json" --clobber
  require_flag=(--require-existing)
else
  echo "No updates release yet. The committed root.json will anchor the first metadata."
fi

python scripts/publish_tuf_release.py \
  --bundle "$bundle" \
  --version "$ver" \
  --metadata-dir previous-metadata \
  --out tuf-out \
  "${require_flag[@]}"

if ! compgen -G "tuf-out/*" >/dev/null; then
  echo "No signed metadata was produced. The versioned zip is already on the release."
  exit 0
fi

if ! gh release view updates >/dev/null 2>&1; then
  gh release create updates \
    --latest=false \
    --title "Update metadata (do not install this)" \
    --notes "Signed TUF metadata for the in-app updater. Install Prolink Listener from a versioned release instead (the ProlinkListener-*-win64.zip asset)."
fi
gh release upload updates tuf-out/* --clobber
echo "Signed update metadata for ${ver} is on the updates release."
