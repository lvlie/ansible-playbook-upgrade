"""Run the incus_upgrade role against the mock Incus API.

Only the `guests` tag is run, so nothing touches the machine running the
tests (no apt, no root needed).
"""

import os
import re
import subprocess

import pytest
from conftest import ROOT
from mock_incus import MockIncus

PLAYBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "playbook.yml")

INSTANCES = [
    {"name": "web", "project": "default", "type": "container", "status": "Running"},
    {"name": "noble", "project": "lab", "type": "container", "status": "Running"},
    {"name": "alpine", "project": "default", "type": "container", "status": "Running"},
    {"name": "stopped", "project": "default", "type": "container", "status": "Stopped"},
    {"name": "vm", "project": "default", "type": "virtual-machine", "status": "Running"},
]

COMPOSE_FILES = {"web": ["/root/app/compose.yaml", "/root/media/docker-compose.yml"]}


class FakeGuests:
    """Stateful fake guests: the first upgrade changes things, later runs do not."""

    def __init__(self):
        self.upgraded = set()
        self.recreated = set()
        self.pruned = set()
        self.restarted = set()

    def on_state_change(self, name, project, body):
        if body.get("action") == "restart":
            self.restarted.add(name)

    def __call__(self, name, project, command, environment, cwd):
        script = " ".join(command)
        if "os-release" in script:
            return 0, ("alpine" if name == "alpine" else "ubuntu") + "\n", ""
        if "dist-upgrade" in script:
            assert environment.get("DEBIAN_FRONTEND") == "noninteractive"
            if name in self.upgraded:
                return 0, "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n", ""
            self.upgraded.add(name)
            return 0, "5 upgraded, 1 newly installed, 0 to remove and 0 not upgraded.\n", ""
        if "command -v docker" in script:
            return 0, "".join(f + "\n" for f in COMPOSE_FILES.get(name, [])), ""
        if "docker compose" in script:
            key = (name, command[-1])
            if key in self.recreated:
                return 0, "", " Container x  Running\n"
            self.recreated.add(key)
            return 0, "", " Container x  Recreated\n Container x  Started\n"
        if command[:3] == ["docker", "image", "prune"]:
            if name in self.pruned:
                return 0, "Total reclaimed space: 0B\n", ""
            self.pruned.add(name)
            return 0, "Deleted Images:\nTotal reclaimed space: 42MB\n", ""
        if "autoremove" in script:
            return 0, "0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.\n", ""
        if command[0] == "test":
            # Every upgraded guest needs a restart until it has been restarted.
            needs = name in self.upgraded and name not in self.restarted
            return (0 if needs else 1), "", ""
        return 127, "", f"unexpected command: {script}\n"


@pytest.fixture
def incus():
    guests = FakeGuests()
    with MockIncus(INSTANCES, guests, state_handler=guests.on_state_change) as mock:
        yield mock


def run_playbook(incus, *extra):
    env = {
        **os.environ,
        "ANSIBLE_ROLES_PATH": os.path.join(ROOT, "roles"),
        "ANSIBLE_FORCE_COLOR": "0",
        "ANSIBLE_NOCOLOR": "1",
        "ANSIBLE_RETRY_FILES_ENABLED": "0",
    }
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            PLAYBOOK,
            "--tags",
            "guests",
            "-e",
            "incus_upgrade_socket=" + incus.socket_path,
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def recap(output):
    match = re.search(r"localhost\s+:\s+ok=(\d+)\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)", output)
    assert match, output
    return dict(zip(["ok", "changed", "unreachable", "failed"], map(int, match.groups()), strict=True))


def execs_for(incus, name):
    return [e["command"] for e in incus.execs if e["name"] == name]


def test_only_running_containers_are_touched(incus):
    run_playbook(incus)
    touched = {e["name"] for e in incus.execs}
    assert touched == {"web", "noble", "alpine"}


def test_non_ubuntu_guest_is_only_inspected(incus):
    run_playbook(incus)
    assert len(execs_for(incus, "alpine")) == 1
    assert "os-release" in " ".join(execs_for(incus, "alpine")[0])


def test_projects_are_respected(incus):
    run_playbook(incus)
    assert {e["project"] for e in incus.execs if e["name"] == "noble"} == {"lab"}


def test_ubuntu_guest_is_upgraded_and_cleaned(incus):
    run_playbook(incus)
    scripts = [" ".join(c) for c in execs_for(incus, "web")]
    assert any("apt-get update" in s and "dist-upgrade" in s for s in scripts)
    assert any("autoremove --purge" in s and "apt-get -q clean" in s for s in scripts)
    assert ["docker", "image", "prune", "--force"] in execs_for(incus, "web")


def test_every_compose_file_is_pulled_and_recreated(incus):
    run_playbook(incus)
    compose_runs = [c for c in execs_for(incus, "web") if "docker compose" in " ".join(c)]
    assert [c[-1] for c in compose_runs] == COMPOSE_FILES["web"]
    for c in compose_runs:
        assert "pull" in c[2]
        assert "up -d --remove-orphans" in c[2]


def test_docker_prune_skipped_without_compose_files(incus):
    run_playbook(incus)
    assert ["docker", "image", "prune", "--force"] not in execs_for(incus, "noble")


def test_guests_not_restarted_by_default(incus):
    output = run_playbook(incus)
    assert incus.state_changes == []
    assert "web requires a restart" in output


def test_guests_restarted_when_enabled(incus):
    run_playbook(incus, "-e", "incus_upgrade_restart_guests=true")
    assert sorted(c["name"] for c in incus.state_changes) == ["noble", "web"]
    assert all(c["action"] == "restart" for c in incus.state_changes)


def test_first_run_reports_changes(incus):
    assert recap(run_playbook(incus))["changed"] > 0


def test_second_run_is_idempotent(incus):
    run_playbook(incus, "-e", "incus_upgrade_restart_guests=true")
    output = run_playbook(incus, "-e", "incus_upgrade_restart_guests=true")
    result = recap(output)
    assert result["changed"] == 0, output
    assert result["failed"] == 0


def test_invalid_argument_is_rejected(incus):
    proc = subprocess.run(
        [
            "ansible-playbook",
            "-i",
            "localhost,",
            PLAYBOOK,
            "--tags",
            "guests",
            "-e",
            "incus_upgrade_compose_search_depth=deep",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "ANSIBLE_ROLES_PATH": os.path.join(ROOT, "roles"), "ANSIBLE_NOCOLOR": "1"},
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode != 0
    assert "incus_upgrade_compose_search_depth" in proc.stdout
