"""Run one bounded task unit over the pure-network backend.

Flow: verify state -> select one target -> approach -> one combat -> loot ->
settle -> logout. Every step is recorded as a contract action with a
structured log line and a checkpoint.
"""
import argparse
import json
import os
import time
from pathlib import Path

from agent_wow_contract import Action, TaskExecutor
from agent_wow_session import PureNetworkBackend, root
from agent_wow_task_boundary import movement_boundary, verify_target
from agent_wow_position import confirm_relogin
from agent_wow_batch import classify
from agent_wow_exit_recovery import close_session


def parse_options(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--combat-entry', type=int, default=3124)
    parser.add_argument('--max-target-level', type=int, default=3)
    parser.add_argument('--quest-item-entry', type=int, default=4862)
    parser.add_argument('--objective-kind', choices=('kill', 'item'), default=None,
                        help='kill counts the quest log, item counts the item '
                             'in the bag; a collecting quest has a quest id too')
    parser.add_argument('--objective-optional', action='store_true',
                        help='the objective counter does not have to advance; '
                             'for a run whose purpose is experience, not progress')
    parser.add_argument('--quest-id', type=int)
    parser.add_argument('--kill-slot', type=int, choices=range(4), default=0)
    parser.add_argument('--objective-total', type=int, default=8)
    parser.add_argument('--combat-budget', type=float, default=35)
    parser.add_argument('--budget-seconds', type=float, default=240)
    parser.add_argument('--action-budget', type=int, default=10)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--movement-only', action='store_true',
                        help='Select and approach one target, then close a safe movement boundary')
    parser.add_argument('--combat-after-verification', action='store_true',
                        help='After boundary verification, recheck and fight the original target once')
    parser.add_argument('--verify-movement-from', type=Path,
                        help='Verify a closed movement-only boundary in a fresh session; no combat')
    parser.add_argument('--boundary-max-age-seconds', type=float, default=1800)
    parser.add_argument('--advisory-record', action='store_true',
                        help='Record the combat recovery state machine decisions into the '
                             'existing evidence without enforcing them')
    parser.add_argument('--enforce-state-machine', action='store_true',
                        help='Let the combat recovery state machine veto further dispatch: '
                             'a low-health or out-of-range exit stops the attack and the '
                             'recovery branch takes over')
    parser.add_argument('--low-health-ratio', type=float, default=None,
                        help='Override the combat recovery state machine low-health ratio '
                             '(default 0.5 of max_health). The default is unreachable '
                             'against the local mobs, which die before the character is '
                             'hurt enough to reach it')
    parser.add_argument('--critical-health-ratio', type=float, default=None,
                        help='Override the combat recovery state machine critical ratio '
                             '(default 0.25 of max_health)')
    options = parser.parse_args(argv)
    if options.objective_total < 1 or (options.quest_id is not None and options.quest_id < 1):
        parser.error('objective total and quest ID must be positive')
    if options.combat_after_verification and not options.verify_movement_from:
        parser.error('combat-after-verification requires verify-movement-from')
    if options.movement_only and options.verify_movement_from:
        parser.error('movement-only and verify-movement-from are mutually exclusive')
    if options.resume and options.verify_movement_from:
        parser.error('resume cannot reopen a consumed movement boundary')
    if options.boundary_max_age_seconds <= 0:
        parser.error('boundary max age must be positive')
    if not options.run_id.replace('_', '').isalnum():
        parser.error('invalid run ID')
    return options


