"""Offline evidence audit: distinguish submitted position from movement updates."""
import hashlib
import json
from pathlib import Path

from agent_wow_position import movement_sequence
from agent_wow_worldstate import replay

ROOT = Path(__file__).resolve().parents[1]


def main():
    run_id = 'p3_batch01_unit1'
    trace_path = ROOT / 'results' / 'pure_network' / f'agent_wow_bridge_{run_id}.packets.json'
    report_path = ROOT / 'results' / 'pure_network' / f'agent_wow_task_{run_id}.json'
    raw = trace_path.read_bytes()
    trace = json.loads(raw)
    state = replay(trace)
    assert not state['errors'], state['errors']
    report = json.loads(report_path.read_text(encoding='utf-8'))
    approach = next(r for r in report['run']['records'] if r['action_id'] == 'approach')
    confirmation = approach['evidence']['position_confirmation']
    events = [{'sequence': e['sequence'], 'kind': e['kind'], 'movement': e['movement']}
              for e in state['event_log'] if e.get('guid') == trace['self_guid'] and 'movement' in e]
    result = {
        'run_id': run_id, 'trace_sha256': hashlib.sha256(raw).hexdigest(),
        'trace_capture_complete': trace.get('capture_complete'),
        'decode_errors': state['errors'], 'self_movement_events': events,
        'last_self_movement_sequence': movement_sequence(state, trace['self_guid']),
        'approach_status': approach['status'], 'position_confirmation': confirmation,
        'new_movement_after_request': any(e['sequence'] > confirmation['before_sequence'] for e in events),
        'logout': report.get('finalize'),
        'scope': 'selected polled substream, not every server packet; absence does not prove movement rejection',
        'source_semantics': {
            'MovementHandler.cpp:395': 'SendMessageToSet(&data, _player) excludes originating player',
            'Object.cpp:2124-2127': 'skipped_rcvr passed to MessageDistDeliverer',
        },
    }
    output = ROOT / 'results' / 'pure_network' / 'p3_batch01_position_audit.json'
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
