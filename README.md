# ansible-playbook-upgrade

[![CI](https://github.com/lvlie/ansible-playbook-upgrade/actions/workflows/ci.yml/badge.svg)](https://github.com/lvlie/ansible-playbook-upgrade/actions/workflows/ci.yml)
[![End-to-end (Incus)](https://github.com/lvlie/ansible-playbook-upgrade/actions/workflows/e2e.yml/badge.svg)](https://github.com/lvlie/ansible-playbook-upgrade/actions/workflows/e2e.yml)

Upgrade playbooks for the home lab. `play.yml` contains one play per inventory group:

| Group     | What it does                                                      |
|-----------|-------------------------------------------------------------------|
| `ubuntu`  | Upgrades Ubuntu machines (robertdebock roles)                     |
| `pi`      | Upgrades Raspberry Pis                                            |
| `compose` | Updates docker compose projects                                   |
| `incus`   | Upgrades Incus hosts **and all of their Ubuntu containers**       |

## Usage

```sh
make install                       # dev tools, galaxy roles and collections, git hooks
cp inventory.example.ini inventory.ini
$EDITOR inventory.ini
ansible-playbook play.yml          # everything
ansible-playbook play.yml --limit incus
```

## Incus hosts

The `incus_upgrade` role (in `roles/incus_upgrade`) does the following on every
host in the `incus` group:

1. Runs `apt dist-upgrade` on the host, which also upgrades incus and netdata
   when they come from apt repositories. A netdata static install in
   `/opt/netdata` is updated with its own `netdata-updater.sh`.
2. Waits for the Incus API, then discovers every **running** container (all
   projects) through the API and, for each one that runs Ubuntu:
   - `apt-get update` and `apt-get dist-upgrade`
   - finds `compose.y(a)ml` / `docker-compose.y(a)ml` files under `/root`
     (hidden directories are skipped) and runs `docker compose pull` +
     `docker compose up -d --remove-orphans`
   - removes dangling docker images
   - `apt-get autoremove --purge` and `apt-get clean`
3. Cleans up apt on the host and reports (or performs) pending reboots.

Guests are **not** part of the inventory: they are discovered and upgraded
through the Incus REST API on the host's unix socket. Commands run inside the
guests with the bundled `incus_exec` module (`roles/incus_upgrade/library`),
the API equivalent of `incus exec`, so guests need no SSH access and no Python.
Stopped containers, virtual machines and non-Ubuntu containers are left alone.

### Variables

Set them in your inventory (`group_vars/incus.yml`, host vars) or with `-e`.
They are validated against `roles/incus_upgrade/meta/argument_specs.yml`.

| Variable                             | Default                      | Description                                            |
|--------------------------------------|------------------------------|--------------------------------------------------------|
| `incus_upgrade_reboot_host`          | `false`                      | Reboot the host when `/var/run/reboot-required` exists |
| `incus_upgrade_restart_guests`       | `false`                      | Restart containers that need a reboot after upgrading  |
| `incus_upgrade_compose_search_depth` | `3`                          | How deep below `/root` to look for compose files       |
| `incus_upgrade_socket`               | `/var/lib/incus/unix.socket` | Unix socket of the Incus API                           |
| `incus_upgrade_exec_timeout`         | `3600`                       | Seconds to wait for one command inside a guest         |
| `incus_upgrade_api_wait_timeout`     | `300`                        | Seconds to wait for the Incus API after the upgrade    |

### Tags

| Tag            | Part                                   |
|----------------|----------------------------------------|
| `host_upgrade` | apt upgrade of the host                |
| `netdata`      | netdata update and service             |
| `guests`       | everything inside the containers       |
| `cleanup`      | apt autoremove/clean on the host       |
| `reboot`       | reboot check (and reboot) of the host  |

For example, only upgrade the containers:

```sh
ansible-playbook play.yml --limit incus --tags guests
```

## Development

```sh
make install   # once
make lint      # all pre-commit hooks
make test      # unit tests + role tests against a mock Incus API
make syntax    # syntax-check play.yml
```

### Linting (pre-commit)

`.pre-commit-config.yaml` runs, on every commit and in CI:

- ansible-lint (`production` profile, strict) and yamllint
- ruff (lint + format) for the Python module and tests
- shellcheck for the end-to-end scripts
- actionlint, zizmor and JSON-schema checks for the GitHub workflows and dependabot config
- markdownlint, codespell and the standard pre-commit-hooks (whitespace, merge conflicts, private keys, ...)

### Tests

- `tests/unit`: unit tests for the `incus_exec` module against a mock Incus API
  served on a unix socket (`tests/mock_incus.py`), including running the module
  the way Ansible does.
- `tests/integration`: runs the role (`--tags guests`) against the mock API and
  checks which guests are touched, projects, compose handling, restarts,
  argument validation and that a second run is idempotent.
- `tests/e2e`: the real thing. The GitHub runner becomes an Incus host (Incus
  from the Ubuntu archive and from Zabbly), with Ubuntu 22.04/24.04 containers
  (one with docker and a compose project on an outdated image, one in another
  project), an Alpine container and a stopped container. `play.yml` is run
  against it, `verify.sh` checks the result, and a second run must report no
  changes. `setup.sh` and `verify.sh` also work on any disposable Incus host.

### CI

| Workflow  | Jobs                                                                          |
|-----------|-------------------------------------------------------------------------------|
| `ci.yml`  | pre-commit, pytest on Python 3.10–3.14 × ansible-core 2.17–2.21, syntax check |
| `e2e.yml` | real Incus end-to-end test (also weekly, to catch upstream changes)           |

Actions are pinned to commit SHAs and kept up to date by Dependabot, together
with the Python tooling in `requirements-dev.txt`. Update the pre-commit hooks
with `pre-commit autoupdate`.
