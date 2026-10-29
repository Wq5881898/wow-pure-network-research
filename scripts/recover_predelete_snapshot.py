"""Export original results blobs from the confirmed pre-deletion OpenCode tree."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = Path.home() / '.local/share/opencode/snapshot/43512500a3d30389c961efa835749c4a38b69d49/fc12399329a4a0e4117ea443687f1ca271fd1d7e'
TREE = 'f0f49b3de3fcd802639e5fc7aa74f349fe793018'


def main():
    output = ROOT / '.cache/incident_private/predelete_results'
    output.mkdir(parents=True, exist_ok=True)
    command = ['git', '--git-dir=' + str(GIT)]
    listing = subprocess.check_output(command + ['ls-tree', '-r', '-z', TREE, '--', 'results/'])
    rows = []
    for entry in listing.split(b'\0'):
        if not entry:
            continue
        header, name = entry.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        path = name.decode('utf-8')
        if kind != 'blob' or mode == '120000':
            continue
        relative = Path(path).relative_to('results')
        if '..' in relative.parts:
            raise ValueError(path)
        raw = subprocess.check_output(command + ['cat-file', 'blob', oid])
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != raw:
                raise RuntimeError(str(target))
        else:
            with target.open('xb') as handle:
                handle.write(raw)
        row = {'original': path, 'blob': oid, 'bytes': len(raw),
               'sha256': hashlib.sha256(raw).hexdigest(), 'all_zero': bool(raw) and not any(raw)}
        if path.endswith('.json'):
            try:
                json.loads(raw)
                row['json_valid'] = True
            except (ValueError, UnicodeError):
                row['json_valid'] = False
        rows.append(row)
    (output.parent / 'predelete_manifest.json').write_text(json.dumps({'tree': TREE, 'files': rows}, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(rows), 'json_invalid': sum(r.get('json_valid') is False for r in rows),
                      'all_zero': sum(r['all_zero'] for r in rows), 'output': str(output)}))


if __name__ == '__main__':
    main()
