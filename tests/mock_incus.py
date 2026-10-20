"""Minimal mock of the Incus REST API served on a unix socket.

Implements just enough of the API for the incus_upgrade role and the
incus_exec module: server info, instance listing, exec with recorded output,
operation wait, exec log download/removal and instance state changes.
"""

import json
import os
import shutil
import socketserver
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, unquote, urlparse


class MockIncus:
    def __init__(self, instances, exec_handler, state_handler=None):
        """
        instances: list of instance dicts as returned by /1.0/instances?recursion=1
        exec_handler: callable(name, project, command, environment, cwd) -> (rc, stdout, stderr)
        state_handler: optional callable(name, project, body) called on state changes
        """
        self.instances = instances
        self.exec_handler = exec_handler
        self.state_handler = state_handler
        self.execs = []
        self.state_changes = []
        self.deleted_logs = []
        self.operations = {}
        self.logs = {}
        # Override per test to simulate an operation that is still running or failed.
        self.operation_override = None
        # AF_UNIX paths are limited to ~108 bytes, so keep the path short.
        self.tmpdir = tempfile.mkdtemp(prefix="incus-", dir="/tmp")
        self.socket_path = os.path.join(self.tmpdir, "unix.socket")
        self._server = None
        self._thread = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def address_string(self):
                return "unix"

            def _send(self, code, obj, raw=False):
                body = obj if raw else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/octet-stream" if raw else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _error(self, code, msg):
                self._send(code, {"type": "error", "error": msg, "error_code": code})

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length)) if length else None

            def _instance(self, parts, query):
                name = unquote(parts[3])
                project = query.get("project", ["default"])[0]
                for inst in mock.instances:
                    if inst["name"] == name and inst.get("project", "default") == project:
                        return inst
                return None

            def _new_operation(self, metadata):
                op_id = str(uuid.uuid4())
                mock.operations[op_id] = metadata
                self._send(202, {"type": "async", "operation": "/1.0/operations/" + op_id})

            def do_GET(self):
                url = urlparse(self.path)
                parts = url.path.split("/")
                if url.path == "/1.0":
                    return self._send(200, {"type": "sync", "metadata": {"api_version": "1.0"}})
                if url.path == "/1.0/instances":
                    return self._send(200, {"type": "sync", "metadata": mock.instances})
                if url.path.startswith("/1.0/operations/") and url.path.endswith("/wait"):
                    op = mock.operations.get(parts[3])
                    if op is None:
                        return self._error(404, "Operation not found")
                    if mock.operation_override is not None:
                        op = mock.operation_override
                    return self._send(200, {"type": "sync", "metadata": op})
                if url.path in mock.logs:
                    return self._send(200, mock.logs[url.path].encode(), raw=True)
                return self._error(404, "Not found")

            def do_DELETE(self):
                url = urlparse(self.path)
                if url.path in mock.logs:
                    del mock.logs[url.path]
                    mock.deleted_logs.append(url.path)
                    return self._send(200, {"type": "sync", "metadata": {}})
                return self._error(404, "Not found")

            def do_POST(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                parts = url.path.split("/")
                if len(parts) == 5 and parts[4] == "exec":
                    inst = self._instance(parts, query)
                    if inst is None:
                        return self._error(404, "Instance not found")
                    body = self._body()
                    project = query.get("project", ["default"])[0]
                    mock.execs.append({"name": inst["name"], "project": project, **body})
                    rc, out, err = mock.exec_handler(
                        inst["name"], project, body["command"], body.get("environment", {}), body.get("cwd")
                    )
                    name = quote(inst["name"], safe="")
                    base = f"/1.0/instances/{name}/logs/exec-output/exec_{uuid.uuid4()}"
                    output = {}
                    if body.get("record-output"):
                        mock.logs[base + ".stdout"] = out
                        mock.logs[base + ".stderr"] = err
                        output = {"1": base + ".stdout", "2": base + ".stderr"}
                    return self._new_operation(
                        {
                            "status": "Success",
                            "status_code": 200,
                            "err": "",
                            "metadata": {"return": rc, "output": output},
                        }
                    )
                return self._error(404, "Not found")

            def do_PUT(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                parts = url.path.split("/")
                if len(parts) == 5 and parts[4] == "state":
                    inst = self._instance(parts, query)
                    if inst is None:
                        return self._error(404, "Instance not found")
                    body = self._body()
                    mock.state_changes.append({"name": inst["name"], **body})
                    if mock.state_handler:
                        mock.state_handler(inst["name"], query.get("project", ["default"])[0], body)
                    return self._new_operation({"status": "Success", "status_code": 200, "err": "", "metadata": {}})
                return self._error(404, "Not found")

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        self._server = Server(self.socket_path, Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
