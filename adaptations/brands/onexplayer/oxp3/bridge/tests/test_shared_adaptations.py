import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('shared_builder', Path(__file__).resolve().parents[1]/'scripts/build-shared-adaptations.py')
builder = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(builder)


class LockedOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b'charge_control_available = False\ndef set_value(*args):\n    raise RuntimeError("Unavailable")\n'
        (self.root/'adapter.py').write_bytes(self.data)
        record = dict(source='adapter.py', target='var/lib/oxp3-battery/battery_control.py', size=len(self.data),
                      sha256=hashlib.sha256(self.data).hexdigest(), mode=0o644)
        part = dict(version='0.2.0', name='Feature', description='Local test', files=[record])
        self.lock = dict(schema_version=1, kind='BoilerSharedAdaptationsInputs',
            components={feature: {model: dict(part, files=[dict(record)]) for model in builder.VERSIONS} for feature in builder.FEATURES})
        for model in builder.VERSIONS:
            targets = set(builder.BATTERY_FILES)
            if model == 'x2-mini':
                targets -= {'var/lib/oxp3-battery/battery_provider.py', 'etc/systemd/system/oxp3-battery.service',
                            'etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf',
                            'etc/steamos-manager/remotes.d/oxp3-battery.toml'}
            self.lock['components']['battery'][model]['files'] = [dict(record, target=target) for target in sorted(targets)]
            self.lock['components']['microphone'][model]['files'] = [dict(record, target=target) for target in sorted(builder.MIC_TARGETS)]

    def read(self):
        path = self.root/'inputs.json'; path.write_text(json.dumps(self.lock))
        return builder.overlay_inputs(path)

    def test_exact_all_feature_inputs(self):
        _, result = self.read()
        self.assertEqual(set(result), {'oxp3', 'x2-mini'})
        self.assertEqual(set(result['x2-mini']), set(builder.FEATURES))

    def test_changed_source_rejected(self):
        (self.root/'adapter.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.read()

    def test_x2_native_provider_rejected(self):
        self.lock['components']['battery']['x2-mini']['files'][0]['target'] = 'etc/systemd/system/oxp3-battery.service'
        with self.assertRaisesRegex(ValueError, 'model-specific Battery|EC or native provider'):
            self.read()

    def test_missing_real_component_rejected(self):
        self.lock['components']['microphone']['oxp3']['files'] = []
        with self.assertRaisesRegex(ValueError, 'Nonempty'):
            self.read()

    def test_fingerprint_inputs_are_rejected(self):
        self.lock['components']['fingerprint'] = self.lock['components']['microphone']
        with self.assertRaisesRegex(ValueError, 'Only Battery and microphone'):
            self.read()

    def test_inventory_keeps_nested_manifests(self):
        (self.root/'manifest.json').write_text('{}')
        nested = self.root/'nested'; nested.mkdir(); (nested/'manifest.json').write_text('{}')
        paths = {row['path'] for row in builder.inventory(self.root)}
        self.assertNotIn('manifest.json', paths)
        self.assertIn('nested/manifest.json', paths)


if __name__ == '__main__':
    unittest.main()
