"""Bounded task-unit contract: Observation, Action, Result and a task executor.

Rules enforced here:
- A submitted request is never reported as success unless the postcondition is
  observed.
- A side-effecting action whose effect cannot be confirmed is UNKNOWN, and a
  resumed run refuses to resend it; it re-reads state instead.
- Every action is recorded as one structured JSON line and the checkpoint is
  rewritten after each action.
- A checkpoint-bound run takes a cooperative per-checkpoint lock before any
  dispatch decision, re-reads the durable checkpoint under the lock (never a
  construction-time cache), and validates plan identity plus record/status
  semantics; anything untrusted blocks without dispatch or overwrite.
"""
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_wow_checkpoint import (
    SCHEMA_VERSION, CheckpointLock, CheckpointRejected, plan_identity,
    read_checkpoint)

SUCCESS = 'success'
VERIFIED = 'success_verified'
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
        self.completed_verified = set()
        self.plan_digest = None
        self._plan_entries = []
        self._checkpoint_rejected = None
        self._checkpoint_loaded = False
        self._lock = (CheckpointLock(self.checkpoint_path, run_id)
                      if self.checkpoint_path else None)

    def _adopt_checkpoint(self, data, loaded=True):
        self.completed = list(data['completed'])
        self.attempted = list(data['attempted'])
        self.records = list(data['records'])
        self.completed_verified = {
            record['action_id'] for record in self.records
            if record.get('status') == SUCCESS and 'action_id' in record}
        self._checkpoint_loaded = loaded

    def _reject(self, detail):
        self._checkpoint_rejected = detail
        self._checkpoint_loaded = False
        self.completed = []
        self.attempted = []
        self.records = []
        self.completed_verified = set()

    def _load_checkpoint(self):
        """Re-read and validate the durable checkpoint. Caller must hold the
        lock; construction-time state is never trusted for dispatch."""
        if not self.checkpoint_path:
            return
        if not self.checkpoint_path.exists():
            # No durable checkpoint yet: fresh state, and a resume has
            # nothing to resume from.
            self._adopt_checkpoint({'status': None, 'completed': [],
                                    'attempted': [], 'records': []}, loaded=False)
            return
        try:
            data = read_checkpoint(self.checkpoint_path, self.run_id,
                                   self.plan_digest)
        except CheckpointRejected as exc:
            self._reject(str(exc))
            return
        if data.get('plan_digest') is None and (data.get('completed')
                                                or data.get('attempted')
                                                or data.get('records')):
            # A writer that skipped plan binding must not claim durable
            # progress: without a digest a resume cannot prove the plan is
            # the same one that produced those records.
            self._reject('plan identity is not bound but the checkpoint '
                         'already records completed/attempted actions')
            return
        self._adopt_checkpoint(data)

    def _write_checkpoint(self, status):
        if not self.checkpoint_path:
            return
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            'schema': SCHEMA_VERSION, 'run_id': self.run_id, 'status': status,
            'plan_digest': self.plan_digest,
            'plan': list(self._plan_entries),
            'completed': self.completed, 'attempted': self.attempted,
            'records': self.records}, indent=2) + '\n'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.checkpoint_path.parent,
                    prefix=self.checkpoint_path.name + '.', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.checkpoint_path)
            self._checkpoint_loaded = True
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

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

    def _blocked(self, detail):
        return {'run_id': self.run_id, 'status': UNKNOWN,
                'results': [Result('checkpoint', 'checkpoint', UNKNOWN, detail).to_dict()],
                'records': list(self.records), 'completed': list(self.completed)}

    def run(self, actions, resume=False):
        results = []
        started = time.monotonic()
        status = SUCCESS
        try:
            self.plan_digest = plan_identity(actions)
        except ValueError as exc:
            return self._blocked(f'invalid action plan: {exc}')
        self._plan_entries = [{'id': action.id, 'kind': action.kind,
                               'params': action.params,
                               'side_effecting': bool(action.side_effecting),
                               'timeout': float(action.timeout)}
                              for action in actions]
        if self._lock is None:
            # Pure in-memory mode stays compatible, but it has no
            # cross-process persistence or dedup ability.
            if not self.checkpoint_path and resume:
                return self._blocked(
                    'resume requested without a checkpoint path; nothing durable to resume')
            self._adopt_checkpoint({'status': None, 'completed': [],
                                    'attempted': [], 'records': []})
        else:
            try:
                self._lock.acquire()
            except CheckpointRejected as exc:
                return self._blocked(str(exc))
            try:
                self._load_checkpoint()
            except OSError as exc:
                # Durable write faults (fsync/replace) stay visible to the
                # caller; the finally below still releases the lock.
                self._lock.release()
                raise
            try:
                return self._run_locked(actions, resume, started, results, status)
            finally:
                self._lock.release()
        return self._run_locked(actions, resume, started, results, status)

    def _run_locked(self, actions, resume, started, results, status):
        durable_exists = bool(self.checkpoint_path) and self.checkpoint_path.exists()
        if not resume and durable_exists and (self._checkpoint_loaded
                                              or self._checkpoint_rejected):
            # A normal run never takes over an existing run checkpoint,
            # whether it validates or not; an untrusted file must be
            # reconciled explicitly.
            return self._blocked(
                self._checkpoint_rejected
                or 'run checkpoint already exists; explicit reconciliation/resume required')
        if resume and not self._checkpoint_loaded:
            return self._blocked(
                self._checkpoint_rejected or 'resume checkpoint missing')
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
                confirmed = (action.expectation is not None
                             and self._check(action.expectation, current, current, {}))
                if confirmed:
                    self._record({'action_id': action.id, 'kind': action.kind,
                                  'status': SKIPPED, 'detail': 'checkpoint says done and postcondition holds',
                                  'elapsed_ms': 0, 'evidence': {}})
                    if action.id not in self.completed_verified:
                        self.completed_verified.add(action.id)
                        self._write_checkpoint(status)
                    continue
                # The durable record says done but the postcondition no
                # longer holds: never silently re-dispatch a (possibly
                # side-effecting) completed action; stop for reconciliation.
                detail = 'completed action postcondition no longer holds; reconcile required'
                self._record({'action_id': action.id, 'kind': action.kind, 'status': UNKNOWN,
                              'detail': detail, 'elapsed_ms': 0, 'evidence': {}})
                results.append(Result(action.id, action.kind, UNKNOWN, detail))
                status = UNKNOWN
                self._write_checkpoint(status)
                break
            if resume and action.side_effecting and action.id in self.attempted:
                # The previous run dispatched this side effect but could not
                # confirm it. Read the state; never resend on assumption.
                current = self.observe()
                confirmed = (action.expectation is not None
                             and self._check(action.expectation, current, current, {}))
                if confirmed:
                    self._record({'action_id': action.id, 'kind': action.kind,
                                  'status': SKIPPED, 'detail': 'previous side effect now confirmed by state',
                                  'elapsed_ms': 0, 'evidence': {}})
                    if action.id not in self.completed_verified:
                        self.completed_verified.add(action.id)
                        self._write_checkpoint(status)
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
                if action.id not in self.attempted:
                    self.attempted.append(action.id)
                self._write_checkpoint(status)
            else:
                # Persist the run checkpoint at least once before the first
                # dispatch so a peer that starts later sees this run exists
                # instead of racing into a double first-start.
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
                self.completed_verified.add(action.id)
            else:
                status = outcome
                self._write_checkpoint(status)
                break
            self._write_checkpoint(status)
        if status == SUCCESS:
            self._write_checkpoint(status)
        return {'run_id': self.run_id, 'status': status, 'results': [r.to_dict() for r in results],
                'records': self.records, 'completed': list(self.completed)}
