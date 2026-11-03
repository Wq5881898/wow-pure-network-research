"""Pure combat recovery state machine: no I/O, no clock, no sleep.

Every decision input is an explicit parameter and every tick carries its own
sampled time (the caller injects both time and observations). Thresholds are
expressed as ratios of max_health because the local character's max_health
drifts with level (70/89/108 observed across archived traces); a bare
absolute-health comparison would silently shift meaning between levels.

Truth rules baked in here (verified against archived traces):
- A health value the server never transmitted is unknown, not zero, so an
  untransmitted health must never read as dead or low (see health_transmitted
  in agent_wow_worldstate.summarize).
- The self position is never broadcast during combat, so an in-combat
  distance is not a confirmed distance; unconfirmed range must not be
  reported as "the target fled".
- AttackStop proves nothing about aggro or combat end (docs/34); leaving
  combat requires consecutive confirmed samples plus a minimum dwell.
"""
from dataclasses import dataclass, field

# States (exact strings required by the task contract).
PRECHECK = 'precheck'
ENGAGING = 'engaging'
IN_COMBAT = 'in_combat'
LOW_HEALTH_EXIT = 'low_health_exit'
TARGET_OUT_OF_RANGE = 'target_out_of_range'
DISENGAGING = 'disengaging'
DISENGAGED_CONFIRMED = 'disengaged_confirmed'
DEAD = 'dead'
GHOST = 'ghost'
UNRESOLVED = 'unresolved'

STATES = (PRECHECK, ENGAGING, IN_COMBAT, LOW_HEALTH_EXIT, TARGET_OUT_OF_RANGE,
          DISENGAGING, DISENGAGED_CONFIRMED, DEAD, GHOST, UNRESOLVED)
TERMINAL_STATES = (DISENGAGED_CONFIRMED, DEAD, GHOST, UNRESOLVED)

# Decisions (exact strings required by the task contract).
DECISION_NONE = 'none'
DECISION_START_ATTACK = 'start_attack'
DECISION_CONTINUE_ATTACK = 'continue_attack'
DECISION_STOP_ATTACK = 'stop_attack'
DECISION_REQUEST_ESCAPE = 'request_escape'
DECISION_HOLD_AND_OBSERVE = 'hold_and_observe'
DECISION_ABORT_UNIT = 'abort_unit'

DECISIONS = (DECISION_NONE, DECISION_START_ATTACK, DECISION_CONTINUE_ATTACK,
             DECISION_STOP_ATTACK, DECISION_REQUEST_ESCAPE,
             DECISION_HOLD_AND_OBSERVE, DECISION_ABORT_UNIT)

# Reason codes (exact strings required by the task contract).
REASON_HEALTH_UNKNOWN = 'health_unknown'
REASON_HEALTH_RATIO_BELOW_LOW = 'health_ratio_below_low'
REASON_HEALTH_RATIO_BELOW_CRITICAL = 'health_ratio_below_critical'
REASON_SELF_DEAD = 'self_dead'
REASON_SELF_GHOST = 'self_ghost'
REASON_TARGET_DEAD = 'target_dead'
REASON_TARGET_MISSING = 'target_missing'
REASON_TARGET_OUT_OF_RANGE_CONFIRMED = 'target_out_of_range_confirmed'
REASON_TARGET_DISTANCE_UNCONFIRMED = 'target_distance_unconfirmed'
REASON_OUT_OF_COMBAT_CONFIRMED = 'out_of_combat_confirmed'
REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED = 'out_of_combat_not_yet_confirmed'
REASON_BUDGET_EXHAUSTED = 'budget_exhausted'

REASONS = (REASON_HEALTH_UNKNOWN, REASON_HEALTH_RATIO_BELOW_LOW,
           REASON_HEALTH_RATIO_BELOW_CRITICAL, REASON_SELF_DEAD,
           REASON_SELF_GHOST, REASON_TARGET_DEAD, REASON_TARGET_MISSING,
           REASON_TARGET_OUT_OF_RANGE_CONFIRMED, REASON_TARGET_DISTANCE_UNCONFIRMED,
           REASON_OUT_OF_COMBAT_CONFIRMED, REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,
           REASON_BUDGET_EXHAUSTED)

