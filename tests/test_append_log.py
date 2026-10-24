from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import tools.append_log as append_log

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools' / 'append_log.py'


def call(tmp, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=str(tmp))


class AppendLogTests(unittest.TestCase):
    def test_prompt_with_ts_written_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            log = tmp / 'log.txt'; log.write_text('existing\n', encoding='utf-8')
            prompt = tmp / 'prompt.txt'; prompt.write_text('hello ${TS}\n', encoding='utf-8')
            before = log.read_bytes(); before_hash = hashlib.sha256(before).hexdigest()
            result = call(tmp, '--title', 'T', '--prompt-file', str(prompt), '--summary', 'ok', '--actions', 'a', '--tool', 'GitHub Copilot', '--model', 'MAI-Code-1.1-Flash', '--log', str(log))
            self.assertEqual(result.returncode, 0, result.stderr)
            data = log.read_bytes(); self.assertEqual(data[:len(before)], before); self.assertEqual(hashlib.sha256(data[:len(before)]).hexdigest(), before_hash); self.assertIn(b'hello ${TS}', data)

    def test_empty_tool_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); log = tmp / 'log.txt'; log.write_text('', encoding='utf-8')
            result = call(tmp, '--title', 'T', '--prompt', 'ok', '--summary', 'ok', '--tool', '', '--model', 'MAI-Code-1.1-Flash', '--log', str(log))
            self.assertNotEqual(result.returncode, 0); self.assertIn('tool invalid', result.stderr)

    def test_bad_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            append_log.ts_utc(datetime(2024, 1, 1, 0, 0, 0))

    def test_existing_log_bytes_unchanged_after_append(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d); log = tmp / 'log.txt'; log.write_text('before\n', encoding='utf-8'); prompt = tmp / 'prompt.txt'; prompt.write_text('hello', encoding='utf-8');
            before = log.read_bytes(); h_before = hashlib.sha256(before).hexdigest();
            result = call(tmp, '--title', 'T', '--prompt-file', str(prompt), '--summary', 'ok', '--actions', 'a', '--tool', 'GitHub Copilot', '--model', 'MAI-Code-1.1-Flash', '--log', str(log))
            self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual(hashlib.sha256(log.read_bytes()[:len(before)]).hexdigest(), h_before)

if __name__ == '__main__': unittest.main()