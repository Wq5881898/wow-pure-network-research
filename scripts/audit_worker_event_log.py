"""Count tool calls from UTF-16/UTF-8 event exports without trusting model prose.

Malformed rows are reported explicitly; counts are not a full integrity proof.
"""
import argparse
import collections
import json
import re
from pathlib import Path


def audit(path):
    raw = Path(path).read_bytes()
    text = raw.decode('utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8')
    counts = collections.Counter()
    malformed = []
    denied = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get('type') != 'tool_use':
                continue
            part = row.get('part', {})
            counts[part.get('tool', '?')] += 1
            denied += part.get('state', {}).get('status') == 'error'
        except (ValueError, TypeError):
            malformed.append(lineno)
            if '"type":"tool_use"' in line:
                match = re.search(r'"tool":"([^"\r\n]+)"', line)
                counts[match.group(1) if match else '?'] += 1
                denied += '"status":"error"' in line
    return {'tool_counts': dict(counts), 'denied': denied, 'malformed_lines': malformed,
            'fully_parseable': not malformed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('events', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.events), ensure_ascii=False, indent=2))