# Position sources that are server-directed motion rather than a broadcast
# self position; a distance built from them is never a confirmed distance.
UNCONFIRMED_POSITION_SOURCES = frozenset({
    'last object update', 'server stop', 'unsupported motion; start only',
    'linear receive-time estimate', 'linear endpoint estimate'})


@dataclass
class CombatSMConfig:
    # Ratios of max_health, never bare absolute health.
    low_health_ratio: float = 0.5
    critical_health_ratio: float = 0.25
    # Consecutive confirmed out-of-combat samples required; one never suffices.
    out_of_combat_confirmations: int = 3
    # Minimum simulated time that must pass inside disengaging before
    # confirmation (caller injects sampled_at; no real clock here).
    min_disengage_seconds: float = 2.0
    # Estimated distance above which the target counts as out of range.
    max_range: float = 5.0

    def __post_init__(self):
        if not 0 <= self.critical_health_ratio <= self.low_health_ratio <= 1:
            raise ValueError('critical ratio must not exceed low ratio')
        if self.out_of_combat_confirmations < 2:
            raise ValueError('a single sample must never confirm disengagement')
        if self.min_disengage_seconds < 0 or self.max_range <= 0:
            raise ValueError('dwell must be nonnegative and range positive')


@dataclass
class ObservationView:
    """Normalized per-sample observation for the state machine.

    health None means "unknown" (never transmitted, or transmitted without a
    usable max_health baseline). in_combat None means unknown.
    distance_confirmed must be False whenever the self position used for the
    distance was not a server-confirmed position (always the case in combat).
    """
    sampled_at: float
    health: object = None
    max_health: int = 0
    health_transmitted: bool = False
    ghost: bool = False
    in_combat: object = None
    target_guid: object = None
    target_present: bool = False
    target_health: object = None
    target_max_health: object = None
    distance: object = None
    distance_confirmed: bool = False

    @classmethod
    def from_mapping(cls, me, now, target=None, distance=None,
                     distance_confirmed=False, target_max_health=None):
        """Build a view from a worldstate-style summary dict.

        me: a summarized self object (health/health_transmitted/max_health/
            ghost/in_combat keys). target: a summarized creature dict or None.
        untransmitted health normalizes to None: unknown is neither death nor
        low health. target_max_health is the creature's own maximum, so the
        remaining fraction of the target can be judged.
        """
        transmitted = bool(me.get('health_transmitted', False))
        health = me.get('health') if transmitted else None
        max_health = int(me.get('max_health') or 0)
        if transmitted and health is not None and max_health <= 0:
            # A raw zero without a usable baseline cannot ground any ratio.
            health = None
        if target_max_health is None and target is not None:
            target_max_health = target.get('max_health')
        return cls(sampled_at=float(now), health=health,
                   max_health=max_health, health_transmitted=transmitted,
                   ghost=bool(me.get('ghost')), in_combat=me.get('in_combat'),
                   target_guid=target.get('guid') if target else None,
                   target_present=target is not None,
                   target_health=(target.get('health') if target else None),
                   target_max_health=target_max_health,
                   distance=distance,
                   distance_confirmed=bool(distance_confirmed))



@dataclass
class StepResult:
    state: str
    decision: str
    reasons: tuple = ()

    def to_dict(self):
        return {'state': self.state, 'decision': self.decision,
                'reasons': list(self.reasons)}


ENDING_ESCAPE = 'escape'
ENDING_FINISH_TARGET = 'finish_target'
ENDING_FIGHT = 'fight'
ENDINGS = (ENDING_ESCAPE, ENDING_FINISH_TARGET, ENDING_FIGHT)

# The share of the health budget the escape has to leave untouched.
#
# Measured on 2026-11-01 (b38_u1, b41_u1): the escape costs almost nothing
# against a MELEE target -- once the gap opens the hostile cannot reach the
# character any more, and the health did not move (118/127). So the escape is
# available at any health, and this ratio does not change the outcome; it is
# kept because it is the documented parameter of the rule, not because it
# gates anything. The rule's real teeth are "a target that is one hit from
# dying is killed instead of run from".
ESCAPE_MIN_HEALTH_RATIO = 0.78


