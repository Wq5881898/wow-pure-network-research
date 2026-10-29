"""Build fixed template locally and verify real Compose/UDS/gRPC RPC roundtrip."""
import json
import os
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path

from wsl_lab_connect import Relay, Server, gateway, root
import threading

work = Path.home() / "wow-research"
module = work / "live/modules/example"
module.mkdir(parents=True, exist_ok=True)
commit = "ef2d827494ac661c1213723a4450a833b9649769"
with zipfile.ZipFile(root / f"sources/agent-wow/go-module-template-{commit}.zip") as archive:
    for item in archive.infolist():
        rel = Path(*Path(item.filename).parts[1:])
        if item.is_dir() or not rel.parts:
            continue
        target = module / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(item))
env = {**os.environ, "GOPROXY": "https://goproxy.cn,direct", "CGO_ENABLED": "0"}
# Local replacement is recorded; use our already fixed core, not a floating download.
subprocess.run(["go", "mod", "edit", f"-replace=github.com/agent-wow/agent-wow={work / 'agent-wow'}"], cwd=module, check=True, env=env)
subprocess.run(["go", "build", "-trimpath", "-o", "module", "./cmd"], cwd=module, check=True, env=env, timeout=120)
# Avoid unavailable Docker Hub builder image; identical Go service in scratch.
(module / "Dockerfile").write_text('FROM scratch\nCOPY module /module\nENTRYPOINT ["/module"]\n')
servers = []
result = {"template_commit": commit, "core_commit": "f79739e3477d05060f91c2c15f67e76444ee3f02", "build_adaptation": "host Go build against fixed local core; scratch image, no Docker Hub builder", "gateway": gateway}
process = None
logpath = root / "results/pure_network/agent_wow_linux_module.log"


def rpc(method):
    req = urllib.request.Request("http://127.0.0.1:18086/rpc", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.load(response)


try:
    for port in (3724, 8085):
        server = Server(("127.0.0.1", port), Relay)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    with logpath.open("w") as log:
        process = subprocess.Popen([str(work / "agent-wow-cli"), "--config", str(work / "live/config.yaml"), "char", "play", "9"], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic()+60
        while time.monotonic()<deadline and process.poll() is None:
            if "world session ready" in logpath.read_text():
                break
            time.sleep(.25)
        if "world session ready" not in logpath.read_text():
            raise RuntimeError("module world session did not become ready")
        result["before"] = rpc("example.status")
        result["send"] = rpc("example.query_time")
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            result["after"] = rpc("example.status")
            if int(result["after"].get("result", {}).get("packetsReceived", 0)) > int(result["before"].get("result", {}).get("packetsReceived", 0)):
                break
            time.sleep(.1)
        result["logout"] = rpc("session.logout")
        result["returncode"] = process.wait(timeout=40)
        result["status"] = "PASS" if result["after"].get("result", {}).get("serverTimeUnix") and result["logout"].get("result", {}).get("status")=="closed" and result["returncode"]==0 else "INCOMPLETE"
except Exception as exc:
    result.update(status="INCOMPLETE", error=f"{type(exc).__name__}: {exc}")
finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=40)
    for server in servers:
        server.shutdown()
        server.server_close()
    result["remaining_containers"] = subprocess.check_output(["docker", "ps", "-a", "--format", "{{.Names}}"], text=True)
    (root / "results/pure_network/agent_wow_linux_module.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))
