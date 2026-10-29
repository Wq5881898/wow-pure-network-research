"""Validate Linux auth/character listing through temporary loopback TCP relays."""
import json
import os
import select
import socket
import socketserver
import subprocess
import threading
from pathlib import Path

root = Path(__file__).resolve().parents[1]
gateway = subprocess.check_output(["ip", "route"], text=True).split("default via ")[1].split()[0]


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.create_connection((gateway, self.server.server_address[1]), timeout=10) as remote:
            while True:
                ready, _, _ = select.select([self.request, remote], [], [], 120)
                if not ready:
                    return
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is self.request else self.request).sendall(data)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    folder = Path.home() / "wow-research/live"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    creds = json.loads((root / ".cache/agent-wow-lab-account.json").read_text())
    config_dir = folder / "config"
    config_dir.mkdir(exist_ok=True, mode=0o700)
    auth = config_dir / "auth.json"
    auth.write_text(json.dumps({"username": creds["username"], "password": creds["password"]}))
    auth.chmod(0o600)
    config = folder / "config.yaml"
    config.write_text(json.dumps({"config_dir": str(config_dir), "data_dir": str(folder / "data"), "module_dir": str(folder / "modules"), "authserver": {"host": "127.0.0.1", "port": 3724}, "worldrpc": {"host": "127.0.0.1", "port": 18086}}))
    servers = []
    try:
        for port in (3724, 8085):
            server = Server(("127.0.0.1", port), Relay)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        rows = []
        for args in (["auth", "login"], ["char", "list", "--json"]):
            run = subprocess.run([str(Path.home() / "wow-research/agent-wow-cli"), "--config", str(config), *args], capture_output=True, text=True, timeout=30)
            rows.append({"args": args, "returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr})
            if run.returncode:
                break
        result = {"gateway": gateway, "relay_scope": "temporary WSL loopback only; Windows server unchanged", "operations": rows}
        (root / "results/pure_network/agent_wow_linux_connection.json").write_text(json.dumps(result, indent=2)+"\n")
        print(json.dumps(result, indent=2))
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
