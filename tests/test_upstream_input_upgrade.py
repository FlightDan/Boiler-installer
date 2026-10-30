"""Regression for preserved, calibrated profiles on an installed device."""
import importlib.util
from pathlib import Path, PurePosixPath
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


class InputUpgrade(unittest.TestCase):
    def test_preserved_gyro_template_gains_connector_only_in_runtime_profile(self):
        builder = load('upstream_builder_fixture', ROOT/'adaptations/scripts/build-upstream-repairs.py')
        folder = ROOT/'adaptations/brands/onexplayer/oxp3/current/upstream-20261029'
        original = (ROOT/'adaptations/brands/onexplayer/common/scripts/input_profile.py').read_bytes()
        patched = builder.patched_input_helper(original, (folder/'dock_input.py').read_bytes())
        ns = {'__name__': 'input_upgrade_fixture'}
        exec(compile(patched, 'generated_input_profile.py', 'exec'), ns)
        template = ('kind: CompositeDevice\nname: ONEXPLAYER 3\n'+ns['PLACEHOLDER']+
            'source_devices:\n  - group: imu\n    iio:\n      name: bmi260\n'
            '      mount_matrix: [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]\n'
            'options:\n  auto_manage: true\n# accepted calibration retained\n')
        before = template.encode()
        result = ns['render_template'](template, PurePosixPath('/sys/devices/platform/oxp3'))
        self.assertIn('name: HID 1a86:fe02', result)
        self.assertIn('mount_matrix: [[0, 1, 0], [-1, 0, 0], [0, 0, 1]]', result)
        self.assertIn('# accepted calibration retained', result)
        self.assertEqual(template.encode(), before)
        self.assertEqual(result.count('name: HID 1a86:fe02'), 1)
        custom = template.replace('name: bmi260', 'name: HID 1a86:fe02')
        self.assertIsNone(ns['render_template'](custom, PurePosixPath('/sys/devices/platform/oxp3')))


if __name__ == '__main__':
    unittest.main()
