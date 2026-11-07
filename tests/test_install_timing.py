import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('summarize_install_timing', ROOT / 'scripts/summarize-install-timing.py')
timing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timing)


class InstallTimingTests(unittest.TestCase):
    def test_cumulative_marks_become_phase_durations_and_survive_serial_noise(self):
        text = (
            '\x1b[0mBOILER_TIMING=integration:partitions:2.500\r\n'
            'BOILER_TIMING=integration:options:2.900\r\n'
            'BOILER_TIMING=common:apk:4.060\r\n'
            'BOILER_TIMING=common:modules:6.360\r\n'
            'BOILER_TIMING=integration:stage-A:120.000\r\n'
            'BOILER_TIMING=integration:stage-B:240.000\r\n'
            'BOILER_TIMING=integration:done:241.000\r\n'
        )
        summary = timing.summarize_text(text, 'run.txt')
        self.assertEqual(summary['kind'], timing.KIND)
        steps = {run['scope']: {step['name']: step['seconds'] for step in run['steps']}
                 for run in summary['phases']}
        self.assertEqual(steps['integration']['partitions'], 2.5)
        self.assertEqual(steps['integration']['options'], 0.4)
        self.assertEqual(steps['integration']['stage-B'], 120.0)
        self.assertEqual(steps['common']['modules'], 2.3)
        self.assertEqual(summary['warnings'], [])

    def test_each_guest_task_restarts_the_common_clock(self):
        text = (
            'BOILER_TIMING=common:apk:4.000\nBOILER_TIMING=common:modules:6.000\n'
            # The next guest boots faster; its first mark is larger than the
            # previous mark only because it is a separate run, not a later one.
            'BOILER_TIMING=common:apk:1.000\nBOILER_TIMING=common:modules:2.000\n'
            'BOILER_TIMING=common:apk:1.500\nBOILER_TIMING=common:modules:3.000\n'
        )
        summary = timing.summarize_text(text)
        runs = [run for run in summary['phases'] if run['scope'] == 'common']
        self.assertEqual(len(runs), 3)
        self.assertEqual([run['run'] for run in runs], [1, 2, 3])
        self.assertEqual(summary['phase_totals']['common:apk'], 6.5)
        self.assertEqual(summary['phase_totals']['common:modules'], 4.5)

    def test_host_vm_records_keep_resources_status_and_report_missing_exit(self):
        text = (
            'BOILER_HOST_RESOURCES=integration:-m 8192 -smp 8 -accel tcg,thread=multi\n'
            'BOILER_HOST_TIMING=integration:vm-start\n'
            'BOILER_HOST_TIMING=integration:vm-exit:1234.500\n'
            'BOILER_HOST_RESOURCES=integration-verify:-m 8192 -smp 8 -accel whpx\n'
            'BOILER_HOST_TIMING=integration-verify:vm-start\n'
        )
        summary = timing.summarize_text(text)
        self.assertEqual(summary['tasks'][0]['vm_seconds'], 1234.5)
        self.assertEqual(summary['tasks'][0]['exit_status'], 'ok')
        self.assertIn('accel tcg', summary['tasks'][0]['resources'])
        self.assertEqual(summary['tasks'][1]['exit_status'], None)
        self.assertIn('accel whpx', summary['tasks'][1]['resources'])
        self.assertIn('vm-exit-missing:integration-verify', summary['warnings'])

    def test_timeout_and_failure_status_are_preserved(self):
        text = (
            'BOILER_HOST_TIMING=integration:vm-start\n'
            'BOILER_HOST_TIMING=integration:vm-exit:60.000:status=timeout\n'
            'BOILER_HOST_TIMING=integration-verify:vm-start\n'
            'BOILER_HOST_TIMING=integration-verify:vm-exit:12.000:status=failed\n'
        )
        summary = timing.summarize_text(text)
        self.assertEqual([entry['exit_status'] for entry in summary['tasks']], ['timeout', 'failed'])
        self.assertEqual(summary['warnings'], [])

    def test_accelerator_fallback_is_reported_not_mistaken_for_resources(self):
        text = (
            'BOILER_HOST_RESOURCES=integration:-m 8192 -smp 8 -accel whpx\n'
            'BOILER_HOST_TIMING=integration:vm-start\n'
            'BOILER_HOST_TIMING=integration:vm-exit:2.000:status=failed\n'
            'BOILER_HOST_RESOURCES=integration:accel-fallback:whpx->tcg,thread=multi:no-guest-output\n'
            'BOILER_HOST_TIMING=integration:vm-start\n'
            'BOILER_HOST_TIMING=integration:vm-exit:60.000\n'
        )
        summary = timing.summarize_text(text)
        self.assertEqual(len(summary['tasks']), 2)
        self.assertIn('accel whpx', summary['tasks'][0]['resources'])
        self.assertIsNone(summary['tasks'][1]['resources'])
        self.assertTrue(any(item.startswith('accelerator-fallback:') for item in summary['warnings']))

    def test_comparison_sums_repeated_tasks_and_matches_phase_metrics(self):
        first = timing.summarize_text(
            'BOILER_HOST_TIMING=integration:vm-start\nBOILER_HOST_TIMING=integration:vm-exit:100.000\n'
            'BOILER_TIMING=integration:stage-A:50.000\n', 'baseline.txt')
        second = timing.summarize_text(
            'BOILER_HOST_TIMING=integration:vm-start\nBOILER_HOST_TIMING=integration:vm-exit:60.000\n'
            'BOILER_HOST_TIMING=integration:vm-start\nBOILER_HOST_TIMING=integration:vm-exit:20.000\n'
            'BOILER_TIMING=integration:stage-A:30.000\n', 'whpx.txt')
        comparison = timing.compare([first, second])
        rows = {row['metric']: row['values'] for row in comparison['rows']}
        self.assertEqual(comparison['sources'], ['baseline.txt', 'whpx.txt'])
        self.assertEqual(rows['task:integration'], [100.0, 80.0])
        self.assertEqual(rows['phase:integration:stage-A'], [50.0, 30.0])

    def test_progress_journal_reports_step_durations(self):
        text = (
            json.dumps({'utc': '2026-11-06T00:00:00Z', 'Stage': 'components', 'Step': '02.01', 'Message': 'start', 'elapsedMs': 1000}) + '\n'
            + json.dumps({'utc': '2026-11-06T00:00:10Z', 'Stage': 'components', 'Step': '02.01', 'Message': 'done', 'elapsedMs': 11000}) + '\n'
            + json.dumps({'utc': '2026-11-06T00:00:20Z', 'Stage': 'components', 'Step': '02.03', 'Message': 'verify', 'elapsedMs': 21000}) + '\n'
            + 'not json\n'
        )
        summary = timing.summarize_progress(text)
        first, last = summary['steps']
        self.assertEqual(first['started_ms'], 1000)
        self.assertEqual(first['finished_ms'], 21000)
        self.assertEqual(first['duration_ms'], 20000)
        self.assertEqual(first['observed_span_ms'], 10000)
        self.assertEqual(first['max_record_gap_ms'], 10000)
        self.assertEqual(first['boundary'], 'next-step')
        # The final singleton is explicitly incomplete evidence, not a measured zero-duration phase.
        self.assertEqual(last['duration_ms'], 0)
        self.assertEqual(last['boundary'], 'end-of-evidence')

    def test_real_internal_journal_boundaries_and_silence(self):
        path = ROOT / 'tests/fixtures/install-sessions/1106-internal/progress.jsonl'
        result = timing.summarize_progress(path.read_text(encoding='utf-8'))
        steps = {row['step']: row for row in result['steps']}
        self.assertEqual(steps['02.01']['observed_span_ms'], 1242301)
        self.assertEqual(steps['02.01']['max_record_gap_ms'], 1242301)
        # Sampling is not a measurement of silence: the full source recorded a
        # slightly smaller maximum gap, retained separately in fixture metadata.
        metadata = json.loads(path.with_name('manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['expectations']['historical_max_component_gap_ms'], 1242259)
        self.assertEqual(steps['02.03']['duration_ms'], 399753)
        self.assertEqual(steps['03.01']['duration_ms'], 27255)
        self.assertEqual(steps['01.06']['duration_ms'], 424679)
        # elapsedMs is independently rounded; UTC timestamps give 27.112582 s.
        self.assertEqual(steps['01.07']['duration_ms'], 27113)
        self.assertEqual(steps['02.03']['boundary'], 'next-step')

    def test_progress_clock_regression_is_not_a_negative_or_zero_duration(self):
        rows = [{'Step': 'A', 'elapsedMs': 20}, {'Step': 'B', 'elapsedMs': 10}]
        with self.assertRaisesRegex(ValueError, 'regressed'):
            timing.summarize_progress('\n'.join(json.dumps(row) for row in rows))

    def test_command_reports_json_and_compares_runs(self):
        with tempfile.TemporaryDirectory() as work:
            first = Path(work) / 'a.txt'
            second = Path(work) / 'b.txt'
            first.write_text('BOILER_TIMING=common:apk:4.000\nBOILER_TIMING=common:modules:6.000\n')
            second.write_text('BOILER_TIMING=common:apk:1.000\nBOILER_TIMING=common:modules:2.000\n')
            import contextlib
            import io
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = timing.main([str(first), str(second), '--json'])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(len(payload['summary']), 2)
            rows = {row['metric']: row['values'] for row in payload['comparison']['rows']}
            self.assertEqual(rows['phase:common:apk'], [4.0, 1.0])

    def test_malformed_records_are_reported_without_failing(self):
        summary = timing.summarize_text('BOILER_TIMING=integration:stage-A:not-a-number\n'
                                        'BOILER_HOST_TIMING=integration:vm-exit:oops\n')
        self.assertTrue(any(item.startswith('malformed-timing:') for item in summary['warnings']))
        self.assertTrue(any(item.startswith('malformed-host-timing:') for item in summary['warnings']))


if __name__ == '__main__':
    unittest.main()
