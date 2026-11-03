from pathlib import Path
import tempfile
import unittest
from unittest import mock

import integration


class SsdIntegrationPreflightTests(unittest.TestCase):
    def test_model_boot_targets_and_hooks_refused_before_component_writes(self):
        options = dict(schema=1, repairs=['ssd:gm7-boot-repair'], plugins=['core:maintenance'],
                       install_mode='custom', device_package='fixture', device_manifest_sha256='a'*64,
                       device_trust='developer-session', device_components=['device:fixture'])
        source = dict(ID='steamos', VERSION_ID='3.10', BUILD_ID='fixture', KERNELS=['kernel'])
        context = dict(kind='physical', bus='NVMe', model='Predator SSD GM7 1TB',
                       serial='synthetic', disposition='match')
        for component in (dict(id='device:fixture', files=[dict(target='efi/EFI/STEAMOS/GRUB.CFG')], hooks={}),
                          dict(id='device:fixture', files=[], hooks=dict(apply='scripts/apply.sh'))):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                descriptor = dict(device_warnings=[])
                with mock.patch.object(integration, 'release', return_value=source), \
                     mock.patch.object(integration, 'device_plan', return_value=(root, [component], {}, descriptor)), \
                     mock.patch('maintenance.shared_models.check_static_component'), \
                     mock.patch.object(integration.system_presets, 'prepare', return_value=(None, {})), \
                     mock.patch.object(integration, 'load_manifest', return_value={}), \
                     mock.patch.object(integration, 'maintenance_api') as writer:
                    with self.assertRaisesRegex(ValueError, 'conflicts|hooks'):
                        integration.stage(root, root, options, 'A', ssd_context=context,
                                          ssd_plan={'target': {}}, ssd_identity='ssd:gm7-boot-repair')
                    writer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
