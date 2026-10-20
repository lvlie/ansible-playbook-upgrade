import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from conftest import MODULE_PATH
from mock_incus import MockIncus

INSTANCES = [
    {"name": "web", "project": "default", "type": "container", "status": "Running"},
    {"name": "db 1", "project": "lab", "type": "container", "status": "Running"},
]


def echo_handler(name, project, command, environment, cwd):
    if command[0] == "fail":
        return 3, "partial\n", "boom\n"
    out = json.dumps({"name": name, "project": project, "command": command, "env": environment, "cwd": cwd})
    return 0, out, "err-" + name


def params(socket_path, **overrides):
    p = {
        "name": "web",
        "project": "default",
        "command": ["echo", "hi"],
        "environment": {},
        "cwd": None,
        "socket": socket_path,
        "timeout": 30,
    }
    p.update(overrides)
    return SimpleNamespace(params=p)


@pytest.fixture
def incus():
    with MockIncus(INSTANCES, echo_handler) as mock:
        yield mock


def test_run_returns_output_and_exit_code(incus_exec, incus):
    rc, out, err = incus_exec.run(params(incus.socket_path))
    assert rc == 0
    assert json.loads(out)["command"] == ["echo", "hi"]
    assert err == "err-web"


def test_request_body_mirrors_incus_exec(incus_exec, incus):
    incus_exec.run(params(incus.socket_path, environment={"A": "1"}, cwd="/root"))
    sent = incus.execs[0]
    assert sent["command"] == ["echo", "hi"]
    assert sent["environment"] == {"A": "1"}
    assert sent["cwd"] == "/root"
    assert sent["record-output"] is True
    assert sent["wait-for-websocket"] is False
    assert sent["interactive"] is False


def test_cwd_omitted_when_not_set(incus_exec, incus):
    incus_exec.run(params(incus.socket_path))
    assert "cwd" not in incus.execs[0]


def test_project_and_url_encoded_name(incus_exec, incus):
    rc, out, _ = incus_exec.run(params(incus.socket_path, name="db 1", project="lab"))
    assert rc == 0
    result = json.loads(out)
    assert result["name"] == "db 1"
    assert result["project"] == "lab"


def test_exec_logs_are_removed(incus_exec, incus):
    incus_exec.run(params(incus.socket_path))
    assert len(incus.deleted_logs) == 2
    assert incus.logs == {}


def test_non_zero_exit_code_is_returned(incus_exec, incus):
    rc, out, err = incus_exec.run(params(incus.socket_path, command=["fail"]))
    assert (rc, out, err) == (3, "partial\n", "boom\n")


def test_unknown_instance_raises(incus_exec, incus):
    with pytest.raises(incus_exec.IncusError, match="Instance not found"):
        incus_exec.run(params(incus.socket_path, name="missing"))


def test_wrong_project_raises(incus_exec, incus):
    with pytest.raises(incus_exec.IncusError):
        incus_exec.run(params(incus.socket_path, project="lab"))


def test_operation_still_running_raises(incus_exec, incus):
    incus.operation_override = {"status": "Running", "status_code": 103, "metadata": {}}
    with pytest.raises(incus_exec.IncusError, match="did not finish"):
        incus_exec.run(params(incus.socket_path))


def test_failed_operation_raises(incus_exec, incus):
    incus.operation_override = {"status": "Failure", "status_code": 400, "err": "container gone", "metadata": {}}
    with pytest.raises(incus_exec.IncusError, match="container gone"):
        incus_exec.run(params(incus.socket_path))


def test_missing_socket_raises_oserror(incus_exec, tmp_path):
    with pytest.raises(FileNotFoundError):
        incus_exec.run(params(str(tmp_path / "nope.sock")))


def run_module(tmp_path, args):
    """Run the module like Ansible does, returning its JSON result."""
    args_file = tmp_path / "args.json"
    args_file.write_text(json.dumps({"ANSIBLE_MODULE_ARGS": args}))
    proc = subprocess.run(
        [sys.executable, MODULE_PATH, str(args_file)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def test_module_success_result(incus, tmp_path):
    rc, result = run_module(tmp_path, {"name": "web", "command": ["echo", "hi"], "socket": incus.socket_path})
    assert rc == 0
    assert result["changed"] is True
    assert result["rc"] == 0
    assert result["cmd"] == ["echo", "hi"]
    assert result["stderr_lines"] == ["err-web"]
    assert json.loads(result["stdout"])["name"] == "web"


def test_module_fails_on_non_zero_exit(incus, tmp_path):
    rc, result = run_module(tmp_path, {"name": "web", "command": ["fail"], "socket": incus.socket_path})
    assert rc == 1
    assert result["failed"] is True
    assert result["msg"] == "non-zero return code"
    assert result["rc"] == 3
    assert result["stdout_lines"] == ["partial"]


def test_module_fails_on_api_error(incus, tmp_path):
    rc, result = run_module(tmp_path, {"name": "missing", "command": ["true"], "socket": incus.socket_path})
    assert rc == 1
    assert result["failed"] is True
    assert "Instance not found" in result["msg"]


def test_module_requires_command(tmp_path):
    rc, result = run_module(tmp_path, {"name": "web"})
    assert rc == 1
    assert "command" in result["msg"]
