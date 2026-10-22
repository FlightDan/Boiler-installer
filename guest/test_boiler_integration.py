"""Linux guest file/ownership acceptance; live Decky/Steam acceptance is separate."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, '/mnt/payload/components')
sys.path.insert(0, '/mnt/payload')
from integration import stage
from plugin_installer import PluginInstaller
from maintenance import Engine


@unittest.skipUnless(sys.platform == 'linux', 'Managed Linux guest required')
class OfflineIntegrationChecks(unittest.TestCase):
    def setUp(self):
        if os.geteuid() != 0: self.skipTest('Root-owned fixture requires guest root')
        self.temporary = tempfile.TemporaryDirectory(prefix='boiler-check-')
        self.root = Path(self.temporary.name) / 'target'; self.root.mkdir()
        (self.root / 'etc').mkdir()
        (self.root / 'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.8.14\nBUILD_ID=20260707.10\n')
        (self.root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/bash\n')
        (self.root / 'home/deck').mkdir(parents=True)
        (self.root / 'usr/lib/modules/stock').mkdir(parents=True)

    def tearDown(self):
        if hasattr(self, 'temporary'): self.temporary.cleanup()

    def test_decky_offline_ownership_repeat_and_restore(self):
        options = dict(schema=1, repairs=[], plugins=['core:decky'])
        stage(self.root, '/mnt/payload', options, 'A')
        second = stage(self.root, '/mnt/payload', options, 'A')
        self.assertEqual('files_installed', second['components']['core:decky']['status'])
        binary = self.root / 'var/lib/boiler/decky/runtime/services/PluginLoader'
        self.assertEqual(0, binary.stat().st_uid); self.assertEqual(0o755, binary.stat().st_mode & 0o777)
        self.assertEqual(1000, (self.root / 'home/deck/homebrew').stat().st_uid)
        self.assertEqual(1000, (self.root / 'home/deck/.local/share/Steam/.cef-enable-remote-debugging').stat().st_uid)
        self.assertIn('Wants=plugin_loader.service', (self.root / 'etc/systemd/system/multi-user.target.d/50-boiler-decky.conf').read_text())
        PluginInstaller(self.root, '/mnt/payload/integration').rollback('decky')
        self.assertFalse(binary.exists())
        self.assertFalse((self.root / 'etc/systemd/system/plugin_loader.service').exists())
        self.assertFalse((self.root / 'home/deck/.local/share/Steam/.cef-enable-remote-debugging').exists())

    def test_preserve_existing_user_debug_marker(self):
        marker = self.root / 'home/deck/.local/share/Steam/.cef-enable-remote-debugging'
        marker.parent.mkdir(parents=True); marker.write_bytes(b'user-owned'); os.chown(marker, 1000, 1000)
        stage(self.root, '/mnt/payload', dict(schema=1, repairs=[], plugins=['core:decky']), 'A')
        PluginInstaller(self.root, '/mnt/payload/integration').rollback('decky')
        self.assertEqual(b'user-owned', marker.read_bytes())

    def test_shared_marker_owner_waits_for_sibling_slot(self):
        stage(self.root, '/mnt/payload', dict(schema=1, repairs=[], plugins=['core:decky']), 'A')
        references = self.root / 'home/.boiler/decky/slots'
        sibling = dict(schema=1, slot='B', epoch='a'*32, owns_marker=False,
                       marker='home/deck/.local/share/Steam/.cef-enable-remote-debugging')
        (references / 'B.json').write_text(__import__('json').dumps(sibling)); os.chmod(references / 'B.json', 0o600)
        with self.assertRaisesRegex(RuntimeError, 'sibling'):
            PluginInstaller(self.root, '/mnt/payload/integration').rollback('decky')
        self.assertTrue((self.root / sibling['marker']).is_file())
        (references / 'B.json').unlink()
        PluginInstaller(self.root, '/mnt/payload/integration').rollback('decky')
        self.assertFalse((self.root / sibling['marker']).exists()); self.assertFalse((references / 'A.json').exists())

    def test_installed_generic_maintenance_can_restore_real_decky_journals(self):
        from maintenance.plugins import PluginMaintenance
        stage(self.root, '/mnt/payload', dict(schema=1, repairs=[], plugins=['core:decky']), 'A')
        adapter = PluginMaintenance(self.root)
        self.assertTrue(adapter.verify('decky')['ok'])
        proof = adapter.restore('decky')
        self.assertEqual('restored', proof['status'])
        self.assertFalse((self.root / 'var/lib/boiler/decky/runtime/services').exists())

    def test_native_firmware_aliases_are_image_confined_and_readonly(self):
        from wireless import existing_firmware, native_matches
        directory = self.root / 'usr/lib/firmware'; directory.mkdir()
        original = directory / 'native.bin'; original.write_bytes(b'native-firmware')
        alias = directory / 'alias.bin'; alias.symlink_to('/usr/lib/firmware/native.bin')
        self.assertTrue(existing_firmware(self.root, 'usr/lib/firmware/alias.bin'))
        self.assertEqual(b'native-firmware', original.read_bytes()); self.assertTrue(alias.is_symlink())
        subdirectory = directory / 'actual'; subdirectory.mkdir(); (subdirectory / 'native.bin').write_bytes(b'alias-directory')
        (directory / 'vendor').symlink_to('/usr/lib/firmware/actual', target_is_directory=True)
        for pattern in ('usr/lib/firmware/vendor/native.bin', 'usr/lib/firmware/vendor/nat*.bin'):
            self.assertEqual([self.root / 'usr/lib/firmware/vendor/native.bin'], native_matches(self.root, pattern))
            self.assertTrue(existing_firmware(self.root, 'usr/lib/firmware/vendor/native.bin'))
        alias.unlink(); alias.symlink_to('../../../../outside')
        with self.assertRaisesRegex(ValueError, 'escapes'): existing_firmware(self.root, 'usr/lib/firmware/alias.bin')
        alias.unlink(); alias.symlink_to('alias.bin')
        with self.assertRaisesRegex(ValueError, 'cycle'): existing_firmware(self.root, 'usr/lib/firmware/alias.bin')

    def test_native_os_release_link_stays_inside_image(self):
        from source_profile import inspect_source
        metadata = self.root / 'etc/os-release'
        native = self.root / 'usr/lib/os-release'; native.write_bytes(metadata.read_bytes()); metadata.unlink()
        metadata.symlink_to('/usr/lib/os-release')
        self.assertEqual('3.8.14', inspect_source(self.root)['version'])
        result = stage(self.root, '/missing', dict(schema=1, repairs=[], plugins=[], install_mode='vanilla'), 'A')
        self.assertEqual('3.8.14', result['source']['VERSION_ID'])
        metadata.unlink(); metadata.symlink_to('../../outside')
        with self.assertRaisesRegex(ValueError, 'escapes'): inspect_source(self.root)
        metadata.unlink(); metadata.symlink_to('os-release')
        with self.assertRaisesRegex(ValueError, 'cycle'): inspect_source(self.root)

    def test_existing_unmanaged_decky_is_refused(self):
        unit = self.root / 'etc/systemd/system/plugin_loader.service'
        unit.parent.mkdir(parents=True); unit.write_text('[Unit]\nDescription=existing Decky\n')
        with self.assertRaisesRegex(RuntimeError, 'migration'):
            stage(self.root, '/mnt/payload', dict(schema=1, repairs=[], plugins=['core:decky']), 'A')
        self.assertFalse((self.root / 'var').exists())

    def test_maintenance_prepared_runtime_is_independent(self):
        result = stage(self.root, '/mnt/payload', dict(schema=1, repairs=[], plugins=['core:maintenance']), 'A')
        self.assertEqual('files_installed', result['components']['core:maintenance']['status'])
        self.assertTrue((self.root / 'usr/local/bin/boiler-maintenance').is_file())
        self.assertTrue((self.root / 'usr/local/lib/boiler/protocol/manifest.py').is_file())
        self.assertFalse((self.root / 'usr/local/lib/oxp3-maintenance').exists())
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly('core:maintenance')['ok'])


if __name__ == '__main__': unittest.main(verbosity=2)
