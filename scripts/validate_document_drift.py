"""Validate source references in a worker's JSON drift candidates; no edits."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    'docs/67_恢复后续跑修复与角色核对_2026-10-29.md',
    'docs/73_792接取持久化与狩猎路线前置核查.md',
    'docs/74_792狩猎区行进与首个目标等级核查.md',
    'docs/78_联合长测试03法术订阅遗漏与退出恢复.md',
}
TAGS = {'historical_status', 'superseded_next_step'}


def validate(path, root=ROOT):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if set(document) != {'scope', 'candidates'} or not isinstance(document['candidates'], list):
        raise ValueError('expected scope and candidates list')
    if document['scope'] != 'historical documentation; no current game state inferred':
        raise ValueError('wrong scope')
    if len(document['candidates']) > 8:
        raise ValueError('too many candidates')
    for index, row in enumerate(document['candidates'], 1):
        if set(row) != {'path', 'line', 'quote', 'tag', 'later_path', 'reason'}:
            raise ValueError(f'candidate {index}: wrong fields')
        if row['path'] not in ALLOWED or row['later_path'] not in ALLOWED:
            raise ValueError(f'candidate {index}: path outside the agreed scope')
        if row['path'] == row['later_path'] or row['tag'] not in TAGS:
            raise ValueError(f'candidate {index}: invalid relationship')
        # These scoped notes use monotonically increasing document numbers.
        # A prior report cannot substantiate a later report's follow-up.
        if int(Path(row['later_path']).name.split('_', 1)[0]) <= int(
                Path(row['path']).name.split('_', 1)[0]):
            raise ValueError(f'candidate {index}: later reference is not later')
        if type(row['line']) is not int or row['line'] < 1:
            raise ValueError(f'candidate {index}: invalid line')
        if not all(isinstance(row[key], str) and row[key].strip()
                   for key in ('quote', 'reason')):
            raise ValueError(f'candidate {index}: missing text')
        if len(row['quote']) > 240 or len(row['reason']) > 240:
            raise ValueError(f'candidate {index}: text too long')
        lines = (root / row['path']).read_text(encoding='utf-8').splitlines()
        if row['line'] > len(lines) or row['quote'] not in lines[row['line'] - 1]:
            raise ValueError(f'candidate {index}: source quote does not match line')
        if not (root / row['later_path']).is_file():
            raise ValueError(f'candidate {index}: later reference missing')
    return len(document['candidates'])


def audit_candidates(path, root=ROOT):
    """Return individually validated rows; malformed neighbors stay visible."""
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('scope') != ('historical documentation; '
            'no current game state inferred') or not isinstance(document.get('candidates'), list):
        raise ValueError('invalid report scope or candidates')
    if len(document['candidates']) > 8:
        raise ValueError('too many candidates')
    rows = []
    for candidate in document['candidates']:
        try:
            # Validate against the same rules without producing a file.
            row = candidate
            if not isinstance(row, dict) or set(row) != {
                    'path', 'line', 'quote', 'tag', 'later_path', 'reason'}:
                raise ValueError('wrong fields')
            if row['path'] not in ALLOWED or row['later_path'] not in ALLOWED:
                raise ValueError('path outside scope')
            if row['path'] == row['later_path'] or row['tag'] not in TAGS:
                raise ValueError('invalid relationship')
            if int(Path(row['later_path']).name.split('_', 1)[0]) <= int(
                    Path(row['path']).name.split('_', 1)[0]):
                raise ValueError('later reference is not later')
            if type(row['line']) is not int or row['line'] < 1:
                raise ValueError('invalid line')
            if not all(isinstance(row[key], str) and row[key].strip() and len(row[key]) <= 240
                       for key in ('quote', 'reason')):
                raise ValueError('missing or oversize text')
            lines = (root / row['path']).read_text(encoding='utf-8').splitlines()
            if row['line'] > len(lines) or row['quote'] not in lines[row['line'] - 1]:
                raise ValueError('source quote does not match line')
            if not (root / row['later_path']).is_file():
                raise ValueError('later reference missing')
            rows.append({'valid': True, 'path': row['path'], 'line': row['line']})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rows.append({'valid': False, 'reason': str(exc),
                         'path': candidate.get('path') if isinstance(candidate, dict) else None})
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidates', type=Path)
    args = parser.parse_args()
    rows = audit_candidates(args.candidates)
    print(json.dumps({'source_checks': rows, 'semantic_review_required': True}, ensure_ascii=False))
    if not all(row['valid'] for row in rows):
        raise SystemExit(1)
