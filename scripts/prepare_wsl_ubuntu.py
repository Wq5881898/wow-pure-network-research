"""Download and hash-check Canonical's WSL image before importing a lab distro."""
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://releases.ubuntu.com/24.04.5/ubuntu-24.04.5-wsl-amd64.wsl"
EXPECTED = "bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e"

if __name__ == "__main__":
    folder = ROOT / ".cache/wsl"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "ubuntu-24.04.5-wsl-amd64.wsl"
    if not target.exists():
        partial = target.with_suffix(".partial")
        with urllib.request.urlopen(URL, timeout=120) as response, partial.open("wb") as output:
            while chunk := response.read(1 << 20):
                output.write(chunk)
        partial.rename(target)
    digest = hashlib.file_digest(target.open("rb"), "sha256").hexdigest()
    if digest != EXPECTED:
        raise SystemExit("WSL image hash mismatch; do not import")
    result = {"url": URL, "sha256": digest, "bytes": target.stat().st_size, "image": str(target),
              "hash_source": "Microsoft WSL DistributionInfo.json via jsDelivr; transport HTTPS, detached signature not checked"}
    (ROOT / "results/wsl_ubuntu_image.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
