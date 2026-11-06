"""The X2 catalog must enumerate all shared OXP3 capabilities."""
import copy
import importlib.util
from pathlib import Path
import unittest

ADAPTATIONS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('x2_full_toon_catalog', ADAPTATIONS.parent/'scripts/toon_catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CompleteX2Catalog(unittest.TestCase):
    def setUp(self):
        self.x2 = catalog.load(ADAPTATIONS/'devices/onexplayer/x2-mini.toon', ADAPTATIONS)
        self.oxp = catalog.load(ADAPTATIONS/'devices/onexplayer/oxp3.toon', ADAPTATIONS)
        self.path = Path('devices/onexplayer/x2-mini.toon')

    def test_all_oxp_guide_capabilities_are_declared_and_hdr_is_x2(self):
        by_id = {row['id']: row for row in self.x2['components']}
        self.assertTrue({row['id'] for row in self.oxp['components']} <= set(by_id))
        self.assertFalse(any(by_id[row['id']]['recommendation']=='excluded' for row in self.oxp['components']))
        for name in ('hdr','desktop-hdr'):
            self.assertIn('475/700',by_id[name]['notes'])

    def test_package_inherits_all_known_components_and_legacy_options(self):
        package=self.x2['package']
        self.assertEqual({'oxp3:legacy-adaptation','oxp3:microphone','oxp3:speaker-route','oxp3:xevmafix'},
                         set(package['inherited_components']))
        self.assertEqual(set(self.oxp['package']['inherited_legacy_capabilities']),
                         set(package['inherited_legacy_capabilities']))
        catalog.validate_model(self.x2,self.path)

    def test_missing_guide_capability_or_changed_base_is_rejected(self):
        missing=copy.deepcopy(self.x2)
        missing['components']=[row for row in missing['components'] if row['id']!='input']
        with self.assertRaises(ValueError): catalog.validate_model(missing,self.path)
        changed=copy.deepcopy(self.x2)
        next(row for row in changed['build']['inputs'] if row['name']=='base')['sha256']='0'*64
        with self.assertRaises(ValueError): catalog.validate_model(changed,self.path)

if __name__=='__main__': unittest.main()
