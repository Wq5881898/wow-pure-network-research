"""Validate unique public run outputs before opening a game session."""
import ast
import re
from pathlib import Path


def validate_run_artifacts(root, run_id, worker_source, extra_outputs=()):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise ValueError('invalid run id')
    root = Path(root).resolve()
    outputs = [f'results/pure_network/{name}' for name in (
        f'trial_{run_id}.json', f'trial_{run_id}.tmp',
        f'agent_wow_bridge_{run_id}.log',
        f'agent_wow_bridge_{run_id}.checkpoint.packets.json',
        f'agent_wow_bridge_{run_id}.packets.json',
        f'activity_{run_id}.jsonl', f'activity_{run_id}.active.json',
        f'activity_{run_id}.active.json.tmp', f'proximity_{run_id}.jsonl')]
    outputs.extend(extra_outputs)
    # A fresh run_id cannot repair a stale embedded report filename.
    for node in ast.walk(ast.parse(worker_source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.replace('\\', '/')
            if value.startswith('trial_') and value.endswith(('.json', '.tmp')):
                if value not in (f'trial_{run_id}.json', f'trial_{run_id}.tmp'):
                    raise ValueError(f'stale embedded trial path: {value}')
    resolved = []
    for value in outputs:
        path = (root / value).resolve()
        if root not in path.parents:
            raise ValueError('output outside workspace')
        if not path.parent.is_dir():
            raise ValueError(f'output parent missing: {path.parent}')
        if path.exists():
            raise FileExistsError(f'run artifact exists: {path}')
        resolved.append(str(path))
    if len(set(resolved)) != len(resolved):
        raise ValueError('duplicate output path')
    return {'run_id': run_id, 'outputs': resolved,
            'scope': 'preflight only; caller must hold unique owner lock through execution'}
