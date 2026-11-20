import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'components')]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class CpuFreqDownloadChecks(unittest.TestCase):
    def test_private_payload_and_rejections(self):
        preparer, downloader = module('prepare-integration'), module('download-firmware')
        for vendor in ('intel', 'amd'):
            with self.subTest(vendor=vendor), tempfile.TemporaryDirectory() as temp:
                base = Path(temp); source = base / 'source'
                manifest = preparer.prepare(source)
                identity = 'cpufreq:' + vendor
                options = dict(schema=1, install_mode='custom', repairs=[identity], plugins=['core:maintenance'])
                catalog = source / 'manifest.json'; plan = base / 'plan.json'
                def bind():
                    plan.write_text(json.dumps(dict(schema=1, selection=options,
                        catalog_sha256=hashlib.sha256(catalog.read_bytes()).hexdigest(), slots={})))
                def no_network(_): self.fail('CPU frequency assets must not use network')
                bind()
                out = downloader.download(catalog, plan, base/'output', source, base/'cache', fetch=no_network)
                for entry in manifest['components'][identity]['files']:
                    self.assertEqual((source/entry['source']).read_bytes(), (out/'integration'/entry['source']).read_bytes())
                self.assertFalse((base/'cache').exists())
                changed = copy.deepcopy(manifest)
                changed['components'][identity]['files'][0]['sha256'] = '0' * 64
                catalog.write_text(json.dumps(changed)); bind()
                with self.assertRaisesRegex(ValueError, 'CPU frequency inventory differs'):
                    downloader.download(catalog, plan, base/'bad-metadata', source, base/'cache', fetch=no_network)
                self.assertFalse((base/'bad-metadata').exists())
                catalog.write_text(json.dumps(manifest)); bind()
                (source/manifest['components'][identity]['files'][0]['source']).write_bytes(b'bad')
                with self.assertRaisesRegex(ValueError, 'Locked payload'):
                    downloader.download(catalog, plan, base/'bad-bytes', source, base/'cache', fetch=no_network)
                self.assertFalse((base/'bad-bytes').exists())


if __name__ == '__main__': unittest.main()
