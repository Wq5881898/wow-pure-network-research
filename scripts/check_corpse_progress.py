"""Check that each new corpse is closer to the graveyard than the last one.

AGENTS.md: "再死时查询新尸体，以可行路径距离核对是否更接近墓地."
The distance that counts is the length of a WALKABLE path, not the straight
line: a corpse can be closer on the map and still be behind a cliff.

Every recovery run records the plan it used
(results/pure_network/agent_wow_danger_recovery_<run>.json):

    plan.corpse_to_graveyard_path_length   the walkable path from the corpse
    plan.reclaim_to_graveyard_path_length  the walkable path from the point
                                            the ghost reclaims at
    plan.path_progress_before_revive       how much of that the ghost covered
                                            before it revived

This script reads those records in order and reports whether the walkable
distance to the graveyard went down, stayed the same, or grew.

  python scripts/check_corpse_progress.py [results/pure_network]
"""
import json
import sys
from pathlib import Path


def collect(folder):
    rows = []
    for path in sorted(Path(folder).glob('agent_wow_danger_recovery_*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        plan = record.get('plan') or {}
        length = plan.get('corpse_to_graveyard_path_length')
        if not isinstance(length, (int, float)):
            continue
        rows.append({'run_id': record.get('run_id') or path.stem,
                     'corpse_path_length': round(float(length), 3),
                     'reclaim_path_length': plan.get('reclaim_to_graveyard_path_length'),
                     'progress_before_revive': plan.get('path_progress_before_revive'),
                     'corpse_xyz': plan.get('corpse_xyz'),
                     'ghost_cleared': record.get('ghost_cleared'),
                     'fresh_ghost': record.get('fresh_ghost'),
                     'fresh_health': record.get('fresh_health')})
    return rows


def main(argv):
    folder = argv[1] if len(argv) > 1 else str(
        Path(__file__).resolve().parents[1] / 'results/pure_network')
    rows = collect(folder)
    if not rows:
        print('no recovery records with a walkable corpse distance in', folder)
        return 1
    print('deaths checked: %d' % len(rows))
    print('A step that goes farther is not necessarily a failure: the character '
          'may have moved to a new area to hunt.\nThe check that matters is '
          'whether the NEXT death in the same area is closer.')
    previous = None
    regressions = 0
    for row in rows:
        if previous is None:
            verdict = 'first record'
        else:
            delta = row['corpse_path_length'] - previous['corpse_path_length']
            if delta < -0.5:
                verdict = 'closer by %.1f yards' % -delta
            elif delta > 0.5:
                verdict = 'FARTHER by %.1f yards' % delta
                regressions += 1
            else:
                verdict = 'unchanged'
        print('  %-14s corpse->graveyard %8.1f  %s'
              % (row['run_id'], row['corpse_path_length'], verdict))
        if row['progress_before_revive'] is not None:
            print('       advanced %.1f yards before reviving, ghost cleared=%s'
                  % (row['progress_before_revive'], row['ghost_cleared']))
        previous = row
    print('regressions: %d' % regressions)
    return 0 if regressions == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
