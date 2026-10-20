#!/usr/bin/env bash
# Check the state after the play ran. Usage: verify.sh <ansible output log>
set -euo pipefail

LOG=$1
failures=0

check() {
  local description=$1
  shift
  if "$@"; then
    echo "ok   - ${description}"
  else
    echo "FAIL - ${description}"
    failures=$((failures + 1))
  fi
}

not() {
  ! "$@"
}

guest() {
  local name=$1
  shift
  if [[ ${name} == */* ]]; then
    incus exec --project "${name%%/*}" "${name#*/}" -- "$@"
  else
    incus exec "${name}" -- "$@"
  fi
}

fully_upgraded() {
  guest "$1" sh -c 'apt-get -s dist-upgrade | grep -q "^0 upgraded, 0 newly installed"'
}

apt_cache_clean() {
  ! guest "$1" sh -c 'ls /var/cache/apt/archives/*.deb' >/dev/null 2>&1
}

task_skipped() {
  grep -A1 "TASK \[incus_upgrade : $1\]" "${LOG}" | grep -q '^skipping:'
}

status_of() {
  incus list "^$1\$" -c s -f csv
}

check "netdata is running on the host" systemctl is-active --quiet netdata

for g in docker-host jammy lab/noble; do
  check "${g} is fully upgraded" fully_upgraded "${g}"
  check "${g} apt cache is clean" apt_cache_clean "${g}"
done

check "compose project was recreated on the new image" \
  test "$(guest docker-host docker inspect --format '{{.Config.Image}}' app-sleeper-1)" \
  = "public.ecr.aws/docker/library/busybox:1.37"
check "compose container is running" \
  test "$(guest docker-host docker inspect --format '{{.State.Status}}' app-sleeper-1)" = "running"
check "dangling docker images were removed" \
  test -z "$(guest docker-host docker images -q -f dangling=true)"
check "compose files in hidden directories were ignored" not grep -q "/root/.hidden" "${LOG}"

check "jammy was restarted (reboot-required cleared)" not guest jammy test -f /var/run/reboot-required
check "jammy is running again" test "$(status_of jammy)" = "RUNNING"

check "alpine was not upgraded" task_skipped "Upgrade apt packages in alpine"
check "stopped container is still stopped" test "$(status_of stopped)" = "STOPPED"
check "stopped container was not touched" not grep -q "default/stopped" "${LOG}"

if ((failures > 0)); then
  echo "${failures} check(s) failed"
  exit 1
fi
echo "All checks passed"