def choose_combat_ending(*, self_health=None, self_max_health=0,
                          target_health=None, target_max_health=None,
                          escape_route_available=False, target_reachable=True,
                          health_floor_ratio=0.25, finish_ratio=0.35,
                          healthy_ratio=0.5,
                          escape_health_ratio=ESCAPE_MIN_HEALTH_RATIO):
    """How an unresolved combat should be ended: flee, kill the target, or fight on.

    This is the single rule for both the state machine and the exit branch.
    They must not have separate copies: the state machine records the
    decision and the exit branch carries it out, and two copies would drift
    and leave the executor acting on one rule while the evidence records
    another.

    The rule comes from the live measurements of 2026-11-01:
      - idling is fatal. A hostile that keeps hitting never lets the combat
        clear on its own (esc003: 45 s, HP 108 -> 39), so standing still
        while being hit only spends the character's health (esc010, esc013,
        esc014 all ended that way).
      - a target that is nearly dead is cheaper to kill than to run from.
        esc020: the target was at 22 of 71 HP and the character at 91 of 108,
        and escaping cost 60 damage and did not clear the combat; one hit
        would have ended it.
      - a target that has BROKEN CONTACT cannot be killed from where the
        character stands. On 2026-11-01 (huntD) the roaming caster backed
        out of melee range, the rule still asked for the kill, the fight
        refused to start ("preconditions_not_met") and the character died
        standing there. So the kill is only offered when the target can
        actually be reached.
      - below the health floor the character does not open a new fight.
      - the escape only works when the character can survive it. Measured on
        2026-11-01 (b24_u1): the escape route has to clear the hostile's
        visibility range, which is 100 yards away from where the hostile is
        NOW, and at the local speeds (7 yards/s running, 2.5 yards/s chase)
        that is about 24 s of running, roughly a dozen mob attacks. Running
        at less than three quarters of the health budget is a death march,
        so below that ratio the kill is preferred.
    """
    ratio = (self_health / self_max_health) if (self_health is not None
                                                 and self_max_health) else None
    at_floor = ratio is not None and ratio <= health_floor_ratio
    can_survive_escape = ratio is None or ratio >= escape_health_ratio
    if not escape_route_available:
        # With nowhere to run, the only ending the server offers is the
        # target dying, so fighting on beats standing still.
        return ENDING_FIGHT
    if at_floor:
        # Too hurt to open a new fight; get out instead.
        return ENDING_ESCAPE
    target_low = bool(target_reachable and target_health and target_max_health
                      and target_health / target_max_health <= finish_ratio)
    if target_low and ratio is not None and ratio >= healthy_ratio:
        return ENDING_FINISH_TARGET
    if can_survive_escape:
        return ENDING_ESCAPE
    if target_low:
        # The run would be fatal and the target is one hit from dying, so
        # killing it costs no run at all. This overrides the healthy_ratio
        # guard above: that guard is about not trading hits, and one hit is
        # all it takes here.
        return ENDING_FINISH_TARGET
    # The run is a death march and the target is not close to dying, so
    # neither ending is cheap. Getting out still wins: if the character
    # dies on the way it dies AWAY from the hostile, and the corpse run
    # (docs/119) is much safer from there than from next to the mob.
    return ENDING_ESCAPE


def distance_confirmed_for(source):
    """True only for a position source that is a confirmed self position.

    During combat the server never broadcasts the mover's own position, so
    every estimated-position source in agent_wow_combat_control counts as
    unconfirmed.
    """
    return source is not None and source not in UNCONFIRMED_POSITION_SOURCES


