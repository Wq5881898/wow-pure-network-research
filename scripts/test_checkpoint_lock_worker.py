"""Subprocess worker for real-OS checkpoint lock tests.

Runs one TaskExecutor against an in-test FakeBackend and prints a single JSON
line describing what it observed and whether it dispatched. Used by
test_checkpoint_concurrency.py via sys.executable so the lock race is
exercised across real OS processes, not threads.

Sequence: construct the executor (construction-time state must not be
trusted) -> append one 'r' to the ready file -> wait for the gate file ->
run() -> optionally hold the lock (inside run, via a slow backend) so the
peer can be observed losing the race deterministically instead of via sleep
roulette.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_wow_contract import Action, Observation, TaskExecutor


class RecordingBackend:
    def __init__(self, marker: Path, hold: float):
        self.marker = marker
        self.hold = hold

    def observe(self):
        return Observation(backend='fake', character='9', session_id='s',
                           sequence=1, sampled_at=0.0, health=89, max_health=89)

    def execute(self, action, timeout=None):
        # O_EXCL create proves real-process single dispatch even if the
        # printed stdout line were lost.
        fd = os.open(self.marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        if self.hold:
            # Still inside backend.execute, therefore still inside the
            # executor's lock hold window.
            deadline = time.monotonic() + self.hold
            while time.monotonic() < deadline:
                time.sleep(0.02)
        return {}


def main(argv):
    checkpoint = Path(argv[0])
    marker = Path(argv[1])
    hold = float(argv[2]) if len(argv) > 2 else 0.0
    ready = Path(argv[3]) if len(argv) > 3 else None
    gate = Path(argv[4]) if len(argv) > 4 else None
    executor = TaskExecutor(RecordingBackend(marker, hold), 'conc-run',
                            checkpoint_path=checkpoint)
    if ready is not None:
        with ready.open('a', encoding='utf-8') as handle:
            handle.write('r')
    if gate is not None:
        deadline = time.monotonic() + 30
        while not gate.exists():
            if time.monotonic() > deadline:
                raise SystemExit('gate file never appeared')
            time.sleep(0.02)
    action = Action('a', 'noop', {}, side_effecting=True,
                    expectation=lambda before, after, evidence: hold == 0.0)
    report = executor.run([action])
    print(json.dumps({'status': report['status'],
                      'dispatched': marker.exists(),
                      'detail': report['results'][-1]['detail'] if report['results'] else ''}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
