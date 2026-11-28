#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT
export ANSIBLE_HOME="$temp/ansible-home"
export ANSIBLE_LOCAL_TEMP="$temp/ansible-local"
export ANSIBLE_REMOTE_TEMP="$temp/ansible-remote"
export MYAPP_SECRET_PAYMENT_DB_PASSWORD='demo: tricky = value'
export MYAPP_SECRET_ORDERS_DB_PASSWORD='demo orders'
export MYAPP_SECRET_REPORTING_DB_PASSWORD='demo reporting'
config="$temp/config"
packages="$temp/packages"
cp -R "${MYAPP_CONFIG_EXAMPLE:-tests/example}" "$config"
rm -rf "$config/.git"
git -C "$config" init -q
git -C "$config" config user.name Test
git -C "$config" config user.email test@example.invalid
python tests/make_packages.py "$packages"
if [[ "$(id -u)" == 0 ]]; then
  chmod 0755 "$temp"
fi

python - "$temp" "$(id -un)" <<'PY'
import hashlib
import sys
from pathlib import Path

import yaml

temp = Path(sys.argv[1])
account = "nobody" if sys.argv[2] == "root" else sys.argv[2]
for environment in ("dev", "test", "prod", "dr"):
    config = temp / "config/envs" / environment / "deployment.yml"
    data = yaml.safe_load(config.read_text())
    first, second = f"{environment}-app-01", f"{environment}-app-02"
    data["deployment"]["host_waves"] = [[first, second]] if environment == "dr" else [[first], [second]]
    data["components"]["orders"]["host_waves"] = [[second], [first]]
    aliases = {}
    for host in (first, second):
        target = temp / "targets" / environment / host
        target.mkdir(parents=True)
        aliases[host] = {"deploy_root": str(target)}
    inventory = {"all": {
        "vars": {"ansible_connection": "local", "deploy_become": False},
        "children": {
            "payment_nodes": {"hosts": aliases},
            "orders_nodes": {"hosts": {first: {}, second: {}}},
            "reporting_nodes": {"hosts": {second: {}}},
        },
    }}
    (temp / f"{environment}.yml").write_text(yaml.safe_dump(inventory))
    for component in data["components"].values():
        component.update(service_user=account, health_retries=2, health_delay=0)
        artifact = component["artifact"]
        artifact["sha256"] = hashlib.sha256((temp / "packages" / artifact["path"]).read_bytes()).hexdigest()
    config.write_text(yaml.safe_dump(data, sort_keys=False))
PY

commit() {
  git -C "$config" add .
  git -C "$config" commit -qm "$1"
}

invoke() {
  local action=$1 environment=$2
  shift 2
  python tools/deploy.py "$action" --environment "$environment" \
    --config-repo "$config" --inventory "$temp/$environment.yml" --package-dir "$packages" \
    --report "$temp/$environment.json" "$@"
}

commit initial
rollback_ref=$(git -C "$config" rev-parse HEAD)
for environment in dev test prod dr; do
  invoke validate "$environment"
done
invoke plan dev > "$temp/selective.json"
invoke plan dev --mode full > "$temp/full.json"
python - "$temp/selective.json" "$temp/full.json" <<'PY'
import json
import sys
from pathlib import Path

selective, full = [json.loads(Path(file).read_text()) for file in sys.argv[1:]]
assert list(selective["components"]) == ["payment", "orders"]
assert list(full["components"]) == ["payment", "orders", "reporting"]
assert full["components"]["orders"]["host_waves"] == [["dev-app-02"], ["dev-app-01"]]
assert full["components"]["reporting"]["host_waves"] == [[], ["dev-app-02"]]
PY

invoke deploy dev | tee "$temp/dev.log"
test ! -e "$temp/targets/dev/dev-app-02/reporting/current"
grep -F 'Stage core-services, host wave 1: payment: dev-app-01 | orders: dev-app-02' "$temp/dev.log"
grep -F 'Stage core-services, host wave 2: payment: dev-app-02 | orders: dev-app-01' "$temp/dev.log"
invoke status dev --mode selective
invoke promote dev --to test --output "$temp/test-candidate.yml"
python - "$config/envs/test/deployment.yml" "$temp/test-candidate.yml" <<'PY'
import sys
from pathlib import Path
import yaml

