"""Bounded non-offensive cleanup; never infer safety from process termination."""
import time


def finalize(backend, budget=15, sleep=time.sleep, clock=time.monotonic):
    result = {'status': 'unresolved', 'samples': []}
    try:
        result['stop'] = backend.rpc('bridge.stop_attack', {})
    except Exception as exc:
        result['stop_error'] = str(exc)
    deadline = clock() + budget
    while True:
        try:
            observation = backend.observe().to_dict()
        except Exception as exc:
            result['observation_error'] = str(exc)
            return result
        result['samples'].append(observation)
        if observation['ghost'] or observation['health'] <= 0:
            result['reason'] = 'death observed; separate recovery required'
            return result
        if observation['in_combat'] is False:
            try:
                result['logout'] = backend._do_logout({}, 45)
                if result['logout'].get('status') == 'closed':
                    result['status'] = 'closed_after_observed_safety'
            except Exception as exc:
                result['logout_error'] = str(exc)
            return result
        # Passive observation is not a recovery strategy under continuing damage.
        if (len(result['samples']) > 1 and observation['health'] < result['samples'][-2]['health']):
            result['reason'] = 'continuing damage; planned retreat required'
            return result
        if clock() >= deadline:
            result['reason'] = 'combat unresolved within budget'
            return result
        sleep(.25)
