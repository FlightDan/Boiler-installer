from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProgressMarkerContractTests(unittest.TestCase):
    def test_verify_script_emits_both_human_and_tracker_markers(self):
        verify = (ROOT / 'guest/integration-verify.py').read_text(encoding='utf-8')
        self.assertIn("print('OXP_INTEGRATION_VERIFIED=' + slot", verify)
        self.assertIn("print('OXP_INTEGRATION_VERIFY=' + json.dumps", verify)
        self.assertIn("dict(slot=slot, readonly=True)", verify)
        self.assertIn("dict(slot=slot,readonly=True)", verify)

    def test_tracker_consumes_the_marker_the_guest_emits(self):
        tracker = (ROOT / 'app/InstallationProgressTracker.cs').read_text(encoding='utf-8')
        self.assertIn('const string check = "OXP_INTEGRATION_VERIFY=";', tracker)
        # The human-readable marker is not a tracker input; it must not replace
        # the JSON contract that carries the slot.
        self.assertNotIn('const string check = "OXP_INTEGRATION_VERIFIED=";', tracker)

    def test_script_path_log_lines_end_with_a_newline(self):
        source = (ROOT / 'app/SteamOSDesktop.cs').read_text(encoding='utf-8')
        lines = [line.strip() for line in source.splitlines()]
        for marker in ('PERSISTENT_STEAMOS_SCRIPT=', 'DESKTOP_STEAMOS_SCRIPT=',
                       'DESKTOP_STEAMOS_SCRIPT_WARNING='):
            entries = [line for line in lines if line.startswith('log("' + marker)]
            self.assertTrue(entries, marker)
            for entry in entries:
                self.assertIn('Environment.NewLine', entry, entry)


if __name__ == '__main__':
    unittest.main()
