"""Repair explicit equipped item GUIDs at Huklah; preserve before/after evidence."""
import base64
import json
import math
import struct
import sys
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session
from sell_gray_once import money


def snapshot(state):
    fields = {}
    objects = {}
    created = set()
    for event in state['event_log']:
        guid = event.get('guid')
        if guid == '9':
            fields.update(event.get('fields', {}))
        if guid:
            if event.get('kind') in (2, 3):
                objects[guid] = {}
                created.add(guid)
            objects.setdefault(guid, {}).update(event.get('fields', {}))
    equipped = []
    for slot in range(19):
        index = 324 + 2 * slot
        guid = str(fields.get(str(index), 0) | (fields.get(str(index+1), 0) << 32))
        if guid == '0':
            continue
        item = objects.get(guid, {})
        equipped.append({'slot': slot, 'guid': guid, 'entry': item.get('3'),
                         'durability': item.get('60', 0), 'max_durability': item.get('61', 0),
                          'current_transmitted': '60' in item,
                          'current_known': '60' in item or guid in created,
                          'current_source': ('transmitted' if '60' in item else
                                             'creation_zero_default' if guid in created else 'unknown'),
                         'maximum_transmitted': '61' in item})
    return {'money': money(state), 'equipped': equipped,
            'self': {k: state['self'].get(k) for k in ('health', 'max_health', 'level', 'ghost', 'in_combat')},
            'login': state['login']}


def main():
    run = sys.argv[1]
    repair = '--repair' in sys.argv[2:]
    vendor_entry = 3160
    if '--vendor-entry' in sys.argv:
        vendor_entry = int(sys.argv[sys.argv.index('--vendor-entry')+1])
    path = Path(__file__).resolve().parents[1] / f'results/pure_network/repair_{run}.json'
    if path.exists():
        raise SystemExit('report exists')
    backend = PureNetworkBackend(run)
    report = {'run_id': run, 'status': 'unknown', 'repairs': []}
    def save(data=None):
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        tmp.replace(path)
    try:
        backend.open()
        state = backend.drain()
        report['before'] = snapshot(state)
        me = state['self']
        if me['in_combat'] is not False or me['ghost'] or me['health'] <= 0:
            raise RuntimeError('unsafe initial state')
        if repair:
            vendors = [o for o in state['visible_objects'] if o.get('entry') == vendor_entry]
            if len(vendors) != 1:
                raise RuntimeError('repair vendor not uniquely visible')
            vendor = vendors[0]
            if math.dist(state['login']['position_xyzo'][:3], vendor['movement']['position_xyzo'][:3]) > 5:
                raise RuntimeError('outside repair interaction range')
            report['vendor_guid'] = vendor['guid']
            baseline = backend.cursor
            backend.rpc('bridge.vendor_inventory', {'guid': vendor['guid']})
            time.sleep(.4)
            state = backend.drain()
            responses = [e for e in state['event_log'] if e.get('opcode') == 0x19f and e['sequence'] > baseline]
            if not responses or str(struct.unpack_from('<Q', base64.b64decode(responses[-1]['payload_base64']))[0]) != vendor['guid']:
                raise RuntimeError('vendor response not confirmed')
            damaged = [i for i in report['before']['equipped']
                       if i['maximum_transmitted'] and 0 <= i['durability'] < i['max_durability']]
            for item in damaged:
                current = backend.observe()
                if current.in_combat is not False or current.ghost or current.health <= 0:
                    raise RuntimeError('unsafe before repair')
                row = {'item': item, 'money_before': money(backend.state), 'dispatched': True}
                report['repairs'].append(row)
                save()
                row['reply'] = backend.rpc('bridge.repair_item', {'vendorGuid': vendor['guid'], 'itemGuid': item['guid']})
                if 'error' in row['reply']:
                    raise RuntimeError('repair RPC rejected')
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    time.sleep(.2)
                    state = backend.drain()
                    after = snapshot(state)
                    observed = next((i for i in after['equipped'] if i['guid'] == item['guid']), None)
                    if observed and observed['current_transmitted'] and observed['durability'] == item['max_durability']:
                        row.update(confirmed=True, after=observed, money_after=after['money'],
                                   cost=row['money_before'] - after['money'])
                        break
                else:
                    raise RuntimeError('repair unconfirmed; no retry sent')
                save()
            report['status'] = 'repairs_verified' if damaged else 'no_damaged_equipment'
        else:
            report['status'] = 'observed'
        report['after'] = snapshot(backend.drain())
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()


if __name__ == '__main__':
    main()
