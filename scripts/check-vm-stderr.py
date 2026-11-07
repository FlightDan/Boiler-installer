"""Compare one retained QEMU guest log with the reviewed diagnostic allowlist.

Usage:
    check-vm-stderr.py LOG --task TASK [--allowlist PATH]

Guest serial output mixes boot, package-manager and tool messages. Only lines
with failure-shaped wording are checked. A matching line is accepted only when
an allowlist entry for TASK (or the shared "*" entry) names it and its count
limit has not been exceeded. Everything else fails.

The allowlist records diagnostics that were observed in retained runs and do
not stop those runs. It is not an acceptance statement, and an entry needs a
reason that says what was and was not isolated.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
FAILURE = re.compile(
    r'(?i)(error|fail|traceback|exception|no such file|not found|cannot|denied|refus|'
    r'unable|invalid|mismatch|corrupt|abort|panic|warning|not supported|unsupported)')
DEFAULT_ALLOWLIST = Path(__file__).resolve().parents[1] / 'tests/fixtures/vm-stderr-allowlist.json'
MAX_SHARED_COUNT = 5


def load_allowlist(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema') != 1 or value.get('kind') != 'BoilerVmDiagnosticAllowlist':
        raise ValueError('Unsupported VM diagnostic allowlist')
    tasks = value.get('tasks')
    if not isinstance(tasks, dict):
        raise ValueError('Allowlist has no task table')
    for task, entries in tasks.items():
        if not isinstance(entries, list):
            raise ValueError('Allowlist entries for ' + task + ' are not a list')
        for entry in entries:
            pattern, maximum, reason = entry.get('pattern'), entry.get('max'), entry.get('reason')
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError('Allowlist pattern is empty for ' + task)
            if type(maximum) is not int or not 1 <= maximum <= 20:
                raise ValueError('Allowlist count is outside 1-20 for ' + task + ': ' + pattern)
            if task == '*' and maximum > MAX_SHARED_COUNT:
                raise ValueError('Shared allowlist entry is too broad: ' + pattern)
            if not isinstance(reason, str) or len(reason.strip()) < 20:
                raise ValueError('Allowlist entry needs a reason: ' + pattern)
    return value


def normalize(text: str) -> list[str]:
    text = ANSI.sub('', text).replace('\r', '\n')
    return [line.strip() for line in text.split('\n') if line.strip()]


def scan(text: str, task: str, allowlist: dict) -> dict:
    entries = list(allowlist['tasks'].get(task, [])) + list(allowlist['tasks'].get('*', []))
    counts: Counter[str] = Counter()
    unexpected = []
    for number, line in enumerate(normalize(text), 1):
        if not FAILURE.search(line):
            continue
        matched = next((entry for entry in entries if entry['pattern'] in line), None)
        if matched is None:
            unexpected.append({'line': number, 'text': line[:300], 'reason': 'not-allowlisted'})
            continue
        counts[matched['pattern']] += 1
        if counts[matched['pattern']] > matched['max']:
            unexpected.append({'line': number, 'text': line[:300],
                               'reason': 'count-exceeded:' + matched['pattern']})
    return {'schema': 1, 'kind': 'BoilerVmDiagnosticScan', 'task': task,
            'passed': not unexpected, 'accepted': dict(counts), 'unexpected': unexpected,
            'scope': 'Diagnostic classification of one retained guest log; not acceptance.'}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--task', required=True)
    parser.add_argument('--allowlist', type=Path, default=DEFAULT_ALLOWLIST)
    options = parser.parse_args(argv[1:])
    try:
        allowlist = load_allowlist(options.allowlist)
        result = scan(options.log.read_text(encoding='utf-8', errors='replace'), options.task, allowlist)
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({'passed': False, 'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
