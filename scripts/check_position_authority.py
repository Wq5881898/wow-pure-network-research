"""Is the character's own position ever confirmed by the server mid-session?

The bridge treats a server reported position as the only authority and falls
back to the locally modelled path endpoint when the server has not reported
one (probes/AgentWowBridge/actions.go, FollowPath). Whether that fallback is
ever used is not a matter of opinion: the archived captures contain the
server's answers.

This script reads the archived packet captures and reports, per session:

  * how many times SMSG_LOGIN_VERIFY_WORLD (0x236) arrived, and whether any
    arrived after the session started;
  * whether MSG_MOVE_TELEPORT (0x0C5) ever arrived;
  * how many server object updates for the character's own guid carried a
    movement payload after the initial create.

The third one is the important one: SMSG_UPDATE_OBJECT does reach the
character, but only its field updates (health, combat flag) and never a new
position. If that holds, the character's position during a session is a model
of where it was sent, never a measurement of where it is.

Read-only. It never connects to anything and never touches the game.

  python scripts/check_position_authority.py [results/pure_network]
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_wow_worldstate import replay  # noqa: E402

SMSG_LOGIN_VERIFY_WORLD = 0x236
MSG_MOVE_TELEPORT = 0x0C5


def scan(folder):
    rows = []
    opcodes = Counter()
    for path in sorted(Path(folder).glob('agent_wow_bridge_*.packets.json')):
        try:
            capture = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        packets = capture.get('packets') or []
        if not packets:
            continue
        codes = [item['opcode'] for item in packets]
        for code in codes:
            opcodes[code] += 1
        login_world = codes.count(SMSG_LOGIN_VERIFY_WORLD)
        first_login = codes.index(SMSG_LOGIN_VERIFY_WORLD) if login_world else -1
        later_login = sum(1 for index, code in enumerate(codes)
                          if code == SMSG_LOGIN_VERIFY_WORLD and index != first_login)
        row = {
            'capture': path.name,
            'packets': len(packets),
            'login_verify_world': login_world,
            'login_verify_world_after_start': later_login,
            'move_teleport': codes.count(MSG_MOVE_TELEPORT),
            'self_movement_updates': None,
            'self_field_only_updates': None,
        }
        try:
            state = replay(capture)
        except Exception as exc:  # noqa: BLE001 - a broken capture is a finding
            row['decode_error'] = '%s: %s' % (type(exc).__name__, exc)
            rows.append(row)
            continue
        guid = str(capture.get('self_guid'))
        if state.get('errors'):
            row['decode_error'] = 'decoder reported %d packet errors' % len(state['errors'])
        row['capture_complete'] = capture.get('capture_complete') is True
        with_movement = 0
        without = 0
        field_only = 0
        for event in state['event_log']:
            if str(event.get('guid')) != guid:
                continue
            kind = event.get('kind')
            if kind in (1, 2, 3):
                # These updates carry a movement payload; kind 0 is a field
                # only delta and has no position in it at all.
                if event.get('movement'):
                    with_movement += 1
                else:
                    without += 1
            elif kind == 0:
                field_only += 1
        row['self_movement_updates'] = with_movement
        row['self_field_only_updates'] = without
        row['self_field_delta_updates'] = field_only
        rows.append(row)
    return rows, opcodes


def main(argv):
    folder = argv[1] if len(argv) > 1 else str(
        Path(__file__).resolve().parents[1] / 'results/pure_network')
    rows, opcodes = scan(folder)
    if not rows:
        print('no archived captures found in', folder)
        return 1
    sessions = len(rows)
    later_login = sum(row['login_verify_world_after_start'] for row in rows)
    teleports = sum(row['move_teleport'] for row in rows)
    movement_updates = sum(row['self_movement_updates'] or 0 for row in rows)
    field_updates = sum(row['self_field_only_updates'] or 0 for row in rows)
    field_deltas = sum(row.get('self_field_delta_updates') or 0 for row in rows)
    broken = [row for row in rows if 'decode_error' in row]

    print('archived captures scanned: %d' % sessions)
    print('SMSG_LOGIN_VERIFY_WORLD (0x236) total      : %d'
          % sum(row['login_verify_world'] for row in rows))
    print('  arriving after the session started        : %d' % later_login)
    print('MSG_MOVE_TELEPORT (0x0C5) total            : %d' % teleports)
    print('self updates carrying a movement payload   : %d' % movement_updates)
    print('self updates carrying movement but empty   : %d' % field_updates)
    print('self field-only deltas (no position at all): %d' % field_deltas)
    print('captures that failed to decode              : %d' % len(broken))
    print('most frequent opcodes in the corpus         : %s'
          % ', '.join('%s=%d' % (hex(code), count)
                      for code, count in opcodes.most_common(6)))
    if broken:
        for row in broken[:5]:
            print('  decode error %s: %s' % (row['capture'], row['decode_error']))

    with_movement = sum(1 for row in rows
                        if (row['self_movement_updates'] or 0) > 1)
    print('')
    if broken:
        print('VERDICT: incomplete analysis; packet decode errors prevent a negative finding.')
        return 2
    if later_login == 0 and teleports == 0 and with_movement == 0:
        print('VERDICT: no additional position payload was found by these checks '
              'in the captured portions. This is not proof of a protocol-wide '
              'impossibility or of complete session coverage.')
        print('  submitted_position remains a local model unless independently '
              'confirmed. Captures may include duplicate checkpoints.')
        return 0
    print('VERDICT: candidate position payload found; inspect its GUID, timing '
          'and decode before claiming a live self-position confirmation.')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main(sys.argv))
