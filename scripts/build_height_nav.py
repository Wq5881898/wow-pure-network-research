"""Build nav directly, avoiding relocated CMake caches and Unicode make bugs."""
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
detour = Path('C:/Azerothcore/azerothcore-wotlk/deps/recastnavigation/Detour')
output = root / '.cache/nav-height-build/agent_wow_nav.exe'
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['C:/APP/Perl/c/bin/c++.exe', '-std=c++17', '-O2',
                '-I' + str(detour / 'Include'), str(root / 'probes/AgentWowNav/nav.cpp'),
                *map(str, sorted((detour / 'Source').glob('*.cpp'))), '-o', str(output)], check=True)
