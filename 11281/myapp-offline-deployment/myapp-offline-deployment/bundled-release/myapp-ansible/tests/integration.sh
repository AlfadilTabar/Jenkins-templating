#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

temp=$(mktemp -d)
export ANSIBLE_HOME="$temp/ansible-home"
export ANSIBLE_LOCAL_TEMP="$temp/ansible-local"
export ANSIBLE_REMOTE_TEMP="$temp/ansible-remote"
trap 'rm -rf "$temp"' EXIT
config="$temp/config"
fixtures="$temp/bundles"
unpack_fixture="${MYAPP_CONFIG_EXAMPLE:-tests/example}"
cp -R "$unpack_fixture" "$config"
rm -rf "$config/.git"
git -C "$config" init -q
git -C "$config" config user.name Test
git -C "$config" config user.email test@example.invalid
python tests/make_bundles.py "$fixtures"
export MYAPP_SECRET_ORDERS_DB_PASSWORD='demo: tricky = value'
export MYAPP_SECRET_BILLING_DB_PASSWORD='demo password'

for environment in dev test prod dr; do
  cat > "$temp/$environment.yml" <<YAML
all:
  vars:
    ansible_connection: local
    deploy_become: false
    deploy_root: "$temp/targets/$environment"
  children:
    orders_nodes:
      hosts:
        local-app:
    billing_nodes:
      hosts:
        local-app:
YAML
  python - "$config/envs/$environment/deployment.yml" "$fixtures" "$(id -un)" <<'PY'
import hashlib
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text())
for entry in data["components"].values():
    # Allow the test itself to be run as root in a CI container. The hook user
    # is replaced with nobody below when the controller is root.
    entry["service_user"] = sys.argv[3] if sys.argv[3] != "root" else "nobody"
    entry["health_retries"] = 2
    entry["health_delay"] = 0
    archive = Path(sys.argv[2]) / entry["artifact"]["path"]
    entry["artifact"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
path.write_text(yaml.safe_dump(data, sort_keys=False))
PY
done

if [[ "$(id -u)" == 0 ]]; then
  chmod 0755 "$temp"
  # The release parent needs traversal permissions when hooks drop to nobody.
  install -d -m 0755 "$temp/targets"
fi

commit() {
  git -C "$config" add .
  git -C "$config" commit -qm "$1"
}

invoke() {
  local action=$1 environment=$2
  shift 2
  python tools/deploy.py "$action" --environment "$environment" \
    --config-repo "$config" --inventory "$temp/$environment.yml" --bundle-dir "$fixtures" \
    --report "$temp/$environment.json" "$@"
}

commit initial
for environment in dev test prod dr; do
  invoke validate "$environment"
done
invoke deploy dev
invoke deploy test
invoke promote dev --to test --components orders --output "$temp/candidate.yml"
python - "$config/envs/test/deployment.yml" "$temp/candidate.yml" <<'PY'
import sys
from pathlib import Path
import yaml

original, candidate = [yaml.safe_load(Path(path).read_text()) for path in sys.argv[1:]]
assert candidate["components"]["orders"]["artifact"]["component_version"] == "3.8.2"
assert candidate["components"]["orders"]["config"] == original["components"]["orders"]["config"]
assert candidate["components"]["billing"] == original["components"]["billing"]
PY
cp "$temp/candidate.yml" "$config/envs/test/deployment.yml"
commit promoted
invoke deploy test --components orders
invoke status test
invoke deploy test --components orders
python - "$temp/test.json" "$temp/targets/test" <<'PY'
import json
import sys
from pathlib import Path
import javaproperties

row, = json.loads(Path(sys.argv[1]).read_text())["components"]
assert row["running_version"] == "3.8.2"
properties = Path(sys.argv[2]) / "orders/current/config/application.properties"
with properties.open("rb") as file:
    parsed = javaproperties.load(file)
assert "test-db" in parsed["db.url"]
assert parsed["db.password"] == "demo: tricky = value"
assert properties.stat().st_mode & 0o777 == 0o640
PY

invoke promote test --to prod --components orders --output "$temp/prod-candidate.yml"
cp "$temp/prod-candidate.yml" "$config/envs/prod/deployment.yml"
commit promoted-to-prod
invoke deploy prod
invoke promote prod --to dr --components orders --output "$temp/dr-candidate.yml"
cp "$temp/dr-candidate.yml" "$config/envs/dr/deployment.yml"
commit promoted-to-dr
invoke deploy dr

replace_artifact() {
  python - "$config/envs/test/deployment.yml" "$fixtures" "$1" "$2" <<'PY'
import hashlib
import sys
from pathlib import Path
import yaml

path = Path(sys.argv[1])
baseline = yaml.safe_load(path.read_text())
version = sys.argv[3]
name = f"myapp-{version}.tar.gz"
checksum = hashlib.sha256((Path(sys.argv[2]) / name).read_bytes()).hexdigest()
baseline["components"]["orders"]["artifact"].update(
    path=name, sha256=checksum, bundle_version=version, component_version=sys.argv[4]
)
path.write_text(yaml.safe_dump(baseline, sort_keys=False))
PY
  commit "$1"
}

for invalid in broken missing-property; do
  if [[ "$invalid" == broken ]]; then version=3.8.3; else version=3.8.4; fi
  replace_artifact "$invalid" "$version"
  if invoke deploy test --components orders > "$temp/$invalid.log" 2>&1; then
    cat "$temp/$invalid.log"
    printf 'Expected failure for %s\n' "$invalid"
    exit 1
  fi
  if [[ "$invalid" == broken ]]; then
    rg -q 'Rollback completed' "$temp/$invalid.log" 2>/dev/null || grep -q 'Rollback completed' "$temp/$invalid.log"
  else
    rg -q 'Missing required keys' "$temp/$invalid.log" 2>/dev/null || grep -q 'Missing required keys' "$temp/$invalid.log"
  fi
  python - "$temp/test.json" <<'PY'
import json
import sys
from pathlib import Path

row, = json.loads(Path(sys.argv[1]).read_text())["components"]
assert row["state"] == "healthy"
assert row["running_version"] == "3.8.2"
assert not row["matches_baseline"]
PY
done
if invoke promote test --to prod --components orders --output "$temp/blocked.yml"; then
  printf 'Expected promotion to fail when the source differs from its baseline\n'
  exit 1
fi
test ! -e "$temp/blocked.yml"
printf 'Integration checks passed.\n'
