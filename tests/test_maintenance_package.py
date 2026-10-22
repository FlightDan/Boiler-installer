"""Release boundary checks use disposable independent Git repositories only."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance import Engine
from maintenance import engine as maintenance_engine
from maintenance import updates
from protocol.manifest import compatible, safe_extract_archive, validate_package

spec = importlib.util.spec_from_file_location('boiler_maintenance_package_tests', ROOT/'scripts/package-maintenance.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
URL = 'https://github.com/FlightDan/Boiler-installer/releases/download/maintenance-v0.1.0/boiler-maintenance-0.1.0.zip'


class MaintenancePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boiler-package-tests-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work/'source'
        self.source.mkdir()
        files = {*builder.RUNTIME_FILES, 'LICENSE', 'maintenance/release.json', 'scripts/package-maintenance.py'}
        for name in files:
            path = self.source/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT/name).read_bytes())
        (self.source/'.gitignore').write_text('*.key\nmaintenance/unreviewed.py\n', encoding='utf-8')
        files |= {'.gitignore', 'scripts/source-files.json'}
        (self.source/'scripts/source-files.json').write_text(json.dumps(dict(kind='BoilerSourceWhitelist', files=sorted(files))))
        self.git('init', '-q')
        self.git('config', 'core.autocrlf', 'false')
        self.git('config', 'user.email', 'unit-test@example.invalid')
        self.git('config', 'user.name', 'Boiler artifact test')
        self.commit = self.commit_sources()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], stderr=subprocess.PIPE).decode().strip()

    def commit_sources(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Disposable source fixture')
        return self.git('rev-parse', 'HEAD')

    def build(self, name='artifact.zip', **kwargs):
        return builder.package(kwargs.get('root', self.source), self.work/name,
                               kwargs.get('commit', self.commit), kwargs.get('url', URL))

    def test_installer_runtime_uses_reviewed_explicit_list_and_rejects_extra_code(self):
        self.assertEqual(maintenance_engine.RUNTIME_FILES, builder.RUNTIME_FILES)
        with mock.patch.object(maintenance_engine, '__file__', str(self.source/'maintenance/engine.py')):
            self.assertEqual({path.relative_to(self.source.resolve()).as_posix() for path in maintenance_engine.runtime_files()},
                             set(builder.RUNTIME_FILES))
            (self.source/'maintenance/__pycache__').mkdir()
            (self.source/'maintenance/__pycache__/ignored.pyc').write_bytes(b'ignored cache')
            self.assertEqual(len(maintenance_engine.runtime_files()),len(builder.RUNTIME_FILES))
            (self.source/'maintenance/unlisted.py').write_bytes(b'unreviewed runtime')
            with self.assertRaisesRegex(maintenance_engine.MaintenanceError,'Unreviewed maintenance runtime'):
                maintenance_engine.runtime_files()

    def test_reproducible_inventory_targets_and_byte_identical_public_key(self):
        # Ignored local material is never discovered or copied by the builder.
        (self.source/'publisher.key').write_bytes(b'not a real key; must never be exported')
        (self.source/'maintenance/unreviewed.py').write_bytes(b'not reviewed')
        first = self.build('first/artifact.zip')
        second = self.build('second/artifact.zip')
        self.assertEqual(first, second)
        self.assertEqual((self.work/'first/artifact.zip').read_bytes(), (self.work/'second/artifact.zip').read_bytes())
        self.assertEqual((self.work/'first/artifact.zip.json').read_bytes(), (self.work/'second/artifact.zip.json').read_bytes())
        with zipfile.ZipFile(self.work/'first/artifact.zip') as archive:
            actual = {entry.filename: archive.read(entry) for entry in archive.infolist()}
            self.assertTrue(all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist()))
            self.assertEqual({entry['path'] for entry in first['inventory']}, actual.keys())
            for entry in first['inventory']:
                self.assertEqual(entry['size'], len(actual[entry['path']]))
                self.assertEqual(entry['sha256'], hashlib.sha256(actual[entry['path']]).hexdigest())
            self.assertEqual(actual['maintenance-runtime/protocol/trusted-public-key.pem'],
                             (ROOT/'protocol/trusted-public-key.pem').read_bytes())
            self.assertFalse(any('unreviewed' in name or name.endswith('.key') for name in actual))
            component = json.loads(actual['component.json'])
            expected_targets = {'usr/local/lib/boiler/'+name for name in builder.RUNTIME_FILES} | {'usr/local/bin/boiler-maintenance'}
            self.assertEqual({entry['target'] for entry in component['files']}, expected_targets)
            self.assertNotIn('hooks', component)
            self.assertEqual(component['version'], json.loads((self.source/'maintenance/release.json').read_text())['version'])
        safe_extract_archive(self.work/'first/artifact.zip', self.work/'unpacked')
        manifest = validate_package(self.work/'unpacked', expected_manifest_sha256=first['index_entry']['sha256'])
        self.assertEqual(manifest['source']['commit'], self.commit)
        self.assertEqual(first['index_entry']['source']['size'], (self.work/'first/artifact.zip').stat().st_size)
        self.assertTrue(compatible(manifest, dict(os_id='steamos', architecture='x86_64', os_version='3.8.14')))
        self.assertFalse(compatible(manifest, dict(os_id='steamos', architecture='x86_64', os_version='3.9')))

    def test_generic_updater_can_plan_and_apply_to_owned_runtime(self):
        result = self.build()
        payload = safe_extract_archive(self.work/'artifact.zip', self.work/'unpacked')
        component = json.loads((payload/'component.json').read_text())
        root = self.work/'target'
        (root/'etc').mkdir(parents=True)
        (root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\n')
        binary = bytearray(20)
        binary[:6] = b'\x7fELF\x02\x01'
        binary[18:20] = (62).to_bytes(2, 'little')
        (root/'usr/bin').mkdir(parents=True)
        (root/'usr/bin/env').write_bytes(binary)
        environment = dict(os_id='steamos', os_version='3.10', architecture='x86_64', kernels=[])
        old = copy.deepcopy(component)
        old['version'] = '0.0.0'
        engine = Engine(root)
        engine.apply(old, payload, environment=environment)
        index = dict(packages=[result['index_entry']], revocations=[])
        anchor = dict(id='registry:boiler-maintenance', version='0.1.0', sequence=1,
                      sha256='a'*64, trusted_public_key_sha256=builder.PUBLIC_KEY_SHA256)
        # Signature cryptography is independently tested by protocol/update tests;
        # this fixture replaces only signed discovery, preserving updater checks.
        with mock.patch.object(updates, '_signed_snapshot', return_value=(index, anchor)):
            plan = updates.plan_update(root, self.work/'cache', payload, 'bundle:maintenance', component['version'])
            self.assertEqual(plan['selected'], ['core:maintenance'])
            self.assertTrue(plan['executable'])
            self.assertEqual(plan['actions'][0]['action'], 'upgrade')
            receipt = updates.execute_plan(root, plan, self.work/'cache', payload, reviewed_sha256=plan['review_sha256'])
        self.assertEqual(receipt['status'], 'committed')
        self.assertTrue(engine.verify('core:maintenance')['ok'])

    def test_reject_dirty_wrong_commit_and_parent_repository(self):
        with self.assertRaisesRegex(ValueError, 'HEAD'):
            self.build(commit='f'*40)
        (self.source/'maintenance/engine.py').write_bytes(b'modified')
        with self.assertRaisesRegex(ValueError, 'clean'):
            self.build()
        nested = self.source/'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'independent'):
            self.build(root=nested)

    def test_reject_unlisted_tracked_files_and_key_rotation(self):
        (self.source/'not-whitelisted.txt').write_text('not reviewed')
        self.commit = self.commit_sources()
        with self.assertRaisesRegex(ValueError, 'whitelist'):
            self.build()
        self.git('rm', 'not-whitelisted.txt')
        (self.source/'protocol/trusted-public-key.pem').write_bytes(b'not the official key')
        self.commit = self.commit_sources()
        with self.assertRaisesRegex(ValueError, 'public key'):
            self.build()

    def test_reject_unsafe_asset_urls_and_existing_outputs(self):
        for url in ('http://github.com/a.zip', URL+'?token=secret', URL.replace('github.com', 'example.invalid'),
                    URL.replace('FlightDan', 'AnotherAccount')):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'URL'):
                self.build(url=url)
        self.build()
        before = (self.work/'artifact.zip').read_bytes()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.build()
        self.assertEqual((self.work/'artifact.zip').read_bytes(), before)

    def test_exact_kernel_restriction_is_preserved(self):
        path = self.source/'maintenance/release.json'
        release = json.loads(path.read_text())
        release['compatibility']['kernels'] = ['6.11.11-valve14-1-neptune-611-generic']
        path.write_text(json.dumps(release))
        self.commit = self.commit_sources()
        result = self.build()
        self.assertEqual(result['index_entry']['compatibility']['kernels'], release['compatibility']['kernels'])

    def test_launcher_ignores_pythonpath_cwd_and_user_site_preserving_arguments(self):
        trusted, hostile, userbase = self.work/'trusted', self.work/'hostile', self.work/'userbase'
        (trusted/'maintenance').mkdir(parents=True)
        hostile.mkdir()
        (trusted/'maintenance/__init__.py').write_text('')
        (trusted/'maintenance/__main__.py').write_text(
            'import json,sys,site\ndef main():\n'
            ' print(json.dumps(dict(arguments=sys.argv[1:],isolated=sys.flags.isolated,user_site=site.ENABLE_USER_SITE)))\n'
            ' return 0\n')
        marker = self.work/'environment-injection-ran'
        poison = 'from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\n'
        (hostile/'maintenance.py').write_text(poison+'raise RuntimeError("hostile module loaded")\n')
        (hostile/'sitecustomize.py').write_text(poison)
        env = dict(os.environ, PYTHONPATH=str(hostile), PYTHONUSERBASE=str(userbase))
        user_site = Path(subprocess.check_output(
            [sys.executable, '-S', '-c', 'import site; print(site.getusersitepackages())'], env=env).decode().strip())
        user_site.mkdir(parents=True)
        (user_site/'usercustomize.py').write_text(poison)
        (user_site/'sitecustomize.py').write_text(poison)
        tokens = shlex.split(builder.LAUNCHER.decode().splitlines()[-1])
        self.assertEqual(tokens[:5], ['exec', '/usr/bin/python3', '-I', '-B', '-c'])
        code = tokens[5].replace('"/usr/local/lib/boiler"', repr(str(trusted)))
        arguments = ['--root', '/target path', 'plugins', 'list', 'quoted"argument']
        output = subprocess.check_output([sys.executable, *tokens[2:5], code, *arguments], cwd=hostile, env=env)
        self.assertEqual(json.loads(output), dict(arguments=arguments, isolated=1, user_site=False))
        self.assertFalse(marker.exists())
        preparation_spec = importlib.util.spec_from_file_location('boiler_launcher_preparation', ROOT/'scripts/prepare-integration.py')
        preparation = importlib.util.module_from_spec(preparation_spec)
        preparation_spec.loader.exec_module(preparation)
        preparation.prepare(self.work/'integration')
        self.assertEqual((self.work/'integration/maintenance-runtime/boiler-maintenance').read_bytes(), builder.LAUNCHER)


if __name__ == '__main__':
    unittest.main()
