"""Checkpoint identity, semantic validation and cooperative execution lock.

Shared by TaskExecutor and offline reconciliation tooling. Rules enforced here:

- A plan identity is a reproducible SHA-256 over canonical JSON of the
  persisted action fields (id/kind/params/side_effecting/timeout) in plan
  order. Python hash(), function reprs and memory addresses are never used.
- A checkpoint file is trusted only when its schema, run identity, plan
  identity and record/status semantics are all consistent. Anything else is
  refused without rewriting the file and without dispatching a backend action.
- The lock is a cooperative single-writer guard for one checkpoint path. It
  covers "both executors constructed, then racing for first execution"; it is
  not a defense against a malicious OS peer and it never auto-preempts.
"""
import errno
import hashlib
import json
import os
import uuid
from pathlib import Path

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1

# Statuses that prove an action finished with an observed postcondition.
FINALIZED_STATUSES = ('success', 'success_verified')
KNOWN_STATUSES = ('success', 'success_verified', 'failure', 'timeout', 'unknown',
                  'paused', 'skipped_verified')


class CheckpointRejected(Exception):
    """Raised when a checkpoint file cannot be trusted for this plan."""


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def plan_identity(actions):
    """Digest over persisted plan fields, in plan order.

    precondition/expectation are callables and are deliberately NOT part of
    the identity: they cannot be serialized reproducibly, so a resume cannot
    prove they are unchanged (documented limitation, never faked).
    """
    entries = []
    seen = set()
    for action in actions:
        action_id = action.id
        if not isinstance(action_id, str) or not action_id:
            raise ValueError('action id must be a non-empty string')
        if action_id in seen:
            raise ValueError(f'duplicate action id in plan: {action_id}')
        seen.add(action_id)
        try:
            params_text = canonical_json(action.params)
        except ValueError as exc:
            raise ValueError(f'action {action_id} params are not canonical-JSON safe: {exc}')
        if not isinstance(action.kind, str) or not action.kind:
            raise ValueError(f'action {action_id} kind must be a non-empty string')
        timeout = float(action.timeout)
        if timeout != timeout or timeout in (float('inf'), float('-inf')):
            raise ValueError(f'action {action_id} timeout must be finite')
        entries.append({'id': action_id, 'kind': action.kind, 'params': params_text,
                        'side_effecting': bool(action.side_effecting), 'timeout': timeout})
    return hashlib.sha256(canonical_json(entries).encode('utf-8')).hexdigest()


def validate_checkpoint(data, run_id, plan_digest):
    """Return the trusted checkpoint dict, or raise CheckpointRejected(reason).

    A syntactically valid JSON file with a matching run ID is not enough:
    plan identity, action set, record/status consistency and the attempted
    evidence for side-effecting completed actions must all hold.
    """
    if not isinstance(data, dict):
        raise CheckpointRejected('checkpoint top level is not a JSON object')
    schema = data.get('schema')
    if schema not in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        raise CheckpointRejected(f'unsupported checkpoint schema: {schema!r}')
    if data.get('run_id') != run_id:
        raise CheckpointRejected('checkpoint run_id does not match this run')
    status = data.get('status')
    if schema == LEGACY_SCHEMA_VERSION:
        # Migration policy: refuse. Never treat an old checkpoint as a fresh
        # run (that would resend side effects) and never rewrite it here.
        # Reconcile read-only with scripts/agent_wow_checkpoint_reconcile.py.
        raise CheckpointRejected(
            'legacy schema-1 checkpoint: reconcile read-only (plan identity is not '
            'bound in schema 1); refusing to resume or overwrite')
    if not isinstance(status, str) or not status:
        raise CheckpointRejected('checkpoint status must be a non-empty string')
    completed = data.get('completed')
    attempted = data.get('attempted')
    records = data.get('records')
    if not isinstance(completed, list) or not all(isinstance(x, str) and x for x in completed):
        raise CheckpointRejected('checkpoint completed must be a list of non-empty action IDs')
    if not isinstance(attempted, list) or not all(isinstance(x, str) and x for x in attempted):
        raise CheckpointRejected('checkpoint attempted must be a list of non-empty action IDs')
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise CheckpointRejected('checkpoint records must be a list of objects')
    if len(set(completed)) != len(completed):
        raise CheckpointRejected('checkpoint completed contains duplicate action IDs')
    if len(set(attempted)) != len(attempted):
        raise CheckpointRejected('checkpoint attempted contains duplicate action IDs')
    if schema == LEGACY_SCHEMA_VERSION:
        # Migration policy: refuse. Never treat an old checkpoint as a fresh
        # run (that would resend side effects) and never rewrite it here.
        # Reconcile read-only with scripts/agent_wow_checkpoint_reconcile.py.
        raise CheckpointRejected(
            'legacy schema-1 checkpoint: reconcile read-only (plan identity is not '
            'bound in schema 1); refusing to resume or overwrite')
    digest = data.get('plan_digest')
    if not isinstance(digest, str) or not digest:
        raise CheckpointRejected('schema-2 checkpoint is missing plan_digest')
    if digest != plan_digest:
        raise CheckpointRejected(
            'plan identity changed since checkpoint was written '
            '(action IDs, kinds, params, side_effecting, timeout or order)')
    plan_ids = [entry.get('id') if isinstance(entry, dict) else None
                for entry in data.get('plan') or []]
    if not isinstance(data.get('plan'), list) or len(plan_ids) != len(data['plan']):
        raise CheckpointRejected('checkpoint plan must be a list of action entries')
    if (not all(isinstance(x, str) and x for x in plan_ids)
            or len(set(plan_ids)) != len(plan_ids)):
        raise CheckpointRejected('checkpoint plan must list unique non-empty action IDs')
    plan_side = {entry['id']: bool(entry.get('side_effecting'))
                 for entry in data['plan']}
    unknown_ids = (set(completed) | set(attempted)) - set(plan_ids)
    if unknown_ids:
        raise CheckpointRejected(
            'completed/attempted reference action IDs outside the plan: '
            + ','.join(sorted(unknown_ids)))
    record_ids = set()
    for record in records:
        action_id = record.get('action_id')
        if not isinstance(action_id, str) or not action_id:
            raise CheckpointRejected('a checkpoint record is missing its action_id')
        if action_id not in plan_ids:
            raise CheckpointRejected(f'record references unknown action ID: {action_id}')
        record_status = record.get('status')
        if record_status not in KNOWN_STATUSES:
            raise CheckpointRejected(
                f'record for {action_id} has unknown status: {record_status!r}')
        if record_status == 'success':
            if action_id in record_ids:
                raise CheckpointRejected(
                    f'duplicate successful execution record for action {action_id}')
            record_ids.add(action_id)
    missing = set(completed) - record_ids
    if missing:
        raise CheckpointRejected(
            'completed actions without a successful execution record: '
            + ','.join(sorted(missing)))
    inconsistent = record_ids - set(completed)
    if inconsistent:
        raise CheckpointRejected(
            'successful execution records not present in completed: '
            + ','.join(sorted(inconsistent)))
    unbacked = set()
    for action_id in completed:
        if plan_side.get(action_id) and action_id not in set(attempted):
            unbacked.add(action_id)
    if unbacked:
        raise CheckpointRejected(
            'side-effecting completed actions lack a durable attempted record: '
            + ','.join(sorted(unbacked)))
    return data


