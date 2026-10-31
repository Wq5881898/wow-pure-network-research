"""Bounded task-unit contract: Observation, Action, Result and a task executor.

Rules enforced here:
- A submitted request is never reported as success unless the postcondition is
  observed.
- A side-effecting action whose effect cannot be confirmed is UNKNOWN, and a
  resumed run refuses to resend it; it re-reads state instead.
- Every action is recorded as one structured JSON line and the checkpoint is
  rewritten after each action.
"""
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

SUCCESS = 'success'
FAILURE = 'failure'
TIMEOUT = 'timeout'
UNKNOWN = 'unknown'
PAUSED = 'paused'
SKIPPED = 'skipped_verified'


@dataclass
class Observation:
    backend: str
    character: str
    session_id: str
    sequence: int
    sampled_at: float
    map_id: int = 0
    position: list = field(default_factory=list)
    health: int = 0
    max_health: int = 0
    ghost: bool = False
    in_combat: bool = False
    level: int = 0
    quest_log: list = field(default_factory=list)
    item_totals: dict = field(default_factory=dict)
    available: tuple = ()
    context: dict = field(default_factory=dict)

    def to_dict(self):
        data = asdict(self)
        data['available'] = list(self.available)
        return data


@dataclass
class Action:
    id: str
    kind: str
    params: dict = field(default_factory=dict)
    timeout: float = 15.0
    side_effecting: bool = False
    precondition: object = None
    expectation: object = None

    def to_dict(self):
        return {'id': self.id, 'kind': self.kind, 'params': self.params,
                'timeout': self.timeout, 'side_effecting': self.side_effecting}


