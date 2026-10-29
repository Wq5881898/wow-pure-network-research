"""Observe upstream empty-module world entry, a ping cycle, and RPC logout."""

import datetime
import json
import subprocess
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    work = ROOT / ".cache/agent-wow-live"
    log = ROOT / "results/pure_network/agent_wow_world_session.log"
    record = {"observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "character": "Netprobe", "guid": "9", "modules": [], "hold_seconds_after_ready": 35}
    command = [str(ROOT / ".cache/agent-wow.exe"), "--config", str(work / "config.yaml"), "char", "play", "9"]
    with log.open("wb") as output:
        process = subprocess.Popen(command, cwd=work, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            ready = False
            while time.monotonic() < deadline and process.poll() is None:
                if "world session ready" in log.read_text(encoding="utf-8", errors="replace"):
                    ready = True
                    break
                time.sleep(0.2)
            record["ready"] = ready
            if ready:
                time.sleep(35)
                record["alive_after_hold"] = process.poll() is None
                if process.poll() is None:
                    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "session.logout"}).encode()
                    request = urllib.request.Request("http://127.0.0.1:18086/rpc", data=payload, headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(request, timeout=35) as response:
                        record["logout_response"] = json.load(response)
            record["returncode"] = process.wait(timeout=40)
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
                record["forced_termination"] = True
    text = log.read_text(encoding="utf-8", errors="replace")
    record["packet_log_lines"] = len(text.splitlines())
    record["status"] = "PASS" if record.get("ready") and record.get("alive_after_hold") and record.get("logout_response", {}).get("result", {}).get("status") == "closed" and record.get("returncode") == 0 else "INCOMPLETE"
    (ROOT / "results/pure_network/agent_wow_world_session.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print(text)
