"""Server MSG_MOVE_TELEPORT_ACK layout from local Player/Unit.cpp.

Carries server movement data, not proof of completed client teleport handshake.
"""
import math
from agent_wow_worldstate import Reader, DecodeError


def decode_teleport(payload):
    r = Reader(payload)
    guid, counter = r.guid(), r.u32()
    flags, extra, clock = r.u32(), r.u16(), r.u32()
    position = r.floats(4)
    if not all(math.isfinite(v) for v in position):
        raise DecodeError('nonfinite teleport position')
    if flags & 0x200:
        r.guid()
        r.take(16 + 4 + 1)
        if extra & 0x400:
            r.take(4)
    if flags & 0x2200000 or extra & 0x20:
        r.take(4)
    r.take(4)
    if flags & 0x1000:
        r.take(16)
    if flags & 0x4000000:
        r.take(4)
    r.end()
    return {'guid': guid, 'counter': counter, 'movement_flags': flags,
            'movement_clock': clock, 'position_xyzo': position,
            'scope': 'server teleport instruction; handshake completion not established'}
