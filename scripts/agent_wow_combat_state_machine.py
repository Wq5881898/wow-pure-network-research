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
    distance: object = None
    distance_confirmed: bool = False

    @classmethod
    def from_mapping(cls, me, now, target=None, distance=None,
                     distance_confirmed=False):
        """Build a view from a worldstate-style summary dict.

        me: a summarized self object (health/health_transmitted/max_health/
            ghost/in_combat keys). target: a summarized creature dict or None.
        Untransmitted health normalizes to None: unknown is neither death nor
        low health.
        """
        transmitted = bool(me.get('health_transmitted', False))
        health = me.get('health') if transmitted else None
        max_health = int(me.get('max_health') or 0)
        if transmitted and health is not None and max_health <= 0:
            # A raw zero without a usable baseline cannot ground any ratio.
            health = None
        return cls(sampled_at=float(now), health=health,
                   max_health=max_health, health_transmitted=transmitted,
                   ghost=bool(me.get('ghost')), in_combat=me.get('in_combat'),
                   target_guid=target.get('guid') if target else None,
                   target_present=target is not None,
                   target_health=(target.get('health') if target else None),
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
        self._engaged = bool(start_sent)
        self._state = PRECHECK
        self._reasons = ()
        self._out_of_combat_streak = 0
        self._disengage_started_at = None
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
            return StepResult(self._state, DECISION_NONE, self._reasons)
        reason = self._classify_self(observation)
        if reason is not None:
            return self._self_exit(observation, reason)
        if self._state in (LOW_HEALTH_EXIT, TARGET_OUT_OF_RANGE):
            # Latched: no later observation re-enters combat or restarts.
            return self._latched(observation)
        return self._advance(observation)

    # --- internals -----------------------------------------------------------
    def _classify_self(self, observation):
        if observation.ghost:
            return REASON_SELF_GHOST
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
        if self.escape_path_planned:
            decision = DECISION_REQUEST_ESCAPE
        else:
            decision = DECISION_HOLD_AND_OBSERVE
        return StepResult(self._state, decision, reasons)

    def _health_gate(self, observation):
        """Health checks shared by engaging and in_combat. None = pass."""
        if observation.health is None:
            return REASON_HEALTH_UNKNOWN
        ratio = self.health_ratio(observation)
        if ratio is None:
            return REASON_HEALTH_UNKNOWN
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

    def _advance(self, observation):
        if observation.in_combat is True:
            self._engaged = True
            self._state = IN_COMBAT
            self._out_of_combat_streak = 0
            self._disengage_started_at = None
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
            if self._state in (PRECHECK, ENGAGING):
                # Not started (or dispatched but unconfirmed) and not in
                # combat: a precheck pass, or a fight that ended before any
                # confirmation. Confirmation rules still apply; the window
                # starts at this sample, so the first confirmation is
                # possible no earlier than the next.
                self._disengage_started_at = observation.sampled_at
            return self._observe_disengage(observation)

        # in_combat unknown: never counts toward any confirmation.
        self._out_of_combat_streak = 0
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
        if self._state == PRECHECK:
            # A unit that dispatched an attack but has not yet seen a
            # confirmed in-combat sample engages on the dispatch, so its
            # recovery window is the disengagement window.
            self._engaged = True
        # A health-unknown sample always flags why the window is invalid;
        # it resets the streak and never counts toward confirmation.
        health_unknown_now = False
        self._state = DISENGAGING
        if self._disengage_started_at is None:
            self._disengage_started_at = observation.sampled_at
        if observation.in_combat is not False:
            # Unknown in-combat resets the streak and never counts.
            self._out_of_combat_streak = 0
        elif observation.health is None:
            # Unknown health resets the streak and never counts.
            self._out_of_combat_streak = 0
            health_unknown_now = True
        elif observation.health <= 0:
            # A transmitted zero seen here is death, decided next classify;
            # for this step it merely does not count.
            self._out_of_combat_streak = 0
            health_unknown_now = False
        elif (self._last_known_health is not None
                and observation.health < self._last_known_health):
            # Health fell during the window: the window is invalid.
            self._out_of_combat_streak = 0
            self._note_health(observation)
            health_unknown_now = False
        else:
            self._note_health(observation)
            self._out_of_combat_streak += 1
            health_unknown_now = False
        if (self._out_of_combat_streak >= self.config.out_of_combat_confirmations
                and (observation.sampled_at - self._disengage_started_at)
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
            self._disengage_started_at = observation.sampled_at
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
