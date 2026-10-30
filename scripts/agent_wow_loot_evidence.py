"""Identify item-push replies strictly after the corresponding take request."""


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
