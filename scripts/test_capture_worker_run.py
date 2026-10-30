"""Raw event capture does not transcode a mock subprocess's UTF-8 bytes."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

from capture_worker_run import run_worker


class CaptureWorkerTests(unittest.TestCase):
    def test_utf8_event_bytes_and_exclusive_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clone, logs = root / 'clone', root / 'logs'
            (clone / '.git').mkdir(parents=True)
            logs.mkdir()
            prompt, permissions = root / 'prompt.txt', root / 'permissions.json'
            prompt.write_text('no tools', encoding='utf-8')
            permissions.write_text(json.dumps({'share': 'disabled', 'permission': {'*': 'deny'}}))
            payload = json.dumps({'type': 'text', 'part': {'text': '中文证据'}},
                                 ensure_ascii=False).encode('utf-8') + b'\n'
            # Python prints raw bytes; no provider, project scripts or shell.
            code = f'import sys;sys.stdout.buffer.write({payload!r})'
            # Stub the executable at Popen boundary while still using real file
            # descriptors and process waiting.
            from unittest.mock import patch
            import capture_worker_run
            original = capture_worker_run.subprocess.Popen
            def popen(command, **kwargs):
                return original([sys.executable, '-c', code], **kwargs)
            with patch.object(capture_worker_run.subprocess, 'Popen', side_effect=popen):
                result = run_worker(clone, logs, prompt, permissions, 'offline/mock', timeout=10)
            self.assertEqual(result['returncode'], 0)
            self.assertEqual((logs / 'events.jsonl').read_bytes(), payload)
            self.assertEqual(json.loads(payload)['part']['text'], '中文证据')
            with self.assertRaises(FileExistsError):
                run_worker(clone, logs, prompt, permissions, 'offline/mock')


if __name__ == '__main__':
    unittest.main()
