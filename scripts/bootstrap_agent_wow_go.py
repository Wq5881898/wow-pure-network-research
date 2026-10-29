"""Fetch a verified portable Go toolchain into the ignored research cache."""

import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
URL = "https://golang.google.cn/dl/go1.27.3.windows-amd64.zip"
SHA256 = "f26081f438319757f8a1b46d7d0b2c5ec7583d4038e485e0aa725cfde7885fff"


if __name__ == "__main__":
    target = ROOT / ".cache" / "agent-wow-toolchain"
    if (target / "go" / "bin" / "go.exe").exists():
        raise SystemExit("Portable Go already present")
    with urllib.request.urlopen(URL, timeout=180) as response:
        data = response.read()
        final_url = response.geturl()
    actual = hashlib.sha256(data).hexdigest()
    if actual != SHA256:
        raise SystemExit(f"Toolchain hash mismatch: {actual}")
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(target)
    record = {"version": "go1.27.3", "os": "windows", "arch": "amd64", "url": URL, "final_url": final_url, "sha256": actual, "bytes": len(data)}
    (ROOT / "results" / "pure_network" / "agent_wow_go_toolchain.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