class CombatStateMachine:
    """Bounded, side-effect-free recovery machine. Call step(observation)."""

    def __init__(self, config=None, target_guid=None,
                 escape_path_planned=False, start_sent=False):
        self.config = config or CombatSMConfig()
        if self.config.low_health_ratio < self.config.critical_health_ratio:
            raise ValueError('critical ratio must not exceed low ratio')
        if self.config.out_of_combat_confirmations < 2:
            raise ValueError('a single sample must never confirm disengagement')
        self.target_guid = target_guid
        self.escape_path_planned = bool(escape_path_planned)
        self._start_sent = bool(start_sent)
        # A start given at construction time models a fight that was already
        # dispatched (and, for the legacy recovery callers, already fought);
        # it seeds the witnessed-combat authority so a recovery window may
        # open. An advisory start advised later inside precheck does not set
        # this: a pending, never-observed start must not be mistaken for a
        # finished fight (combat-state-002 fix A).
        self._engaged = bool(start_sent)
        # Whether any sample so far witnessed a confirmed in_combat=True.
        # This, not _engaged, is what distinguishes "combat ended" from "an
        # attack is still pending server confirmation".
        self._combat_observed = bool(start_sent)
        self._state = PRECHECK
        self._reasons = ()
        self._out_of_combat_streak = 0
        # Start of the current unbroken clean (counted) sample run; the
        # minimum dwell must be covered by this run alone. None means no
        # clean run is currently open.
        self._clean_run_started_at = None
        self._last_known_health = None
        self._finished = False
        self._finish_reason = None
        self._low_exit_reasons = ()
        self._range_reasons = ()

    # --- introspection -----------------------------------------------------
    @property
    def state(self):
        return self.state_name()

    def state_name(self):
        return self._state

    @property
    def reasons(self):
        return self._reasons

    @property
    def finished(self):
        return self._finished

    @property
    def low_exit_latched(self):
        return self._state in (LOW_HEALTH_EXIT, UNRESOLVED) and bool(
            self._low_exit_reasons)

    @property
    def out_of_combat_streak(self):
        return self._out_of_combat_streak

    @property
    def combat_observed(self):
        """True once any sample confirmed in_combat=True (ever, across revivals)."""
        return self._combat_observed

    def health_ratio(self, observation):
        if observation.health is None:
            return None
        if observation.max_health <= 0:
            return None
        return observation.health / observation.max_health

    # --- core step -----------------------------------------------------------
    def step(self, observation):
        """Consume one injected observation; return the decision for it."""
        if self._finished:
            if (self._state == DISENGAGED_CONFIRMED
                    and observation.in_combat is True):
                # A confirmed disengagement is a claim about a past window,
                # not a permanent session assertion: a later confirmed
                # in-combat sample revives tracking (combat-state-002 fix B).
                # dead/ghost/unresolved stay truly terminal.
                self._finished = False
                self._reasons = ()
            else:
                return StepResult(self._state, DECISION_NONE, self._reasons)
        reason = self._classify_self(observation)
        if reason is not None:
            return self._self_exit(observation, reason)
        if self._state in (LOW_HEALTH_EXIT, TARGET_OUT_OF_RANGE):
            # Latched: no later observation re-enters combat or restarts.
            return self._latched(observation)
        if self._state == PRECHECK and observation.in_combat is not True:
            if (self._engaged and observation.in_combat is False
                    and not self._combat_observed):
                # Pending start: the attack was advised/dispatched but no
                # sample ever confirmed combat. A confirmed non-combat
                # sample here does not prove the fight ended — the fight
                # outcome is still unknown — so it must not open a recovery
                # window. Wait in engaging (hold_and_observe) for a
                # confirmed in-combat sample (combat-state-002 fix A).
                self._state = ENGAGING
                return StepResult(ENGAGING, DECISION_HOLD_AND_OBSERVE,
                                  (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
            # Precheck is a gate, not a disengagement window: it advises
            # start_attack (or withholds it) without consuming any
            # confirmation budget. Confirmation rules begin once combat
            # was actually observed or an attack was dispatched.
            return self._precheck(
                observation, opening=(observation.in_combat is False))
        return self._advance(observation)

    # --- internals -----------------------------------------------------------
    def _classify_self(self, observation):
        if observation.ghost:
            return REASON_SELF_GHOST
        # Self-respect the transmission flag: an untransmitted health is
        # unknown here even when a caller constructs the view directly with
        # health=0 (run40 regression guard at the decision-core level).
        if not observation.health_transmitted:
            return None
        if observation.health is not None and observation.health <= 0:
            return REASON_SELF_DEAD
        return None

    def _self_exit(self, observation, reason):
        self._state = GHOST if reason == REASON_SELF_GHOST else DEAD
        self._reasons = (reason,)
        self._finished = True
        return StepResult(self._state, DECISION_NONE, self._reasons)

    def _latched(self, observation):
        base = self._low_exit_reasons or self._range_reasons
        if observation.health is None and observation.max_health > 0:
            reasons = base + (REASON_HEALTH_UNKNOWN,)
        else:
            reasons = base
        # The fight is over and the character must get out. How it gets out
        # is decided by the shared ending rule, so the state machine cannot
        # recommend the one thing that is fatal: idling while a hostile keeps
        # hitting (esc003, esc010, esc013, esc014).
        #
        # A target that is out of range has broken contact, so the kill
        # ending is not executable from where the character stands
        # (2026-11-01 huntD).
        ending = choose_combat_ending(
            self_health=observation.health,
            self_max_health=observation.max_health,
            target_health=observation.target_health,
            target_max_health=observation.target_max_health,
            escape_route_available=self.escape_path_planned,
            target_reachable=(self._state != TARGET_OUT_OF_RANGE))
        if ending == ENDING_ESCAPE:
            decision = DECISION_REQUEST_ESCAPE
        else:
            decision = DECISION_CONTINUE_ATTACK
        return StepResult(self._state, decision, reasons)

    def _health_gate(self, observation):
        """Health checks shared by engaging and in_combat. None = pass."""
        if (not observation.health_transmitted
                or observation.health is None
                or observation.max_health <= 0):
            # Unknown is decided by the flag first, never by a raw zero:
            # untransmitted health is not death and not low health.
            return REASON_HEALTH_UNKNOWN
        ratio = observation.health / observation.max_health
        if ratio < self.config.critical_health_ratio:
            return REASON_HEALTH_RATIO_BELOW_CRITICAL
        if ratio < self.config.low_health_ratio:
            return REASON_HEALTH_RATIO_BELOW_LOW
        return None

    def _low_exit(self, observation, reason):
        self._state = LOW_HEALTH_EXIT
        self._low_exit_reasons = (reason,)
        self._reasons = (reason,)
        # Latch: the start (if any) is consumed; never dispatched again.
        self._start_sent = True
        if self.escape_path_planned:
            return StepResult(self._state, DECISION_REQUEST_ESCAPE, self._reasons)
        return StepResult(self._state, DECISION_STOP_ATTACK, self._reasons)

    def _target_gate(self, observation):
        """Target checks shared by engaging and in_combat. None = pass."""
        if not observation.target_present:
            return REASON_TARGET_MISSING
        if observation.target_health is not None and observation.target_health <= 0:
            return REASON_TARGET_DEAD
        if (observation.distance is not None
                and observation.distance > self.config.max_range):
            if observation.distance_confirmed:
                return REASON_TARGET_OUT_OF_RANGE_CONFIRMED
            return REASON_TARGET_DISTANCE_UNCONFIRMED
        return None

    def _range_exit(self, reason):
        # A confirmed out-of-range target never auto-restarts; an
        # unconfirmed distance stops the attack but must not claim flight.
        self._state = TARGET_OUT_OF_RANGE
        self._range_reasons = (reason,)
        self._reasons = (reason,)
        self._start_sent = True
        return StepResult(self._state, DECISION_STOP_ATTACK, self._reasons)

    def _precheck(self, observation, opening=False):
        """Precheck: advise starting the attack once preconditions are known.

        start_attack is emitted exactly once (latched by _start_sent) and
        only on a sample that confirms the unit is NOT in combat: unknown
        combat never authorizes an action (same discipline as
        agent_wow_effect_gate.attempt_gate, which rejects None). The machine
        stays in PRECHECK with no dispatch side effect; the actual bridge
        dispatch is the caller's decision. Once advised (or already
        dispatched by an older controller), unknown-combat samples are
        uninformative and stay in precheck: a pending start could already
        have entered combat, so they must not open a recovery window. A
        confirmed non-combat sample after a pending start is handled by the
        _combat_observed gate in step()/_advance(), which routes a
        never-witnessed start to engaging and only a witnessed fight to the
        recovery window (combat-state-002 fix A). Precheck itself never
        consumes any confirmation budget.
        """
        if self._engaged:
            # Reached only via the precheck-re-entry gate (combat_observed):
            # the fight was witnessed and has ended, so the confirmed
            # non-combat sample opens the recovery window. Unknown combat
            # is uninformative and never opens one.
            if opening:
                return self._observe_disengage(observation)
            return StepResult(PRECHECK, DECISION_NONE,
                              (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
        if observation.in_combat is not False:
            # Unknown combat: no action is authorized before a confirmed
            # non-combat sample (S6).
            return StepResult(PRECHECK, DECISION_NONE,
                              (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
        health_reason = self._health_gate(observation)
        if health_reason == REASON_HEALTH_UNKNOWN:
            return StepResult(PRECHECK, DECISION_NONE,
                              (REASON_HEALTH_UNKNOWN,))
        if health_reason is not None:
            # A known-ratio low before any combat is a precheck failure, not
            # a combat exit: never advise starting into a known low budget,
            # and never open a recovery window for a fight that was never
            # observed (combat-state-002 fix A).
            return StepResult(PRECHECK, DECISION_NONE, (health_reason,))
        target_reason = self._target_gate(observation)
        if target_reason is not None:
            # Missing/dead/over-range target: no start advice, and no
            # recovery window either — nothing was ever fought here.
            return StepResult(PRECHECK, DECISION_NONE, (target_reason,))
        self._start_sent = True
        # An advised start is pending: unknown-combat samples must not
        # re-advise it, and confirmed non-combat samples must not open a
        # recovery window for a fight that was never observed (they route
        # to engaging instead). Precheck itself never consumes any
        # confirmation budget.
        self._engaged = True
        return StepResult(PRECHECK, DECISION_START_ATTACK, ())

    def _advance(self, observation):
        if observation.in_combat is True:
            self._engaged = True
            self._combat_observed = True
            self._state = IN_COMBAT
            self._out_of_combat_streak = 0
            self._clean_run_started_at = None
            health_reason = self._health_gate(observation)
            if health_reason in (REASON_HEALTH_RATIO_BELOW_LOW,
                                 REASON_HEALTH_RATIO_BELOW_CRITICAL):
                return self._low_exit(observation, health_reason)
            target_reason = self._target_gate(observation)
            if target_reason in (REASON_TARGET_MISSING, REASON_TARGET_DEAD):
                return self._finish(observation, target_reason)
            if target_reason is not None:
                return self._range_exit(target_reason)
            if health_reason == REASON_HEALTH_UNKNOWN:
                return StepResult(self._state, DECISION_HOLD_AND_OBSERVE,
                                  (REASON_HEALTH_UNKNOWN,))
            self._note_health(observation)
            return StepResult(self._state, DECISION_CONTINUE_ATTACK, ())

        if observation.in_combat is False:
            if self._state == PRECHECK:
                # Precheck keeps its own gate bookkeeping: the engaged flag
                # distinguishes "start advised/dispatched" from "never
                # started", and only a witnessed combat (_combat_observed)
                # may open the recovery window; a pending start moves to
                # engaging instead (combat-state-002 fix A).
                return self._precheck(observation, opening=True)
            return self._observe_disengage(observation)

        # in_combat unknown: never counts toward any confirmation.
        self._out_of_combat_streak = 0
        self._clean_run_started_at = None
        if self._state == PRECHECK and self._engaged:
            # An attack was dispatched but combat is not yet confirmed.
            return StepResult(ENGAGING, DECISION_HOLD_AND_OBSERVE,
                              (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
        if self._state == PRECHECK:
            # No dispatch has happened yet; keep prechecking with no action.
            return StepResult(PRECHECK, DECISION_NONE,
                              (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
        if self._state == ENGAGING:
            return StepResult(ENGAGING, DECISION_HOLD_AND_OBSERVE,
                              (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
        return StepResult(DISENGAGING, DECISION_HOLD_AND_OBSERVE,
                          (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))

    def _note_health(self, observation):
        if observation.health is not None:
            self._last_known_health = observation.health

    def _observe_disengage(self, observation):
        if not self._combat_observed:
            # Combat was never witnessed: a legacy recovery start injected
            # via the constructor, a pending advised start, or a precheck
            # failure must not be mistaken for a finished fight. Wait in
            # engaging (hold_and_observe); only a confirmed in-combat sample
            # may open the recovery window (combat-state-002 fix A).
            self._engaged = True
            self._state = ENGAGING
            self._out_of_combat_streak = 0
            self._clean_run_started_at = None
            return StepResult(ENGAGING, DECISION_HOLD_AND_OBSERVE,
                              (REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED,))
        # A health-unknown sample always flags why the window is invalid;
        # it resets the streak and never counts toward confirmation.
        # Only reachable once combat was witnessed, so the unit is engaged.
        self._engaged = True
        health_unknown_now = False
        self._state = DISENGAGING
        if observation.in_combat is not False:
            # Unknown in-combat ends the clean run: no clean run is open
            # until a later clean sample restarts one.
            self._out_of_combat_streak = 0
            self._clean_run_started_at = None
        elif not observation.health_transmitted or observation.health is None:
            # Unknown health ends the clean run and never counts.
            self._out_of_combat_streak = 0
            self._clean_run_started_at = None
            health_unknown_now = True
        elif observation.health <= 0:
            # A transmitted zero seen here is death, decided next classify;
            # for this step it merely ends the clean run.
            self._out_of_combat_streak = 0
            self._clean_run_started_at = None
            health_unknown_now = False
        elif (self._last_known_health is not None
                and observation.health < self._last_known_health):
            # Health fell during the window: the clean run is invalid and
            # ends here; the minimum dwell must be covered by the current
            # clean run only.
            self._out_of_combat_streak = 0
            self._clean_run_started_at = None
            self._note_health(observation)
            health_unknown_now = False
        else:
            self._note_health(observation)
            self._out_of_combat_streak += 1
            health_unknown_now = False
            if self._out_of_combat_streak == 1:
                # The first clean sample of a run anchors its dwell clock;
                # it cannot confirm by itself.
                self._clean_run_started_at = observation.sampled_at
        if (self._out_of_combat_streak >= self.config.out_of_combat_confirmations
                and self._clean_run_started_at is not None
                and (observation.sampled_at - self._clean_run_started_at)
                >= self.config.min_disengage_seconds):
            return self._finish(observation, REASON_OUT_OF_COMBAT_CONFIRMED)
        reasons = [REASON_OUT_OF_COMBAT_NOT_YET_CONFIRMED]
        if health_unknown_now:
            reasons.append(REASON_HEALTH_UNKNOWN)
        return StepResult(self._state, DECISION_HOLD_AND_OBSERVE, tuple(reasons))

    def _finish(self, observation, reason):
        self._reasons = (reason,)
        if reason == REASON_TARGET_DEAD:
            # Target killed: leave via the normal confirmed path so logout
            # safety still requires confirmed out-of-combat samples.
            self._state = DISENGAGING
            # The post-kill window starts with the next counted clean
            # sample's clean-run anchor, not this sample.
            self._clean_run_started_at = None
            self._out_of_combat_streak = 0
            return StepResult(self._state, DECISION_STOP_ATTACK, (reason,))
        if reason == REASON_TARGET_MISSING:
            # Capture ends with no target and no further sampling planned.
            self._state = UNRESOLVED
            self._finished = True
            return StepResult(self._state, DECISION_ABORT_UNIT, (reason,))
        if reason == REASON_OUT_OF_COMBAT_CONFIRMED:
            self._state = DISENGAGED_CONFIRMED
            self._finished = True
            return StepResult(self._state, DECISION_NONE, (reason,))
        self._state = UNRESOLVED
        self._finished = True
        return StepResult(self._state, DECISION_ABORT_UNIT, (reason,))

    # --- explicit budget end (no diagnostic loops allowed in combat) --------
    def finish_budget(self):
        """Caller-injected budget exhaustion; idempotent."""
        if self._finished:
            return StepResult(self._state, DECISION_NONE, self._reasons)
        self._state = UNRESOLVED
        self._reasons = (REASON_BUDGET_EXHAUSTED,)
        self._finished = True
        return StepResult(self._state, DECISION_ABORT_UNIT, self._reasons)
