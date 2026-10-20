# ansible-playbook-upgrade

Upgrade playbooks for the home lab.

## Incus hosts

The `Upgrade Incus hosts and their Ubuntu containers` play targets the `incus`
inventory group and:

1. Runs `apt dist-upgrade` on the host, which also upgrades incus and netdata
   when they come from apt repositories. A netdata static install in
   `/opt/netdata` is updated with its own `netdata-updater.sh`.
2. Waits for the Incus API, then discovers every **running** incus container
   (all projects) through the API and, for each one that runs Ubuntu:
   - `apt-get update` and `apt-get dist-upgrade`
   - finds `compose.y(a)ml` / `docker-compose.y(a)ml` files under `/root` and
     runs `docker compose pull` + `docker compose up -d --remove-orphans`
   - removes dangling docker images
   - `apt-get autoremove --purge` and `apt-get clean`
3. Cleans up apt on the host.

Guests are **not** part of the inventory: they are discovered and upgraded
through the Incus REST API on the host's unix socket
(`/var/lib/incus/unix.socket`). Commands run inside the guests with the bundled
`library/incus_exec.py` module, the API equivalent of `incus exec`, so guests
need no SSH access and no Python.

Variables (set with `-e` or in inventory):

| Variable                       | Default | Description                                            |
|--------------------------------|---------|--------------------------------------------------------|
| `upgrade_reboot_host`          | `false` | Reboot the host when `/var/run/reboot-required` exists |
| `upgrade_restart_guests`       | `false` | Restart containers that need a reboot after upgrading  |
| `upgrade_compose_search_depth` | `3`     | How deep below `/root` to look for compose files       |
| `incus_socket`                 | `/var/lib/incus/unix.socket` | Unix socket of the Incus API      |

Run only this part with:

```sh
ansible-playbook -i inventory play.yml --limit incus
```
