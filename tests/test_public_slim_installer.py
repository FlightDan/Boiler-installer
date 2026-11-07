import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('package_release_installer', ROOT / 'scripts/package-release-installer.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def record(name, data):
    return dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())


def make_candidate(path, *, info_overrides=None, integration_overrides=None, extra_files=None,
                   maintenance_source='maintenance-runtime/maintenance/__main__.py',
                   maintenance_reference=None, maintenance_size=None, maintenance_sha256=None,
                   maintenance_component_version=None):
    version = '0.1.0'
    display_version = '0.1.0-preview'
    maintenance_version = '0.1.0+maintenance.27'
    payload = {
        'components/release.json': json.dumps(dict(version=version, display_version=display_version)).encode(),
        'maintenance/release.json': json.dumps(dict(version=maintenance_version)).encode(),
        'components/model-catalog/lock.json': b'{"kind":"ModelCatalogLock"}\n',
        'adaptations/adaptations.json': b'{"id":"device:oxp3","guide_only":true}\n',
        'cache/integration/' + maintenance_source: b'from maintenance import main\n',
    }
    maintenance_bytes = payload['cache/integration/' + maintenance_source]
    maintenance_file = {
        'source': maintenance_reference or maintenance_source,
        'size': len(maintenance_bytes) if maintenance_size is None else maintenance_size,
        'sha256': hashlib.sha256(maintenance_bytes).hexdigest() if maintenance_sha256 is None else maintenance_sha256,
        'target': 'usr/local/lib/boiler/maintenance/__main__.py',
        'mode': 0o644,
    }
    runtime = [maintenance_file]
    integration = {
        'schema': 1,
        'product': 'Boiler Installer',
        'components': {
            'core:maintenance': {
                'available': True,
                'files': runtime,
                'component': {
                    'schema_version': 1,
                    'kind': 'ComponentManifest',
                    'id': 'core:maintenance',
                    'version': maintenance_version if maintenance_component_version is None
                    else maintenance_component_version,
                    'files': runtime,
                },
            },
            'core:decky': {'available': False, 'reason': 'not prepared'},
        },
    }
    integration.update(integration_overrides or {})
    payload['cache/integration/manifest.json'] = json.dumps(integration, sort_keys=True).encode()
    payload.update(extra_files or {})
    files = [record(name, data) for name, data in sorted(payload.items())]
    info = {
        'schema': 1,
        'product': 'Boiler Installer',
        'version': version,
        'display_version': display_version,
        'source_commit': 'a' * 40,
        'configuration': 'Release',
        'package_channel': 'preview',
        'source_published': True,
        'files': files,
    }
    info.update(info_overrides or {})
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(payload.items()):
            archive.writestr(name, data)
        archive.writestr('build-info.json', json.dumps(info, sort_keys=True).encode())
    return info


