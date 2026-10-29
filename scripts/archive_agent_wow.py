"""Archive pinned upstream sources and articles with byte hashes for research."""

import datetime
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "sources" / "agent-wow"
REPOS = {
    "agent-wow": "f79739e3477d05060f91c2c15f67e76444ee3f02",
    "go-module-template": "ef2d827494ac661c1213723a4450a833b9649769",
    "agent-wow-workspace": "7c75881ccf70db4d374aea6449c44b29faaacda7",
}
ARTICLES = {
    "single-agent.html": "https://agent-wow.sh/gpt-6-astra-plays-world-of-warcraft-for-the-first-time-with-agent-wow/",
    "five-agents.html": "https://agent-wow.sh/five-agents-running-on-gpt-61-sol-clear-world-of-warcrafts-ragefire-chasm-in-24-hours/",
}


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "WoWResearch/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read(), response.geturl()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    manifest_path = DEST / "manifest.json"
    if manifest_path.exists():
        raise SystemExit("Archive already exists; inspect before replacing it")
    manifest = {"retrieved_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "repositories": [], "articles": []}
    for repo, commit in REPOS.items():
        url = f"https://codeload.github.com/agent-wow/{repo}/zip/{commit}"
        data, final_url = download(url)
        archive_path = DEST / f"{repo}-{commit}.zip"
        archive_path.write_bytes(data)
        # Full immutable zip is evidence; extracted code is an ignored work copy.
        extracted = ROOT / ".cache" / "agent-wow-upstream" / repo
        extracted.mkdir(parents=True, exist_ok=True)
        files = []
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for item in archive.infolist():
                relative = Path(*Path(item.filename).parts[1:])
                if item.is_dir() or not relative.parts:
                    continue
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Unsafe archive path")
                contents = archive.read(item)
                target = extracted / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(contents)
                files.append({"path": relative.as_posix(), "sha256": digest(contents)})
        manifest["repositories"].append({"repository": f"agent-wow/{repo}", "commit": commit, "url": url, "final_url": final_url, "archive": archive_path.name, "sha256": digest(data), "files": files})
        print(f"Archived {repo}: {len(files)} files, {len(data)} bytes", flush=True)
    for filename, url in ARTICLES.items():
        data, final_url = download(url)
        (DEST / filename).write_bytes(data)
        manifest["articles"].append({"file": filename, "url": url, "final_url": final_url, "bytes": len(data), "sha256": digest(data)})
        print(f"Archived {filename}: {len(data)} bytes", flush=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
