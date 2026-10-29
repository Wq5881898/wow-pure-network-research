"""Run and record normal upstream CLI operations with the existing lab account."""

import argparse
import datetime
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".cache/agent-wow-live"


def setup():
    creds = json.loads((ROOT / ".cache/agent-wow-lab-account.json").read_text(encoding="utf-8"))
    if len(creds["password"]) > 16:
        raise ValueError("Stored password exceeds verified server limit")
    WORK.mkdir(parents=True, exist_ok=True)
    for name in ("config", "data", "modules"):
        (WORK / name).mkdir(exist_ok=True)
    (WORK / "config/auth.json").write_text(json.dumps({"username": creds["username"], "password": creds["password"]}), encoding="utf-8")
    config = {"config_dir": str(WORK / "config"), "data_dir": str(WORK / "data"), "module_dir": str(WORK / "modules"),
              "authserver": {"host": "127.0.0.1", "port": 3724}, "worldrpc": {"host": "127.0.0.1", "port": 18086}, "log_level": "debug"}
    (WORK / "config.yaml").write_text(json.dumps(config), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["auth", "status", "realms", "characters", "create"])
    args = parser.parse_args()
    setup()
    operations = {"auth": ["auth", "login"], "status": ["auth", "status"], "realms": ["realm", "list", "--json"],
                  "characters": ["char", "list", "--json"], "create": ["char", "create", "--name", "Netprobe", "--race", "orc", "--class", "warrior", "--gender", "male"]}
    command = [str(ROOT / ".cache/agent-wow.exe"), "--config", str(WORK / "config.yaml"), *operations[args.operation]]
    result = subprocess.run(command, cwd=WORK, capture_output=True, timeout=40)
    row = {"observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "operation": args.operation,
           "returncode": result.returncode, "stdout": result.stdout.decode("utf-8", errors="replace"), "stderr": result.stderr.decode("utf-8", errors="replace")}
    path = ROOT / "results/pure_network/agent_wow_live_operations.jsonl"
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    raise SystemExit(result.returncode)
