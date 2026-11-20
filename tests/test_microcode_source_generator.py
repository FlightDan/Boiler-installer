import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / 'scripts/prepare-microcode-catalog.py'))


class MicrocodeSourceGeneratorChecks(unittest.TestCase):
    def test_pinned_inventory_and_cache_are_verified(self):
        import base64
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'components').mkdir()
            spec = json.loads((ROOT / 'components/microcode-sources.json').read_text())
            (root / 'components/microcode-sources.json').write_text(json.dumps(spec))
            sources = {'intel-ucode/06-bd-01': b'intel-source', 'license': b'intel-license',
                       'amd-ucode/microcode_amd_fam1ah.bin': b'amd-source',
                       'LICENSES/LICENSE.amd-ucode': b'amd-license', 'WHENCE': b'origin'}
            blobs = {name: MODULE['git_blob'](data) for name, data in sources.items()}
            calls = []
            def fetch(url):
                calls.append(url)
                if '/contents/intel-ucode?' in url:
                    return json.dumps([dict(type='file', name='06-bd-01', sha=blobs['intel-ucode/06-bd-01'])]).encode()
                if '/contents/license?' in url:
                    return json.dumps(dict(sha=blobs['license'])).encode()
                if url.endswith('?format=JSON'):
                    directory = 'amd-ucode' if '/amd-ucode/' in url else 'LICENSES' if '/LICENSES/' in url else ''
                    entries = [dict(mode=0o100644, name=Path(name).name, id=blobs[name]) for name in sources
                               if str(Path(name).parent).replace('\\', '/') == (directory or '.') and name != 'license']
                    return b")]}'\n" + json.dumps(dict(entries=entries)).encode()
                for name, data in sources.items():
                    if url.endswith('/' + name + '?format=TEXT'): return base64.b64encode(data)
                    if url.endswith('/' + name): return data
                raise AssertionError(url)
            cache = root / 'cache'
            result = MODULE['refresh'](root, cache, fetch)
            intel = result['components']['microcode:intel']['files'][0]
            self.assertEqual('microcode/intel/intel-ucode/06-bd-01', intel['source'])
            self.assertEqual(hashlib.sha256(sources['intel-ucode/06-bd-01']).hexdigest(), intel['sha256'])
            self.assertEqual(blobs['intel-ucode/06-bd-01'], intel['git_blob'])
            self.assertEqual(2, len(result['components']['microcode:amd']['licenses']))
            calls.clear(); MODULE['refresh'](root, cache, fetch)
            self.assertFalse(any('/raw.githubusercontent.com/' in call or call.endswith('?format=TEXT') for call in calls))
            (cache / blobs['intel-ucode/06-bd-01']).write_bytes(b'corrupt')
            calls.clear(); MODULE['refresh'](root, cache, fetch)
            self.assertEqual(sources['intel-ucode/06-bd-01'], (cache / blobs['intel-ucode/06-bd-01']).read_bytes())
            def wrong_bytes(url):
                if 'raw.githubusercontent.com' in url and url.endswith('06-bd-01'): return b'changed'
                return fetch(url)
            (cache / blobs['intel-ucode/06-bd-01']).unlink()
            with self.assertRaisesRegex(ValueError, 'Git object mismatch'):
                MODULE['refresh'](root, cache, wrong_bytes)

    def test_other_repository_and_unpinned_ref_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'components').mkdir()
            for key, value in [('repository', 'someone/firmware'), ('revision', 'main')]:
                spec = json.loads((ROOT / 'components/microcode-sources.json').read_text())
                spec['sources']['intel'][key] = value
                (root / 'components/microcode-sources.json').write_text(json.dumps(spec))
                with self.assertRaisesRegex(ValueError, 'immutable official source'):
                    MODULE['refresh'](root, root / 'cache', lambda url: self.fail('Network before validation'))


if __name__ == '__main__':
    unittest.main()
