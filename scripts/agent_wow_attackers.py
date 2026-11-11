"""Identify current attackers from this session's directed attack events."""


def active_attackers(state, player_guid):
    active = set()
    for event in state.get('event_log', []):
        attack = event.get('attack') or {}
        attacker = attack.get('attacker_guid')
        if not attacker or attacker == player_guid:
            continue
        if event.get('kind') == 'attack_stop':
            active.discard(attacker)
        elif (event.get('kind') in ('attack_start', 'attacker_state')
              and attack.get('target_guid') == player_guid):
            active.add(attacker)
        elif event.get('kind') == 'attack_start':
            active.discard(attacker)
    return [o for o in state.get('visible_objects', [])
            if o.get('guid') in active and o.get('type_id') == 3
            and o.get('health', 0) > 0]


def bind_single_attacker(backend):
    """Bind only a uniquely evidenced attacker; never guess from proximity."""
    state = backend.drain()
    if (state.get('self') or {}).get('in_combat') is not True:
        return False
    attackers = active_attackers(state, backend.self_guid)
    if len(attackers) != 1:
        return False
    backend.target = attackers[0]
    backend._escape_route = None
    backend._escape_route_target = None
    return True


def planned_attacker(state, player_guid, entry, minimum, maximum, health_ratio=.7):
    me = state.get('self') or {}
    if (me.get('in_combat') is not True or me.get('ghost')
            or not me.get('max_health')
            or me.get('health', 0) < me['max_health'] * health_ratio):
        return None
    attackers = active_attackers(state, player_guid)
    if len(attackers) != 1:
        return None
    target = attackers[0]
    return target if (target.get('entry') == entry
                      and minimum <= target.get('level', 0) <= maximum) else None