@dataclass
class Result:
    action_id: str
    kind: str
    status: str
    detail: str = ''
    elapsed_ms: int = 0
    evidence: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class TaskExecutor:
    """Runs a bounded action list against a backend that exposes observe/execute."""

    def __init__(self, backend, run_id, log_path=None, checkpoint_path=None,
                 budget_seconds=240, action_budget=12):
        self.backend = backend
        self.run_id = run_id
        self.log_path = Path(log_path) if log_path else None
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self.budget_seconds = budget_seconds
        self.action_budget = action_budget
        self.records = []
        self.completed = []
        self.attempted = []
        self._checkpoint_rejected = None
        self._load_checkpoint()

    def _load_checkpoint(self):
        if not (self.checkpoint_path and self.checkpoint_path.exists()):
            return
        try:
            data = json.loads(self.checkpoint_path.read_text(encoding='utf-8'))
            completed = data['completed']
            records = data['records']
            attempted = data['attempted']
            if (data.get('schema') != 1 or data.get('run_id') != self.run_id
                    or not isinstance(completed, list) or not isinstance(records, list)
                    or not isinstance(attempted, list) or not all(isinstance(x, str) for x in completed + attempted)
                    or not all(isinstance(x, dict) for x in records)):
                raise ValueError('checkpoint schema or run identity is invalid')
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self._checkpoint_rejected = f'{type(exc).__name__}: {exc}'
            return
        self.completed = list(completed)
        self.records = list(records)
        self.attempted = list(attempted)

    def _write_checkpoint(self, status):
        if not self.checkpoint_path:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(json.dumps({
            'schema': 1, 'run_id': self.run_id, 'status': status,
            'completed': self.completed, 'attempted': self.attempted,
            'records': self.records}, indent=2) + '\n')

    def _record(self, record):
        self.records.append(record)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open('a') as handle:
                handle.write(json.dumps(record) + '\n')

    def observe(self):
        return self.backend.observe()

    @staticmethod
    def _check(callable_, before, after, evidence):
        if callable_ is None:
            return True
        return bool(callable_(before, after, evidence))

    def run(self, actions, resume=False):
        results = []
        started = time.monotonic()
        status = SUCCESS
        if self._checkpoint_rejected:
            self._record({'action_id': 'checkpoint', 'kind': 'checkpoint', 'status': SKIPPED,
                          'detail': self._checkpoint_rejected, 'elapsed_ms': 0, 'evidence': {}})
        for action in actions:
            if (time.monotonic() - started > self.budget_seconds
                    or len(self.records) >= self.action_budget):
                detail = ('time budget exhausted'
                          if time.monotonic() - started > self.budget_seconds
                          else 'action budget exhausted')
                self._record({'action_id': action.id, 'kind': action.kind, 'status': PAUSED,
                              'detail': detail, 'elapsed_ms': 0, 'evidence': {}})
                results.append(Result(action.id, action.kind, PAUSED, detail))
                status = PAUSED
                self._write_checkpoint(status)
                break
            if resume and action.id in self.completed:
                current = self.observe()
                confirmed = self._check(action.expectation, current, current, {})
                if confirmed:
                    self._record({'action_id': action.id, 'kind': action.kind,
                                  'status': SKIPPED, 'detail': 'checkpoint says done and postcondition holds',
                                  'elapsed_ms': 0, 'evidence': {}})
                    continue
            if resume and action.side_effecting and action.id in self.attempted:
                # The previous run dispatched this side effect but could not
                # confirm it. Read the state; never resend on assumption.
                current = self.observe()
                confirmed = self._check(action.expectation, current, current, {})
                if confirmed:
                    self._record({'action_id': action.id, 'kind': action.kind, 'status': SKIPPED,
                                  'detail': 'previous side effect now confirmed by state',
                                  'elapsed_ms': 0, 'evidence': {}})
                    continue
                detail = 'previous side effect not confirmed; refusing to resend'
                self._record({'action_id': action.id, 'kind': action.kind, 'status': UNKNOWN,
                              'detail': detail, 'elapsed_ms': 0, 'evidence': {}})
                results.append(Result(action.id, action.kind, UNKNOWN, detail))
                status = UNKNOWN
                self._write_checkpoint(status)
                break
            before = self.observe()
            if action.precondition and not action.precondition(before):
                detail = 'precondition not met'
                self._record({'action_id': action.id, 'kind': action.kind, 'status': FAILURE,
                              'detail': detail, 'elapsed_ms': 0, 'evidence': {},
                              'observation_before': before.to_dict()})
                results.append(Result(action.id, action.kind, FAILURE, detail))
                status = FAILURE
                self._write_checkpoint(status)
                break
            began = time.monotonic()
            remaining = max(0.0, self.budget_seconds - (began - started))
            effective_timeout = min(float(action.timeout), remaining)
            if action.side_effecting:
                # Record the dispatch before it happens so a crash or a lost
                # reply still leaves evidence that a side effect may exist.
                self.attempted.append(action.id)
                self._write_checkpoint(status)
            try:
                evidence = self.backend.execute(action, timeout=effective_timeout) or {}
            except TimeoutError:
                evidence = {'timeout': True}
            except Exception as exc:  # noqa: BLE001 - backend faults must stay bounded
                evidence = {'error': f'{type(exc).__name__}: {exc}'}
            elapsed = int((time.monotonic() - began) * 1000)
            if elapsed > effective_timeout * 1000:
                evidence = dict(evidence) if isinstance(evidence, dict) else {'evidence': evidence}
                evidence['executor_deadline_exceeded'] = True
            try:
                after = self.observe()
            except Exception as exc:  # noqa: BLE001 - a closed session must not crash the run
                after = before
                evidence = dict(evidence) if isinstance(evidence, dict) else {}
                evidence['observation_error'] = f'{type(exc).__name__}: {exc}'
            if evidence.get('timeout') or evidence.get('executor_deadline_exceeded'):
                outcome = UNKNOWN if action.side_effecting else TIMEOUT
                detail = 'backend timed out; effect state unknown'
            elif evidence.get('side_effect_applied') is False:
                # Explicit non-dispatch takes precedence over a generic backend
                # error, but only the backend can prove this negative claim.
                outcome = FAILURE
                detail = 'backend reports no side effect applied'
            elif evidence.get('observation_error'):
                outcome = UNKNOWN if action.side_effecting else FAILURE
                detail = 'post-action observation unavailable; effect state unknown'
            elif evidence.get('error'):
                # A backend exception after dispatch says nothing about whether
                # the side effect reached the server. Only an explicit negative
                # dispatch acknowledgement proves it was not sent.
                outcome = (UNKNOWN if action.side_effecting
                           and evidence.get('side_effect_applied') is not False else FAILURE)
                detail = str(evidence['error'])
            elif self._check(action.expectation, before, after, evidence):
                outcome = SUCCESS
                detail = 'postcondition observed'
            elif action.side_effecting:
                outcome = UNKNOWN
                detail = 'request applied but postcondition not observed'
            else:
                outcome = FAILURE
                detail = 'postcondition not observed'
            record = {'action_id': action.id, 'kind': action.kind, 'status': outcome,
                      'detail': detail, 'elapsed_ms': elapsed, 'evidence': evidence,
                      'observation_before': before.to_dict(), 'observation_after': after.to_dict()}
            self._record(record)
            results.append(Result(action.id, action.kind, outcome, detail, elapsed, evidence))
            if outcome == SUCCESS:
                self.completed.append(action.id)
            else:
                status = outcome
                self._write_checkpoint(status)
                break
            self._write_checkpoint(status)
        if status == SUCCESS:
            self._write_checkpoint(status)
        return {'run_id': self.run_id, 'status': status, 'results': [r.to_dict() for r in results],
                'records': self.records, 'completed': list(self.completed)}
