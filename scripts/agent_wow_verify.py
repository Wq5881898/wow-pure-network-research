"""Record upstream build/test results and read-only local navigation metadata."""

import datetime
import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    if "--navigation-only" in sys.argv:
        exe = ROOT / ".cache/agent-wow-nav-build/Release/agent_wow_nav.exe"
        nav = r"C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps"
        cases = [("short_trial_valley", "1", "-618", "-4251", "38", "-600", "-4250", "38"),
                 ("invalid_height", "1", "-618", "-4251", "10000", "-600", "-4250", "10000")]
        rows = []
        for name, *args in cases:
            result = subprocess.run([str(exe), nav, *args], capture_output=True, timeout=15)
            rows.append({"case": name, "args": args, "returncode": result.returncode, "stdout": result.stdout.decode(), "stderr": result.stderr.decode()})
        (ROOT / "results/pure_network/agent_wow_navigation.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(rows, indent=2))
        raise SystemExit(0 if rows[0]["returncode"] == 0 and rows[1]["returncode"] != 0 else 1)
    go = ROOT / ".cache/agent-wow-toolchain/go/bin/go.exe"
    upstream = ROOT / ".cache/agent-wow-upstream/agent-wow"
    env = dict(os.environ, GOPROXY="https://goproxy.cn,direct", GOMODCACHE=str(ROOT / ".cache/go-mod"), GOCACHE=str(ROOT / ".cache/go-build"))
    record = {"observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "commit": "f79739e3477d05060f91c2c15f67e76444ee3f02", "platform": "windows/amd64"}
    test = subprocess.run([str(go), "test", "-json", "./..."], cwd=upstream, env=env, capture_output=True, timeout=180)
    (ROOT / "results/pure_network/agent_wow_upstream_tests.jsonl").write_bytes(test.stdout)
    packages = []
    for line in test.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("Action") in ("pass", "fail", "skip") and "Test" not in event:
            packages.append({k: event[k] for k in ("Package", "Action", "Elapsed") if k in event})
    record["tests"] = {"command": "go test -json ./...", "returncode": test.returncode, "packages": packages, "stderr": test.stderr.decode("utf-8", errors="replace")}
    exe = ROOT / ".cache/agent-wow.exe"
    build = subprocess.run([str(go), "build", "-o", str(exe), "."], cwd=upstream, env=env, capture_output=True, timeout=120)
    record["build"] = {"returncode": build.returncode, "stderr": build.stderr.decode("utf-8", errors="replace")}
    if build.returncode == 0:
        record["build"]["sha256"] = hashlib.sha256(exe.read_bytes()).hexdigest()
        record["cli_help"] = subprocess.run([str(exe), "realm", "--help"], capture_output=True, timeout=10).stdout.decode("utf-8")
    nav = Path(r"C:\Azerothcore\azerothcore-wotlk\build\bin\Debug\data\mmaps")
    tiles = sorted(nav.glob("*.mmtile"))
    record["navigation"] = {"path": str(nav), "tile_count": len(tiles), "map_count": len(list(nav.glob("*.mmap"))), "samples": []}
    for filename in ["000.mmap", "001.mmap", "389.mmap"]:
        path = nav / filename
        if path.exists():
            data = path.read_bytes()
            record["navigation"]["samples"].append({"file": filename, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if tiles:
        path = next(iter(sorted(nav.glob("001*.mmtile"))), None)
        if path is None:
            path = tiles[0]
        data = path.read_bytes()
        magic, detour, version, size = struct.unpack_from("<4I", data)
        record["navigation"]["tile_header_sample"] = {"file": path.name, "magic": hex(magic), "detour_version": detour, "mmap_version": version, "payload_size": size, "file_bytes": len(data), "header_56_plus_payload_matches": len(data) == 56 + size, "sha256": hashlib.sha256(data).hexdigest()}
    nav_exe = ROOT / ".cache/agent-wow-nav-build/Release/agent_wow_nav.exe"
    if nav_exe.exists():
        command = [str(nav_exe), str(nav), "1", "-618", "-4251", "38", "-600", "-4250", "38"]
        result = subprocess.run(command, capture_output=True, timeout=15)
        record["navigation"]["query"] = {"command": command, "returncode": result.returncode, "stdout": result.stdout.decode(), "stderr": result.stderr.decode()}
    (ROOT / "results/pure_network/agent_wow_verification.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"build_returncode": build.returncode, "test_returncode": test.returncode, "packages": packages, "navigation": record["navigation"]}, ensure_ascii=False, indent=2))
