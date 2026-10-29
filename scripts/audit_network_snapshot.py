"""Read representative VSS samples without restoring or modifying their contents."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/pure_network'


def inspect(path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {'status': 'missing'}
    except OSError as exc:
        return {'status': 'read_error', 'error': str(exc)}
    row = {'status': 'read', 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
           'all_zero': bool(raw) and not any(raw)}
    try:
        json.loads(raw)
        row['json_valid'] = True
    except (ValueError, UnicodeError):
        row['json_valid'] = False
    return row


def main():
    names = ['agent_wow_bridge_run01.packets.json', 'agent_wow_bridge_run02.packets.json',
             'agent_wow_bridge_run13_approach.packets.json',
             'agent_wow_bridge_run100_task_unit.packets.json',
             'agent_wow_task_run111_movement_boundary.json']
    rows = []
    for name in names:
        row = {'name': name, 'current': inspect(OUT / name),
               'recovery': inspect(OUT / 'recovery/vss11_2026-10-29' / name)}
        for number in (8, 9, 10, 11):
            source = Path('\\\\?\\GLOBALROOT\\Device\\HarddiskVolumeShadowCopy' + str(number))
            original_root = ROOT.parent if ROOT.name == 'pure-network' else ROOT
            path = source / original_root.relative_to(original_root.anchor) / 'results' / name
            row[f'vss{number}'] = inspect(path)
        rows.append(row)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = OUT / f'snapshot_source_audit_{stamp}.json'
    with output.open('x', encoding='utf-8') as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    print(json.dumps(rows, ensure_ascii=True, indent=2))
    print(output)


if __name__ == '__main__':
    main()