def write_json_atomic(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _supervise_until_safe(backend, report, *, budget_seconds=180, poll_interval=0.5):
    """Keep the session open and polling until the character is safe.

    Used when close() refused because the character was alive and still in
    combat. The server keeps attacking such a character after the client is
    gone, so the session must stay open and supervised until the combat
    clears (or the character dies). Returns a summary of the supervision.
    """
    deadline = time.monotonic() + budget_seconds
    samples = []
    while time.monotonic() < deadline:
        try:
            observation = backend.observe()
        except Exception as exc:  # noqa: BLE001
            return {'cleared': False, 'error': f'{type(exc).__name__}: {exc}',
                    'sample_count': len(samples)}
        samples.append({'health': observation.health, 'ghost': observation.ghost,
                        'in_combat': observation.in_combat})
        if observation.health <= 0 or observation.ghost:
            return {'cleared': True, 'died': True, 'sample_count': len(samples),
                    'final': {'health': observation.health, 'ghost': observation.ghost}}
        if observation.in_combat is not True:
            return {'cleared': True, 'died': False, 'sample_count': len(samples),
                    'final': {'health': observation.health, 'in_combat': observation.in_combat}}
        time.sleep(poll_interval)
    return {'cleared': False, 'died': False, 'sample_count': len(samples),
            'reason': 'still in combat after the supervision budget'}


def run_recovery_branch(backend, run_id, root):
    """Run the recovery branch as its own executed unit.

    It is a separate executor with its own checkpoint, because the main
    plan's checkpoint belongs to the main plan: the recovery never
    re-dispatches the fight, so it cannot violate the rule that a side
    effect whose effect is unknown is never resent.

    The branch is the recovery itself. The logout stays in the existing
    finalize path: a logout closes the session, so the post-action
    observation is unavailable and the executor cannot confirm it - that is
    a property of the logout, not of the recovery.
    """
    from agent_wow_contract import Action, TaskExecutor
    plan = [Action('recover_combat', 'recover_combat', {'budget': 60},
                   timeout=90, side_effecting=True,
                   expectation=lambda before, after, evidence:
                       evidence.get('combat_cleared') is True)]
    executor = TaskExecutor(
        backend, f'{run_id}_recovery',
        log_path=root / f'results/pure_network/agent_wow_task_{run_id}_recovery.actions.jsonl',
        checkpoint_path=root / f'results/pure_network/agent_wow_task_{run_id}_recovery.checkpoint.json',
        budget_seconds=120, action_budget=4)
    outcome = executor.run(plan)
    return {'status': outcome.get('status'),
            'records': outcome.get('records', []),
            'scope': 'independent recovery branch; never re-dispatches the fight'}



def _combat_config(options):
    """The combat recovery state machine's thresholds for this run.

    None means the defaults are used. The ratios are a fraction of the
    character's own max_health, so they scale with the character's level
    instead of drifting with an absolute blood value.
    """
    from agent_wow_combat_state_machine import CombatSMConfig
    if options.low_health_ratio is None and options.critical_health_ratio is None:
        return None
    defaults = CombatSMConfig()
    return CombatSMConfig(
        low_health_ratio=(defaults.low_health_ratio if options.low_health_ratio is None
                          else options.low_health_ratio),
        critical_health_ratio=(defaults.critical_health_ratio
                               if options.critical_health_ratio is None
                               else options.critical_health_ratio))


def main(argv=None):
    options = parse_options(argv)
    from agent_wow_objective import progress, increased
    # The objective kind is explicit: a quest that collects an item still has a
    # quest id, and the item counter lives in the bag, not in the kill
    # counters of the quest log (2026-11-01 quest 794 "Burning Blade
    # Medallion" collects item 4859 and has no kill objective at all).
    kind = options.objective_kind or ('item' if not options.quest_id else 'kill')
    if kind == 'item':
        objective = {'kind': 'item', 'entry': options.quest_item_entry,
                     'total': options.objective_total}
    else:
        objective = {'kind': 'kill', 'quest': options.quest_id,
                     'slot': options.kill_slot, 'entry': options.combat_entry,
                     'total': options.objective_total}
    report_path = root / f'results/pure_network/agent_wow_task_{options.run_id}.json'
    unified_name = f'agent_wow_task_{options.run_id.removesuffix("_verify")}_unified.json'
    if report_path.exists() or (options.verify_movement_from
                                and (report_path.parent / unified_name).exists()):
        raise SystemExit('run ID already archived; choose a new one')
    boundary = None
    if options.verify_movement_from:
        if options.verify_movement_from.with_name(options.verify_movement_from.stem + '.consumed.json').exists():
            raise SystemExit('movement boundary already consumed; use a fresh boundary')
        boundary_report = json.loads(options.verify_movement_from.read_text(encoding='utf-8'))
        boundary = movement_boundary(boundary_report, max_age_seconds=options.boundary_max_age_seconds)
        if boundary['character'] != '9':
            raise SystemExit('backend supports character 9 only')

    def alive(observation):
        return observation.health > 0 and not observation.ghost and observation.in_combat is False

    def eligible(observation):
        count = progress(observation.to_dict(), objective)
        return alive(observation) and isinstance(count, int) and count < objective['total']

    def build_plan():
        if boundary and not options.combat_after_verification:
            return [Action('verify_alive', 'require_alive', precondition=alive,
                           expectation=lambda before, after, evidence: alive(after)),
                    Action('logout', 'logout', timeout=45, side_effecting=True,
                           expectation=lambda before, after, evidence: evidence.get('status') == 'closed')]
        actions = [
            Action('verify_alive', 'require_alive', {}, timeout=10,
                   precondition=eligible,
                   expectation=lambda before, after, evidence: after.health > 0 and not after.ghost),
            Action('select_target', 'select_target',
                   {'entry': options.combat_entry, 'max_level': options.max_target_level}, timeout=15,
                   expectation=lambda before, after, evidence: bool(evidence.get('target'))),
            Action('approach', 'approach', {}, timeout=90, side_effecting=True,
                   expectation=lambda before, after, evidence: bool(evidence.get('reached'))),
            Action('fight', 'fight', {'budget': options.combat_budget}, timeout=options.combat_budget + 30,
                   side_effecting=True,
                   expectation=lambda before, after, evidence: evidence.get('outcome') == 'target_dead'
                   and (options.objective_optional or not options.quest_id
                        or objective['kind'] == 'item'
                        or increased(before.to_dict(), after.to_dict(), objective))),
            Action('loot', 'loot', {}, timeout=30, side_effecting=True,
                    expectation=lambda before, after, evidence: (
                        not any(evidence.get(key) for key in
                                ('take_error', 'observation_error', 'reason', 'release_error',
                                 'loot_open_error'))
                        and evidence.get('released', {}).get('result') is not None
                        and any(push['entry'] == options.quest_item_entry
                                for push in evidence.get('confirmed_item_pushes', [])))
                    and int(after.item_totals.get(str(options.quest_item_entry), 0))
                   > int(before.item_totals.get(str(options.quest_item_entry), 0))),
            Action('settle', 'settle', {}, timeout=90, side_effecting=True,
                   expectation=lambda before, after, evidence: after.in_combat is False),
            Action('logout', 'logout', {}, timeout=45, side_effecting=True,
                   expectation=lambda before, after, evidence: evidence.get('status') == 'closed'),
        ]
        if options.quest_id and objective['kind'] != 'item':
            # A kill objective needs no loot. An ITEM objective does: the
            # quest item only reaches the bag through the loot action, and
            # dropping it here is why a collecting quest (794) never saw its
            # counter move (2026-11-01 q794d).
            actions = [a for a in actions if a.id != 'loot']
        if boundary and options.combat_after_verification:
            # A two-session unit: the movement leg is proven by this fresh
            # login landing on the previous session's submitted endpoint, so
            # the in-session approach action is replaced by a synthesized
            # confirmation record; the verification session itself executes
            # fight, loot, settle and logout. select_target re-points the
            # original boundary target (it may have respawned with a new GUID).
            return actions[:2] + actions[3:]
        if boundary:
            return actions[:1] + actions[3:]
        return actions[:3] if options.movement_only else actions

    plan = build_plan()
    backend = PureNetworkBackend(options.run_id, advisory=options.advisory_record,
                                 enforce=options.enforce_state_machine,
                                 combat_config=_combat_config(options))

    report = {'run_id': options.run_id, 'objective': objective, 'plan': [a.to_dict() for a in plan]}
    started = time.monotonic()
    try:
        backend.open()
        if boundary:
            report['position_verification'] = confirm_relogin(
                boundary['source_session'], boundary['expected_xyz'], boundary['map'],
                {'session_id': backend.session_id, 'self_guid': backend.self_guid,
                 'capture_complete': False, 'packets': backend.packets},
                character=boundary['character'])
            if not report['position_verification']['confirmed']:
                raise ValueError('new login does not confirm submitted endpoint')
            report['verification_login_position'] = backend.state['login']['position_xyzo'][:3]
            consumed_path = options.verify_movement_from.with_name(
                options.verify_movement_from.stem + '.consumed.json')
            write_json_atomic(consumed_path, {
                'source_report': str(options.verify_movement_from),
                'verification_run_id': options.run_id,
                'consumed_at': time.time(),
                'scope': 'one fresh-session verification dispatch; no combat action was implied'})
            if options.combat_after_verification:
                # The synthesized approach record stands in for the movement
                # leg. The original target may have wandered or respawned away
                # from the landing point, so the approach action re-navigates
                # from this session's login before the fight rechecks it; a
                # beyond-melee target must not stop the unit here.
                backend.current_position = backend.state['login']['position_xyzo'][:3]
        executor = TaskExecutor(backend, options.run_id,
                                log_path=root / f'results/pure_network/agent_wow_task_{options.run_id}.actions.jsonl',
                                checkpoint_path=root / f'results/pure_network/agent_wow_task_{options.run_id}.checkpoint.json',
                                budget_seconds=options.budget_seconds, action_budget=options.action_budget)
        report['run'] = executor.run(plan, resume=options.resume)
        if boundary:
            # In a two-session unit the approach motion is proven by this fresh
            # login landing on the previous session's submitted endpoint, not by
            # an in-session self echo (which the server never sends). Synthesize
            # an approach-confirmation record so downstream classification can
            # treat the movement leg as server-confirmed at the session boundary.
            report['run']['records'].append({
                'action_id': 'approach', 'kind': 'approach', 'status': 'success',
                'detail': 'submitted endpoint confirmed by fresh-session login',
                'elapsed_ms': 0,
                'evidence': {'reached': True,
                             'confirmed_by': 'fresh_session_login',
                             'submitted_position': boundary['expected_xyz'],
                             'verification_login_position': report.get('verification_login_position'),
                             'distance': report['position_verification']['distance'],
                             'scope': 'cross-session boundary confirmation'},
                'observation_before': {}, 'observation_after': {}})
        records = report['run'].get('records', [])
        # The synthesized cross-session approach confirmation is evidence, not
        # an executed action; keep it out of the action-id set used by the
        # batch classifier so the seven executed steps stay authoritative.
        action_records = [r for r in records
                          if r.get('evidence', {}).get('confirmed_by') != 'fresh_session_login']
        final = action_records[-1].get('observation_after') if action_records else None
        if final:
            report['final_observation'] = final
        if 'logout' not in executor.completed:
            try:
                report['final_observation'] = backend.observe().to_dict()
            except Exception as exc:  # noqa: BLE001
                report.setdefault('final_observation_error', f'{type(exc).__name__}: {exc}')
            # The recovery is a branch of its own, dispatched as an executed
            # action so the evidence shows it ran. It used to be an implicit
            # part of the teardown, and it used to be a plan step that sat
            # after the fight - which never ran, because a fight that ends
            # with the combat unresolved stops the plan.
            report['recovery_branch'] = run_recovery_branch(
                backend, options.run_id, root)
            records = (report.get('run') or {}).get('records')
            if records is not None:
                records.extend(report['recovery_branch'].get('records', []))
            try:
                # Never leave a living character in combat for the session
                # teardown to decide: an in-combat character whose logout is
                # refused is killed by the server after the client is gone.
                close_session(backend, report=report)
            except Exception as exc:  # noqa: BLE001
                report['finalize'] = {'error': f'{type(exc).__name__}: {exc}'}
        backend.result['status'] = 'PASS' if report['run']['status'] == 'success' else 'INCOMPLETE'
    except Exception as exc:  # noqa: BLE001 - the report must record the failure
        report['error'] = f'{type(exc).__name__}: {exc}'
        if backend.session_id:
            try:
                # Same rule on the failure path: resolve an unresolved combat
                # state before closing, never abandon a living in-combat
                # character to the server.
                close_session(backend, report=report)
            except Exception as close_exc:  # noqa: BLE001
                report['finalize'] = {'error': f'{type(close_exc).__name__}: {close_exc}'}
    finally:
        try:
            # close() refuses to tear down a living character that is still in
            # combat: the server keeps attacking it after the client is gone
            # and it dies there (2026-11-01 cs002, esc003). The refusal is
            # recorded rather than raised past the report.
            backend.close()
        except Exception as exc:  # noqa: BLE001
            report.setdefault('close_error', f'{type(exc).__name__}: {exc}')
            report['close_refused'] = True
            # The session is still open with the character in combat. Leaving
            # it unattended is what kills it, so keep polling until the server
            # clears the combat (or the character dies) and only then close.
            report['supervised_after_refusal'] = _supervise_until_safe(
                backend, report)
            try:
                backend.close()
            except Exception as exc:  # noqa: BLE001
                report.setdefault('close_error_after_supervision',
                                  f'{type(exc).__name__}: {exc}')
    report['elapsed_s'] = round(time.monotonic() - started, 3)
    write_json_atomic(report_path, report)
    if options.verify_movement_from:
        unified_path = report_path.parent / unified_name
        write_json_atomic(unified_path, report)
    classification = classify(report)
    print(json.dumps({'run_id': report['run_id'], 'status': report.get('run', {}).get('status', 'ERROR'),
                      'batch_class': classification[0],
                      'error': report.get('error'),
                      'results': [{'action_id': r['action_id'], 'status': r['status'], 'detail': r['detail']}
                                  for r in report.get('run', {}).get('results', [])],
                      'item_totals': report.get('final_observation', {}).get('item_totals'),
                      'health': report.get('final_observation', {}).get('health'),
                      'in_combat': report.get('final_observation', {}).get('in_combat'),
                      'elapsed_s': report['elapsed_s']}, indent=2))
    return report


if __name__ == '__main__':
    main()