original, candidate = [yaml.safe_load(Path(path).read_text()) for path in sys.argv[1:]]
assert candidate["deployment"] == original["deployment"]
assert candidate["components"]["payment"]["artifact"]["version"] == "10.1"
assert candidate["components"]["orders"]["artifact"]["version"] == "11.2"
assert candidate["components"]["orders"]["config"] == original["components"]["orders"]["config"]
assert candidate["components"]["orders"]["host_waves"] == original["components"]["orders"]["host_waves"]
assert candidate["components"]["reporting"] == original["components"]["reporting"]
PY
cp "$temp/test-candidate.yml" "$config/envs/test/deployment.yml"
commit promoted-to-test
invoke deploy test --mode full
invoke status test
python - "$temp/test.json" "$temp/targets/test" <<'PY'
import json
import sys
from pathlib import Path
import javaproperties

rows = json.loads(Path(sys.argv[1]).read_text())["components"]
assert len(rows) == 5
assert {row["running_version"] for row in rows} == {"10.1", "11.2", "4.0"}
properties = Path(sys.argv[2]) / "test-app-01/payment/current/config/application.properties"
with properties.open("rb") as file:
    parsed = javaproperties.load(file)
assert "test-db" in parsed["db.url"]
assert parsed["db.password"] == "demo: tricky = value"
assert properties.stat().st_mode & 0o777 == 0o640
PY

invoke promote test --to prod --output "$temp/prod-candidate.yml"
cp "$temp/prod-candidate.yml" "$config/envs/prod/deployment.yml"
commit promoted-to-prod
invoke deploy prod
invoke promote prod --to dr --output "$temp/dr-candidate.yml"
cp "$temp/dr-candidate.yml" "$config/envs/dr/deployment.yml"
commit promoted-to-dr
invoke deploy dr --mode full | tee "$temp/dr.log"
grep -F 'Stage core-services, host wave 1: payment: dr-app-01, dr-app-02 | orders: dr-app-02' "$temp/dr.log"

replace_artifact() {
  python - "$config/envs/test/deployment.yml" "$packages" "$1" <<'PY'
import hashlib
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text())
version = sys.argv[3]
package = f"payment/{version}/payment-{version}.tar.gz"
checksum = hashlib.sha256((Path(sys.argv[2]) / package).read_bytes()).hexdigest()
data["deployment"]["selective"]["components"] = ["payment"]
data["components"]["payment"]["artifact"].update(path=package, sha256=checksum, version=version)
path.write_text(yaml.safe_dump(data, sort_keys=False))
PY
  commit "$1"
}

for version in 10.2 10.3; do
  replace_artifact "$version"
  if invoke deploy test > "$temp/$version.log" 2>&1; then
    cat "$temp/$version.log"
    printf 'Expected failure for %s\n' "$version"
    exit 1
  fi
  if [[ "$version" == 10.2 ]]; then
    grep -q 'Rollback completed' "$temp/$version.log"
    if grep -q 'Stage core-services, host wave 2' "$temp/$version.log"; then
      printf 'A later wave ran after rollback\n'
      exit 1
    fi
  else
    grep -q 'Missing required keys' "$temp/$version.log"
  fi
  python - "$temp/test.json" <<'PY'
import json
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())["components"]
assert len(rows) == 2
assert all(row["state"] == "healthy" and row["running_version"] == "10.1" for row in rows)
assert all(not row["matches_baseline"] for row in rows)
PY
done
if invoke promote test --to prod --output "$temp/blocked.yml"; then
  printf 'Expected promotion to fail on source drift\n'
  exit 1
fi
test ! -e "$temp/blocked.yml"

# Use the same historical ref and explicit selection passed by the CI rollback job.
invoke deploy test --config-ref "$rollback_ref" --mode selective --components payment
python - "$temp/test.json" "$temp/targets/test" "$rollback_ref" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
targets = Path(sys.argv[2])
assert report["baseline_commit"] == sys.argv[3]
assert len(report["components"]) == 2
assert all(
    row["component"] == "payment" and row["running_version"] == "10.0"
    and row["state"] == "healthy" and row["matches_baseline"]
    for row in report["components"]
)
assert (targets / "test-app-01/orders/shared/running-version").read_text().strip() == "11.2"
assert (targets / "test-app-02/reporting/shared/running-version").read_text().strip() == "4.0"
PY

invoke deploy test --config-ref "$rollback_ref" --mode full
python - "$temp/test.json" <<'PY'
import json
import sys
from pathlib import Path

rows = json.loads(Path(sys.argv[1]).read_text())["components"]
assert len(rows) == 5
assert {row["component"]: row["running_version"] for row in rows} == {
    "payment": "10.0", "orders": "11.1", "reporting": "4.0",
}
assert all(row["state"] == "healthy" and row["matches_baseline"] for row in rows)
PY
printf 'Integration checks passed.\n'
