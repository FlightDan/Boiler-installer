"""The repair overlay must pass the maintenance loader's inner inventory check."""
import hashlib
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


builder = load('repair_manifest_builder', ROOT/'adaptations/scripts/build-upstream-repairs.py')
loader = load('repair_manifest_loader', ROOT/'adaptations/brands/onexplayer/oxp3/scripts/maintenance_tool.py')


class MaintenanceInventory(unittest.TestCase):
    def test_final_xevma_loader_uses_fixed_multicall_arguments(self):
        source=(ROOT/'adaptations/brands/onexplayer/oxp3/current/xevma-20261029/load.py').read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),'bf49070cb295c872c3059480cc0a393c77bc1b129ffdaba05e12e90ec78dc646')
        result=builder.corrected_xevma_loader(source)
        self.assertIn(b"subprocess.run(['modprobe', 'xe'], executable='/usr/bin/kmod', check=True)",result)
        self.assertIn(b"subprocess.run(['insmod', str(module)], executable='/usr/bin/kmod', check=True)",result)
        self.assertIn(b"'ONE-NETBOOK', 'ONEXPLAYER 3'",result)
        with self.assertRaises(ValueError): builder.corrected_xevma_loader(result)

    def test_final_inventory_contains_current_bridge_shim(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/'adapter.py').write_bytes(b'old adapter')
            (directory/'maintenance_adapter.py').write_bytes(b'old maintenance bridge')
            nested = directory/'payload/manifest.json'
            nested.parent.mkdir()
            nested.write_bytes(b'{}')
            changed = []
            builder.refresh_bridge_sources(directory, ROOT, changed)
            expected = (ROOT/'adaptations/brands/onexplayer/oxp3/bridge/maintenance_adapter.py').read_bytes()
            self.assertEqual((directory/'maintenance_adapter.py').read_bytes(), expected)
            self.assertIn('maintenance_adapter.py', changed)
            inventory = {row['path']: row for row in builder.package_inventory(directory)}
            self.assertEqual(inventory['maintenance_adapter.py'],
                             {'path': 'maintenance_adapter.py', 'size': len(expected), 'sha256': hashlib.sha256(expected).hexdigest()})
            self.assertIn('payload/manifest.json', inventory)
            self.assertEqual(builder.VERSION, '0.1.0-localtest.20261102.14')

    def fixture(self, directory):
        payload = directory/'payload/legacy'
        app = payload/'maintenance/app'
        package = app/'oxp3_maintenance'
        package.mkdir(parents=True)
        (package/'__init__.py').write_bytes(b'')
        (package/'operations.py').write_bytes(
            b'def runtime_files(app, release, state, installed_runtime=None):\n    return {"fixture": b"ok"}\n')
        for name in ('components.py', 'input_profile.py'):
            (package/name).write_bytes(b'# original\n')
        (app.parent/'manifest.json').write_text(json.dumps(dict(schema=1, files={
            p.relative_to(app).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in app.rglob('*') if p.is_file()})))
        return payload, package

    def test_overlay_fails_old_inventory_and_passes_regenerated_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            payload, package = self.fixture(directory)
            for name in ('components.py', 'input_profile.py'):
                (package/name).write_bytes(b'# generated repair overlay\n')
            with self.assertRaisesRegex(RuntimeError, 'differs from its staged manifest'):
                loader.files(directory/'target', payload, {})
            builder.refresh_maintenance_manifest(directory)
            self.assertEqual(loader.files(directory/'target', payload, {}), {'fixture': b'ok'})
            (package/'components.py').write_bytes(b'# later corruption\n')
            with self.assertRaisesRegex(RuntimeError, 'differs from its staged manifest'):
                loader.files(directory/'target', payload, {})

    def test_unlisted_or_missing_member_is_not_silently_accepted(self):
        for extra in (False, True):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                _, package = self.fixture(directory)
                if extra:
                    (package/'unexpected.py').write_bytes(b'# extra\n')
                else:
                    (package/'components.py').unlink()
                with self.assertRaisesRegex(ValueError, 'member inventory changed'):
                    builder.refresh_maintenance_manifest(directory)


if __name__ == '__main__':
    unittest.main()
