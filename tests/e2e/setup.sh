#!/usr/bin/env bash
# Create the guests the end-to-end test upgrades. Run as root on a host with
# an initialised Incus. Safe to run on a disposable machine only.
set -euo pipefail

IMAGE_REGISTRY="public.ecr.aws/docker/library"

wait_for_network() {
  local name=$1
  shift
  echo "Waiting for network in ${name}"
  # shellcheck disable=SC2016 # expanded inside the guest
  incus exec "$@" "${name}" -- sh -c \
    'for i in $(seq 90); do getent hosts archive.ubuntu.com >/dev/null 2>&1 && exit 0; sleep 2; done; exit 1'
}

# Ubuntu guest with docker and compose projects.
incus launch images:ubuntu/24.04 docker-host \
  -c security.nesting=true \
  -c security.syscalls.intercept.mknod=true \
  -c security.syscalls.intercept.setxattr=true
# Plain Ubuntu guests, one of them in another project.
incus launch images:ubuntu/22.04 jammy
incus project create lab -c features.images=false -c features.profiles=false
incus launch images:ubuntu/24.04 noble --project lab
# Guests that must be left alone.
incus launch images:alpine/edge alpine
incus init images:ubuntu/24.04 stopped

wait_for_network docker-host
wait_for_network jammy
wait_for_network noble --project lab
wait_for_network alpine

incus exec docker-host --env DEBIAN_FRONTEND=noninteractive -- sh -c \
  'apt-get update -q && apt-get install -y -q docker.io docker-compose-v2'

# A compose project running an "old" image; the compose file is then bumped to
# a new tag, so the play has to pull it and recreate the container.
incus exec docker-host -- mkdir -p /root/app /root/.hidden
incus exec docker-host -- sh -c "cat > /root/app/compose.yaml" <<YAML
services:
  sleeper:
    image: ${IMAGE_REGISTRY}/busybox:1.36
    command: [sleep, infinity]
    restart: unless-stopped
YAML
incus exec docker-host -- docker compose -f /root/app/compose.yaml up -d
incus exec docker-host -- sed -i 's/busybox:1.36/busybox:1.37/' /root/app/compose.yaml

# Compose files in hidden directories must be ignored; this one would fail.
incus exec docker-host -- sh -c "cat > /root/.hidden/compose.yaml" <<YAML
services:
  broken:
    image: example.invalid/must-not-be-pulled
YAML

# Leave a dangling image behind for the play to prune.
incus exec docker-host -- sh -c "
  docker pull -q ${IMAGE_REGISTRY}/alpine:3.19 &&
  docker tag ${IMAGE_REGISTRY}/alpine:3.19 dangling:latest &&
  docker rmi ${IMAGE_REGISTRY}/alpine:3.19 &&
  docker pull -q ${IMAGE_REGISTRY}/alpine:3.20 &&
  docker tag ${IMAGE_REGISTRY}/alpine:3.20 dangling:latest"
test -n "$(incus exec docker-host -- docker images -q -f dangling=true)"

# Pretend jammy needs a reboot so the restart path is exercised.
incus exec jammy -- touch /var/run/reboot-required

incus list --all-projects
