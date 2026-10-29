"""Inspect transmitted equipment GUIDs from an archived character trace."""
import argparse
import json
from pathlib import Path
from agent_wow_worldstate import replay


def equipment(trace):
    state = replay(trace)
    fields = {}
    for event in state['event_log']:
        if event.get('guid') == trace['self_guid']:
            fields.update(event.get('fields', {}))
    objects = {o['guid']: o for o in state['visible_objects']}
    result = {}
    # Local UpdateFields.h: UNIT_END=0x94; INV_SLOT_HEAD=UNIT_END+0xB0.
    for name, slot in [('waist', 5), ('back', 14)]:
        index = 0x144 + slot * 2
        guid = str(fields.get(str(index), 0) | (fields.get(str(index + 1), 0) << 32))
        result[name] = {'guid': guid, 'entry': objects.get(guid, {}).get('entry', 0),
                        'semantics': 'creation zero-default for absent fields'}
    return {'class_id': (state['self']['bytes0'] >> 8) & 255,
            'level': state['self']['level'], 'equipment': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('trace', type=Path)
    args = parser.parse_args()
    print(json.dumps(equipment(json.loads(args.trace.read_text())), indent=2))
