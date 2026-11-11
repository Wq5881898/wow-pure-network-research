"""Compare one upstream-inspired change on fixed failures; no game connection."""
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

CASES = [
    ('known_ground', [-778,-4985,19], [-686.19,-4821.91,35.7]),
    ('known_surface_reject', [-940.0933,-4980.8608,7.9378], [-1000,-4900,15]),
    ('kolkar_surface_reject', [-778,-4985,19], [-916.15,-4749.1,21.26]),
    ('tiger_disconnected', [-940.0933,-4980.8608,7.9378], [-1012.25,-5144.59,2.503]),
]

def main():
    executable, maps, output = map(Path, sys.argv[1:4])
    report = {'scope': 'offline single-variable corridor merge comparison',
              'binary_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(), 'cases': []}
    for name, start, end in CASES:
        row = {'case': name, 'start': start, 'end': end, 'variants': {}}
        for enabled in (False, True):
            env = dict(os.environ)
            env.pop('AGENT_WOW_NAV_CORRIDOR_AB', None)
            if enabled:
                env['AGENT_WOW_NAV_CORRIDOR_AB'] = '1'
            process = subprocess.run([str(executable), str(maps), '1', *map(str,start+end)],
                                     env=env, capture_output=True, text=True, timeout=30)
            result = {'returncode': process.returncode, 'stderr': process.stderr}
            if process.returncode == 0:
                data = json.loads(process.stdout)
                samples = data['surface_samples']
                result.update(end_distance=math.dist(samples[-1], end),
                              length=sum(math.dist(a,b) for a,b in zip(samples,samples[1:])),
                              max_vertical_step=max((abs(a[2]-b[2]) for a,b in zip(samples,samples[1:])),default=0),
                              sample_count=len(samples), updates=data['experimental_corridor_updates'])
            row['variants']['merge' if enabled else 'original'] = result
        report['cases'].append(row)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
