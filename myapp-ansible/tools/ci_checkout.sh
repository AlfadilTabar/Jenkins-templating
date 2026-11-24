#!/usr/bin/env bash
set -euo pipefail

[[ "$CONFIG_REF" =~ ^([a-f0-9]{40}|[a-f0-9]{64})$ ]]
[[ "$CONFIG_PROJECT_PATH" =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$ ]]
[[ "$CI_SERVER_URL" == https://* ]]

# Scope the job token to this GitLab server and this clone invocation.
credential=$(printf 'gitlab-ci-token:%s' "$CI_JOB_TOKEN" | base64 -w0)
GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0="http.${CI_SERVER_URL}/.extraHeader" \
  GIT_CONFIG_VALUE_0="Authorization: Basic $credential" \
  git clone --no-tags "${CI_SERVER_URL}/${CONFIG_PROJECT_PATH}.git" .config-repo

git -C .config-repo merge-base --is-ancestor "$CONFIG_REF" origin/HEAD