class PublicSlimInstallerTests(unittest.TestCase):
    def test_package_checkout_must_match_executing_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            other = Path(temporary).resolve()
            self.assertNotEqual(other, release.ROOT.resolve())
            with mock.patch.object(release.subprocess, 'check_output') as git:
                with self.assertRaisesRegex(ValueError, 'public checkout being packaged'):
                    release.assert_public_checkout(other, 'a' * 40)
                git.assert_not_called()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def candidate(self, **kwargs):
        path = self.root / release.artifact_name('0.1.0')
        make_candidate(path, **kwargs)
        return path

    def test_preview_slim_candidate_keeps_catalog_and_generic_maintenance(self):
        path = self.candidate()
        result = release.verify_slim_archive(path, source_commit='a' * 40)
        self.assertEqual(result['status'], 'verified-slim-installer-candidate')
        self.assertEqual(result['package_channel'], 'preview')
        self.assertEqual(result['model_catalog_files'], 1)
        self.assertEqual(result['generic_maintenance_version'], '0.1.0+maintenance.27')
        with zipfile.ZipFile(path) as archive:
            self.assertIn('cache/integration/maintenance-runtime/maintenance/__main__.py', archive.namelist())
            self.assertNotIn('maintenance-runtime/maintenance/__main__.py', archive.namelist())
        self.assertEqual(result['model_package_files'], 0)
        self.assertFalse(result['signature_verified'])
        self.assertFalse(result['publication_authorized'])
        self.assertFalse(result['upload_performed'])

    def test_rejects_local_test_channel_and_unpublished_source(self):
        with self.subTest(case='local-test'):
            path = self.candidate(info_overrides={'package_channel': 'local-test'})
            with self.assertRaisesRegex(ValueError, 'normal Release preview'):
                release.verify_slim_archive(path)
        with self.subTest(case='unpublished'):
            path = self.candidate(info_overrides={'source_published': False})
            with self.assertRaisesRegex(ValueError, 'normal Release preview'):
                release.verify_slim_archive(path)

    def test_rejects_top_level_model_bundle_bytes(self):
        path = self.candidate(extra_files={'cache/model-bundles/packages/oxp3/manifest.json': b'{}'})
        with self.assertRaisesRegex(ValueError, 'device model package bytes'):
            release.verify_slim_archive(path)
        path = self.candidate(extra_files={'Cache/Model-Bundles/packages/oxp3/manifest.json': b'{}'})
        with self.assertRaisesRegex(ValueError, 'device model package bytes'):
            release.verify_slim_archive(path)

    def test_rejects_retained_maintenance_model_bundle_bytes(self):
        path = self.candidate(extra_files={
            'cache/integration/maintenance-runtime/cache/model-bundles/packages/oxp3/manifest.json': b'{}'})
        with self.assertRaisesRegex(ValueError, 'device model package bytes'):
            release.verify_slim_archive(path)

    def test_rejects_local_test_maintenance_flag(self):
        path = self.candidate(integration_overrides={'local_test_models': True})
        with self.assertRaisesRegex(ValueError, 'local-test model assets'):
            release.verify_slim_archive(path)

    def test_rejects_unbound_maintenance_runtime_inventory(self):
        with self.subTest(case='missing-member'):
            path = self.candidate(maintenance_reference='maintenance-runtime/maintenance/missing.py')
            with self.assertRaisesRegex(ValueError, 'references a missing ZIP member'):
                release.verify_slim_archive(path)
        with self.subTest(case='size-mismatch'):
            path = self.candidate(maintenance_size=len(b'from maintenance import main\n') + 1)
            with self.assertRaisesRegex(ValueError, 'size differs from its ZIP member'):
                release.verify_slim_archive(path)
        with self.subTest(case='hash-mismatch'):
            path = self.candidate(maintenance_sha256='0' * 64)
            with self.assertRaisesRegex(ValueError, 'digest differs from its ZIP member'):
                release.verify_slim_archive(path)
        with self.subTest(case='version-mismatch'):
            path = self.candidate(maintenance_component_version='0.1.0+maintenance.26')
            with self.assertRaisesRegex(ValueError, 'core:maintenance runtime'):
                release.verify_slim_archive(path)

    def test_rejects_maintenance_inventory_reference_to_model_bundle(self):
        path = self.candidate(maintenance_reference='maintenance-runtime/cache/model-bundles/packages/oxp3/manifest.json')
        with self.assertRaisesRegex(ValueError, 'integration manifest references'):
            release.verify_slim_archive(path)

    def test_rejects_missing_display_catalog_or_non_slim_filename(self):
        with self.subTest(case='missing-display-catalog'):
            path = self.candidate()
            with zipfile.ZipFile(path) as source:
                info = json.loads(source.read('build-info.json'))
                payload = {name: source.read(name) for name in source.namelist()
                           if name not in ('build-info.json', 'adaptations/adaptations.json')}
            info['files'] = [row for row in info['files'] if row['path'] != 'adaptations/adaptations.json']
            with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as destination:
                for name, data in payload.items():
                    destination.writestr(name, data)
                destination.writestr('build-info.json', json.dumps(info, sort_keys=True).encode())
            with self.assertRaisesRegex(ValueError, 'retain its pinned model catalog'):
                release.verify_slim_archive(path)
        with self.subTest(case='filename'):
            path = self.root / 'boiler-installer-0.1.0-preview-win-x64.zip'
            make_candidate(path)
            with self.assertRaisesRegex(ValueError, 'slim artifact identity'):
                release.verify_slim_archive(path)

    def test_rejects_member_digest_tampering(self):
        path = self.candidate()
        with zipfile.ZipFile(path) as source:
            members = {name: source.read(name) for name in source.namelist()}
        target = 'adaptations/adaptations.json'
        members[target] = b'X' * len(members[target])
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        with self.assertRaisesRegex(ValueError, 'digest differs'):
            release.verify_slim_archive(path)

    def test_package_entrypoint_delegates_to_non_local_packager(self):
        source = self.root / 'source'
        (source / 'components').mkdir(parents=True)
        (source / 'components/release.json').write_text(json.dumps({
            'version': '0.1.0', 'display_version': '0.1.0-preview'}), encoding='utf-8')
        output = self.root / release.artifact_name('0.1.0')

        def fake_package(root, destination, source_commit, configuration, *, local_test):
            self.assertFalse(local_test)
            make_candidate(destination)
            return {'path': str(destination), 'size': destination.stat().st_size,
                    'sha256': release.sha256(destination), 'source_commit': source_commit}

        with mock.patch.object(release, 'assert_public_checkout'), \
             mock.patch.object(release.packager, 'package', side_effect=fake_package) as builder:
            result = release.package_slim(source, output, 'a' * 40)
        self.assertFalse(builder.call_args.kwargs['local_test'])
        self.assertEqual(result['verification']['status'], 'verified-slim-installer-candidate')
        self.assertFalse(result['publication_authorized'])

    def test_package_rejects_artifact_inside_public_source_checkout(self):
        source = self.root / 'source'
        (source / 'components').mkdir(parents=True)
        (source / 'components/release.json').write_text(json.dumps({
            'version': '0.1.0', 'display_version': '0.1.0-preview'}), encoding='utf-8')
        output = source / release.artifact_name('0.1.0')
        with self.assertRaisesRegex(ValueError, 'outside the public source checkout'):
            release.package_slim(source, output, 'a' * 40)


if __name__ == '__main__':
    unittest.main()
