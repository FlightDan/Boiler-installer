import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preparer = load('provider_preparer', ROOT/'scripts/prepare-oxp3-efi-provider-lock.py')
provider = load('provider_validator', ROOT/'components/oxp3_efi_provider.py')


class ProviderLockTests(unittest.TestCase):
    def test_locked_public_sources_and_bridge_are_enforced(self):
        lock = preparer.build()
        public = json.loads((ROOT/'adaptations/source-lock.json').read_bytes())
        by_upstream = {row['upstream_path']: row for row in public['files']}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); package = root/'package'; package.mkdir()
            for row in lock['files']:
                name = row['path']; target = package/name; target.parent.mkdir(parents=True, exist_ok=True)
                if name.startswith('payload/legacy/components/'):
                    upstream = preparer.BASE + name.removeprefix('payload/legacy/components/')
                    source = ROOT/'adaptations'/by_upstream[upstream]['path']
                elif name == preparer.ADOPTION:
                    source = preparer.BRIDGE/'public-adoption.py'
                else:
                    source = preparer.BRIDGE/name
                target.write_bytes(source.read_bytes())
            lock_file = root/'lock.json'; lock_file.write_text(json.dumps(lock))
            self.assertEqual(len(provider.verify(package, lock_file)), 64)
            decoder = package/preparer.ADOPTION
            decoder.write_bytes(decoder.read_bytes() + b'\n# changed')
            with self.assertRaisesRegex(ValueError, 'differs'): provider.verify(package, lock_file)
            decoder.write_bytes((preparer.BRIDGE/'public-adoption.py').read_bytes())
            target = package/'payload/legacy/components/split_lock.py'
            target.write_bytes(target.read_bytes() + b'\n# changed')
            with self.assertRaisesRegex(ValueError, 'differs'): provider.verify(package, lock_file)
            target.write_bytes((ROOT/'adaptations/common/scripts/split_lock.py').read_bytes())
            (target.parent/'subprocess.py').write_text('raise RuntimeError("injected")')
            with self.assertRaisesRegex(ValueError, 'Unexpected'): provider.verify(package, lock_file)


if __name__ == '__main__':
    unittest.main()
