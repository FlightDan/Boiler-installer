"""Checks for the Plasma lock-screen pam_fprintd installer.

These are synthetic: they build a fake package archive and a fake root, and they
never touch a real PAM configuration. Physical unlock remains a separate gate.
"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1] / 'common' / 'fingerprint' / 'kde' / 'install-kde-pam.py'


def load():
    spec = importlib.util.spec_from_file_location('kde_pam_install', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Minimal ELF64 header: magic, class 2, little endian, version 1, then e_machine
# 62 (x86-64) at offset 18, which is the only field the installer reads.
ELF_HEADER = b'\x7fELF' + bytes([2, 1, 1]) + bytes(9) + bytes(2) + bytes([62, 0]) + bytes(48)


def package(path, payload):
    with tarfile.open(path, 'w') as archive:
        info = tarfile.TarInfo('usr/lib/security/pam_fprintd.so')
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.security = self.root / 'usr/lib/security'
        self.pam = self.root / 'usr/lib/pam.d'
        self.security.mkdir(parents=True)
        self.pam.mkdir(parents=True)
        (self.pam / 'kde-fingerprint').write_text(
            '#%PAM-1.0\n-auth required pam_fprintd.so\n', encoding='utf-8')
        self.destination = self.security / 'pam_fprintd.so'
        self.record = self.root / 'var/lib/boiler-fingerprint/kde-pam.json'
        self.archive = self.root / 'fprintd.pkg.tar'
        package(self.archive, ELF_HEADER)
        self.module = load()
        self.patches = [patch.object(self.module, 'SECURITY_DIR', self.security),
                        patch.object(self.module, 'PAM_DIR', self.pam),
                        patch.object(self.module, 'SERVICE', self.pam / 'kde-fingerprint'),
                        patch.object(self.module, 'DESTINATION', self.destination),
                        patch.object(self.module, 'RECORD', self.record),
                        patch.object(self.module, 'euid', return_value=0),
                        patch.object(self.module, '_security_stat', side_effect=self.security_stat)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def install(self):
        return self.module.install(self.archive)

    def security_stat(self, path):
        # Windows reports 0o777 for every directory, which the real check would
        # reject; report the POSIX modes the synthetic fixture stands for.
        kind = stat.S_IFDIR if Path(path).is_dir() else stat.S_IFREG
        return SimpleNamespace(st_uid=0, st_mode=kind | 0o755)

    def test_installs_reviewed_module_and_records_digest(self):
        facts = self.install()
        self.assertEqual(self.destination.read_bytes(), ELF_HEADER)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o755)
        self.assertEqual(facts['sha256'], hashlib.sha256(ELF_HEADER).hexdigest())
        self.assertEqual(json.loads(self.record.read_text())['sha256'], facts['sha256'])

    def test_reinstall_of_unchanged_module_is_allowed(self):
        first = self.install()
        second = self.install()
        self.assertEqual(first, second)

    def test_modified_module_is_never_overwritten(self):
        self.install()
        self.destination.write_bytes(ELF_HEADER + b'tampered')
        with self.assertRaisesRegex(self.module.Refused, 'changed since it was recorded'):
            self.install()

    def test_foreign_module_is_refused(self):
        self.destination.write_bytes(ELF_HEADER)
        with self.assertRaisesRegex(self.module.Refused, 'not installed by this tool'):
            self.install()

    def test_non_elf_payload_is_refused(self):
        package(self.archive, b'not an elf object')
        with self.assertRaisesRegex(self.module.Refused, 'not an ELF object'):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_package_without_module_is_refused(self):
        with tarfile.open(self.archive, 'w') as archive:
            info = tarfile.TarInfo('usr/bin/unrelated')
            info.size = 1
            archive.addfile(info, io.BytesIO(b'x'))
        with self.assertRaisesRegex(self.module.Refused, 'does not contain'):
            self.install()

    def test_missing_vendor_service_is_refused(self):
        (self.pam / 'kde-fingerprint').unlink()
        with self.assertRaisesRegex(self.module.Refused, 'does not install'):
            self.install()

    def test_vendor_service_without_module_reference_is_refused(self):
        (self.pam / 'kde-fingerprint').write_text('#%PAM-1.0\n', encoding='utf-8')
        with self.assertRaisesRegex(self.module.Refused, 'does not call'):
            self.install()

    def test_uninstall_removes_only_recorded_module(self):
        self.install()
        self.module.uninstall()
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.record.exists())

    def test_uninstall_refuses_changed_module(self):
        self.install()
        self.destination.write_bytes(ELF_HEADER + b'tampered')
        with self.assertRaisesRegex(self.module.Refused, 'changed; leaving it in place'):
            self.module.uninstall()
        self.assertTrue(self.destination.exists())

    def test_uninstall_without_record_is_refused(self):
        with self.assertRaisesRegex(self.module.Refused, 'not installed by this tool'):
            self.module.uninstall()

    def test_check_reports_mismatch(self):
        self.install()
        self.assertEqual(self.module.check(), 0)
        self.destination.write_bytes(ELF_HEADER + b'x')
        self.assertEqual(self.module.check(), 1)


if __name__ == '__main__':
    unittest.main()
