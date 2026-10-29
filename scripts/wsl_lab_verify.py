"""Run inside WoWLab: verify host ports and a registry-independent container."""
import json
import socket
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
gateway = subprocess.check_output(["ip", "route"], text=True).split("default via ")[1].split()[0]
rows = []
for host in ("127.0.0.1", gateway):
    for port in (3724, 8085):
        try:
            with socket.create_connection((host, port), timeout=3):
                rows.append({"host": host, "port": port, "connected": True})
        except OSError as exc:
            rows.append({"host": host, "port": port, "connected": False, "error": str(exc)})
result = {"windows_gateway": gateway, "connections": rows}
with tempfile.TemporaryDirectory(prefix="wowlab-container-") as folder:
    folder = Path(folder)
    (folder / "main.go").write_text('package main\nimport "fmt"\nfunc main(){fmt.Println("WoWLab Linux container OK")}\n')
    subprocess.run(["go", "build", "-o", str(folder / "check"), str(folder / "main.go")], check=True, env={**__import__('os').environ, "CGO_ENABLED": "0"})
    (folder / "Dockerfile").write_text('FROM scratch\nCOPY check /check\nENTRYPOINT ["/check"]\n')
    subprocess.run(["docker", "build", "-t", "wowlab-local-check:1", str(folder)], check=True)
    run = subprocess.run(["docker", "run", "--rm", "wowlab-local-check:1"], capture_output=True, text=True)
    result["container"] = {"returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
(root / "results/wsl_lab_runtime.json").write_text(json.dumps(result, indent=2)+"\n")
print(json.dumps(result, indent=2))
