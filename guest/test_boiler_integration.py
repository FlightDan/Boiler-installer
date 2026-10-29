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

    def test_graphics_candidates_native_links_transactions_and_restore(self):
        import hashlib
        import json
        import graphics
        import graphics_boot
        from integration import merged_firmware_plan
        from payload_lock import record
        identity = 'graphics:amd-780m'
        payload = Path(self.temporary.name) / 'graphics-payload'
        integration = payload / 'integration'; integration.mkdir(parents=True)
        module = self.root / 'usr/lib/modules/stock/kernel/drivers/gpu/drm/amd/amdgpu/amdgpu.ko.zst'
        module.parent.mkdir(parents=True); module.write_bytes(b'native-graphics-driver')
        boot = self.root / 'boot'; boot.mkdir()
        original_initramfs = b''.join((graphics_boot._entry('lib', b'usr/lib', 0o120777, 1),
            graphics_boot._entry('init', b'fixture-init', 0o100755, 2), graphics_boot._entry('TRAILER!!!', b'', 0, 3)))
        initramfs = boot / 'initramfs-linux.img'; initramfs.write_bytes(original_initramfs); initramfs.chmod(0o600)
        directory = self.root / 'usr/lib/firmware/amdgpu'; directory.mkdir(parents=True)
        native = directory / 'native.bin'; native.write_bytes(b'original-firmware')
        alias = directory / 'preserved.bin'; alias.symlink_to('/usr/lib/firmware/amdgpu/native.bin')
        compressed = directory / 'compressed.bin.zst'; compressed.write_bytes(b'original-compressed')
        files = []
        for name in ('preserved.bin', 'compressed.bin', 'added.bin'):
            data = ('candidate-' + name).encode()
            source = 'graphics/amd-780m/firmware/amdgpu/' + name
            path = integration / source; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            files.append(record(source, data, target='usr/lib/firmware/amdgpu/' + name))
        notice = b'Fixture redistribution notice'
        (integration / 'NOTICE').write_bytes(notice)
        bundle = dict(schema=1, kind='GraphicsFirmwareBundle', id=identity, version='0.1.0-baseline.1',
                      drivers=['amdgpu'], available=True, delivery='on-demand', runtime='not_validated',
                      status='experimental-baseline', baseline=dict(version='fixture', revision='a' * 40),
                      provenance=dict(revision='a' * 40, url='https://example.invalid/firmware/'), files=files,
                      licenses=[record('NOTICE', notice, target='licenses/NOTICE')])
        manifest = {identity: bundle}
        options = dict(schema=1, repairs=[], plugins=[], graphics_profile=identity)
        catalog = json.dumps(dict(schema=1, components=manifest)).encode()
        (integration / 'manifest.json').write_bytes(catalog)
        records, plans = merged_firmware_plan(self.root, manifest, options)
        self.assertEqual({'usr/lib/firmware/amdgpu/added.bin'}, set(records))
        (payload / 'firmware-plan.json').write_text(json.dumps(dict(schema=1, selection=options,
            catalog_sha256=hashlib.sha256(catalog).hexdigest(),
            slots={slot: dict(records=records, plans=plans) for slot in ('A', 'B')})))
        result = stage(self.root, payload, options, 'A')
        installed = result['components'][identity]
        self.assertEqual('runtime_dependent', installed['coverage'])
        self.assertTrue(graphics.verify_inventory(self.root, installed))
        self.assertTrue(graphics_boot.verify(self.root, installed['early_boot_proof']))
        self.assertEqual('firmware-appended-original-preserved', installed['early_boot'])
        self.assertTrue(initramfs.read_bytes().startswith(original_initramfs))
        self.assertEqual(0o600, initramfs.stat().st_mode & 0o777)
        boot_entries = graphics_boot.inspect(initramfs.read_bytes())
        for name in ('preserved.bin', 'compressed.bin.zst', 'added.bin'):
            self.assertIn('usr/lib/firmware/amdgpu/' + name, boot_entries)
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(identity)['ok'])
        # Model a crash after firmware was written but before the boot file and
        # registry commit. A new planner/stager must not let Engine.apply recover
        # underneath evidence captured from this partially installed state.
        engine = Engine(self.root)
        journal_path = engine._journal(installed['operation']['transaction'])
        journal = json.loads(journal_path.read_text())
        journal['status'] = 'applying'
        journal_path.write_text(json.dumps(journal))
        (engine.state_dir / 'state.json').write_text(json.dumps(journal['before_state']))
        initramfs.write_bytes(original_initramfs)
        partial_firmware = (directory / 'added.bin').read_bytes()
        pending_journal = journal_path.read_bytes()
        pending_state = (engine.state_dir / 'state.json').read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
            graphics.plan_required(self.root, manifest, identity)
        with self.assertRaisesRegex(RuntimeError, 'explicit recovery and fresh graphics planning'):
            stage(self.root, payload, options, 'A')
        self.assertEqual(partial_firmware, (directory / 'added.bin').read_bytes())
        self.assertEqual(original_initramfs, initramfs.read_bytes())
        self.assertEqual(pending_journal, journal_path.read_bytes())
        self.assertEqual(pending_state, (engine.state_dir / 'state.json').read_bytes())
        self.assertEqual([journal['id']], engine.recover())
        self.assertFalse((directory / 'added.bin').exists())
        records, plans = merged_firmware_plan(self.root, manifest, options)
        self.assertEqual({'usr/lib/firmware/amdgpu/added.bin'}, set(records))
        (payload / 'firmware-plan.json').write_text(json.dumps(dict(schema=1, selection=options,
            catalog_sha256=hashlib.sha256(catalog).hexdigest(),
            slots={slot: dict(records=records, plans=plans) for slot in ('A', 'B')})))
        installed = stage(self.root, payload, options, 'A')['components'][identity]
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(identity)['ok'])
        first_initramfs = initramfs.read_bytes()
        added = directory / 'added.bin'
        first_added = added.read_bytes()

        def replan():
            records, plans = merged_firmware_plan(self.root, manifest, options)
            self.assertEqual({}, records)
            (payload / 'firmware-plan.json').write_text(json.dumps(dict(schema=1, selection=options,
                catalog_sha256=hashlib.sha256(catalog).hexdigest(),
                slots={slot: dict(records=records, plans=plans) for slot in ('A', 'B')})))

        replan()
        repeated = stage(self.root, payload, options, 'A')
        second = repeated['components'][identity]
        self.assertTrue(repeated['install_plan']['components'][0]['retain_owned_paths'])
        self.assertEqual('existing-initramfs-coverage-verified', second['early_boot'])
        self.assertTrue(graphics.verify_inventory(self.root, second))
        self.assertTrue(graphics_boot.verify(self.root, second['early_boot_proof']))
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(identity)['ok'])
        self.assertEqual(first_initramfs, initramfs.read_bytes())
        self.assertEqual(first_added, added.read_bytes())
        for target in (added, initramfs):
            before = target.read_bytes()
            target.write_bytes(before + b'\0\0\0\0')
            replan()
            with self.assertRaisesRegex(RuntimeError, 'Retained managed file has changed'):
                stage(self.root, payload, options, 'A')
            self.assertEqual(before + b'\0\0\0\0', target.read_bytes())
            target.write_bytes(before)
            self.assertTrue(Engine(self.root, read_only=True).verify_readonly(identity)['ok'])
        Engine(self.root).restore(second['operation']['transaction'])
        self.assertEqual(first_initramfs, initramfs.read_bytes())
        self.assertEqual(first_added, added.read_bytes())
        self.assertTrue(Engine(self.root, read_only=True).verify_readonly(identity)['ok'])
        Engine(self.root).restore(installed['operation']['transaction'])
        self.assertFalse((directory / 'added.bin').exists())
        self.assertEqual(b'original-firmware', native.read_bytes()); self.assertTrue(alias.is_symlink())
        self.assertEqual(b'original-compressed', compressed.read_bytes())
        self.assertEqual(b'native-graphics-driver', module.read_bytes())
        self.assertEqual(original_initramfs, initramfs.read_bytes())
        native.unlink()
        with self.assertRaisesRegex(ValueError, 'dangling native firmware alias'):
            graphics.plan_required(self.root, manifest, identity)

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

    def test_presets_only_native_transaction_ownership_privacy_and_restore(self):
        import system_presets
        secret_hash = '$6$fixture$' + 'a' * 86
        shadow = self.root / 'etc/shadow'
        original_shadow = b'root:!:1:0:99999:7:::\ndeck:!:1:0:99999:7:::\n'
        shadow.write_bytes(original_shadow); shadow.chmod(0o600)
        unit = self.root / 'usr/lib/systemd/system/sshd.service'
        unit.parent.mkdir(parents=True); unit.write_text('[Service]\nExecStart=/usr/bin/sshd -D\n')
        shell = self.root / 'bin/bash'; shell.parent.mkdir(); shell.write_bytes(b'fixture'); shell.chmod(0o755)
        plasma = self.root / 'usr/bin/plasmashell'; plasma.parent.mkdir(); plasma.write_bytes(b'fixture'); plasma.chmod(0o755)
        translation = self.root / 'usr/share/locale/zh_CN/LC_MESSAGES/plasmashell.mo'
        import struct
        translation.parent.mkdir(parents=True)
        translation.write_bytes(struct.pack('<7I', 0x950412de, 0, 1, 28, 36, 0, 0) + struct.pack('<4I', 7, 44, 4, 52) + b'fixture\0test\0')
        for index, category in enumerate(system_presets.LOCALE_CATEGORIES):
            path = self.root / 'usr/lib/locale/zh_CN.utf8' / category; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(system_presets.locale_magic(index if index < 6 else index + 1) + struct.pack('<II', 1, 12) + b'fixture\0')
        user = self.root / 'home/deck/.config/plasma-localerc'
        user.parent.mkdir(); user.write_text('[Translations]\nLANGUAGE=zh_CN\n[Formats]\nLC_TIME=de_DE.UTF-8\n')
        original_user = user.read_bytes()
        for path in (self.root / 'home/deck', user.parent, user): os.chown(path, 1000, 1000)
        options = dict(schema=1, repairs=[], plugins=[], kde_language='zh_CN', set_deck_password=True, enable_ssh=True)
        result = stage(self.root, '/mnt/payload', options, 'A', secret_hash)
        self.assertEqual({system_presets.IDENTITY}, set(result['components']))
        self.assertNotIn(secret_hash, __import__('json').dumps(result))
        system_presets.verify(self.root, options, Engine(self.root, read_only=True))
        self.assertEqual(1000, user.stat().st_uid)
        self.assertEqual(0o600, shadow.stat().st_mode & 0o777)
        self.assertIn(b'LC_TIME=de_DE.UTF-8', user.read_bytes())
        for path in (self.root / 'var/lib/boiler/maintenance').rglob('*.json'):
            self.assertNotIn(secret_hash, path.read_text())
        for path in (self.root / 'home/.boiler/maintenance-blobs').rglob('*'):
            self.assertEqual(0, path.stat().st_uid); self.assertFalse(path.stat().st_mode & 0o077)
        Engine(self.root).restore(result['components'][system_presets.IDENTITY]['operation']['transaction'])
        self.assertEqual(original_shadow, shadow.read_bytes())
        self.assertEqual(original_user, user.read_bytes()); self.assertEqual(1000, user.stat().st_uid)
        self.assertFalse((self.root / system_presets.SSH_TARGET).exists())
        self.assertFalse((self.root / 'etc/locale.conf').exists())

    def test_presets_interoperate_with_maintenance_and_refuse_modified_shadow(self):
        import system_presets
        shadow = self.root / 'etc/shadow'; shadow.write_text('deck:!:1:0:99999:7:::\n'); shadow.chmod(0o600)
        options = dict(schema=1, repairs=[], plugins=['core:maintenance'], set_deck_password=True)
        result = stage(self.root, '/mnt/payload', options, 'A', '$6$fixture$' + 'a' * 86)
        self.assertEqual({'core:maintenance', system_presets.IDENTITY}, set(result['components']))
        system_presets.verify(self.root, options, Engine(self.root, read_only=True))
        shadow.write_text('deck:!:2:0:99999:7:::\n')
        with self.assertRaisesRegex(ValueError, 'differ'):
            system_presets.verify(self.root, options, Engine(self.root, read_only=True))
        with self.assertRaisesRegex(RuntimeError, 'modified|changed'):
            Engine(self.root).restore(result['components'][system_presets.IDENTITY]['operation']['transaction'])

    def test_invalid_preset_never_installs_selected_maintenance(self):
        shadow = self.root / 'etc/shadow'; shadow.write_text('deck:!:1:0:99999:7:::\n'); shadow.chmod(0o600)
        options = dict(schema=1, repairs=[], plugins=['core:maintenance'], set_deck_password=True)
        with self.assertRaisesRegex(ValueError, 'crypt hash'):
            stage(self.root, '/mnt/payload', options, 'A', 'invalid')
        self.assertFalse((self.root / 'var').exists())

    def test_readonly_glibc_hardlinks_are_allowed_but_shadow_links_are_refused(self):
        import struct
        import system_presets
        category = self.root / 'usr/lib/locale/fixture/LC_CTYPE'
        category.parent.mkdir(parents=True)
        category.write_bytes(system_presets.locale_magic(0) + struct.pack('<II', 1, 12) + b'fixture\0')
        os.link(category, category.with_name('hardlinked-ctype'))
        self.assertEqual(2, category.stat().st_nlink)
        self.assertEqual(system_presets.locale_magic(0), system_presets._artifact(self.root, 'usr/lib/locale/fixture/LC_CTYPE', locale_category=0))
        shadow = self.root / 'etc/shadow'; shadow.write_text('deck:!:1:0:99999:7:::\n'); shadow.chmod(0o600)
        os.link(shadow, shadow.with_name('hardlinked-shadow'))
        with self.assertRaisesRegex(RuntimeError, 'single-link'):
            system_presets.prepare(self.root, dict(set_deck_password=True), '$6$fixture$' + 'a' * 86)
        category.chmod(0o666)
        with self.assertRaisesRegex(ValueError, 'ownership is untrusted'):
            system_presets._artifact(self.root, 'usr/lib/locale/fixture/LC_CTYPE', locale_category=0)


if __name__ == '__main__': unittest.main(verbosity=2)
