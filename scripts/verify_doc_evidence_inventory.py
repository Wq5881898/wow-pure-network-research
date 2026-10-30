"""Check literal evidence references in an offline worker inventory.

Only canonical results/pure_network root files count as present. A referenced
name under recovery/ or an archive is not a substitute for a run's original.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'docs/73_792接取持久化与狩猎路线前置核查.md',
    'docs/75_长测试01联合验收与AttackStop解析修复.md',
    'docs/82_撤离候选非战斗逆向通行验收.md',
}


def check(inventory, root=ROOT):
    data = json.loads(Path(inventory).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'references'} or not isinstance(data['references'], list):
        raise ValueError('expected references list')
    if len(data['references']) > 40:
        raise ValueError('too many references')
    rows = []
    for ref in data['references']:
        if not isinstance(ref, dict) or set(ref) != {'document', 'line', 'quote', 'basename'}:
            raise ValueError('reference fields invalid')
        name, document, line, quote = (ref[k] for k in ('basename', 'document', 'line', 'quote'))
        if document not in SOURCES or type(line) is not int or line < 1:
            raise ValueError('source outside scope')
        if (not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name
                or name in ('.', '..') or not name.endswith(('.json', '.jsonl', '.packets.json'))):
            raise ValueError('invalid basename')
        lines = (root / document).read_text(encoding='utf-8').splitlines()
        if (not isinstance(quote, str) or not quote.strip() or line > len(lines)
                or quote not in lines[line - 1] or name not in quote):
            raise ValueError('reference not on claimed line')
        path = root / 'results' / 'pure_network' / name
        result = {'document': document, 'line': line, 'basename': name,
                  'status': 'present' if path.is_file() else 'missing'}
        if path.is_file():
            result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(result)
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('inventory', type=Path)
    args = parser.parse_args()
    rows = check(args.inventory)
    print(json.dumps({'references': rows, 'checked': len(rows)}, ensure_ascii=False, indent=2))
