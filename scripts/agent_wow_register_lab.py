"""Register a dedicated lab account through a specified public website."""

import argparse
import datetime
import http.cookiejar
import json
import re
import secrets
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--retry-with-compatible-password", action="store_true", help="Reuse pending account name with a password within AzerothCore's 16-character limit")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if base not in ("http://127.0.0.1:8000", "https://reg.151077.xyz"):
        raise SystemExit("Use a known lab registration endpoint")
    root = Path(__file__).resolve().parents[1]
    private = root / ".cache" / "agent-wow-lab-account.json"
    previous = None
    if private.exists() and args.retry_with_compatible_password:
        previous = json.loads(private.read_text(encoding="utf-8"))
        if previous.get("status") != "submitted_outcome_pending":
            raise SystemExit("Only an unconfirmed submission can be retried")
    elif private.exists():
        raise SystemExit("Lab credentials already exist; not registering again")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")]
    with opener.open(base + "/", timeout=30) as response:
        html = response.read().decode("utf-8")
    match = re.search(r"验证码[：:]\s*(\d+)\s*\+\s*(\d+)", html)
    if not match:
        raise SystemExit("Public registration form challenge was not recognized")
    username = previous["username"] if previous else "AWLAB" + secrets.token_hex(3).upper()
    password = secrets.token_hex(8)
    # Preserve credentials before sending: a failed response can have an unknown
    # server-side outcome. Never blindly create another account after that.
    private.write_text(json.dumps({"username": username, "password": password, "registration_url": base, "status": "submitted_outcome_pending", "previous_submission": previous}), encoding="utf-8")
    evidence = root / "results" / "pure_network" / "agent_wow_lab_registration.json"
    if evidence.exists() and previous:
        archive = root / "results" / "pure_network" / "agent_wow_lab_registration_password24.json"
        if not archive.exists():
            archive.write_bytes(evidence.read_bytes())
    payload = urllib.parse.urlencode({"username": username, "password": password, "confirm_password": password,
                                       "email": username.lower() + "@example.invalid", "captcha_answer": str(sum(map(int, match.groups())))}).encode()
    request = urllib.request.Request(base + "/register", data=payload, headers={"Accept": "application/json"})
    try:
        with opener.open(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            result = json.loads(raw)
        except ValueError:
            result = {"message": "Non-JSON error response"}
        record = {"observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": "REJECTED_RESPONSE_OUTCOME_UNVERIFIED", "username": username, "route": "POST " + base + "/register", "http_status": exc.code, "response": result}
        (root / "results" / "pure_network" / "agent_wow_lab_registration.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, ensure_ascii=False))
        raise SystemExit(1)
    if not result.get("ok"):
        raise SystemExit("Public registration rejected the request")
    private.write_text(json.dumps({"username": username, "password": password}), encoding="utf-8")
    record = {"observed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": "PASS", "username": username,
              "route": "POST " + base + "/register", "credentials": "ignored .cache file; not included in evidence"}
    (root / "results" / "pure_network" / "agent_wow_lab_registration.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
