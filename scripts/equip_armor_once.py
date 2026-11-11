"""Equip five explicitly approved inventory upgrades; no purchasing or weapons."""
import json
import sys
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session
from repair_equipped_once import snapshot
from sell_gray_once import backpack_guids

UPGRADES = [(1364, 4), (4921, 6), (1369, 5), (4915, 7), (2652, 14)]


def main():
    run = sys.argv[1]
    verify = '--verify' in sys.argv[2:]
    upgrades = [(1368, 9), (2649, 5), (2651, 8), (2654, 6)] if '--level8-kit' in sys.argv[2:] else UPGRADES
    path = Path(__file__).resolve().parents[1] / f'results/pure_network/equip_{run}.json'
    if path.exists():
        raise SystemExit('report exists')
    backend = PureNetworkBackend(run)
    report = {'run_id': run, 'status': 'unknown', 'equips': []}
    def save(data=None):
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        tmp.replace(path)
    try:
        backend.open()
        state = backend.drain()
        report['before'] = snapshot(state)
        expected = []
        for entry, slot in upgrades:
            if verify:
                matches = [i for i in report['before']['equipped'] if i['slot'] == slot and i['entry'] == entry]
                if len(matches) != 1:
                    raise RuntimeError('verification equipment mismatch')
                continue
            obs = backend.observe()
            if obs.in_combat is not False or obs.ghost or obs.health <= 0:
                raise RuntimeError('unsafe equipment change state')
            state = backend.state
            items = [o for o in state['visible_objects'] if o.get('entry') == entry
                     and o.get('owner_guid') == '9' and o['guid'] in backpack_guids(state)]
            if len(items) != 1:
                raise RuntimeError('approved item not uniquely in backpack: %d' % entry)
            item = items[0]
            row = {'entry': entry, 'slot': slot, 'item_guid': item['guid'], 'dispatched': True}
            report['equips'].append(row)
            save()
            row['reply'] = backend.rpc('bridge.equip_item', {'itemGuid': item['guid'], 'slot': slot})
            if 'error' in row['reply']:
                raise RuntimeError('equip RPC rejected')
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                time.sleep(.2)
                state = backend.drain()
                equipped = snapshot(state)['equipped']
                if any(i['slot'] == slot and i['guid'] == item['guid'] for i in equipped):
                    row['confirmed'] = True
                    break
            else:
                raise RuntimeError('equip not confirmed; no retry')
            save()
        report['after'] = snapshot(backend.drain())
        report['status'] = 'verified_by_login' if verify else 'equipped_verified'
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()


if __name__ == '__main__':
    main()
