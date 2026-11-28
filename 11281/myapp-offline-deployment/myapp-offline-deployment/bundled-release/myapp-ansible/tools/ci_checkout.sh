#!/usr/bin/env bash
set -euo pipefail

[[ "$CONFIG_REF" =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]]
[[ "$CONFIG_PROJECT_PATH" =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$ ]]
python - "$CI_SERVER_URL" "${MYAPP_GITLAB_AUTHORITY:-}" <<'PY'
import sys

sys.path.insert(0, "tools")
from network import approved_https

approved_https(sys.argv[1], sys.argv[2], "GitLab URL")
PY

# Scope credentials and transport policy to this HTTPS clone invocation.
credential=$(printf 'gitlab-ci-token:%s' "$CI_JOB_TOKEN" | base64 -w0)
unset GIT_SSL_NO_VERIFY GIT_CONFIG_PARAMETERS http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY
GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_COUNT=4 \
  GIT_CONFIG_KEY_0="http.${CI_SERVER_URL}/.extraHeader" \
  GIT_CONFIG_VALUE_0="Authorization: Basic $credential" \
  GIT_CONFIG_KEY_1=http.followRedirects GIT_CONFIG_VALUE_1=false \
  GIT_CONFIG_KEY_2=http.proxy GIT_CONFIG_VALUE_2= \
  GIT_CONFIG_KEY_3=http.sslVerify GIT_CONFIG_VALUE_3=true \
  git -c protocol.allow=never -c protocol.https.allow=always \
    clone --no-tags --no-recurse-submodules "${CI_SERVER_URL%/}/${CONFIG_PROJECT_PATH}.git" .config-repo

git -C .config-repo merge-base --is-ancestor "$CONFIG_REF" origin/HEAD
