import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest'))
import fixture_layout


class FixtureLayoutTests(unittest.TestCase):
    def test_production_profile_has_six_preserved_and_roles_seven_to_fourteen(self):
        names = fixture_layout.expected_names('production')
        self.assertEqual(len(names), 14)
        self.assertEqual(names[:6], ['Windows EFI fixture', 'Windows MSR fixture',
                                     'Windows data fixture', 'Windows D fixture',
                                     'Windows RecoveryImages fixture', 'Windows RECOVERY fixture'])
        self.assertEqual(fixture_layout.role_numbers('production'), list(range(7, 15)))
        item = fixture_layout.profile('production')
        preserved_end = max(part['start_mib'] + part['size_mib'] for part in item['preserved'])
        self.assertLess(preserved_end, item['range']['start_mib'])
        start, size = fixture_layout.range_bytes('production')
        self.assertLessEqual(start + size, fixture_layout.LAYOUT['disk_mib'] * 1024 * 1024)

    def test_external_profile_is_a_blank_disk_with_roles_one_to_eight(self):
        self.assertEqual(fixture_layout.expected_names('external'), fixture_layout.ROLES)
        self.assertEqual(fixture_layout.role_numbers('external'), list(range(1, 9)))
        self.assertEqual(fixture_layout.profile('external')['preserved'], [])
        start, size = fixture_layout.range_bytes('external')
        self.assertLessEqual(start + size, fixture_layout.LAYOUT['disk_mib'] * 1024 * 1024)

    def test_seed_and_legacy_profiles_keep_the_expected_shapes(self):
        self.assertEqual(fixture_layout.expected_names('seed'),
                         ['Windows EFI fixture', 'Windows data fixture'])
        self.assertEqual(fixture_layout.expected_names('legacy')[:2],
                         ['Windows EFI fixture', 'Windows data fixture'])
        self.assertEqual(fixture_layout.role_numbers('legacy'), list(range(3, 11)))

    def test_profile_detection_uses_the_exact_partition_names(self):
        for name in ('production', 'seed', 'legacy', 'external'):
            actual = {'partitions': [{'name': value} for value in fixture_layout.expected_names(name)]}
            self.assertEqual(fixture_layout.profile_for(actual), name)

    def test_preserved_sentinels_are_complete(self):
        for profile in ('production', 'seed', 'legacy'):
            for part in fixture_layout.profile(profile)['preserved']:
                if part.get('sentinel'):
                    self.assertTrue(part['content'].endswith('\n'))
                    self.assertIn(part['fs'], ('vfat', 'ntfs'))


if __name__ == '__main__':
    unittest.main()
