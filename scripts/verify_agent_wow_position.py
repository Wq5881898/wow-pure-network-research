"""Compare an archived approach submission with a later login trace."""
import argparse
import json
from pathlib import Path
from agent_wow_position import confirm_relogin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_report', type=Path)
    parser.add_argument('source_trace', type=Path)
    parser.add_argument('verification_trace', type=Path)
    args = parser.parse_args()
    report = json.loads(args.task_report.read_text())
    source = json.loads(args.source_trace.read_text())
    trace = json.loads(args.verification_trace.read_text())
    approach = next(r for r in report['run']['records'] if r['action_id'] == 'approach')
    before = approach['observation_before']
    if before['session_id'] != source['session_id'] or before['character'] != source['self_guid']:
        raise ValueError('task report/source trace identity mismatch')
    evidence = confirm_relogin(source['session_id'], approach['evidence']['submitted_position'],
                               before['map_id'], trace, character=before['character'])
    print(json.dumps(evidence, indent=2))
    return 0 if evidence['confirmed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
