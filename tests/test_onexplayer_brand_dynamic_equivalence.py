"""Compare legacy dynamic file maps to resources built from canonical TOON."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from protocol.resource_pool import validate_pool

COMPONENTS = os.environ.get('BOILER_BRAND_OLD_COMPONENTS')
POOL = os.environ.get('BOILER_BRAND_RESOURCE_POOL')


@unittest.skipUnless(COMPONENTS and POOL, 'explicit legacy components and canonical resource pool required')
class DynamicResourceEquivalence(unittest.TestCase):
    def test_selected_dynamic_file_outputs_match_canonical_resources(self):
        components, pool = Path(COMPONENTS), Path(POOL)
        resources = {row['id']: row for row in validate_pool(pool)['resources']}
        for identity, name in (
            ('resource:common-slot-switch', 'slot_switch'),
            ('resource:common-partition-mount', 'partition_mount'),
            ('resource:common-windows-reboot', 'windows_reboot'),
        ):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as directory:
                spec = importlib.util.spec_from_file_location('brand_test_' + name, components / (name + '.py'))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                root = Path(directory)
                for binary in ('python3', 'efibootmgr', 'systemctl', 'pkexec', 'kdialog'):
                    path = root / 'usr/bin' / binary
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'synthetic tool')
                with patch.object(module.os.path, 'lexists', return_value=False):
                    if hasattr(module, 'image_tool'):
                        module.image_tool = lambda root, tool: True
                    actual = module.files(root, components)
                expected = {row['target']: ((pool / row['source']).read_bytes(), row.get('mode', 0o644))
                            for row in resources[identity]['install']['files']}
                observed = {target: (row['data'], row.get('mode', 0o644))
                            for target, row in actual.items()}
                self.assertEqual(observed, expected)


if __name__ == '__main__':
    unittest.main()
