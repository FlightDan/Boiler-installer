"""Targeted additive inputs; plugin code and game configuration are never run."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('opti_builder_tests', ROOT/'scripts/build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class OptiUpdateTests(unittest.TestCase):
    def fixture(self, work):
        runtime = io.BytesIO()
        with tarfile.open(fileobj=runtime, mode='w:gz') as archive:
            data = b'public runtime fixture\n'
            info = tarfile.TarInfo('runtime/fixture.txt')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        runtime = runtime.getvalue()
        runtime_pin = dict(schema=1, version='20261005', size_bytes=len(runtime),
                           sha256=hashlib.sha256(runtime).hexdigest(), published=True,
                           url='https://example.invalid/public-runtime.tar.gz')
        records = {'plugin.json': b'{"name":"Opti2XMX"}', 'package.json': b'{"version":"0.3.0"}',
                   'runtime-source.json': json.dumps(runtime_pin).encode(),
                   'main.py': b'raise RuntimeError("Do not execute downloaded plugin code")\n'}
        plugin = work/'opti2xmx-0.3.0.zip'
        with zipfile.ZipFile(plugin, 'w') as archive:
            for name, data in records.items():
                archive.writestr('opti2xmx/'+name, data)
        plugin_pin = dict(version='0.3.0', filename=plugin.name, size_bytes=plugin.stat().st_size,
                          sha256=builder.digest(plugin), url='https://example.invalid/opti.zip')
        paths, archives = [], []
        for version in ('0.9.4', '0.7.9', '10.0.0-pre1_20261025'):
            path = work/(version+'.7z')
            data = ('original archive '+version).encode()
            path.write_bytes(data)
            paths.append(path)
            archives.append(dict(version=version, tag='v'+version, filename=path.name,
                                 size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        pins = dict(plugin_archive=plugin_pin, plugin_release=dict(plugin_version='0.3.0', runtime=runtime_pin,
                    files={name: hashlib.sha256(data).hexdigest() for name, data in records.items()}),
                    optiscaler_release=dict(schema=1, archives=archives))
        output = work/'output'
        legacy = output/'payload/legacy'
        (legacy/'integration/opti2xmx').mkdir(parents=True)
        (legacy/'integration/opti2xmx/runtime.tar.gz').write_bytes(runtime)
        (legacy/'integration/opti2xmx/000.bin').write_bytes(b'old public plugin')
        (legacy/'components/opti2xmx').mkdir(parents=True)
        (legacy/'components/opti2xmx/obsolete.py').write_bytes(b'old public source')
        unrelated = legacy/'integration/xe-dsb/000.bin'
        unrelated.parent.mkdir()
        unrelated.write_bytes(b'unchanged independent component')
        resources = dict(opti2xmx=dict(files=[dict(target='home/deck/homebrew/plugins/opti2xmx/main.py',
            source='opti2xmx/000.bin', mode=420, user=False)], runtime_archive='opti2xmx/runtime.tar.gz',
            units=[], packages={}, missing=[]), xe_dsb=dict(files=[dict(source='xe-dsb/000.bin')]))
        return plugin, paths, pins, records, output, resources

    def test_lock_allows_only_approved_published_versions(self):
        pins = builder.opti_lock((ROOT/'opti-offline-lock.json').read_bytes())
        self.assertEqual(pins['plugin_archive']['version'], '0.3.0')
        self.assertEqual(len(pins['optiscaler_release']['archives']), 3)
        changed = copy.deepcopy(pins)
        changed['plugin_archive']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'Unapproved'):
            builder.opti_lock(json.dumps(changed))

    def test_tampered_or_incomplete_additive_inputs_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin, paths, pins, *_ = self.fixture(Path(temporary))
            with self.assertRaisesRegex(ValueError, 'all three'):
                builder.read_opti_inputs(plugin, paths[:2], pins)
            paths[2].write_bytes(b'changed archive')
            with self.assertRaises(ValueError):
                builder.read_opti_inputs(plugin, paths, pins)
            plugin.write_bytes(b'changed plugin')
            with self.assertRaises(ValueError):
                builder.read_opti_inputs(plugin, paths, pins)

    def test_update_rebuilds_only_opti_component_and_consumable_cache_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin, paths, pins, expected, output, resources = self.fixture(Path(temporary))
            records, offline = builder.read_opti_inputs(plugin, paths, pins)
            unrelated = copy.deepcopy(resources['xe_dsb'])
            runtime = (output/'payload/legacy/integration/opti2xmx/runtime.tar.gz').read_bytes()
            report = builder.apply_opti_update(output, resources, records, offline, pins)
            self.assertEqual(resources['xe_dsb'], unrelated)
            self.assertEqual((output/'payload/legacy/integration/xe-dsb/000.bin').read_bytes(), b'unchanged independent component')
            self.assertEqual((output/'payload/legacy/integration/opti2xmx/runtime.tar.gz').read_bytes(), runtime)
            self.assertFalse((output/'payload/legacy/components/opti2xmx/obsolete.py').exists())
            targets = {item['target']: item for item in resources['opti2xmx']['files']}
            for name, data in expected.items():
                entry = targets['home/deck/homebrew/plugins/opti2xmx/'+name]
                self.assertFalse(entry['user'])
                self.assertEqual((output/'payload/legacy/integration'/entry['source']).read_bytes(), data)
                self.assertEqual((output/'payload/legacy/components/opti2xmx'/name).read_bytes(), data)
            for name, data in offline.items():
                entry = targets['home/deck/.local/share/opti2xmx/downloads/'+name]
                self.assertTrue(entry['user'])
                self.assertEqual((output/'payload/legacy/integration'/entry['source']).read_bytes(), data)
            self.assertFalse(report['contains_game_state'])
            self.assertEqual(report['file_count'], len(records)+1+3)
            self.assertTrue(all(path.startswith(('home/deck/homebrew/plugins/opti2xmx/',
                'home/deck/.local/share/opti2xmx/downloads/')) or path=='home/deck/.local/share/opti2xmx/runtime.json' for path in targets))

    def test_extra_plugin_members_fail_even_with_a_different_whole_archive_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin, paths, pins, *_ = self.fixture(Path(temporary))
            with zipfile.ZipFile(plugin, 'a') as archive:
                archive.writestr('unapproved/private.py', b'not part of the public layout')
            pins['plugin_archive'].update(size_bytes=plugin.stat().st_size, sha256=builder.digest(plugin))
            with self.assertRaisesRegex(ValueError, 'membership'):
                builder.read_opti_inputs(plugin, paths, pins)

    def test_changed_base_runtime_is_refused_before_component_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin, paths, pins, _, output, resources = self.fixture(Path(temporary))
            records, offline = builder.read_opti_inputs(plugin, paths, pins)
            (output/'payload/legacy/integration/opti2xmx/runtime.tar.gz').write_bytes(b'not the original public runtime')
            old = (output/'payload/legacy/integration/opti2xmx/000.bin').read_bytes()
            with self.assertRaisesRegex(ValueError, 'unchanged published Opti runtime'):
                builder.apply_opti_update(output, resources, records, offline, pins)
            self.assertEqual((output/'payload/legacy/integration/opti2xmx/000.bin').read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
