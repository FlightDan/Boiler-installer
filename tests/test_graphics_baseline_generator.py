"""Reproducible generator tests use small Git-blob fixtures without network."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
from graphics_catalog import load_spec, firmware_metadata, metadata, BASE_PREFIX

loader = importlib.util.spec_from_file_location('graphics_baseline_generator', ROOT / 'scripts/prepare-graphics-catalog.py')
generator = importlib.util.module_from_spec(loader)
loader.loader.exec_module(generator)


class GeneratorChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'components').mkdir()
        original = json.loads((ROOT / 'components/graphics-baseline-spec.json').read_text(encoding='utf-8'))
        self.identity = 'graphics:amd-780m'
        self.spec = dict(original, profiles={self.identity: dict(original['profiles'][self.identity],
            patterns=['amdgpu/gc_11_0_1_*.bin'], extra_paths=[])})
        catalogue = json.loads((ROOT / 'components/graphics-support.json').read_text(encoding='utf-8'))
        self.catalogue = dict(catalogue, profiles=[catalogue['profiles'][0]], requests=[catalogue['requests'][0]])
        self.objects = {
            'amdgpu/gc_11_0_1_mes_2.bin': b'firmware fixture\x00\x01',
            'LICENSES/LICENSE.amdgpu': b'fixture redistributable license',
            'WHENCE': (b'Driver: amdgpu\nFile: amdgpu/gc_11_0_1_mes_2.bin\n'
                       b'Link: amdgpu/gc_11_0_1_mes.bin -> gc_11_0_1_mes_2.bin\n'
                       b'Licence: Redistributable. See LICENSE.amdgpu for details.\n'),
        }
        self.write_inputs()

    def write_inputs(self):
        for name, value in [('graphics-baseline-spec.json', self.spec), ('graphics-support.json', self.catalogue)]:
            (self.root / 'components' / name).write_text(json.dumps(value), encoding='utf-8')

    def fetch(self, url):
        base = self.spec['upstream_url']
        self.assertTrue(url.startswith(base), url)
        path, form = url[len(base):].split('?format=')
        if form == 'TEXT': return base64.b64encode(self.objects[path])
        self.assertEqual('JSON', form)
        directory = path.rstrip('/')
        entries = []
        for source, data in self.objects.items():
            p = Path(source)
            parent = p.parent.as_posix()
            if parent == '.': parent = ''
            if parent == directory: entries.append(dict(name=p.name, mode=0o100644, id=generator.git_blob(data)))
        return b")]}'" + json.dumps(dict(entries=entries)).encode()

    def refresh(self): return generator.refresh(self.root, self.root / 'cache', fetch=self.fetch)

    def test_real_byte_hashes_aliases_and_notices_are_reproducible(self):
        first = self.refresh()
        second = self.refresh()
        self.assertEqual(first, second)
        self.assertEqual(first['components'], firmware_metadata(self.root))
        bundle = first['components'][self.identity]
        self.assertEqual(2, len(bundle['files']))
        alias = next(f for f in bundle['files'] if 'alias_of' in f)
        self.assertEqual('amdgpu/gc_11_0_1_mes_2.bin', alias['alias_of'])
        self.assertEqual(alias['git_blob'], generator.git_blob(self.objects[alias['alias_of']]))
        self.assertEqual({'licenses/WHENCE', 'licenses/LICENSE.amdgpu'}, {f['target'] for f in bundle['licenses']})
        self.assertEqual(0, metadata(self.root)['firmware_binary_count'])

    def test_upstream_revision_and_version_edits_are_accepted_by_regeneration(self):
        self.refresh()
        self.spec['revision'] = 'a' * 40
        self.spec['upstream_url'] = BASE_PREFIX + self.spec['revision'] + '/'
        self.spec['version'] = '0.1.0-baseline.2'
        self.write_inputs()
        # load_spec has no fixed revision or approval requirement.
        self.assertEqual('a' * 40, load_spec(self.root)[0]['revision'])
        with self.assertRaisesRegex(ValueError, 'stale'): firmware_metadata(self.root)
        refreshed = self.refresh()
        self.assertEqual('a' * 40, refreshed['revision'])
        self.assertEqual('0.1.0-baseline.2', firmware_metadata(self.root)[self.identity]['version'])

    def test_wrong_downloaded_git_blob_is_rejected(self):
        original = self.fetch
        def corrupt(url):
            if 'gc_11_0_1_mes_2.bin?format=TEXT' in url: return base64.b64encode(b'wrong bytes')
            return original(url)
        with self.assertRaisesRegex(ValueError, 'Git blob mismatch'):
            generator.refresh(self.root, self.root / 'cache', fetch=corrupt)

    def test_cached_bytes_are_reverified_against_current_git_object(self):
        self.refresh()
        object_id = generator.git_blob(self.objects['amdgpu/gc_11_0_1_mes_2.bin'])
        (self.root / 'cache' / object_id).write_bytes(b'corrupt cached bytes')
        self.refresh()
        self.assertEqual(self.objects['amdgpu/gc_11_0_1_mes_2.bin'], (self.root / 'cache' / object_id).read_bytes())

    def test_alias_cannot_escape_candidate_family_or_form_cycle(self):
        allowed = lambda p: p.startswith('amdgpu/gc_11_0_1_')
        for links in ({'amdgpu/gc_11_0_1_mes.bin': ('../xe/ptl_guc_70.bin', [])},
                      {'amdgpu/gc_11_0_1_mes.bin': ('gc_11_0_1_mes.bin', [])}):
            with self.subTest(links=links), self.assertRaises(ValueError):
                generator.resolve_alias('amdgpu/gc_11_0_1_mes.bin', links, {}, allowed)


if __name__ == '__main__': unittest.main()
