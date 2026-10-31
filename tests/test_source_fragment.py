import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / 'Boiler-adaptations'
SOURCE_ROOT = Path(os.environ.get('BOILER_ADAPTATIONS_SOURCE', str(DEFAULT_SOURCE))).resolve()
_archive_value = os.environ.get('BOILER_DESKTOP_NATIVE_ARCHIVE')
DESKTOP_ARCHIVE = Path(_archive_value).resolve() if _archive_value else None
_missing_config = []
if not SOURCE_ROOT.is_dir():
    _missing_config.append(f'BOILER_ADAPTATIONS_SOURCE (or sibling {DEFAULT_SOURCE})')
if DESKTOP_ARCHIVE is None or not DESKTOP_ARCHIVE.is_file():
    _missing_config.append('BOILER_DESKTOP_NATIVE_ARCHIVE')

sys.path.insert(0, str(ROOT / 'scripts'))
from protocol.resource_pool import decoder, validate_pool
from protocol.resource_selection import _choices
from resources.source_fragment import build


EXPECTED_RESOURCE_IDS = frozenset({
    'resource:common-slot-switch',
    'resource:common-partition-mount',
    'resource:common-serial-console-policy',
    'resource:common-windows-reboot',
    'resource:onexplayer-light',
    'resource:oxp3-speaker-route',
    'resource:common-charge-compatibility-guard',
    'resource:onexplayer-battery-common',
    'resource:onexplayer-battery-charge-backend',
    'resource:onexplayer-microphone',
    'resource:intel-xevmafix-724',
    'resource:intel-be201-firmware',
    'resource:gamma22-hdr-game',
    'resource:intel-g3e-public-cpu-controls',
    'resource:common-boot-fixes',
    'resource:onexplayer-ec-fan-driver',
    'resource:onexplayer-fan',
    'resource:steamos-manager',
    'resource:onexplayer-legacy-runtime',
    'resource:gamescope-environment',
    'resource:intel-xe-dsb',
    'resource:common-intel-rapl',
    'resource:onexplayer-input',
    'resource:bosch-bmi260-gyro',
    'resource:common-kde-keyboard',
    'resource:common-flathub-mirror',
    'resource:common-telemetry-overlay',
    'resource:gamma22-hdr-desktop',
    'resource:common-opti2xmx',
    'resource:common-waydroid',
})


