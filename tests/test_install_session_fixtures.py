import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import unittest
import uuid


def load_sanitizer():
    spec = importlib.util.spec_from_file_location(
        'install_session_sanitizer', ROOT / 'scripts/sanitize-install-session.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def firmware_entry(identity):
    description = ('SteamOS fixture\0').encode('utf-16-le')
    drive = struct.pack('<BBHIQQ16sBB', 4, 1, 42, 7, 2048, 4096,
                        identity.bytes_le, 2, 2)
    path = ('\\EFI\\BOOT\\BOOTX64.EFI\0').encode('utf-16-le')
    nodes = drive + struct.pack('<BBH', 4, 4, 4 + len(path)) + path + b'\x7f\xff\x04\x00'
    return struct.pack('<IH', 1, len(nodes)) + description + nodes


def firmware_identity(entry):
    end = 6
    while entry[end:end + 2] != b'\0\0':
        end += 2
    return uuid.UUID(bytes_le=entry[end + 2 + 24:end + 2 + 40])

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/install-sessions'
ROLES = ['esp', 'efi-A', 'efi-B', 'rootfs-A', 'rootfs-B', 'var-A', 'var-B', 'home']


class InstallSessionFixtureTests(unittest.TestCase):
    def fixture(self, name):
        return FIXTURES / name

    def manifest(self, name):
        return json.loads((self.fixture(name) / 'manifest.json').read_text(encoding='utf-8'))

    def layout(self, name):
        return json.loads((self.fixture(name) / 'partition-layout.json').read_text(encoding='utf-8'))

    def test_manifest_hashes_match_the_committed_fixture(self):
        for name in ('1105-external', '1106-internal'):
            manifest = self.manifest(name)
            for member, expected in manifest['files'].items():
                data = (self.fixture(name) / member).read_bytes()
                self.assertEqual(len(data), expected['size'], member)
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected['sha256'], member)

    def test_real_topologies_are_preserved(self):
        external = self.layout('1105-external')
        self.assertEqual(external['kind'], 'external')
        self.assertEqual(external['preserved'], [])
        self.assertEqual([role['number'] for role in external['roles']], list(range(1, 9)))
        self.assertEqual([role['name'] for role in external['roles']], ROLES)
        self.assertEqual(external['selection']['start'], 1048576)
        self.assertEqual(external['selection']['size'], 137438953472)
        self.assertTrue(external['selection']['align_right'])
        self.assertEqual(external['home_used_bytes'], 775274496)

        internal = self.layout('1106-internal')
        self.assertEqual(internal['kind'], 'internal')
        self.assertEqual([item['number'] for item in internal['preserved']], [1, 2, 3, 4, 5, 6])
        self.assertEqual([role['number'] for role in internal['roles']], list(range(7, 15)))
        self.assertEqual([role['name'] for role in internal['roles']], ROLES)
        self.assertEqual(internal['selection']['start'], 779415977984)
        self.assertEqual(internal['selection']['size'], 137438953472)
        home_end = internal['roles'][-1]['offset'] + internal['roles'][-1]['size']
        trailing = [p for p in internal['preserved'] if p['offset'] >= home_end]
        self.assertEqual([p['number'] for p in trailing], [5, 6])
        self.assertEqual(internal['home_used_bytes'], 2409873408)

    def test_transcript_has_consistent_partition_progress_and_no_deployed_marker(self):
        for name in ('1105-external', '1106-internal'):
            layout = self.layout(name)
            roles = {role['name']: role for role in layout['roles']}
            totals = {}
            final = {}
            for line in (self.fixture(name) / 'full-transcript.log').read_text(encoding='utf-8').splitlines():
                self.assertNotIn('PARTITION_DEPLOYED=', line)
                if not line.startswith('PARTITION_PROGRESS='):
                    continue
                fields = line[len('PARTITION_PROGRESS='):].split(':')
                self.assertEqual(len(fields), 5, line)
                role, phase, written, verified, total = fields
                self.assertIn(role, roles, role)
                self.assertIn(phase, ('writing', 'verifying'))
                written, verified, total = int(written), int(verified), int(total)
                expected = layout['home_used_bytes'] if role == 'home' else roles[role]['size']
                self.assertEqual(total, expected, line)
                self.assertGreaterEqual(written, 0)
                self.assertGreaterEqual(verified, 0)
                self.assertLessEqual(written, total)
                self.assertLessEqual(verified, total)
                if phase == 'writing':
                    self.assertEqual(verified, 0, line)
                totals.setdefault(role, set()).add(total)
                final[role] = (written, verified, total)
            self.assertEqual(set(totals), set(ROLES))
            for role in ROLES:
                self.assertEqual(final[role][0], final[role][2], role)
                self.assertEqual(final[role][1], final[role][2], role)

    def test_progress_journal_is_monotone_and_keeps_the_observed_silence(self):
        external = self.rows('1105-external')
        internal = self.rows('1106-internal')
        for rows in (external, internal):
            percents = [row['Percent'] for row in rows]
            self.assertEqual(percents, sorted(percents))
            self.assertGreaterEqual(percents[-1], 99)
        external_steps = [row['Step'] for row in external]
        internal_steps = [row['Step'] for row in internal]
        self.assertEqual(external_steps[:8], ['01.01', '01.01', '01.03', '01.04',
                                              '01.05', '01.06', '01.07', '04.00'])
        self.assertIn('04.11', external_steps)
        self.assertIn('04.81', external_steps)
        self.assertEqual(internal_steps[:5], ['01.01', '01.02', '01.02.04', '01.02.05', '01.02.05'])
        self.assertIn('01.03', internal_steps)
        for step in ('02.01', '02.02', '02.03', '03.01', '01.06', '01.07', '04.00', '04.81', '04.99'):
            self.assertIn(step, internal_steps, step)
        component = [row for row in internal if row['Step'] == '02.01']
        self.assertGreater(max(row['elapsedMs'] for row in component) -
                           min(row['elapsedMs'] for row in component), 1_000_000)

    def test_components_plans_and_firmware_match_the_real_session(self):
        components = json.loads((self.fixture('1106-internal') / 'components.json').read_text(encoding='utf-8'))
        self.assertEqual(len(components), 18)
        self.assertEqual({row['slot'] for row in components}, {'A', 'B'})
        self.assertTrue(all(row['status'] == 'files_installed' for row in components))
        self.assertIn('x2-mini:ssd-gm7-boot-repair', {row['id'] for row in components})

        plan_a = json.loads((self.fixture('1106-internal') / 'install-plan-A.json').read_text(encoding='utf-8'))
        plan_b = json.loads((self.fixture('1106-internal') / 'install-plan-B.json').read_text(encoding='utf-8'))
        self.assertEqual(plan_a['slot'], 'A')
        self.assertEqual(plan_b['slot'], 'B')
        left, right = dict(plan_a), dict(plan_b)
        left.pop('slot'); right.pop('slot')
        left.pop('ssd_repair'); right.pop('ssd_repair')
        self.assertEqual(left, right)
        self.assertEqual(plan_a['ssd_repair']['id'], plan_b['ssd_repair']['id'])
        self.assertNotEqual(plan_a['ssd_repair']['root_partuuid'], plan_b['ssd_repair']['root_partuuid'])
        self.assertNotEqual(plan_a['ssd_repair']['esp_partuuid'], plan_b['ssd_repair']['esp_partuuid'])
        self.assertEqual(len(plan_a['components']), 8)
        self.assertEqual(plan_a['device_package']['id'], 'device:x2-mini')
        self.assertEqual(plan_a['model_efi']['kind'], 'x2-split-lock-efi-v1')
        self.assertEqual(plan_a['ssd_repair']['id'], 'x2-mini:ssd-gm7-boot-repair')

        before = json.loads((self.fixture('1106-internal') / 'firmware-before.json').read_text(encoding='utf-8'))
        result = json.loads((self.fixture('1106-internal') / 'firmware-result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'registered')
        self.assertEqual(result['name'], before['name'])
        self.assertTrue(result['makeDefault'])
        self.assertFalse(result['defaultPreserved'])

        markers = (self.fixture('1106-internal') / 'verify-markers.log').read_text(encoding='utf-8').splitlines()
        prefix = 'OXP_INTEGRATION_VERIFY='
        self.assertEqual([json.loads(line[len(prefix):]) for line in markers],
                         [{'slot': 'A', 'readonly': True}, {'slot': 'B', 'readonly': True}])
        self.assertEqual((self.fixture('1105-external') / 'verify-markers.log').read_text(encoding='utf-8'), '')

    def test_user_paths_and_serials_are_synthetic(self):
        import re
        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.lower() == 'serial' and isinstance(item, str) and item:
                        self.assertRegex(item, r'^SANITIZED[_A-F0-9]*$')
                    if key == 'Snapshot' and isinstance(item, str):
                        walk(json.loads(item))
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, str):
                for match in re.finditer(r'(?i)[A-Z]:\\Users\\([^\\/\s"\']+)', value):
                    self.assertEqual(match.group(1), 'fixture')
        for name in ('1105-external', '1106-internal'):
            for path in self.fixture(name).glob('*.json'):
                walk(json.loads(path.read_text(encoding='utf-8')))

    def test_identity_mapping_is_consistent_across_all_fixture_views(self):
        for name in ('1105-external', '1106-internal'):
            directory = self.fixture(name)
            physical = json.loads((directory / 'physical-state.json').read_bytes())
            layout = self.layout(name)
            snapshots = json.loads((directory / 'disk-snapshots.json').read_bytes())
            disk = next(row for row in snapshots if row['number'] == layout['disk']['number'])
            self.assertEqual(uuid.UUID(layout['disk']['guid']), uuid.UUID(disk['guid']))
            self.assertEqual(layout['disk']['serial'], disk['serial'])
            self.assertEqual(layout['disk']['serial'], physical['device']['Serial'])
            for role, added in zip(layout['roles'], physical['plan']['Added']):
                self.assertEqual(uuid.UUID(role['id']), uuid.UUID(added['Id']))
        directory = self.fixture('1106-internal')
        before = json.loads((directory / 'firmware-before.json').read_bytes())
        identity = firmware_identity(base64.b64decode(before['entry'], validate=True))
        layout = self.layout('1106-internal')
        esp = next(role for role in layout['roles'] if role['name'] == 'esp')
        self.assertEqual(identity, uuid.UUID(esp['id']))
        after = json.loads((ROOT / 'tests/fixtures/disks/1106-gm7-after-install.json').read_bytes())
        for role in layout['roles']:
            part = next(part for part in after['partitions'] if part['number'] == role['number'])
            self.assertEqual(uuid.UUID(part['guid']), uuid.UUID(role['id']))

    def rows(self, name):
        return [json.loads(line) for line in
                (self.fixture(name) / 'progress.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


class InstallSessionSanitizerTests(unittest.TestCase):
    def setUp(self):
        self.sanitizer = load_sanitizer()
        self.identity = uuid.UUID('abcdef01-2345-4678-9abc-def012345678')

    def test_guid_case_and_braces_resolve_to_one_synthetic_identity(self):
        raw = str(self.identity)
        expected = uuid.UUID(self.sanitizer.fake_guid(raw))
        for spelling in (raw, raw.upper(), '{' + raw + '}', '{' + raw.upper() + '}'):
            sanitized = self.sanitizer.sanitize_obj({'id': spelling}, set())['id']
            self.assertEqual(uuid.UUID(sanitized), expected)
            self.assertNotEqual(uuid.UUID(sanitized), self.identity)

    def test_binary_efi_identity_uses_the_same_mapping_without_changing_topology(self):
        original = firmware_entry(self.identity)
        sanitized = self.sanitizer.sanitize_obj(
            {'entry': base64.b64encode(original).decode('ascii')}, set())
        actual = base64.b64decode(sanitized['entry'], validate=True)
        expected = uuid.UUID(self.sanitizer.fake_guid(str(self.identity)))
        self.assertEqual(firmware_identity(actual), expected)
        self.assertNotIn(self.identity.bytes_le, actual)
        self.assertEqual(actual, original.replace(self.identity.bytes_le, expected.bytes_le))

    def test_unreviewed_or_malformed_efi_records_are_not_exported_unchanged(self):
        original = firmware_entry(self.identity)
        start = 6 + len(('SteamOS fixture\0').encode('utf-16-le'))
        bad_length = bytearray(original)
        struct.pack_into('<H', bad_length, start + 2, 0)
        unknown_node = bytearray(original)
        unknown_node[start] = 1
        mbr_signature = bytearray(original)
        mbr_signature[start + 41] = 1
        for entry in (b'', original[:-1], bytes(bad_length), bytes(unknown_node),
                      bytes(mbr_signature), original + b'unreviewed-optional-data'):
            with self.subTest(size=len(entry)), self.assertRaises(ValueError):
                self.sanitizer.sanitize_obj({'entry': base64.b64encode(entry).decode('ascii')}, set())
        with self.assertRaises(ValueError):
            self.sanitizer.sanitize_obj({'entry': 'invalid base64'}, set())


if __name__ == '__main__':
    unittest.main()
