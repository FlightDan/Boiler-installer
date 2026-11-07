import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_vm_stderr', ROOT / 'scripts/check-vm-stderr.py')
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)

ALLOWLIST = scanner.load_allowlist(scanner.DEFAULT_ALLOWLIST)
REALPATH = 'realpath: /dev/disk/by-partsets/self/rootfs: No such file or directory'
PMBR = 'GPT PMBR size mismatch (167772159 != 293603327) will be corrected by write.'
KERNEL = '[    0.624069] acpi PNP0A03:00: fail to add MMCONFIG information, can\'t access extended configuration space under this bridge'


class VmStderrAllowlistTests(unittest.TestCase):
    def test_committed_allowlist_is_valid_and_bounded(self):
        for task, entries in ALLOWLIST['tasks'].items():
            for entry in entries:
                self.assertTrue(entry['pattern'].strip())
                self.assertIn(entry['max'], range(1, 21))
                self.assertGreaterEqual(len(entry['reason']), 20)
                if task == '*':
                    self.assertLessEqual(entry['max'], scanner.MAX_SHARED_COUNT)

    def test_clean_guest_log_passes_with_no_accepted_diagnostics(self):
        text = '[    0.000000] Linux version 6.18.52\nOXP_DEPLOY_CORE=completed\nOXP_TASK_RESULT=passed\n'
        result = scanner.scan(text, 'deployment-test', ALLOWLIST)
        self.assertTrue(result['passed'])
        self.assertEqual(result['accepted'], {})

    def test_known_diagnostic_within_its_limit_is_accepted_for_its_task(self):
        text = 'localhost:~# ' + REALPATH + '\n' + REALPATH + '\nOXP_TASK_RESULT=passed\n'
        result = scanner.scan(text, 'deployment-test', ALLOWLIST)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['accepted'], {REALPATH: 2})

    def test_count_limit_fails_even_for_a_known_line(self):
        text = (REALPATH + '\n') * 3 + 'OXP_TASK_RESULT=passed\n'
        result = scanner.scan(text, 'deployment-test', ALLOWLIST)
        self.assertFalse(result['passed'])
        self.assertTrue(any(item['reason'].startswith('count-exceeded') for item in result['unexpected']))

    def test_task_specific_entries_do_not_leak_to_other_tasks(self):
        text = REALPATH + '\nOXP_TASK_RESULT=passed\n'
        result = scanner.scan(text, 'integration', ALLOWLIST)
        self.assertFalse(result['passed'])
        self.assertEqual(result['unexpected'][0]['reason'], 'not-allowlisted')

    def test_pmbr_correction_is_only_accepted_for_resize_prepared(self):
        text = PMBR + '\n' + PMBR + '\nOXP_TASK_RESULT=passed\n'
        self.assertTrue(scanner.scan(text, 'resize-prepared', ALLOWLIST)['passed'])
        self.assertFalse(scanner.scan(text, 'deployment-test', ALLOWLIST)['passed'])

    def test_shared_kernel_notice_is_accepted_everywhere(self):
        text = KERNEL + '\nOXP_TASK_RESULT=passed\n'
        self.assertTrue(scanner.scan(text, 'integration-verify', ALLOWLIST)['passed'])

    def test_unlisted_failure_is_reported_with_its_line_number(self):
        text = 'OXP_TASK_RESULT=passed\nFailed to access \'/dev/vdd5\': No such file or directory\n'
        result = scanner.scan(text, 'partition-test', ALLOWLIST)
        self.assertFalse(result['passed'])
        self.assertEqual(result['unexpected'][0]['line'], 2)
        self.assertEqual(result['unexpected'][0]['reason'], 'not-allowlisted')

    def test_serial_escape_noise_does_not_hide_a_known_line(self):
        noisy = '\x1b[0m' + REALPATH.replace(' ', ' \x1b[K') + '\r\n'
        result = scanner.scan(noisy, 'deployment-test', ALLOWLIST)
        self.assertEqual(result['accepted'].get(REALPATH), 1)

    def test_command_line_exit_codes_and_invalid_allowlist(self):
        with tempfile.TemporaryDirectory() as work, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            clean = Path(work) / 'clean.txt'
            clean.write_text('OXP_TASK_RESULT=passed\n', encoding='utf-8')
            self.assertEqual(scanner.main(['check', str(clean), '--task', 'deployment-test']), 0)
            dirty = Path(work) / 'dirty.txt'
            dirty.write_text('Traceback (most recent call last):\n', encoding='utf-8')
            self.assertEqual(scanner.main(['check', str(dirty), '--task', 'deployment-test']), 1)
            broken = Path(work) / 'allowlist.json'
            broken.write_text(json.dumps({'schema': 1, 'kind': 'BoilerVmDiagnosticAllowlist',
                                          'tasks': {'*': [{'pattern': 'x', 'max': 50, 'reason': 'short'}]}}),
                              encoding='utf-8')
            self.assertEqual(scanner.main(['check', str(clean), '--task', 'x', '--allowlist', str(broken)]), 2)


if __name__ == '__main__':
    unittest.main()
