"""Re-derive quest789 item transition from raw packets, not report status."""
import json
from pathlib import Path
from agent_wow_worldstate import IncrementalWorld

FOLDER = Path(__file__).resolve().parents[1] / 'results/pure_network'


def inspect(name):
    trace = json.loads((FOLDER / f'agent_wow_bridge_{name}.packets.json').read_bytes())
    world = IncrementalWorld(trace['session_id'], trace['self_guid'])
    changes = []
    previous = None
    for packet in trace['packets']:
        state = world.apply([packet])
        me = state.get('self')
        if not me:
            continue
        totals = {}
        for obj in state['visible_objects']:
            if obj.get('type_id') in (1, 2) and obj.get('owner_guid') == trace['self_guid']:
                key = str(obj['entry'])
                totals[key] = totals.get(key, 0) + obj['stack_count']
        snapshot = {'tails': totals.get('4862', 0), 'belt': totals.get('4919', 0),
                    'quests': [q['quest'] for q in me.get('quest_log', [])]}
        if snapshot != previous:
            changes.append({'sequence': packet['sequence'], **snapshot})
            previous = snapshot
    return {'session': trace['session_id'], 'changes': changes,
            'final': {k: me.get(k) for k in ('health', 'max_health', 'ghost', 'in_combat')},
            'errors': state['errors']}


def main():
    turnin = inspect('run120_789_belt_reward')
    relog = inspect('run121_789_delivered_verify')
    before = any(s['tails'] == 8 and 789 in s['quests'] for s in turnin['changes'])
    after = turnin['changes'][-1]
    persisted = relog['changes'][-1]
    checks = {'eight_tails_and_quest_before': before,
              'reward_and_consumption': after['tails'] == 0 and after['belt'] == 1 and 789 not in after['quests'],
              'fresh_session': turnin['session'] != relog['session'],
              'persisted_reward': persisted['tails'] == 0 and persisted['belt'] == 1 and 789 not in persisted['quests'],
              'safe_relogin': relog['final']['health'] > 0 and relog['final']['ghost'] is False and relog['final']['in_combat'] is False,
              'no_decode_errors': not turnin['errors'] and not relog['errors']}
    result = {'checks': checks, 'turnin': turnin, 'relogin': relog}
    output = FOLDER / 'recovery_789_chain_20261029.json'
    with output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps(result, indent=2))
    raise SystemExit(not all(checks.values()))


if __name__ == '__main__':
    main()
