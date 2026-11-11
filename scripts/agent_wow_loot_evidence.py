"""Identify item-push replies strictly after the corresponding take request."""


def loot_confirmed(before, after, evidence, required_entry=None):
    """Require actual matching inventory gain, not an unrelated default item."""
    if any(evidence.get(key) for key in ('take_error', 'observation_error', 'reason',
                                       'release_error', 'loot_open_error')):
        return False
    if (evidence.get('released') or {}).get('result') is None:
        return False
    attempted = set(evidence.get('attempted_take_entries') or [])
    for push in evidence.get('confirmed_item_pushes') or []:
        entry = push.get('entry')
        if entry not in attempted or (required_entry is not None and entry != required_entry):
            continue
        start = before.get('item_totals', {}).get(str(entry), 0)
        end = after.get('item_totals', {}).get(str(entry), 0)
        if (type(start) is int and type(end) is int and type(push.get('count')) is int
                and push['count'] > 0 and end >= start + push['count']):
            return True
    return False


def item_pushes(state, after_sequence, player_guid, item_entry, before_inventory_count=None):
    return [
        {'sequence': event['sequence'], **event['loot']}
        for event in state['event_log']
        if (event.get('opcode') == 0x166
            and type(event.get('sequence')) is int
            and event['sequence'] > after_sequence
            and isinstance(event.get('loot'), dict)
            and event['loot'].get('player_guid') == player_guid
            and event['loot'].get('entry') == item_entry
            # PlayerStorage.cpp:4821: 0 means looted, 1 means NPC reward.
            and event['loot'].get('received') == 0
            and event['loot'].get('created') == 0
            and type(event['loot'].get('count')) is int
            and event['loot']['count'] > 0
            and type(event['loot'].get('inventory_count')) is int
            and event['loot']['inventory_count'] >= event['loot']['count']
            and (before_inventory_count is None
                 or event['loot']['inventory_count'] >= before_inventory_count + event['loot']['count']))
    ]
