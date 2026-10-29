"""Audit canonical evidence only; recovery/ remains preserved forensic material."""
import hashlib
import json
from pathlib import Path
from agent_wow_worldstate import replay

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'


def main():
    manifest = json.loads((FOLDER / 'predelete_restore_manifest.json').read_text())
    hashes = []
    for row in manifest:
        if row.get('network') is not True:
            continue
        path = FOLDER / Path(row['file']).relative_to('results')
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        hashes.append({'path': path.relative_to(ROOT).as_posix(), 'matches': actual == row['sha256']})
    traces = []
    for path in sorted(FOLDER.glob('*.packets.json')):
        row = {'path': path.relative_to(ROOT).as_posix()}
        try:
            trace = json.loads(path.read_bytes())
            packets = trace['packets']
            row.update(packets=len(packets), session=trace.get('session_id'),
                       character=trace.get('self_guid'), capture_complete=trace.get('capture_complete'),
                       checkpoint='.checkpoint.' in path.name,
                       contiguous=all(p['sequence'] == i + 1 for i, p in enumerate(packets)))
            state = replay(trace)
            row.update(errors=state['errors'], login=state.get('login'), self_present=bool(state.get('self')))
            me = state.get('self') or {}
            totals = {}
            for obj in state['visible_objects']:
                if obj.get('type_id') in (1, 2) and obj.get('owner_guid') == trace.get('self_guid'):
                    key = str(obj['entry'])
                    totals[key] = totals.get(key, 0) + obj.get('stack_count', 0)
            row['final_state'] = {k: me.get(k) for k in ('health', 'max_health', 'ghost', 'in_combat', 'quest_log')}
            row['final_state']['item_totals'] = totals
            row['valid_replay'] = row['contiguous'] and not row['errors']
        except Exception as exc:
            row.update(valid_replay=False, error=f'{type(exc).__name__}: {exc}')
        traces.append(row)
    result = {'scope': 'canonical root traces incl checkpoints, excluding recovery and archive directories',
              'hashes': hashes, 'traces': traces,
              'summary': {'restored_network_files': len(hashes),
                          'hash_mismatches': sum(not r['matches'] for r in hashes),
                          'traces': len(traces), 'replay_failures': sum(not r['valid_replay'] for r in traces),
                          'checkpoints': sum(r.get('checkpoint', False) for r in traces)}}
    output = FOLDER / 'recovery_verification_20261029.json'
    if output.exists():
        raise RuntimeError('audit already exists; preserve prior result')
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result['summary'], indent=2))
    for row in traces:
        if not row['valid_replay']:
            print(json.dumps(row))


if __name__ == '__main__':
    main()
