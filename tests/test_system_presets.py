"""Host generation checks and native Linux Engine apply/restore acceptance."""
import json
import os
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'components')); sys.path.insert(0, str(ROOT / 'guest'))
import system_presets as presets
from integration import stage, validate_options
from maintenance import Engine

HASH = '$6$fixture$' + 'a' * 86


def locale_fixture(category):
    return presets.locale_magic(category) + struct.pack('<II', 1, 12) + b'fixture\0'


def translation_fixture():
    return struct.pack('<7I', 0x950412de, 0, 1, 28, 36, 0, 0) + struct.pack('<4I', 7, 44, 4, 52) + b'fixture\0test\0'


class PresetChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='boiler-preset-test-')
        self.root = Path(self.tmp.name) / 'root'; self.root.mkdir()
        self.write('etc/os-release', b'ID=steamos\nVERSION_ID=3.10\nBUILD_ID=test\n')
        self.write('etc/passwd', b'root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/bash\n')
        self.shadow = b'root:!:1:0:99999:7:::\ndeck:!:1:0:99999:7:::\n'
        self.write('etc/shadow', self.shadow, 0o600)
        self.write('usr/lib/systemd/system/sshd.service', b'[Unit]\nDescription=SSH\n[Service]\nExecStart=/usr/bin/sshd -D\n')
        self.write('usr/bin/plasmashell', b'fixture executable', 0o755)
        self.write('bin/bash', b'fixture executable', 0o755)
        self.write('usr/share/locale/zh_CN/LC_MESSAGES/plasmashell.mo', translation_fixture())
        for index, category in enumerate(presets.LOCALE_CATEGORIES): self.write('usr/lib/locale/zh_CN.utf8/' + category, locale_fixture(index if index < 6 else index + 1))
        (self.root / 'usr/lib/modules/stock').mkdir(parents=True)
        self.payload = Path(self.tmp.name) / 'payload'
        self.write_payload('integration/manifest.json', b'{"schema":1,"components":{}}')
        self.options = dict(schema=1, repairs=[], plugins=[], install_mode='vanilla', kde_language='zh_CN', set_deck_password=True, enable_ssh=True)

    def write(self, name, data, mode=0o644):
        path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); os.chmod(path, mode)
        return path

    def write_payload(self, name, data):
        path = self.payload / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)

    def tearDown(self): self.tmp.cleanup()

    def test_all_presets_generate_one_manifest_without_secret_in_json(self):
        component, files = presets.prepare(self.root, self.options, HASH)
        self.assertEqual(presets.IDENTITY, component['id'])
        self.assertEqual({'etc/locale.conf', 'etc/xdg/plasma-localerc', 'etc/shadow', presets.SSH_TARGET}, set(files))
        self.assertNotIn(HASH, json.dumps(component))
        self.assertIn(HASH.encode(), files['etc/shadow'])
        self.assertFalse((self.root / 'home').exists())

    def test_existing_user_locale_is_preserved_and_conflicts_refused(self):
        user = self.write('home/deck/.config/plasma-localerc', b'[Translations]\nLANGUAGE=zh_CN\n[Formats]\nLANG=zh_CN.UTF-8\nLC_TIME=de_DE.UTF-8\n')
        before = user.read_bytes()
        component, files = presets.prepare(self.root, self.options, HASH)
        self.assertNotIn('home/deck/.config/plasma-localerc', files)
        self.assertEqual(before, user.read_bytes())
        user.write_bytes(b'[Translations]\nLANGUAGE=en_US\n')
        with self.assertRaisesRegex(ValueError, 'conflicts'): presets.prepare(self.root, self.options, HASH)

    def test_missing_locale_or_translation_and_immutable_preferences_refuse(self):
        for content in (b'[Translations][$i]\nLANGUAGE=zh_CN\n', b'[Translations]\nLANGUAGE[$i]=zh_CN\n', b'[Formats]\nLC_ALL=C\n', b'[Formats]\nLANG=zh_CN.UTF-8\n[Formats]\nLANG=C\n'):
            self.write('home/deck/.config/plasma-localerc', content)
            with self.assertRaisesRegex(ValueError, 'ambiguous|Immutable'): presets.prepare(self.root, self.options, HASH)
        (self.root / 'home/deck/.config/plasma-localerc').unlink()
        translation = self.root / 'usr/share/locale/zh_CN/LC_MESSAGES/plasmashell.mo'; translation.unlink()
        with self.assertRaisesRegex(ValueError, 'translation'): presets.prepare(self.root, self.options, HASH)
        self.write('usr/share/locale/zh_CN/LC_MESSAGES/plasmashell.mo', translation_fixture())
        (self.root / 'usr/lib/locale/zh_CN.utf8/LC_CTYPE').unlink()
        with self.assertRaisesRegex(ValueError, 'generated libc'): presets.prepare(self.root, self.options, HASH)

    def test_initial_password_updates_age_and_refuses_expired_account(self):
        _, files = presets.prepare(self.root, self.options, HASH)
        self.assertEqual(str(int(__import__('time').time() // 86400)), files['etc/shadow'].decode().splitlines()[1].split(':')[2])
        self.write('etc/shadow', b'deck:!:1:0:99999:7::1:\n', 0o600)
        with self.assertRaisesRegex(ValueError, 'expired'): presets.prepare(self.root, self.options, HASH)

    def test_archive_uses_live_bounded_records_and_compiled_categories(self):
        data = bytearray(476)
        struct.pack_into('<14I', data, 0, 0xde020109, 0, 56, 1, 3, 92, 11, 16, 108, 1, 1, 0, 0, 0)
        struct.pack_into('<III', data, 56, 1, 92, 108)
        data[92:103] = b'zh_CN.utf8\0'
        struct.pack_into('<I', data, 108, 1)
        for category in range(13):
            if category == 6: continue
            content = locale_fixture(category)
            struct.pack_into('<II', data, 112 + category * 8, 216 + category * 20, len(content))
            data[216 + category * 20:216 + category * 20 + len(content)] = content
        archive = self.write('usr/lib/locale/locale-archive', data)
        self.assertTrue(presets._archive_locale(self.root, 'zh_CN'))
        os.link(archive, archive.with_name('archive-hardlink'))
        self.assertTrue(presets._archive_locale(self.root, 'zh_CN'))
        self.assertFalse(presets._archive_locale(self.root, 'ja_JP'))
        struct.pack_into('<I', data, 64, 0); archive.write_bytes(data)
        self.assertFalse(presets._archive_locale(self.root, 'zh_CN'))
        struct.pack_into('<I', data, 64, 108)
        struct.pack_into('<II', data, 112, len(data) + 1, 4); archive.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'escapes'): presets._archive_locale(self.root, 'zh_CN')

    def test_magic_only_locale_and_translation_do_not_establish_support(self):
        self.write('usr/lib/locale/zh_CN.utf8/LC_CTYPE', presets.locale_magic(0))
        with self.assertRaisesRegex(ValueError, 'Truncated'): presets.prepare(self.root, self.options, HASH)
        self.write('usr/lib/locale/zh_CN.utf8/LC_CTYPE', locale_fixture(0))
        self.write('usr/share/locale/zh_CN/LC_MESSAGES/plasmashell.mo', b'\xde\x12\x04\x95')
        with self.assertRaisesRegex(ValueError, 'Truncated'): presets.prepare(self.root, self.options, HASH)

    def test_hardlinked_artifacts_are_readable_but_shadow_remains_single_link(self):
        category = self.root / 'usr/lib/locale/zh_CN.utf8/LC_CTYPE'
        os.link(category, category.with_name('ctype-hardlink'))
        translation = self.root / 'usr/share/locale/zh_CN/LC_MESSAGES/plasmashell.mo'
        os.link(translation, translation.with_name('translation-hardlink'))
        self.assertGreater(category.stat().st_nlink, 1)
        presets.require_language(self.root, 'zh_CN')
        shadow = self.root / 'etc/shadow'; os.link(shadow, shadow.with_name('shadow-hardlink'))
        with self.assertRaisesRegex(RuntimeError, 'single-link'):
            presets.prepare(self.root, self.options, HASH)

    def test_readonly_artifact_change_is_detected(self):
        path = self.root / 'usr/lib/locale/zh_CN.utf8/LC_CTYPE'
        with self.assertRaisesRegex(ValueError, 'changed during inspection'):
            with presets._artifact_file(path, maximum=1024) as (stream, info):
                path.write_bytes(path.read_bytes() + b'changed')

    def test_invalid_presets_are_rejected_before_writes(self):
        before = {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        for changed in ({'enable_ssh': 1}, {'kde_language': '../../etc'}, {'install_mode': 'imported'}, {'set_deck_password': 'yes'}):
            with self.assertRaises(ValueError): validate_options(dict(self.options, **changed))
        with self.assertRaises(ValueError): presets.prepare(self.root, self.options, 'not-a-hash')
        self.assertEqual(before, {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_missing_service_and_ambiguous_locale_refuse_before_engine_state(self):
        self.write('etc/locale.conf', b'LANG=C\nLANG=en_US.UTF-8\n')
        with self.assertRaisesRegex(ValueError, 'Ambiguous'): stage(self.root, self.payload, self.options, 'A', HASH)
        self.assertFalse((self.root / 'var').exists())
        (self.root / 'etc/locale.conf').unlink(); (self.root / 'usr/lib/systemd/system/sshd.service').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'): stage(self.root, self.payload, self.options, 'A', HASH)
        self.assertFalse((self.root / 'var').exists())

    def test_password_only_and_ssh_only_are_not_empty(self):
        for options in (dict(self.options, kde_language='system', enable_ssh=False), dict(self.options, kde_language='system', set_deck_password=False)):
            self.assertTrue(presets.selected(options))
            component, files = presets.prepare(self.root, options, HASH if options['set_deck_password'] else None)
            self.assertEqual(1, len(component['files']))

    def test_device_target_collision_is_rejected_before_any_component_writes(self):
        options = dict(self.options, device_package='trusted', device_manifest_sha256='a' * 64,
                       device_trust='developer-session', device_components=['device:locale'])
        component = dict(id='device:locale', files=[dict(target='etc/locale.conf')])
        with mock.patch('integration.device_plan', return_value=(self.payload, [component], {}, {'device_warnings': []})):
            with self.assertRaisesRegex(ValueError, 'conflicts'):
                stage(self.root, self.payload, options, 'A', HASH)
        self.assertFalse((self.root / 'var').exists())

    def test_masked_ssh_and_symlinked_shadow_fail_closed(self):
        override = self.root / 'etc/systemd/system/sshd.service'
        override.parent.mkdir(parents=True, exist_ok=True)
        try: override.symlink_to('/dev/null')
        except OSError: self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError): presets.prepare(self.root, self.options, HASH)
        self.assertFalse((self.root / 'var').exists())

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Native Linux root ownership required')
    def test_native_transaction_presets_only_verify_restore_and_private_secrets(self):
        user = self.write('home/deck/.config/plasma-localerc', b'[Translations]\nLANGUAGE=zh_CN\n')
        for path in (self.root / 'home/deck', user.parent, user): os.chown(path, 1000, 1000)
        result = stage(self.root, self.payload, self.options, 'A', HASH)
        self.assertEqual({presets.IDENTITY}, set(result['components']))
        self.assertNotIn(HASH, json.dumps(result))
        engine = Engine(self.root, read_only=True); presets.verify(self.root, self.options, engine)
        self.assertEqual(1000, user.stat().st_uid)
        self.assertEqual(0o600, (self.root / 'etc/shadow').stat().st_mode & 0o777)
        for path in (self.root / 'var/lib/boiler/maintenance').rglob('*.json'):
            self.assertNotIn(HASH, path.read_text())
        for path in (self.root / 'home/.boiler/maintenance-blobs').rglob('*'):
            self.assertEqual(0, path.stat().st_uid)
            self.assertFalse(path.stat().st_mode & 0o077)
        transaction = result['components'][presets.IDENTITY]['operation']['transaction']
        Engine(self.root).restore(transaction)
        self.assertEqual(self.shadow, (self.root / 'etc/shadow').read_bytes())
        self.assertEqual(b'[Translations]\nLANGUAGE=zh_CN\n', user.read_bytes())
        self.assertFalse((self.root / presets.SSH_TARGET).exists())
        self.assertFalse((self.root / 'etc/locale.conf').exists())


if __name__ == '__main__': unittest.main()
