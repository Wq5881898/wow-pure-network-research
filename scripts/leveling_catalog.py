"""Offline lookup of reusable leveling evidence; never opens a game session."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/leveling_stages.json'


def load_catalog(path=CATALOG):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data['version'] != 1:
        raise ValueError('unsupported catalog version')
    boundary = 1
    for stage in data['stages']:
        if stage['from'] != boundary or stage['to'] <= boundary:
            raise ValueError('level bands must be consecutive and increasing')
        if stage['status'] not in {'historical', 'trial', 'unmapped'}:
            raise ValueError('unknown evidence status')
        boundary = stage['to']
    if boundary != 60:
        raise ValueError('catalog must terminate at level 60')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('level', type=int, choices=range(1, 61), metavar='LEVEL')
    args = parser.parse_args()
    data = load_catalog()
    stage = next((s for s in data['stages'] if s['from'] <= args.level < s['to']), None)
    if stage is not None:
        stage = {**stage, 'evidence': [
            {'path': path, 'available_locally': (ROOT / path).is_file()}
            for path in stage['evidence']]}
    print(json.dumps({'profile': data['profile'], 'level': args.level, 'stage': stage}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
