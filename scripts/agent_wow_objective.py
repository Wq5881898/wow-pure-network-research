"""Explicit objective counters. Missing quest evidence is never a zero count."""


def progress(observation, objective):
    if objective['kind'] == 'item':
        return observation.get('item_totals', {}).get(str(objective['entry']), 0)
    if objective['kind'] != 'kill':
        raise ValueError('unsupported objective kind')
    slot = objective['slot']
    if not isinstance(slot, int) or not 0 <= slot < 4:
        raise ValueError('kill objective slot must be 0..3')
    quests = [q for q in observation.get('quest_log', []) if q.get('quest') == objective['quest']]
    if len(quests) != 1:
        return None
    packed = quests[0].get('counters', [])
    if len(packed) != 2 or not all(isinstance(v, int) and 0 <= v <= 0xffffffff for v in packed):
        return None
    # PLAYER_QUEST_LOG_*_COUNTS stores four uint16 counters in two uint32s.
    return (packed[slot // 2] >> (16 * (slot % 2))) & 0xffff


def increased(before, after, objective):
    start, end = progress(before, objective), progress(after, objective)
    return (isinstance(start, int) and isinstance(end, int)
            and 0 <= start < end <= objective['total'])