@unittest.skipUnless(not _missing_config, 'Set ' + ' and '.join(_missing_config) + ' to run source-fragment integration tests')
class SourceFragmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        shutil.copytree(SOURCE_ROOT, self.source)
        self.toon = decoder()
        self.desktop_archive = DESKTOP_ARCHIVE

    def tearDown(self):
        self.temp.cleanup()

    def build(self, source, output):
        return build(source, output, desktop_archive=self.desktop_archive)

    def lock_doc(self, lock_path):
        path = self.source / lock_path
        return self.toon.loads(path.read_text(encoding='utf-8'), strict=True)

    def update_lock_member(self, lock_path, member_path):
        """Rehash exactly one listed member in one lock document."""
        lock_file = self.source / lock_path
        lock = self.toon.loads(lock_file.read_text(encoding='utf-8'), strict=True)
        row = next(row for row in lock['files'] if row['path'] == member_path)
        raw = (self.source / member_path).read_bytes()
        row['size'] = len(raw)
        row['sha256'] = hashlib.sha256(raw).hexdigest()
        lock_file.write_text(self.toon.dumps(lock) + '\n', encoding='utf-8')

    def resource_lock_path(self, resource_id):
        repository = self.toon.loads((self.source / 'repository.toon').read_text(encoding='utf-8'), strict=True)
        return next(row['source_lock'] for row in repository['resource_index'] if row['id'] == resource_id)

    def relock(self, path, *, resource_id, update_baseline=False):
        """Update only the owning resource lock and global records it pins."""
        resource_lock = self.resource_lock_path(resource_id)
        resource_doc = self.lock_doc(resource_lock)
        if not any(row['path'] == path for row in resource_doc['files']):
            raise AssertionError(f'{path} is not covered by {resource_lock}')

        lock_paths = {resource_lock}
        members_by_lock = {resource_lock: {path}}
        global_members = {path, resource_lock}
        if update_baseline:
            baseline_lock = 'locks/public-baseline.toon'
            self.update_lock_member(baseline_lock, path)
            repository = self.toon.loads((self.source / 'repository.toon').read_text(encoding='utf-8'), strict=True)
            for entry in repository['resource_index']:
                candidate = entry['source_lock']
                candidate_doc = self.lock_doc(candidate)
                if any(row['path'] == baseline_lock for row in candidate_doc['files']):
                    lock_paths.add(candidate)
                    members_by_lock.setdefault(candidate, set()).add(baseline_lock)
            global_members.add(baseline_lock)
            global_members.update(lock_paths)

        for lock_path in sorted(lock_paths):
            for member_path in sorted(members_by_lock.get(lock_path, set())):
                self.update_lock_member(lock_path, member_path)

        global_paths={row['path'] for row in self.lock_doc('locks/base-resource-sources.toon')['files']}
        for member_path in sorted(global_members & global_paths):
            self.update_lock_member('locks/base-resource-sources.toon', member_path)

    def test_source_edits_drive_real_fragment_and_inventory(self):
        old = self.root / 'old'
        self.build(self.source, old)
        spec = 'common/system/serial-console-policy/resource.toon'
        path = self.source / spec
        value = self.toon.loads(path.read_text(encoding='utf-8'), strict=True)
        value['version'] = '0.1.0+source-edit.1'
        path.write_text(self.toon.dumps(value) + '\n', encoding='utf-8')
        self.relock(spec, resource_id='resource:common-serial-console-policy')

        template = 'common/system/serial-console-policy/templates/90-oxp3-console.conf'
        with (self.source / template).open('ab') as stream:
            stream.write(b'# source change\n')
        self.relock(template, resource_id='resource:common-serial-console-policy')

        new = self.root / 'new'
        self.build(self.source, new)
        model = validate_pool(new)
        serial = next(row for row in model['resources'] if row['id'] == 'resource:common-serial-console-policy')
        self.assertEqual(serial['version'], '0.1.0+source-edit.1')
        self.assertTrue((new / serial['payload']['files'][0]['path']).read_bytes().endswith(b'# source change\n'))
        self.assertNotEqual((old / 'catalog.toon').read_bytes(), (new / 'catalog.toon').read_bytes())

    def test_resource_and_global_source_locks_are_both_enforced(self):
        path = 'common/system/serial-console-policy/templates/90-oxp3-console.conf'
        with (self.source / path).open('ab') as stream:
            stream.write(b'# lock check\n')

        with self.assertRaisesRegex(ValueError, 'Resource source lock mismatch'):
            self.build(self.source, self.root / 'stale-resource-lock')

        resource_lock = self.resource_lock_path('resource:common-serial-console-policy')
        self.update_lock_member(resource_lock, path)
        with self.assertRaisesRegex(ValueError, 'Source lock mismatch'):
            self.build(self.source, self.root / 'stale-global-lock')

    def test_tampered_locked_source_and_unknown_renderer_are_rejected(self):
        path = 'common/boot/slot-switch/resource.toon'
        target = self.source / path
        value = self.toon.loads(target.read_text(encoding='utf-8'), strict=True)
        value['recipe']['payload'][0]['renderer'] = 'arbitrary'
        target.write_text(self.toon.dumps(value) + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Resource source lock mismatch'):
            self.build(self.source, self.root / 'bad-locked-source')

        self.relock(path, resource_id='resource:common-slot-switch')
        with self.assertRaisesRegex(ValueError, 'Unsupported payload renderer'):
            self.build(self.source, self.root / 'bad-renderer')

    def test_unlisted_source_is_rejected(self):
        (self.source / 'private-unlisted.txt').write_text('not an input', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'explicit inventory'):
            self.build(self.source, self.root / 'unlisted')

    @unittest.skipUnless(sys.platform != 'win32', 'Requires ordinary symlink creation')
    def test_linked_source_root_is_rejected(self):
        link = self.root / 'source-link'
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked source root'):
            self.build(link, self.root / 'linked-output')

    def test_cpu_code_remains_fixed_even_if_all_relevant_locks_are_rehashed(self):
        path = 'baseline/brands/onexplayer/oxp3/scripts/controls/cpu/cpu_control.py'
        target = self.source / path
        target.write_bytes(target.read_bytes() + b'\n# changed\n')
        self.relock(path, resource_id='resource:intel-g3e-public-cpu-controls', update_baseline=True)
        with self.assertRaisesRegex(ValueError, 'fixed contract'):
            self.build(self.source, self.root / 'changed-cpu')

    def test_two_models_share_pool_and_keep_distinct_hdr_profiles(self):
        out = self.root / 'pool'
        self.build(self.source, out)
        pool = validate_pool(out)
        self.assertEqual(len(pool['resources']), 30)
        self.assertEqual({row['id'] for row in pool['resources']}, EXPECTED_RESOURCE_IDS)
        models = {row['id']: row for row in pool['models']}
        self.assertEqual(set(models), {'device:oxp3', 'device:x2-mini'})
        refs = lambda model: {row['resource'] for row in model['resources']}
        self.assertEqual(refs(models['device:oxp3']), EXPECTED_RESOURCE_IDS)
        self.assertEqual(refs(models['device:x2-mini']), EXPECTED_RESOURCE_IDS)

        cpu = next(row for row in pool['resources'] if row['id'] == 'resource:intel-g3e-public-cpu-controls')
        self.assertEqual(cpu['dependencies'], ['resource:common-intel-rapl'])
        self.assertNotIn('native-tdp', cpu['external_component_dependencies'])
        for identity, profile in [('device:oxp3', 'oxp3'), ('device:x2-mini', 'x2-mini')]:
            row = next(row for row in models[identity]['resources'] if row['resource'] == 'resource:gamma22-hdr-game')
            self.assertEqual(row['parameters'], {'profile': profile})
            choices = {item['id']: item for item in _choices(pool, models[identity])}
            self.assertTrue(choices['resource:gamma22-hdr-game']['default'])
            self.assertTrue(choices['resource:common-slot-switch']['default'])
            self.assertTrue(choices['resource:intel-g3e-public-cpu-controls']['default'])
            self.assertTrue(choices['resource:intel-g3e-public-cpu-controls']['selectable'])
            self.assertFalse(choices['resource:oxp3-speaker-route']['default'])

    def test_no_implicit_project_sources_or_cache_reads(self):
        original = Path.open
        adapt = (ROOT / 'adaptations').resolve()
        allowed_lock = adapt / 'toon-parser-lock.json'
        vendor = adapt / 'vendor'
        cache = (ROOT / 'cache').resolve()

        def guarded(path, *args, **kwargs):
            resolved = path.resolve()
            if resolved.is_relative_to(cache) or (resolved.is_relative_to(adapt) and resolved != allowed_lock and not resolved.is_relative_to(vendor)):
                raise AssertionError('Unauthorized SDK adaptation/cache read: ' + str(path))
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', guarded):
            self.build(self.source, self.root / 'isolated')

    def test_sdk_identity_is_required_and_recorded(self):
        loader = importlib.util.spec_from_file_location('sdk_compile_test', ROOT / 'scripts/compile-adaptations.py')
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        manifest = ROOT / 'sdk-source-contract.toon'
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            module.verified_sdk(manifest, '0' * 64)
        sdk = module.verified_sdk(manifest, digest)
        self.assertEqual(sdk['root'], ROOT)
        out = self.root / 'sdk-pool'
        module.compile(self.source, out, sdk=sdk, desktop_archive=self.desktop_archive)
        self.assertEqual(
            hashlib.sha256((out / 'provenance/sdk-source-contract.toon').read_bytes()).hexdigest(),
            digest,
        )


if __name__ == '__main__':
    unittest.main()
