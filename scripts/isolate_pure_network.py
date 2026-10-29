"""Manifest-first, no-overwrite migration of confirmed network artifacts.

Run without --apply to inventory; --apply moves only unchanged listed files.
Original JSON contents (including historical paths) are never rewritten.
"""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'results' / 'pure_network'


def confirmed(name):
    return (name.startswith('agent_wow_') or name.startswith('stage2_batch')
            or name.startswith('p3_batch01') or name.startswith('network_world_challenge_'))


def fingerprint(path):
    raw = path.read_bytes()
    return {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'all_zero': bool(raw) and not any(raw)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify', type=Path, help='verify an existing migration manifest')
    args = parser.parse_args()
    if args.verify:
        data = json.loads(args.verify.read_text(encoding='utf-8'))
        for row in data['files']:
            target = ROOT / row['destination']
            if fingerprint(target)['sha256'] != row['sha256']:
                raise RuntimeError(f'hash mismatch: {target}')
            if (ROOT / row['source']).exists():
                raise RuntimeError(f'source reappeared: {row["source"]}')
        print(json.dumps({'verified': len(data['files'])}))
        return
    sources = [(ROOT / 'results', DEST),
               (ROOT / 'results_archive_failed_stage2', DEST / 'archive_failed_stage2'),
               (ROOT / 'results_archive_wrong' / 'results_recovered_vss11_2026-10-29',
                DEST / 'recovery' / 'vss11_2026-10-29')]
    rows, untouched = [], []
    for source, target in sources:
        if not source.exists():
            continue
        for path in sorted(source.iterdir()):
            if not path.is_file() or not confirmed(path.name):
                if path != DEST:
                    untouched.append(str(path.relative_to(ROOT)))
                continue
            destination = target / path.name
            if destination.exists():
                raise RuntimeError(f'destination exists: {destination}')
            rows.append({'source': str(path.relative_to(ROOT)),
                         'destination': str(destination.relative_to(ROOT)),
                         **fingerprint(path)})
    DEST.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    manifest = DEST / f'migration_{stamp}.json'
    with manifest.open('x', encoding='utf-8') as handle:
        json.dump({'apply': args.apply, 'files': rows, 'untouched': untouched}, handle,
                  ensure_ascii=False, indent=2)
    if args.apply:
        journal = DEST / f'migration_{stamp}.journal.jsonl'
        with journal.open('x', encoding='utf-8') as log:
            for row in rows:
                source, target = ROOT / row['source'], ROOT / row['destination']
                if fingerprint(source)['sha256'] != row['sha256']:
                    raise RuntimeError(f'source changed: {source}')
                target.parent.mkdir(parents=True, exist_ok=True)
                # Windows rename refuses an existing target; never overwrite.
                if target.exists():
                    raise RuntimeError(f'target appeared: {target}')
                source.rename(target)
                if fingerprint(target)['sha256'] != row['sha256']:
                    raise RuntimeError(f'hash mismatch: {target}')
                log.write(json.dumps({'source': row['source'], 'destination': row['destination'],
                                      'sha256': row['sha256'], 'verified': True}) + '\n')
                log.flush()
    print(json.dumps({'manifest': str(manifest), 'files': len(rows),
                      'applied': args.apply, 'untouched': len(untouched)}))


if __name__ == '__main__':
    main()
