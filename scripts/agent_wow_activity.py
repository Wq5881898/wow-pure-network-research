"""Operation timings and visible long-wait state; never infer actual motion."""
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import threading
import time


class ActivityRecorder:
    def __init__(self, base, long_seconds=5):
        self.base = Path(base)
        self.long_seconds = long_seconds
        self.lock = threading.RLock()
        self.stack = []
        self.counter = 0

    def _write(self, path, data):
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def _active(self):
        now = time.monotonic()
        self._write(self.base.with_suffix('.active.json'), {
            'sampled_at': time.time(), 'operations': [
                {**row, 'elapsed_seconds': now - row['started_monotonic']}
                for row in self.stack],
            'position_semantics': 'operation state only; actual stationary/moving not measured'})

    @contextmanager
    def phase(self, name, **details):
        with self.lock:
            self.counter += 1
            row = {'id': self.counter, 'phase': name, 'details': details,
                   'started_at': time.time(), 'started_monotonic': time.monotonic()}
            self.stack.append(row)
            try:
                self._active()
            except OSError:
                pass  # Diagnostics must not interrupt gameplay control.
        def long_wait():
            with self.lock:
                if row in self.stack:
                    try:
                        self._active()
                        self._append({**row, 'event': 'long_operation',
                                      'elapsed_seconds': time.monotonic()-row['started_monotonic']})
                    except OSError:
                        pass
        timer = threading.Timer(self.long_seconds, long_wait)
        timer.daemon = True
        timer.start()
        outcome = 'completed'
        try:
            yield
        except BaseException:
            outcome = 'raised'
            raise
        finally:
            timer.cancel()
            with self.lock:
                self.stack.remove(row)
                try:
                    self._append({**row, 'event': 'end', 'outcome': outcome,
                                  'elapsed_seconds': time.monotonic()-row['started_monotonic']})
                    self._active()
                except OSError:
                    pass

    def _append(self, row):
        with self.base.with_suffix('.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')


def timed_operation(name, describe=None):
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            recorder = getattr(self, 'activity', None)
            if recorder is None:
                return function(self, *args, **kwargs)
            details = describe(*args, **kwargs) if describe else {}
            with recorder.phase(name, **details):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate
