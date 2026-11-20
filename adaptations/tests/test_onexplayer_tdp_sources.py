"""Exercise the derived PL1 runtime with synthetic DMI and RAPL interfaces."""
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
TDP = runpy.run_path(str(ROOT / 'brands/onexplayer/common/tdp_sources.py'))
LOCK = {row['path']: row for row in json.loads((ROOT / 'source-lock.json').read_bytes())['files']}


def derive(name):
    raw = (ROOT / (TDP['PREFIX'] + name)).read_bytes()
    return TDP['transform'](name, raw, LOCK)


class OnexplayerPowerLimits(unittest.TestCase):
    def test_model_floor_and_controller(self):
        scope = {'__name__': 'test_rapl'}
        # File locking is only needed when starting the Linux service.
        with patch.dict(sys.modules, fcntl=Mock()):
            exec(derive(TDP['PROVIDER']), scope)
        with tempfile.TemporaryDirectory() as tmp:
            dmi = Path(tmp)
            minimum = scope['pl1_minimum']
            self.assertEqual(minimum(dmi), 10)
            (dmi / 'sys_vendor').write_text('ONE-NETBOOK')
            cases = [('ONEXPLAYER 3', 8), ('ONEXPLAYER X2Mini', 8),
                     ('ONEXPLAYER X2 Mini', 8), ('Another model', 10)]
            for model, expected in cases:
                with self.subTest(model=model):
                    (dmi / 'product_name').write_text(model)
                    scope['pl1_minimum'] = lambda: minimum(dmi)
                    values = dict(a=16000000, b=16000000, c=20000000, d=20000000)
                    controller = scope['RaplController'](
                        paths=('a', 'b'), pl2_paths=('c', 'd'), validator=lambda: None,
                        busy_checker=lambda: None, reader=values.__getitem__, writer=values.__setitem__)
                    self.assertEqual(controller.get_power_limits()['pl1_min_watts'], expected)
                    with self.assertRaises(scope['InvalidLimit']):
                        controller.set_limit(expected - 1)
                    controller.set_limit(expected)
                    self.assertEqual(controller.get_power_limits()['pl1_values'], [expected, expected])
                    self.assertEqual(controller.get_power_limits()['pl2_values'], [20, 20])
                    self.assertEqual(controller.get_power_limits()['pl2_min_watts'], 10)
                    with self.assertRaises(scope['InvalidLimit']):
                        controller.set_pl2(9)
                    controller.set_pl2(10)
                    self.assertEqual(controller.get_power_limits()['pl2_values'], [10, 10])
            (dmi / 'product_name').write_text('ONEXPLAYER 3')
            (dmi / 'sys_vendor').write_text('Different vendor')
            self.assertEqual(minimum(dmi), 10)

    def test_decky_helper_and_frontends(self):
        scope = {'__name__': 'test_helper', '__file__': __file__}
        exec(derive(TDP['HELPER']), scope)
        properties = Mock()
        dbus = Mock(UInt32=int)
        scope['_connect'] = lambda: (dbus, properties, None)
        scope['_status'] = lambda _: {'pl1_values': [8, 8]}
        scope['set_pl1'](8)
        properties.Set.assert_called_once_with(scope['TDP_INTERFACE'], 'TdpLimit', 8, timeout=5)
        with self.assertRaises(ValueError):
            scope['set_pl1'](7)
        with self.assertRaises(ValueError):
            scope['set_pl2'](9)
        self.assertIn(b'key==="pl1"?Number(power?.pl1_min_watts??10)', derive(TDP['PLUGIN']))
        localized = (ROOT / 'brands/onexplayer/oxp3/current/plugin-i18n/cpu/index.js').read_text(encoding='utf-8')
        self.assertIn('const pl1Min=Number(power?.pl1_min_watts??10)', localized)
        self.assertIn('const low=key==="pl1"?pl1Min:minimumPL2(power)', localized)

    def test_rejects_modified_public_sources(self):
        for name in (TDP['PROVIDER'], TDP['HELPER'], TDP['PLUGIN']):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'public baseline'):
                TDP['transform'](name, b'changed', LOCK)
