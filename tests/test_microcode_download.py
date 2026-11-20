import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from urllib.error import URLError
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'components'))
NS = runpy.run_path(str(ROOT / 'scripts/download-firmware.py'))


class IntelMicrocodeDownloadChecks(unittest.TestCase):
    def setUp(self):
        self.item = json.loads((ROOT / 'components/microcode-firmware.json').read_text())['components']['microcode:intel']['files'][0]
        self.item = dict(self.item, source=self.item['original_source'])

    def test_fixed_intel_source_and_api_fallback(self):
        content = b'example'
        item = dict(self.item, size=len(content), git_blob='a' * 40)
        class Opener:
            def open(self, request, timeout):
                url = request if isinstance(request, str) else request.full_url
                if url.startswith('https://raw.githubusercontent.com/'): raise URLError('unavailable')
                return BytesIO(json.dumps(dict(sha='a' * 40, size=len(content), encoding='base64', content=base64.b64encode(content).decode())).encode())
        with patch('urllib.request.build_opener', return_value=Opener()):
            self.assertEqual(content, NS['fetch_bytes'](item))

    def test_other_repository_and_revision_alias_are_rejected(self):
        for url in [self.item['url'].replace('/intel/', '/someone/'), self.item['url'].replace(self.item['url'].split('/')[5], 'main')]:
            with self.assertRaisesRegex(ValueError, 'fixed upstream source'):
                NS['source_url'](dict(self.item, url=url))

    def test_all_intel_sources_unavailable_is_distinct(self):
        class Opener:
            def open(self, *args, **kwargs): raise URLError('offline')
        with patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaises(NS['FirmwareSourcesUnavailable']): NS['fetch_bytes'](self.item)

    def test_wrong_api_blob_is_not_a_source_fallback(self):
        class Opener:
            def open(self, request, timeout):
                if isinstance(request, str): raise URLError('unavailable')
                return BytesIO(json.dumps(dict(sha='f' * 40, size=self_size, encoding='base64', content='')).encode())
        self_size = self.item['size']
        with patch('urllib.request.build_opener', return_value=Opener()):
            with self.assertRaisesRegex(ValueError, 'Git object metadata differs'): NS['fetch_bytes'](self.item)

    def test_whole_vendor_downloads_even_when_native_files_exist(self):
        from payload_lock import record
        origin = 'https://raw.githubusercontent.com/intel/Intel-Linux-Processor-Microcode-Data-Files/' + 'a' * 40 + '/'
        data = {'intel-ucode/06-bd-01': b'cpu-a', 'intel-ucode/06-2a-07': b'cpu-b', 'license': b'notice'}
        entries = []
        for name, content in data.items():
            entries.append(record('microcode/intel/' + name, content, original_source=name,
                target='usr/lib/firmware/' + name if name != 'license' else 'licenses/license',
                git_blob=hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest(), url=origin + name))
        bundle = dict(schema=1, kind='CpuMicrocodeBundle', id='microcode:intel', vendor='intel', version='fixture',
            available=True, delivery='on-demand', runtime='not_validated', files=entries[:2], licenses=entries[2:],
            provenance=dict(url=origin, revision='a' * 40, repository='intel/Intel-Linux-Processor-Microcode-Data-Files'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); catalog = root / 'catalog.json'; plan = root / 'plan.json'
            raw = json.dumps(dict(schema=1, components={'microcode:intel': bundle})).encode(); catalog.write_bytes(raw)
            records = {entry['target']: entry for entry in entries[:2]}
            component_plan = dict(files=[], coverage='runtime_dependent', runtime='not_validated')
            selection = dict(schema=1, repairs=['microcode:intel'], plugins=[])
            bound = dict(schema=1, catalog_sha256=hashlib.sha256(raw).hexdigest(), selection=selection,
                slots={slot: dict(records=records, plans={'microcode:intel': component_plan}) for slot in ('A', 'B')})
            plan.write_text(json.dumps(bound))
            calls = []
            def fetch(entry):
                calls.append(entry['source']); return data[entry['source']]
            with patch.object(NS['microcode'], 'firmware_metadata', return_value={'microcode:intel': bundle}):
                NS['download'](catalog, plan, root / 'output', root, root / 'cache', fetch=fetch)
                self.assertEqual(set(data), set(calls))
                for entry in entries:
                    self.assertEqual(data[entry['original_source']], (root / 'output/integration' / entry['source']).read_bytes())
                calls.clear()
                NS['download'](catalog, plan, root / 'cached-output', root, root / 'cache', fetch=fetch)
                self.assertEqual([], calls)
                bound['slots']['B']['records'] = {}; plan.write_text(json.dumps(bound))
                with self.assertRaisesRegex(ValueError, 'Slot records differ'):
                    NS['download'](catalog, plan, root / 'invalid-output', root, root / 'cache', fetch=fetch)
                self.assertFalse((root / 'invalid-output').exists())


if __name__ == '__main__': unittest.main()
