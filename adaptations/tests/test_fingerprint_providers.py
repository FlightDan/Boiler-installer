"""Synthetic source-candidate matching; no device operations or biometric data."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = load_module('fingerprint_provider_identity', ROOT / 'common/fingerprint/providers/identity.py')
common = load_module('fingerprint_provider_toon', ROOT / 'scripts/common-device-sources.py')


def provider(name):
    return common.load('common/fingerprint/providers/' + name + '/source.toon')


def observation(device, hid, bus='spi'):
    return {'device_id': device, 'bus': bus, 'acpi_hid': hid}


class FingerprintProviderSourceTests(unittest.TestCase):
    def setUp(self):
        self.bf = provider('bf63112')
        self.fte = provider('fte3600')
        self.providers = [self.bf, self.fte]

    def test_sensor_hid_not_model_selects_the_candidate(self):
        for hid, expected in [('BF63112', 'fingerprint:bf63112'),
                              ('FTE3600', 'fingerprint:fte3600')]:
            with self.subTest(hid=hid):
                result = identity.candidates(self.providers, [observation('target-device', hid)])
                self.assertEqual(result, {'state': 'candidate', 'matches': [
                    {'device_id': 'target-device', 'provider_id': expected}],
                    'unknown': [], 'installable': False})

    def test_empty_unknown_wrong_bus_and_unknown_with_match_are_not_selected(self):
        cases = [([], 'absent'), ([observation('s1', 'UNKNOWN')], 'unknown'),
                 ([observation('s1', 'FTE3600', 'usb')], 'unknown'),
                 ([observation('s1', 'FTE3600'), observation('s2', 'UNKNOWN')], 'unknown')]
        for observations, expected in cases:
            with self.subTest(observations=observations):
                result = identity.candidates(self.providers, observations)
                self.assertEqual(result['state'], expected)
                self.assertFalse(result['installable'])

    def test_multiple_sensors_are_ambiguous_even_of_same_type(self):
        for hid in ('BF63112', 'FTE3600'):
            result = identity.candidates(self.providers, [observation('s1', 'BF63112'), observation('s2', hid)])
            self.assertEqual(result['state'], 'ambiguous')
            self.assertEqual(len(result['matches']), 2)
            self.assertFalse(result['installable'])

    def test_duplicate_observations_and_extra_host_dmi_are_rejected(self):
        for observations in ([observation('s1', 'BF63112'), observation('s1', 'FTE3600')],
                             [dict(observation('s1', 'BF63112'), model='OXP3')],
                             [observation('', 'FTE3600')], [observation('s1', ' FTE3600')]):
            with self.subTest(observations=observations), self.assertRaises(ValueError):
                identity.candidates(self.providers, observations)

    def test_duplicate_provider_or_non_source_record_is_rejected(self):
        for mutation in ('same-id', 'same-hid', 'installable', 'schema-bool'):
            other = copy.deepcopy(self.fte)
            if mutation == 'same-id': other['id'] = self.bf['id']
            elif mutation == 'same-hid': other['matching'] = dict(self.bf['matching'])
            elif mutation == 'installable': other['installable'] = True
            else: other['schema_version'] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                identity.candidates([self.bf, other], [observation('s1', 'BF63112')])

    def test_models_reference_only_applicable_provider_sources(self):
        source = 'common/fingerprint/providers/'
        for model, expected in [('oxp3', {'bf63112'}), ('x2-mini', {'bf63112', 'fte3600'})]:
            record = common.load('devices/onexplayer/' + model + '.toon')
            component = next(c for c in record['components'] if c['id'] == 'fingerprint-unlock')
            paths = set(component['sources'])
            self.assertIn(source + 'identity.py', paths)
            self.assertEqual({name for name in ('bf63112', 'fte3600')
                              if source + name + '/source.toon' in paths}, expected)
            self.assertEqual(component['status'], 'external_runtime_required')
            self.assertTrue(component['default'])
            self.assertFalse(record['installable'])


if __name__ == '__main__':
    unittest.main()
