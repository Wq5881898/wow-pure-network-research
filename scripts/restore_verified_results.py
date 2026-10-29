"""Install byte-verified predelete evidence, preserving displaced VSS copies."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / '.cache/incident_private/predelete_results'
    manifest = json.loads((source.parent / 'predelete_manifest.json').read_text())
    rows = []
    for row in manifest['files']:
        relative = Path(row['original']).relative_to('results')
        name = relative.name
        network = name.startswith(('agent_wow_', 'stage2_batch', 'p3_', 'network_world_challenge_'))
        target = (ROOT / 'results/pure_network' if network else ROOT.parent / 'results') / relative
        data = (source / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise RuntimeError(name)
        if target.exists() and target.read_bytes() != data:
            if not network:
                rows.append({'file': row['original'], 'status': 'parent_conflict_preserved'})
                continue
            old = ROOT / 'results/pure_network/recovery/displaced_working' / relative
            old.parent.mkdir(parents=True, exist_ok=True)
            if old.exists():
                raise RuntimeError(str(old))
            target.rename(old)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with target.open('xb') as handle:
                handle.write(data)
        rows.append({'file': row['original'], 'status': 'verified', 'network': network,
                     'sha256': row['sha256']})
    (ROOT / 'results/pure_network/predelete_restore_manifest.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(json.dumps({'processed': len(rows), 'conflicts': sum(r['status'] != 'verified' for r in rows)}))


if __name__ == '__main__':
    main()
