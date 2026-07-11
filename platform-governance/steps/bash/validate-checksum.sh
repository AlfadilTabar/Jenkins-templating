#!/usr/bin/env bash
# validate-checksum.sh <artifactsDir>  - verify each artifact against its .sha1
set -euo pipefail
DIR="${1:-artifacts/}"
shopt -s nullglob
fail=0
for f in "${DIR}"/*.jar "${DIR}"/*.war "${DIR}"/*.ear "${DIR}"/*.whl "${DIR}"/*.dll "${DIR}"/*.exe; do
    sha="${f}.sha1"
    [[ -f "${sha}" ]] || { echo "WARN no checksum for $(basename "$f")"; continue; }
    calc="$(sha1sum "$f" | awk '{print $1}')"
    want="$(tr -d '[:space:]' < "$sha")"
    if [[ "$calc" != "$want" ]]; then echo "CHECKSUM MISMATCH: $(basename "$f")"; fail=1
    else echo "ok $(basename "$f")"; fi
done
exit $fail
