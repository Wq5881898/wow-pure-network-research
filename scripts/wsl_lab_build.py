"""Restore fixed core to Linux filesystem; build and record Linux tests."""
import json
import os
import subprocess
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
work = Path.home() / "wow-research"
work.mkdir(exist_ok=True)
dest = work / "agent-wow"
commit = "f79739e3477d05060f91c2c15f67e76444ee3f02"
if not dest.exists():
    dest.mkdir()
    with zipfile.ZipFile(root / f"sources/agent-wow/agent-wow-{commit}.zip") as archive:
        for item in archive.infolist():
            rel = Path(*Path(item.filename).parts[1:])
            if item.is_dir() or not rel.parts:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
env = {**os.environ, "GOPROXY": "https://goproxy.cn,direct"}
test = subprocess.run(["go", "test", "-json", "./..."], cwd=dest, env=env, capture_output=True, timeout=240)
(root / "results/pure_network/agent_wow_linux_tests.jsonl").write_bytes(test.stdout)
build = subprocess.run(["go", "build", "-o", str(work / "agent-wow-cli"), "."], cwd=dest, env=env, capture_output=True, timeout=120)
record = {"commit": commit, "workdir": str(dest), "tests_returncode": test.returncode, "tests_stderr": test.stderr.decode(), "build_returncode": build.returncode, "build_stderr": build.stderr.decode()}
(root / "results/pure_network/agent_wow_linux_build.json").write_text(json.dumps(record, indent=2)+"\n")
print(json.dumps(record, indent=2))
