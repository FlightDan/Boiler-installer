"""Per-run GPU candidate download scope and adjustable snapshot integrity."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components')]
spec = importlib.util.spec_from_file_location('graphics_download_checks', ROOT / 'scripts/download-firmware.py')
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class GraphicsDownloadChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identity = 'graphics:amd-780m'
        self.origin = 'https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/' + 'b' * 40 + '/'
        self.content = {'amdgpu/test.bin': b'candidate bytes', 'amdgpu/other.bin': b'other candidate', 'LICENSES/LICENSE.amdgpu': b'license'}
        def record(path, target, source):
            data = self.content[path]
            return dict(source=source, target=target, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        git_blob=hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest(), url=self.origin + path)
        self.first = record('amdgpu/test.bin', 'usr/lib/firmware/amdgpu/test.bin', 'graphics/amd-780m/firmware/amdgpu/test.bin')
        self.second = record('amdgpu/other.bin', 'usr/lib/firmware/amdgpu/other.bin', 'graphics/amd-780m/firmware/amdgpu/other.bin')
        self.license = record('LICENSES/LICENSE.amdgpu', 'licenses/LICENSE.amdgpu', 'graphics/amd-780m/licenses/LICENSE.amdgpu')
        self.bundle = dict(schema=1, kind='GraphicsFirmwareBundle', id=self.identity, version='test-baseline', drivers=['amdgpu'],
                           available=True, delivery='on-demand', runtime='not_validated', status='experimental-baseline',
                           baseline=dict(revision='b' * 40, version='test-baseline', spec_sha256='c' * 64),
                           provenance=dict(revision='b' * 40, url=self.origin), files=[self.first, self.second], licenses=[self.license])
        self.options = dict(schema=1, repairs=[], plugins=[], install_mode='custom', graphics_profile=self.identity)
        self.fetched = []

    def materialize(self, slots, *, changed_bundle=None):
        catalog = dict(schema=1, components={self.identity: changed_bundle or self.bundle})
        data = json.dumps(catalog).encode()
        catalog_path = self.root / 'catalog.json'; catalog_path.write_bytes(data)
        plan = dict(schema=1, catalog_sha256=hashlib.sha256(data).hexdigest(), selection=self.options, slots=slots)
        plan_path = self.root / 'plan.json'; plan_path.write_text(json.dumps(plan))
        def fetch(item):
            self.fetched.append(item['source'])
            return self.content[item['source']]
        with patch.object(downloader, 'graphics_metadata', return_value={self.identity: self.bundle}):
            return downloader.download(catalog_path, plan_path, self.root / 'output', self.root / 'no-core',
                                       self.root / 'cache', fetch=fetch)

    def slot(self, *records):
        return dict(records={r['target']: r for r in records}, plans={self.identity: dict(files=[r['target'] for r in records], coverage='runtime_dependent', runtime='not_validated')})

    def test_downloads_union_of_missing_slot_candidates_and_license_only(self):
        output = self.materialize(dict(A=self.slot(self.first), B=self.slot(self.second)))
        self.assertEqual(set(self.content), set(self.fetched))
        self.assertEqual(self.content['amdgpu/test.bin'], (output / 'integration' / self.first['source']).read_bytes())
        self.assertEqual(self.content['amdgpu/other.bin'], (output / 'integration' / self.second['source']).read_bytes())

    def test_preserved_root_candidates_need_only_notices_for_boot_copy(self):
        output = self.materialize(dict(A=self.slot(), B=self.slot()))
        self.assertEqual(['LICENSES/LICENSE.amdgpu'], self.fetched)
        self.assertTrue((output / 'integration' / self.license['source']).is_file())
        self.assertFalse((output / 'integration' / self.first['source']).exists())

    def test_wrong_source_snapshot_fails_before_network_or_publication(self):
        changed = copy.deepcopy(self.bundle); changed['files'][0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'adjustable baseline'):
            self.materialize(dict(A=self.slot(self.first), B=self.slot()), changed_bundle=changed)
        self.assertEqual([], self.fetched)
        self.assertFalse((self.root / 'output').exists())

    def test_unknown_candidate_and_false_hardware_claim_fail_before_fetch(self):
        slot = self.slot(self.first)
        slot['plans'][self.identity]['files'] = ['usr/lib/firmware/amdgpu/injected.bin']
        with self.assertRaisesRegex(ValueError, 'Unknown firmware target'):
            self.materialize(dict(A=slot, B=self.slot()))
        self.assertEqual([], self.fetched)
        slot = self.slot(self.first); slot['plans'][self.identity]['runtime'] = 'validated'
        with self.assertRaisesRegex(ValueError, 'runtime dependent'):
            self.materialize(dict(A=slot, B=self.slot()))

    def test_snapshot_revision_is_editable_and_origins_remain_scoped(self):
        original = dict(self.first, source='amdgpu/test.bin')
        self.assertEqual('amdgpu/test.bin', downloader.source_url(original))
        original['url'] = original['url'].replace('b' * 40, 'd' * 40)
        self.assertEqual('amdgpu/test.bin', downloader.source_url(original))
        original['url'] = original['url'].replace('kernel.googlesource.com', 'example.invalid')
        with self.assertRaises(ValueError): downloader.source_url(original)


if __name__ == '__main__': unittest.main()
