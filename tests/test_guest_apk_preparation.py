from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuestApkPreparationTests(unittest.TestCase):
    def test_common_script_installs_locked_packages_only_once(self):
        text = (ROOT / 'guest/common.sh').read_text()
        self.assertEqual(len(re.findall(r'\bapk add\b', text)), 1)
        self.assertRegex(text, r'if \[ ! -f /var/cache/apk/\.boiler-apk-prepared \]; then\s*\n\s*apk add')
        # The timing mark stays outside the guard so a skipped install is still
        # visible as a near-zero phase instead of a missing record.
        self.assertIn('BOILER_TIMING=common:apk', text)

    def test_multi_volume_payload_marks_the_packages_it_installed(self):
        source = (ROOT / 'app/PayloadVolumes.cs').read_text(encoding='utf-8')
        self.assertIn('touch /var/cache/apk/.boiler-apk-prepared', source)
        # The marker only follows a successful install in the same command chain.
        self.assertRegex(source, r'apk --cache-dir /var/cache/apk add[^"]*&& touch /var/cache/apk/\.boiler-apk-prepared')


if __name__ == '__main__':
    unittest.main()
