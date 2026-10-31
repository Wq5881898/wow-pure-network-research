"""Read-only reconciliation report for one run checkpoint file.

Prints a JSON summary of what the checkpoint durably proves (plan identity,
completed actions, dispatched-but-unconfirmed side effects) so an operator or
the main window can decide the next step. This tool never writes, deletes,
migrates or locks anything, and never dispatches a backend action.

Usage: python scripts/agent_wow_checkpoint_reconcile.py <checkpoint.json> [--plan plan.json]

--plan takes a JSON list of {id,kind,params,side_effecting,timeout} entries in
plan order; without it the plan digest cannot be recomputed and identity
binding is reported as unverifiable (for schema 2 the file's embedded plan is
echoed, but the digest check still needs the live plan the next run would use).
"""
import argparse
import json
from pathlib import Path

from agent_wow_checkpoint import (
    LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, CheckpointRejected, plan_identity,
    validate_checkpoint)


def reconcile(path, plan=None):
    path = Path(path)
    report = {'checkpoint': str(path), 'verdict': None, 'details': {}}
    try:
        data = json.loads(path.read_bytes().decode('utf-8'))
    except FileNotFoundError:
        report['verdict'] = 'missing'
        return report
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report['verdict'] = 'unreadable'
        report['details']['error'] = f'{type(exc).__name__}: {exc}'
        return report
    if not isinstance(data, dict):
        report['verdict'] = 'invalid'
        report['details']['error'] = 'top level is not a JSON object'
        return report
    schema = data.get('schema')
    report['details'].update({
        'schema': schema, 'run_id': data.get('run_id'), 'status': data.get('status'),
        'plan_digest': data.get('plan_digest'),
        'completed': data.get('completed'), 'attempted': data.get('attempted'),
        'record_count': len(data.get('records') or [])})
    if schema == LEGACY_SCHEMA_VERSION:
        report['verdict'] = 'legacy_schema1'
        report['details']['policy'] = (
            'schema 1 does not bind plan identity; resume is refused and the '
            'file must not be rewritten by the executor; reconcile manually '
            'against the original plan and backend state')
        return report
    if schema != SCHEMA_VERSION:
        report['verdict'] = 'invalid'
        report['details']['error'] = f'unsupported schema: {schema!r}'
        return report
    unconfirmed = sorted(set(data.get('attempted') or [])
                         - set(data.get('completed') or []))
    report['details']['dispatched_but_unconfirmed'] = unconfirmed
    if plan is None:
        report['verdict'] = 'identity_unverified'
        report['details']['policy'] = (
            'pass --plan with the live action plan (persisted fields in plan '
            'order) to verify plan identity before any resume')
        return report
    try:
        digest = plan_identity(plan)
    except ValueError as exc:
        report['verdict'] = 'invalid_plan'
        report['details']['error'] = str(exc)
        return report
    report['details']['recomputed_plan_digest'] = digest
    try:
        validate_checkpoint(data, data.get('run_id'), digest)
    except CheckpointRejected as exc:
        report['verdict'] = 'rejected'
        report['details']['reason'] = str(exc)
        return report
    report['verdict'] = 'consistent'
    report['details']['note'] = (
        'unconfirmed side effects must be re-checked against live game state; '
        'a resume re-reads state and never resends them')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('--plan', type=Path,
                        help='JSON list of {id,kind,params,side_effecting,timeout} in plan order')
    options = parser.parse_args(argv)
    plan = None
    if options.plan:
        raw = json.loads(options.plan.read_text(encoding='utf-8'))

        class _Action:
            def __init__(self, entry):
                self.id = entry['id']
                self.kind = entry['kind']
                self.params = entry.get('params', {})
                self.side_effecting = entry.get('side_effecting', False)
                self.timeout = entry.get('timeout', 15.0)

        plan = [_Action(entry) for entry in raw]
    report = reconcile(options.checkpoint, plan)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['verdict'] in ('consistent', 'identity_unverified',
                                      'legacy_schema1') else 1


if __name__ == '__main__':
    raise SystemExit(main())
