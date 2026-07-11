#!/usr/bin/env bash
# fetch-artifact.sh <artifactId(group:name)> <version> <packaging> <destDir>
# Build-Once-Deploy-Many: pulls the EXACT version from Nexus once, onto the agent.
set -euo pipefail
ARTIFACT="$1"; VERSION="$2"; PACKAGING="${3:-jar}"; DEST="${4:-artifacts/}"
: "${NEXUS_BASE:=https://nexus.internal.example.sa/repository/releases}"
GROUP="${ARTIFACT%%:*}"; NAME="${ARTIFACT##*:}"
GROUP_PATH="${GROUP//.//}"
URL="${NEXUS_BASE}/${GROUP_PATH}/${NAME}/${VERSION}/${NAME}-${VERSION}.${PACKAGING}"
mkdir -p "${DEST}"
echo "GET ${URL}"
curl -fsSL --retry 3 -o "${DEST}/${NAME}-${VERSION}.${PACKAGING}" "${URL}"
# also fetch the published checksum for later verification
curl -fsSL --retry 3 -o "${DEST}/${NAME}-${VERSION}.${PACKAGING}.sha1" "${URL}.sha1" || true
echo "Fetched ${NAME}-${VERSION}.${PACKAGING}"
