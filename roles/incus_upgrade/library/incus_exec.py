#!/usr/bin/python
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)

DOCUMENTATION = r"""
---
module: incus_exec
short_description: Run a command inside an Incus instance through the Incus REST API
description:
  - Equivalent of C(incus exec) that talks directly to the Incus API over its
    local unix socket, so the instance needs neither SSH nor Python and does
    not have to be in the Ansible inventory.
  - Runs on the Incus host. Output is recorded by Incus, read back and then
    removed from the instance log directory.
options:
  name:
    description: Name of the instance.
    type: str
    required: true
  project:
    description: Incus project the instance belongs to.
    type: str
    default: default
  command:
    description: Command to run, as a list of arguments.
    type: list
    elements: str
    required: true
  environment:
    description: Extra environment variables for the command.
    type: dict
    default: {}
  cwd:
    description: Working directory for the command inside the instance.
    type: str
  socket:
    description: Path of the Incus unix socket.
    type: path
    default: /var/lib/incus/unix.socket
  timeout:
    description: Seconds to wait for the command to finish.
    type: int
    default: 3600
"""

EXAMPLES = r"""
- name: Upgrade packages in a container
  incus_exec:
    name: web01
    command: [apt-get, -y, dist-upgrade]
    environment:
      DEBIAN_FRONTEND: noninteractive
"""

RETURN = r"""
rc:
  description: Exit code of the command.
  type: int
  returned: always
stdout:
  description: Standard output of the command.
  type: str
  returned: always
stderr:
  description: Standard error of the command.
  type: str
  returned: always
"""

import http.client
import json
import socket
from urllib.parse import quote, urlencode

from ansible.module_utils.basic import AnsibleModule


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


class IncusClient:
    def __init__(self, socket_path, project, timeout):
        self.socket_path = socket_path
        self.project = project
        self.timeout = timeout

    def request(self, method, path, body=None):
        sep = "&" if "?" in path else "?"
        url = path + sep + urlencode({"project": self.project})
        conn = UnixHTTPConnection(self.socket_path, self.timeout + 30)
        try:
            headers = {}
            payload = None
            if body is not None:
                payload = json.dumps(body)
                headers["Content-Type"] = "application/json"
            conn.request(method, url, body=payload, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def json_request(self, method, path, body=None):
        status, data = self.request(method, path, body)
        result = json.loads(data)
        if result.get("type") == "error" or status >= 400:
            raise IncusError(f"{method} {path} failed: {result.get('error', status)}")
        return result


class IncusError(Exception):
    pass


def run(module):
    p = module.params
    client = IncusClient(p["socket"], p["project"], p["timeout"])
    instance = quote(p["name"], safe="")

    body = {
        "command": p["command"],
        "environment": p["environment"],
        "wait-for-websocket": False,
        "interactive": False,
        "record-output": True,
    }
    if p["cwd"]:
        body["cwd"] = p["cwd"]

    op = client.json_request("POST", f"/1.0/instances/{instance}/exec", body)
    op = client.json_request("GET", f"{op['operation']}/wait?timeout={p['timeout']}")
    metadata = op["metadata"]
    if metadata.get("status") == "Running":
        raise IncusError(f"command did not finish within {p['timeout']} seconds")
    if metadata.get("status_code") != 200:
        raise IncusError(f"exec failed: {metadata.get('err') or metadata.get('status')}")

    exec_meta = metadata.get("metadata") or {}
    output = {}
    for fd, log_path in (exec_meta.get("output") or {}).items():
        status, data = client.request("GET", log_path)
        output[fd] = data.decode("utf-8", errors="replace") if status == 200 else ""
        client.request("DELETE", log_path)

    return exec_meta.get("return", -1), output.get("1", ""), output.get("2", "")


def main():
    module = AnsibleModule(
        argument_spec=dict(
            name=dict(type="str", required=True),
            project=dict(type="str", default="default"),
            command=dict(type="list", elements="str", required=True),
            environment=dict(type="dict", default={}),
            cwd=dict(type="str"),
            socket=dict(type="path", default="/var/lib/incus/unix.socket"),
            timeout=dict(type="int", default=3600),
        ),
        supports_check_mode=False,
    )

    try:
        rc, stdout, stderr = run(module)
    except (IncusError, OSError, ValueError, KeyError) as e:
        module.fail_json(msg=f"incus exec on {module.params['name']} failed: {e}")

    result = dict(
        changed=True,
        cmd=module.params["command"],
        rc=rc,
        stdout=stdout,
        stderr=stderr,
        stdout_lines=stdout.splitlines(),
        stderr_lines=stderr.splitlines(),
    )
    if rc != 0:
        module.fail_json(msg="non-zero return code", **result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
