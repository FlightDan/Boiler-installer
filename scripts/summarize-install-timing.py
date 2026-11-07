"""Summarize Boiler guest/host timing records from an installer log.

The BOILER_TIMING / BOILER_HOST_TIMING records are plain log output. They do
not feed any installation decision, and this tool never changes a result.
It converts the per-script cumulative clocks into phase durations and compares
several runs of the same configuration.

Usage:
    python summarize-install-timing.py LOG [LOG ...] [--json] [--top N]
        [--progress PROGRESS.jsonl ...]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

SCHEMA_VERSION = 1
KIND = 'BoilerInstallTimingSummary'
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
TIMING_PREFIX = 'BOILER_TIMING='
HOST_TIMING_PREFIX = 'BOILER_HOST_TIMING='
HOST_RESOURCES_PREFIX = 'BOILER_HOST_RESOURCES='


def _lines(text):
    # Guest serial output uses CRLF and may carry terminal control sequences.
    for raw in ANSI.sub('', text).replace('\r', '\n').split('\n'):
        line = raw.strip()
        if line:
            yield line


def _seconds(value):
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def _new_run(scope, phases):
    return {'scope': scope, 'run': sum(1 for item in phases if item['scope'] == scope) + 1,
            'steps': [], 'last': None}


def summarize_text(text, source='<log>'):
    tasks = []
    phases = []
    warnings = []
    open_runs = {}
    pending_resources = {}

    def open_task(task):
        entry = {'task': task, 'resources': pending_resources.pop(task, None),
                 'vm_seconds': None, 'exit_status': None}
        tasks.append(entry)
        return entry

    for line in _lines(text):
        if line.startswith(TIMING_PREFIX):
            scope, _, rest = line[len(TIMING_PREFIX):].partition(':')
            phase, _, value_text = rest.rpartition(':')
            value = _seconds(value_text)
            if not scope or not phase or value is None:
                warnings.append('malformed-timing:' + line)
                continue
            run = open_runs.get(scope)
            # A scope restarts its clock on every guest task. A repeat of its
            # first phase or a smaller value begins a new run.
            if run is None or (run['steps'] and phase == run['steps'][0]['name']) or value < run['last']:
                run = _new_run(scope, phases)
                phases.append(run)
                open_runs[scope] = run
            previous = run['last'] or 0.0
            run['steps'].append({'name': phase, 'seconds': round(max(0.0, value - previous), 3)})
            run['last'] = value
        elif line.startswith(HOST_TIMING_PREFIX):
            body = line[len(HOST_TIMING_PREFIX):]
            task, _, rest = body.partition(':')
            fields = rest.split(':')
            if not task or not fields:
                warnings.append('malformed-host-timing:' + line)
                continue
            if fields[0] == 'vm-start':
                open_task(task)
            elif fields[0] == 'vm-exit' and len(fields) >= 2:
                seconds = _seconds(fields[1])
                if seconds is None:
                    warnings.append('malformed-host-timing:' + line)
                    continue
                status = fields[2].removeprefix('status=') if len(fields) > 2 and fields[2].startswith('status=') else 'ok'
                entry = next((item for item in reversed(tasks)
                              if item['task'] == task and item['vm_seconds'] is None), None)
                if entry is None:
                    entry = open_task(task)
                    warnings.append('vm-start-missing:' + task)
                entry['vm_seconds'] = round(seconds, 3)
                entry['exit_status'] = status
            else:
                warnings.append('malformed-host-timing:' + line)
        elif line.startswith(HOST_RESOURCES_PREFIX):
            task, _, summary = line[len(HOST_RESOURCES_PREFIX):].partition(':')
            if not task:
                warnings.append('malformed-host-resources:' + line)
                continue
            if summary.startswith('accel-fallback:'):
                warnings.append('accelerator-fallback:' + summary)
                continue
            pending_resources[task] = summary

    for entry in tasks:
        if entry['vm_seconds'] is None:
            warnings.append('vm-exit-missing:' + entry['task'])
    totals = {}
    for run in phases:
        for step in run['steps']:
            key = run['scope'] + ':' + step['name']
            totals[key] = round(totals.get(key, 0.0) + step['seconds'], 3)
    return {
        'schema_version': SCHEMA_VERSION,
        'kind': KIND,
        'source': source,
        'tasks': tasks,
        'phases': phases,
        'phase_totals': dict(sorted(totals.items())),
        'warnings': warnings,
    }


def summarize_progress(text, source='<progress>'):
    entries = []
    for line in _lines(text):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        elapsed = row.get('elapsedMs')
        step = row.get('Step')
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not isinstance(step, str):
            continue
        if entries and elapsed < entries[-1]['last_record_ms']:
            raise ValueError('Progress elapsed time regressed')
        if not entries or entries[-1]['step'] != step:
            if entries:
                entries[-1]['finished_ms'] = elapsed
                entries[-1]['boundary'] = 'next-step'
            entries.append({'step': step, 'started_ms': elapsed, 'finished_ms': elapsed,
                            'last_record_ms': elapsed, 'max_record_gap_ms': 0,
                            'boundary': 'end-of-evidence'})
        else:
            entry = entries[-1]
            entry['max_record_gap_ms'] = max(entry['max_record_gap_ms'], elapsed - entry['last_record_ms'])
            entry['last_record_ms'] = elapsed
            entry['finished_ms'] = elapsed
    for entry in entries:
        entry['duration_ms'] = entry['finished_ms'] - entry['started_ms']
        entry['observed_span_ms'] = entry['last_record_ms'] - entry['started_ms']
    return {'schema_version': SCHEMA_VERSION, 'kind': 'BoilerInstallProgressSummary',
            'source': source, 'steps': entries}


def _metrics(summary):
    values = {}
    for entry in summary['tasks']:
        if entry['vm_seconds'] is not None:
            key = 'task:' + entry['task']
            values[key] = round(values.get(key, 0.0) + entry['vm_seconds'], 3)
    for key, seconds in summary['phase_totals'].items():
        values['phase:' + key] = seconds
    return values


def compare(summaries):
    rows = []
    metrics = [_metrics(summary) for summary in summaries]
    labels = sorted({key for values in metrics for key in values})
    for label in labels:
        rows.append({'metric': label,
                     'values': [values.get(label) for values in metrics]})
    return {'schema_version': SCHEMA_VERSION, 'kind': 'BoilerInstallTimingComparison',
            'sources': [summary['source'] for summary in summaries], 'rows': rows}


def _format_number(value):
    return '-' if value is None else '%.3f' % value


def _print_summary(summary, top):
    print('== %s' % summary['source'])
    for entry in summary['tasks']:
        resources = entry['resources'] or '?'
        print('  vm %-28s %10s s  %-9s %s' % (entry['task'], _format_number(entry['vm_seconds']),
                                              entry['exit_status'] or 'pending', resources))
    totals = sorted(summary['phase_totals'].items(), key=lambda item: item[1], reverse=True)
    for key, seconds in totals[:top]:
        print('  phase %-50s %10.3f s' % (key, seconds))
    for warning in summary['warnings']:
        print('  warning: %s' % warning)


def _print_comparison(comparison):
    print('== comparison')
    widths = [max(12, len(Path(source).name)) for source in comparison['sources']]
    for row in comparison['rows']:
        cells = ' '.join(_format_number(value).rjust(width) for value, width in zip(row['values'], widths))
        print('  %-40s %s' % (row['metric'], cells))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    parser.add_argument('--json', action='store_true', help='emit JSON instead of tables')
    parser.add_argument('--top', type=int, default=15, help='phase rows per log in table output')
    parser.add_argument('--progress', action='append', default=[], type=Path,
                        help='optional installation progress.jsonl (repeatable)')
    options = parser.parse_args(argv)
    summaries = []
    progress = []
    try:
        for path in options.logs:
            summaries.append(summarize_text(path.read_text(encoding='utf-8', errors='replace'), str(path)))
        for path in options.progress:
            progress.append(summarize_progress(path.read_text(encoding='utf-8', errors='replace'), str(path)))
    except (OSError, ValueError) as error:
        print('cannot summarize timing input: %s' % error, file=sys.stderr)
        return 2
    comparison = compare(summaries) if len(summaries) > 1 else None
    if options.json:
        payload = {'summary': summaries, 'progress': progress}
        if comparison is not None:
            payload['comparison'] = comparison
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for summary in summaries:
        _print_summary(summary, options.top)
    for summary in progress:
        print('== %s' % summary['source'])
        for entry in summary['steps']:
            print('  step %-10s %10.3f s' % (entry['step'], entry['duration_ms'] / 1000.0))
    if comparison is not None:
        _print_comparison(comparison)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
