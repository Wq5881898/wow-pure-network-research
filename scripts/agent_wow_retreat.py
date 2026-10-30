"""One-dispatch retreat from an independently verified, fixed staging route.

The journal is created before any RPC and never reopened for dispatch. A bridge
reply only proves submission: no in-session self position echo is available.
"""
import json
import math
import time
from pathlib import Path


def prepare(folder):
    folder = Path(folder)
    chunks = []
    previous = None
    for i in range(1, 7):
        plan = json.loads((folder / f'height_R792_segment{i}_plan.json').read_text(encoding='utf-8'))
        verdict = json.loads((folder / f'height_R792_segment{i}_reconciled.json').read_text(encoding='utf-8'))
        points = plan['points']
        if not verdict.get('confirmed') or not 2 <= len(points) <= 128:
            raise ValueError(f'unverified retreat chunk {i}')
        if any(len(p) != 3 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in p)
               for p in points):
            raise ValueError(f'invalid retreat coordinates {i}')
        if previous is not None and math.dist(previous, points[0]) > .1:
            raise ValueError(f'retreat corridor discontinuity {i}')
        if any(math.hypot(b[0]-a[0], b[1]-a[1]) > 15
               or abs(b[2]-a[2]) > 3 for a, b in zip(points, points[1:])):
            raise ValueError(f'retreat waypoint exceeds bridge segment bound {i}')
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        if not .05 <= length <= 20.1 or abs(length - plan['length']) > .1:
            raise ValueError(f'retreat chunk length mismatch {i}')
        chunks.append(points)
        previous = points[-1]
    return chunks


def execute(backend, chunks, journal, *, clock=time.monotonic,
            limit_seconds=12, minimum_health=50):
    """No attack, no session logout, no repositioning after ambiguous request.

    Caller must keep the session alive and take a fresh independent observation
    after any unresolved result. The same journal path may never dispatch twice.
    This experimental interface is not wired to live combat: the current backend
    does not provide a fresh in-session self position after movement.
    """
    # The mover never receives its own position updates. Do not chain a second
    # chunk from a submitted (not independently observed) first endpoint.
    if len(chunks) != 1 or not 0 < limit_seconds <= 12:
        raise ValueError('one confirmed-start retreat chunk per dispatch')
    if not isinstance(minimum_health, int) or not 1 <= minimum_health <= 108:
        raise ValueError('invalid health floor')
    for i, points in enumerate(chunks):
        if not 2 <= len(points) <= 128:
            raise ValueError('invalid retreat point count')
        if any(len(p) != 3 or not all(isinstance(x, (int, float)) and math.isfinite(x)
                                     for x in p) for p in points):
            raise ValueError('invalid retreat coordinates')
        if i and math.dist(chunks[i-1][-1], points[0]) > .1:
            raise ValueError('retreat corridor discontinuity')
        if any(math.hypot(b[0]-a[0], b[1]-a[1]) > 15
               or abs(b[2]-a[2]) > 3 for a, b in zip(points, points[1:])):
            raise ValueError('retreat waypoint exceeds bridge segment bound')
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        if not .05 <= length <= 20.1:
            raise ValueError('invalid retreat chunk length')
    journal = Path(journal)
    report = {'status': 'unknown', 'chunks': [], 'scope': 'movement submission, not arrival'}
    with journal.open('x', encoding='utf-8') as handle:
        json.dump(report, handle)

    def persist():
        temporary = journal.with_name(journal.name + '.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(journal)

    def sample():
        observation = backend.observe().to_dict()
        if (observation.get('character') != '9' or observation.get('map_id') != 1
                or observation.get('ghost') is not False or not isinstance(observation.get('health'), int)
                or observation['health'] <= 0):
            raise RuntimeError('character/map/life cannot be confirmed')
        return observation

    try:
        current = sample()
        report['initial'] = current
        position_evidence = current.get('context', {}).get('position_evidence', {})
        if (current.get('backend') != 'pure-network'
                or position_evidence.get('current_position_confirmed') is not True
                or position_evidence.get('source') != 'fresh_server_self_movement'
                or position_evidence.get('session_id') != current.get('session_id')
                or position_evidence.get('movement_sequence', 0) <= position_evidence.get('login_sequence', 0)
                or math.dist(current['position'], chunks[0][0]) > .1
                or current['health'] < minimum_health
                or current.get('in_combat') is not True):
            report['status'] = 'precondition_failed'
            return report
        report['stop_dispatched'] = True
        persist()
        stop = backend.rpc('bridge.stop_attack', {})
        report['stop'] = stop
        if 'error' in stop:
            report['status'] = 'stop_unknown'
            return report
        deadline = clock() + limit_seconds
        for i, points in enumerate(chunks, 1):
            current = sample()
            fresh = current.get('context', {}).get('position_evidence', {})
            if (current['health'] < minimum_health or current['in_combat'] is not True
                    or fresh.get('current_position_confirmed') is not True
                    or fresh.get('source') != 'fresh_server_self_movement'
                    or fresh.get('session_id') != current.get('session_id')
                    or fresh.get('movement_sequence', 0) <= fresh.get('login_sequence', 0)
                    or math.dist(current['position'], points[0]) > .1):
                report['status'] = 'state_changed'
                return report
            if clock() >= deadline:
                report['status'] = 'budget_exhausted'
                return report
            # Persist BEFORE the potentially side-effecting request. The
            # receiver cannot safely resend if the reply never arrives.
            entry = {'index': i, 'status': 'unknown', 'expected': points[-1]}
            report['chunks'].append(entry)
            persist()
            reply = backend.rpc('bridge.follow_path', {
                'points': [dict(zip(('x', 'y', 'z'), p)) for p in points], 'run': True})
            entry['reply'] = reply
            if 'error' in reply:
                entry['status'] = 'unknown'
                report['status'] = 'movement_unknown'
                return report
            submitted = reply.get('result', {}).get('submittedPosition')
            if not submitted or math.dist(submitted[:3], points[-1]) > .1:
                report['status'] = 'submission_unknown'
                return report
            entry['status'] = 'submitted_unconfirmed'
            persist()
            if clock() >= deadline:
                report['status'] = 'budget_exhausted_after_submission'
                return report
        final = sample()
        report['final_observation'] = final
        report['status'] = ('await_fresh_login' if final['in_combat'] is False
                            else 'combat_unresolved')
        return report
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        return report
    finally:
        persist()
