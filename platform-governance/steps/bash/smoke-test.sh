#!/usr/bin/env bash
# smoke-test.sh <env> - post-deploy health probe. Wire to your real endpoints.
set -euo pipefail
ENVIRONMENT="${1:?env required}"
echo "Smoke testing environment: ${ENVIRONMENT}"
# Example: curl the health endpoint of each server group behind the LB.
# curl -fsS "https://${ENVIRONMENT}.payments.internal/health" >/dev/null
echo "Smoke test placeholder passed for ${ENVIRONMENT}"
