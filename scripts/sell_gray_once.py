"""Sell explicitly verified grey junk to Duokna; verify every transaction."""
import base64
import json
import math
import struct
import sys
import time
from pathlib import Path
from agent_wow_session import PureNetworkBackend
from agent_wow_exit_recovery import finish_session

# Read-only item_template verification: quality=0, class=15, positive SellPrice.
PRICES = {4865: 5, 4867: 8, 7073: 6, 7074: 4, 7098: 6, 7099: 6, 7101: 5}
ROOT = Path(__file__).resolve().parents[1]


def money(state):
    fields = {}
    for event in state['event_log']:
        if event.get('guid') == '9':
            fields.update(event.get('fields', {}))
    # PLAYER_FIELD_COINAGE = UNIT_END(148) + 0x3fe = 1170.
    return fields.get('1170', 0)


def backpack_guids(state):
    fields = {}
    for event in state['event_log']:
        if event.get('guid') == '9':
            fields.update(event.get('fields', {}))
    # 16 backpack slots, GUID pairs, excluding equipped and vendor buyback.
    return {str(fields.get(str(i), 0) | (fields.get(str(i + 1), 0) << 32))
            for i in range(370, 402, 2)} - {'0'}


def main():
    run = sys.argv[1]
    prices = dict(PRICES)
    if '--retired-gear' in sys.argv:
        prices.update({140: 1, 3363: 1, 4919: 5, 1378: 1, 1380: 4, 139: 1, 1369: 4, 4921: 12})
    path = ROOT / f'results/pure_network/vendor_sale_{run}.json'
    if path.exists():
        raise SystemExit('report already exists')
    backend = PureNetworkBackend(run)
    report = {'run_id': run, 'status': 'unknown', 'sales': []}
    def save(data=None):
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(report, indent=2), encoding='utf-8')
        tmp.replace(path)
    try:
        backend.open()
        state = backend.drain()
        me = state['self']
        if me.get('quest_log'):
            raise RuntimeError('active quests present; recheck junk whitelist before sale')
        if me['health'] <= 0 or me['ghost'] or me['in_combat'] is not False:
            raise RuntimeError('not confirmed alive and out of combat')
        vendor_entry = int(sys.argv[2]) if len(sys.argv) > 2 else 3158
        vendors = [o for o in state['visible_objects'] if o.get('entry') == vendor_entry]
        if len(vendors) != 1:
            raise RuntimeError('selected vendor not uniquely visible')
        vendor = vendors[0]
        here = state['login']['position_xyzo'][:3]
        location = vendor['movement']['position_xyzo'][:3]
        report.update(vendor_guid=vendor['guid'], vendor_entry=vendor_entry,
                      login_position=here, vendor_position=location,
                      distance=math.dist(here, location), money_before=money(state))
        report['coinage_baseline_scope'] = ('transmitted coinage when present; otherwise '
                                           'creation zero-default, not an explicit zero transmission')
        if report['distance'] > 5:
            raise RuntimeError('not within vendor interaction distance')
        cursor = backend.cursor
        reply = backend.rpc('bridge.vendor_inventory', {'guid': vendor['guid']})
        if 'error' in reply:
            raise RuntimeError('vendor inventory request rejected')
        time.sleep(.5)
        state = backend.drain()
        responses = [e for e in state['event_log'] if e.get('opcode') == 0x19f
                     and e['sequence'] > cursor]
        if not responses:
            raise RuntimeError('no vendor inventory response')
        payload = base64.b64decode(responses[-1]['payload_base64'])
        if len(payload) < 9 or str(struct.unpack_from('<Q', payload)[0]) != vendor['guid']:
            raise RuntimeError('vendor response identity mismatch')
        report['vendor_response'] = responses[-1]
        items = [dict(o) for o in state['visible_objects'] if o.get('owner_guid') == '9'
                 and o['guid'] in backpack_guids(state)
                 and o.get('entry') in prices and o.get('stack_transmitted')
                 and o.get('stack_count', 0) > 0]
        report['candidate_stacks'] = len(items)
        if money(state) is None:
            raise RuntimeError('coinage never transmitted')
        save()
        for item in items:
            obs = backend.observe()
            if obs.in_combat is not False or obs.health <= 0:
                raise RuntimeError('combat or death before sale')
            before = money(backend.state)
            count, entry = item['stack_count'], item['entry']
            row = {'item_guid': item['guid'], 'entry': entry, 'count': count,
                   'price_each': prices[entry], 'money_before': before, 'dispatched': True}
            report['sales'].append(row)
            save()  # Persist intent before sending; never blindly retry a sale.
            row['reply'] = backend.rpc('bridge.sell_item', {
                'vendorGuid': vendor['guid'], 'itemGuid': item['guid'], 'count': count})
            if 'error' in row['reply']:
                raise RuntimeError('sale RPC rejected')
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                time.sleep(.2)
                state = backend.drain()
                remaining = count if item['guid'] in backpack_guids(state) else 0
                after = money(state)
                if remaining == 0 and after == before + count * prices[entry]:
                    row.update(confirmed=True, remaining=remaining, money_after=after)
                    break
            else:
                row.update(confirmed=False, remaining=remaining, money_after=after)
                raise RuntimeError('sale postcondition unknown; stopped without retry')
            save()
        report.update(status='sales_verified', money_after=money(backend.state))
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        finish_session(backend, report=report, hold_ownership=True,
                       finish_budget_seconds=0, checkpoint=save)
        save()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
