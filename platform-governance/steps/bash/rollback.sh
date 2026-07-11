#!/usr/bin/env bash
# rollback.sh <env> [deployable-id]
# Resolve the version to roll BACK to from the deployment ledger and print it.
# This is the air-gapped, shell-only counterpart to platformState.previousGood():
# CI uses the Groovy engine; operators can use this at the CLI to see the target
# before triggering the rollback job. It PRINTS the plan; it does not deploy.
set -euo pipefail
ENVIRONMENT="${1:?env required}"
ONLY_ID="${2:-}"
STATE_DIR="${STATE_DIR:-.deploy-state}"

if [[ ! -d "${STATE_DIR}" ]]; then
    echo "No deployment ledger at ${STATE_DIR}; nothing to roll back."
    exit 0
fi

resolve_previous() {
    # args: history file, current version -> prints previous good version
    local hist="$1" cur="$2"
    [[ -f "${hist}" ]] || return 0
    # newest-first, successful events only, first version != current
    tac "${hist}" | while IFS= read -r line; do
        local res ver
        res=$(printf '%s' "${line}" | sed -n 's/.*"result":"\([^"]*\)".*/\1/p')
        ver=$(printf '%s' "${line}" | sed -n 's/.*"version":"\([^"]*\)".*/\1/p')
        [[ "${res}" == "deployed" || "${res}" == "rolled-back" ]] || continue
        [[ "${ver}" != "${cur}" ]] || continue
        printf '%s\n' "${ver}"; break
    done
}

shopt -s nullglob
for cur_file in "${STATE_DIR}/${ENVIRONMENT}-"*.current; do
    id=$(basename "${cur_file}" .current); id="${id#"${ENVIRONMENT}-"}"
    [[ -z "${ONLY_ID}" || "${ONLY_ID}" == "${id}" ]] || continue
    cur=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "${cur_file}" | head -1)
    prev=$(resolve_previous "${STATE_DIR}/${ENVIRONMENT}-${id}.history.jsonl" "${cur}")
    if [[ -n "${prev}" ]]; then
        echo "ROLLBACK PLAN | ${ENVIRONMENT}/${id}: ${cur} -> ${prev}"
    else
        echo "ROLLBACK PLAN | ${ENVIRONMENT}/${id}: ${cur} -> (no previous good version on record)"
    fi
done
