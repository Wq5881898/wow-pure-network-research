"""Turn a quest in at its quest giver, once, with the evidence recorded.

The turn-in is two server steps (from the local AzerothCore sources):

  CMSG_QUESTGIVER_COMPLETE_QUEST  (0x18A)  guid + questId
      -> the server completes the quest and offers the reward
  CMSG_QUESTGIVER_CHOOSE_REWARD   (0x18E)  guid + questId + rewardIndex
      -> only needed when the quest has more than one reward choice

The bridge already implements both (probes/AgentWowBridge/quest.go), with the
payload shapes the server's handlers read
(QuestHandler.cpp: HandleQuestgiverCompleteQuestOpcode reads guid + questId;
HandleQuestgiverChooseRewardOpcode reads guid + questId + reward).

The script refuses to act unless the character is alive, out of combat, at full
health, and the giver is uniquely in reach, and it records what it observed on
both sides of the request.

  python scripts/turn_in_quest.py --quest 794 --giver-entry 3145
"""
import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from agent_wow_session import PureNetworkBackend

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / 'results/pure_network'
REACH = 4.0


def quest_state(observation, quest):
    for entry in observation.get('quest_log') or []:
        if entry.get('quest') == quest:
            return entry
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quest', type=int, required=True)
    parser.add_argument('--giver-entry', type=int, required=True)
    parser.add_argument('--run-id', default=None)
    options = parser.parse_args()
    quest = options.quest
    run_id = options.run_id or 'quest%d_turn_in' % quest
    out = FOLDER / ('%s.json' % run_id)

    report = {'quest': quest, 'giver_entry': options.giver_entry,
              'run_id': run_id, 'status': 'attempt_started',
              'request_dispatched': False}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    backend = PureNetworkBackend(run_id)
    try:
        active = subprocess.run(['pgrep', '-x', 'agent-wow-cli'], capture_output=True)
        if active.returncode != 1:
            raise RuntimeError('active session or failed process check')
        backend.open()
        before = backend.observe().to_dict()
        report['before'] = before
        if before['ghost'] or before['in_combat'] is not False or before['health'] != before['max_health']:
            raise RuntimeError('unsafe character state')
        entry = quest_state(before, quest)
        report['quest_before'] = entry
        if entry is None:
            report['status'] = 'not_in_log'
        elif entry.get('state') != 1:
            report['status'] = 'quest_not_complete'
            raise RuntimeError('quest %d is not complete (state=%s)'
                                % (quest, entry.get('state')))
        else:
            candidates = [obj for obj in backend.state['visible_objects']
                          if obj.get('entry') == options.giver_entry
                          and math.dist(before['position'],
                                        obj['movement']['position_xyzo'][:3]) <= REACH]
            if len(candidates) != 1:
                raise RuntimeError('no unique nearby questgiver')
            report['npc'] = candidates[0]['guid']
            report['request_dispatched'] = True
            report['status'] = 'unknown'
            out.write_text(json.dumps(report, indent=2), encoding='utf-8')
            report['complete_reply'] = backend.rpc(
                'bridge.complete_quest', {'guid': report['npc'], 'quest': quest})
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                time.sleep(.5)
                now = backend.observe().to_dict()
                report['after'] = now
                if quest_state(now, quest) is None or quest_state(now, quest).get('state') != 1:
                    report['status'] = 'turned_in'
                    break
            if report['status'] == 'unknown':
                report['reward_reply'] = backend.rpc(
                    'bridge.reward_quest', {'guid': report['npc'], 'quest': quest})
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    time.sleep(.5)
                    now = backend.observe().to_dict()
                    report['after'] = now
                    if quest_state(now, quest) is None or quest_state(now, quest).get('state') != 1:
                        report['status'] = 'turned_in_after_reward'
                        break
                if report['status'] == 'unknown':
                    report['status'] = 'still_listed'
        final = backend.observe().to_dict()
        report['final'] = final
        report['quest_final'] = quest_state(final, quest)
        if final['health'] > 0 and not final['ghost'] and final['in_combat'] is False:
            report['logout'] = backend._do_logout({}, 45)
    except Exception as exc:  # noqa: BLE001 - the report must survive any failure
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        backend.close()
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report.get(k) for k in
                      ('status', 'quest_before', 'quest_final', 'error')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
