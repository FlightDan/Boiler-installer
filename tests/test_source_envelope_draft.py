import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.resources.source_envelope import validate

class EnvelopeTests(unittest.TestCase):
    def test_fixed_desktop_recipe_and_extra_rejection(self):
        e=dict(schema_version=1,kind='BoilerResourceSource',id='resource:gamma22-hdr-desktop',version='1.0.0',
               dependencies=[],external_component_dependencies=[],hardware_requires={},compatibility={},
               install_kind='gamma22-hdr-desktop-v1',verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'},
               renderer='fixed-desktop-hdr-v1',source_lock='locks/resources/gamma22-hdr-desktop.toon',
               recipe=dict(native_manifest='native/manifest.json',runtime_sources=[],license_sources=[]))
        self.assertEqual(validate(e),e)
        e['recipe']['command']='/tmp/arbitrary'
        with self.assertRaises(Exception):validate(e)

if __name__=='__main__':unittest.main()
