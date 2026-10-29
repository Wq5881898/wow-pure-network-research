"""Check tracked candidates against local credential values; print paths only."""
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    secrets = set()
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if re.search(r'password|token|secret|cookie', key, re.I) and isinstance(item, str) and len(item) >= 8:
                    secrets.add(item.encode())
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    for path in (ROOT / '.cache').rglob('*.json'):
        if 'incident_private' in path.parts:
            continue
        try:
            collect(json.loads(path.read_bytes()))
        except (ValueError, OSError):
            pass
    paths = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).split(b'\0')
    issues = []
    total = 0
    for raw in paths:
        if not raw:
            continue
        name = raw.decode('utf-8')
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        total += len(data)
        reasons = []
        if any(secret in data for secret in secrets):
            reasons.append('known credential value')
        if re.search(rb'gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY', data):
            reasons.append('token/private-key pattern')
        if len(data) > 90_000_000:
            reasons.append('oversize')
        if reasons:
            issues.append({'path': name, 'reasons': reasons})
    print(json.dumps({'bytes': total, 'issues': issues}, ensure_ascii=True, indent=2))
    raise SystemExit(bool(issues))


if __name__ == '__main__':
    main()
