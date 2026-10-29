"""Position provenance only: never turn a submitted endpoint into confirmation."""
import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Evidence:
    xyz: tuple
    at: float
    sequence: int
    source: str
    action_id: str = ''


class PositionModel:
    def __init__(self, session, character, map_id):
        if not session or not character:
            raise ValueError('session and character required')
        self.identity = (session, character, map_id)
        self.observed = None
        self.predicted = None
        self.reason = 'no observation'
        self.blocked = False

    def _check(self, identity, position, at, sequence):
        if tuple(identity) != self.identity:
            self.observed = self.predicted = None
            self.blocked = True
            self.reason = 'identity changed; create a new model'
            raise ValueError(self.reason)
        if self.blocked:
            raise ValueError('model invalidated; reacquire in a new model')
        if len(position) != 3 or not all(math.isfinite(v) for v in (*position, at)):
            raise ValueError('finite XYZ and timestamp required')
        if sequence < 0:
            raise ValueError('negative sequence')

    def observe(self, identity, position, at, sequence, source='movement'):
        self._check(identity, position, at, sequence)
        if source not in ('login', 'movement', 'correction', 'teleport'):
            raise ValueError('not position evidence')
        if self.observed and (sequence <= self.observed.sequence or at < self.observed.at):
            return False  # Polling cached state must not refresh its timestamp.
        if self.predicted and (sequence <= self.predicted.sequence or at < self.predicted.at):
            return False
        self.observed = Evidence(tuple(position), at, sequence, source)
        self.predicted = None
        self.reason = None
        return True

    def submit(self, identity, position, at, sequence, action_id):
        self._check(identity, position, at, sequence)
        if not self.observed or not action_id:
            raise ValueError('observation anchor and action ID required')
        latest = self.predicted or self.observed
        if at < latest.at or sequence < latest.sequence:
            raise ValueError('submission precedes current evidence')
        self.predicted = Evidence(tuple(position), at, sequence, 'submitted_prediction', action_id)
        self.reason = 'awaiting independent position evidence'

    def invalidate(self, reason):
        self.predicted = None
        self.blocked = True
        self.reason = reason

    def view(self, now, max_age=5):
        if not math.isfinite(now) or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError('invalid freshness budget')
        selected = self.predicted or self.observed
        age = now - selected.at if selected else None
        usable = bool(selected and not self.blocked and 0 <= age <= max_age)
        return {'identity': self.identity,
                'observed': asdict(self.observed) if self.observed else None,
                'predicted': asdict(self.predicted) if self.predicted else None,
                'usable_for_estimation': usable,
                'selected_age_seconds': age, 'reason': self.reason,
                'position_confirmed': usable and self.predicted is None}