def read_checkpoint(path, run_id, plan_digest):
    """Read and validate. Raises CheckpointRejected for any untrusted content."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CheckpointRejected(f'checkpoint file unreadable: {type(exc).__name__}: {exc}')
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CheckpointRejected(
            f'checkpoint is not valid UTF-8 JSON: {type(exc).__name__}: {exc}')
    return validate_checkpoint(data, run_id, plan_digest)


class CheckpointLock:
    """Cooperative single-executor lock bound to one checkpoint path.

    Acquisition is an exclusive O_CREAT|O_EXCL create, so two executors that
    were both constructed while the checkpoint was absent can still race for
    first execution and exactly one wins. The lock carries owner and run IDs.
    Release only removes a lock this owner still holds. A lock left behind by
    a crashed process blocks conservatively with diagnostics; it is never
    stolen by age or by an unknown PID.
    """

    def __init__(self, checkpoint_path, run_id, owner_id=None):
        self.checkpoint_path = Path(checkpoint_path)
        self.run_id = run_id
        self.owner_id = owner_id or f'{os.getpid()}-{uuid.uuid4().hex}'
        self.path = self.checkpoint_path.parent / (self.checkpoint_path.name + '.lock')
        self.held = False
        self._fd = None

    def acquire(self):
        if self.held:
            raise RuntimeError('lock already held by this executor')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_json({'owner_id': self.owner_id, 'run_id': self.run_id,
                                  'pid': os.getpid(),
                                  'checkpoint': str(self.checkpoint_path)})
        try:
            self._fd = os.open(self.path,
                               os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise self._holder_error()
            raise
        try:
            os.write(self._fd, payload.encode('utf-8'))
            os.fsync(self._fd)
        except OSError:
            # The create itself already excludes other executors; losing the
            # metadata write must not be read as losing the lock.
            pass
        self.held = True
        return self

    def _holder_error(self):
        try:
            raw = self.path.read_bytes()
        except OSError:
            raw = b''
        try:
            holder = json.loads(raw.decode('utf-8'))
        except Exception:
            holder = {}
        detail = {
            'reason': 'another executor holds the checkpoint lock',
            'lock_path': str(self.path),
            'holder_run_id': holder.get('run_id'),
            'holder_owner_id': holder.get('owner_id'),
            'holder_pid': holder.get('pid'),
            'note': ('cooperative lock: a crashed holder blocks conservatively; '
                     'remove the lock file manually only after confirming the '
                     'holder process is gone'),
        }
        return CheckpointRejected(json.dumps(detail, sort_keys=True))

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            # Close before unlinking: Windows refuses to remove a file that
            # is still open by handle.
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            # Re-read before unlinking: never delete a lock another owner
            # already recreated after an unexpected removal.
            holder = json.loads(self.path.read_bytes().decode('utf-8'))
        except Exception:
            holder = {}
        if holder.get('owner_id') == self.owner_id:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False
