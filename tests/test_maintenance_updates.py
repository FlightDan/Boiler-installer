import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from maintenance import Engine, MaintenanceError
from maintenance import updates
from protocol.manifest import ProtocolError


class MaintenanceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.root = self.work/'target'
        (self.root/'etc').mkdir(parents=True)
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID="3.10"\nBUILD_ID=test-build\n')
        binary = bytearray(20)
        binary[:6] = b'\x7fELF\x02\x01'
        binary[18:20] = (62).to_bytes(2, 'little')
        (self.root/'usr/bin').mkdir(parents=True)
        (self.root/'usr/bin/env').write_bytes(binary)
        (self.root/'usr/lib/modules/kernel-test').mkdir(parents=True)
        self.environment = {'architecture': 'x86_64', 'os_id': 'steamos', 'os_version': '3.10', 'kernels': ['kernel-test']}
        self.engine = Engine(self.root)
        self.old = self.work/'old-payload'
        self.old.mkdir()
        self.old_component = self.component(self.old, 'core:maintenance', '1.0.0', b'old runtime\n')
        self.engine.apply(self.old_component, self.old, environment=self.environment)
        self.key = Ed25519PrivateKey.generate()
        self.public_key = self.work/'test-only-public.pem'
        self.public_key.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        self.cache = self.work/'registry-cache'
        self.cache.mkdir()

    def component(self, payload, identity, version, data, *, target=None, requires=()):
        name = identity.replace(':', '-')+'.bin'
        (payload/name).write_bytes(data)
        target = target or 'usr/local/lib/boiler-test/'+identity.replace(':', '-')+'.txt'
        return {'schema_version': 1, 'kind': 'ComponentManifest', 'id': identity, 'version': version,
                'compatibility': {'architectures': ['x86_64'], 'os_ids': ['steamos'], 'os_versions': ['3.10'], 'kernels': ['kernel-test']},
                'requires': list(requires), 'conflicts': [],
                'files': [{'source': name, 'target': target, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o644}]}

    def package(self, *, version='2.0.0', components=None, scripted=False):
        payload = self.work/('package-'+str(len(list(self.work.glob('package-*')))))
        payload.mkdir()
        if components is None:
            components = [self.component(payload, 'core:maintenance', version, b'new runtime\n')]
        else:
            components = components(payload)
        extra = {}
        if scripted:
            (payload/'scripts').mkdir()
            (payload/'scripts/apply.sh').write_text('#!/bin/sh\nexit 0\n')
            (payload/'scripts/maintenance.sh').write_text('#!/bin/sh\nexit 0\n')
            components[0]['hooks'] = {'apply': 'scripts/apply.sh'}
            extra['maintenance_entry'] = 'scripts/maintenance.sh'
        inventory = [{'path': path.relative_to(payload).as_posix(), 'size': path.stat().st_size,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(payload.rglob('*')) if path.is_file()]
        value = {'schema_version': 1, 'kind': 'DevicePackage', 'id': 'bundle:maintenance', 'version': version,
                 'source': {'kind': 'github', 'repository': 'FlightDan/Boiler-installer', 'commit': 'a'*40},
                 'compatibility': {'architectures': ['x86_64'], 'os_ids': ['steamos'], 'kernels': ['kernel-test']},
                 'components': components, 'files': inventory, **extra}
        (payload/'manifest.json').write_text(json.dumps(value))
        return payload, value

    def sign_index(self, package, *, sequence=10, revoked=False, repository='FlightDan/Boiler-installer'):
        payload, manifest = package
        data = (payload/'manifest.json').read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        index = {'schema_version': 1, 'kind': 'DeviceIndex', 'id': 'registry:boiler-devices', 'version': '0.1.0', 'sequence': sequence,
                 'generated_at': '2026-10-23T00:00:00Z', 'revocations': [digest] if revoked else [],
                 'packages': [{'id': manifest['id'], 'version': manifest['version'], 'size': len(data), 'sha256': digest,
                               'source': {'kind': 'github', 'repository': repository, 'commit': 'b'*40}, 'compatibility': manifest['compatibility']}]}
        raw = json.dumps(index, sort_keys=True).encode()
        raw_digest = hashlib.sha256(raw).hexdigest()
        generation = self.cache/raw_digest
        generation.mkdir(exist_ok=True)
        (generation/'index.json').write_bytes(raw)
        (generation/'index.sig').write_bytes(self.key.sign(raw))
        (self.cache/'current.json').write_text(json.dumps({'generation': raw_digest, 'sha256': raw_digest, 'sequence': sequence}))
        return index

    def plan(self, package, **kwargs):
        return updates.plan_update(self.root, self.cache, package[0], 'bundle:maintenance', package[1]['version'], public_key=self.public_key, **kwargs)

    def execute(self, plan, package):
        return updates.execute_plan(self.root, plan, self.cache, package[0], reviewed_sha256=plan['review_sha256'], public_key=self.public_key)

    def target_bytes(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def test_inventory_and_signed_planning_are_readonly(self):
        package = self.package()
        self.sign_index(package)
        before = self.target_bytes()
        inventory = updates.inventory(self.root)
        self.assertEqual(inventory['environment']['architecture'], 'x86_64')
        self.assertEqual(inventory['environment']['kernels'], ['kernel-test'])
        self.assertTrue(inventory['components']['core:maintenance']['verified'])
        discovery = updates.discover(self.root, self.cache, public_key=self.public_key)
        self.assertTrue(discovery['candidates'][0]['compatible'])
        plan = self.plan(package)
        self.assertTrue(plan['executable'])
        self.assertEqual(plan['actions'][0]['from_version'], '1.0.0')
        self.assertEqual(before, self.target_bytes())

    def test_inventory_does_not_create_absent_target_state(self):
        fresh = self.work/'unmanaged-target'
        for name in ('etc/os-release', 'usr/bin/env'):
            path = fresh/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((self.root/name).read_bytes())
        result = updates.inventory(fresh)
        self.assertEqual(result['components'], {})
        self.assertFalse((fresh/'var').exists())

    def test_existing_dependency_outside_bundle_is_not_reinstalled(self):
        base = self.work/'base'
        base.mkdir()
        installed = self.component(base, 'core:base', '1.0.0', b'base runtime\n')
        self.engine.apply(installed, base, environment=self.environment)
        package = self.package(components=lambda payload: [self.component(payload, 'core:maintenance', '2.0.0', b'new runtime\n', requires=['core:base'])])
        self.sign_index(package)
        plan = self.plan(package)
        self.assertEqual([item['action'] for item in plan['actions']], ['unchanged', 'upgrade'])
        self.assertEqual(len(self.execute(plan, package)['transactions']), 1)
        self.assertEqual((self.root/installed['files'][0]['target']).read_bytes(), b'base runtime\n')

    def test_execute_signed_file_upgrade_and_keep_source_identity(self):
        package = self.package()
        self.sign_index(package)
        plan = self.plan(package)
        result = self.execute(plan, package)
        self.assertEqual(result['status'], 'committed')
        self.assertTrue(self.engine.verify('core:maintenance')['ok'])
        target = self.old_component['files'][0]['target']
        self.assertEqual((self.root/target).read_bytes(), b'new runtime\n')
        provenance = json.loads((self.engine.state_dir/'updates.json').read_text())
        self.assertEqual(provenance['packages']['bundle:maintenance']['source_identity'], 'github:flightdan/boiler-installer')
        self.assertEqual(provenance['registries']['registry:boiler-devices']['sequence'], 10)
        self.assertEqual(json.loads(Path(result['receipt']).read_text())['status'], 'committed')

    def test_ordinary_signed_upgrade_cannot_replace_installed_signing_key(self):
        payload = self.work/'key-payload'
        payload.mkdir()
        target = 'usr/local/lib/boiler/protocol/trusted-public-key.pem'
        component = self.component(payload, 'core:trust', '1.0.0', self.public_key.read_bytes(), target=target)
        self.engine.apply(component, payload, environment=self.environment)
        package = self.package(components=lambda output: [self.component(output, 'core:trust', '2.0.0', b'replacement key\n', target=target)])
        self.sign_index(package)
        before = self.target_bytes()
        with self.assertRaisesRegex(updates.UpdateError, 'signing key'):
            self.plan(package)
        self.assertEqual(self.target_bytes(), before)

    def test_downgrade_equal_version_edits_and_unknown_component_refused(self):
        for version, identity in (('0.9.0', 'core:maintenance'), ('1.0.0', 'core:maintenance'), ('2.0.0', 'core:unknown')):
            with self.subTest(version=version, identity=identity):
                package = self.package(version=version, components=lambda payload: [self.component(payload, identity, version, b'changed\n')])
                self.sign_index(package)
                with self.assertRaises(UpdateErrorTypes):
                    self.plan(package, selected=[identity])

    def test_existing_unowned_new_target_is_not_adopted(self):
        (self.root/'etc/local.conf').write_bytes(b'user owned configuration')
        def components(payload):
            item = self.component(payload, 'core:maintenance', '2.0.0', b'new runtime\n')
            additional = self.component(payload, 'core:extra', '2.0.0', b'override', target='etc/local.conf')['files'][0]
            item['files'].append(additional)
            return [item]
        package = self.package(components=components)
        self.sign_index(package)
        with self.assertRaises(updates.UpdateError):
            self.plan(package)
        self.assertEqual((self.root/'etc/local.conf').read_bytes(), b'user owned configuration')

    def test_stale_review_or_modified_installed_file_refused(self):
        package = self.package()
        self.sign_index(package)
        plan = self.plan(package)
        with self.assertRaises(updates.UpdateError):
            updates.execute_plan(self.root, plan, self.cache, package[0], reviewed_sha256='0'*64, public_key=self.public_key)
        (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID="3.10"\nBUILD_ID=changed\n')
        with self.assertRaises(updates.UpdateError):
            self.execute(plan, package)
        target = self.root/self.old_component['files'][0]['target']
        target.write_bytes(b'edited runtime')
        with self.assertRaises(updates.UpdateError):
            self.plan(package)
        self.assertEqual(target.read_bytes(), b'edited runtime')

    def test_bad_signature_revocation_and_pending_journal_refused(self):
        package = self.package()
        self.sign_index(package)
        pointer = json.loads((self.cache/'current.json').read_text())
        signature = self.cache/pointer['generation']/'index.sig'
        signature.write_bytes(b'bad signature')
        with self.assertRaises(ProtocolError):
            self.plan(package)
        self.sign_index(package, sequence=11, revoked=True)
        with self.assertRaises(updates.UpdateError):
            self.plan(package)
        self.sign_index(package, sequence=12)
        pending = self.engine.journals/('f'*32+'.json')
        pending.write_text(json.dumps({'status': 'manual_recovery_required'}))
        with self.assertRaises(updates.UpdateError):
            self.plan(package)
        self.assertEqual(json.loads(pending.read_text())['status'], 'manual_recovery_required')

    def test_scripted_package_requires_separate_authenticated_model_entry(self):
        package = self.package(scripted=True)
        self.sign_index(package)
        plan = self.plan(package)
        self.assertFalse(plan['executable'])
        self.assertEqual(plan['maintenance_entry'], 'scripts/maintenance.sh')
        with mock.patch.object(Engine, 'apply') as apply:
            with self.assertRaises(updates.UpdateError):
                self.execute(plan, package)
            apply.assert_not_called()

    def test_failed_execution_preserves_receipt_and_old_journals(self):
        package = self.package()
        self.sign_index(package)
        plan = self.plan(package)
        journals = {path.name: path.read_bytes() for path in self.engine.journals.glob('*.json')}
        with mock.patch.object(Engine, 'apply', side_effect=RuntimeError('simulated I/O refusal')):
            with self.assertRaises(RuntimeError):
                self.execute(plan, package)
        receipt = self.engine.state_dir/'updates'/(plan['review_sha256']+'.json')
        self.assertEqual(json.loads(receipt.read_text())['status'], 'failed-preserved')
        self.assertTrue(all((self.engine.journals/name).read_bytes() == content for name, content in journals.items()))
        retried = self.execute(plan, package)
        self.assertEqual(retried['status'], 'committed')
        self.assertIn('-retry-2.json', retried['receipt'])
        self.assertEqual(json.loads(receipt.read_text())['status'], 'failed-preserved')

    def test_concurrent_upgrade_cannot_be_downgraded_by_reviewed_plan(self):
        package = self.package()
        self.sign_index(package)
        plan = self.plan(package)
        concurrent = self.work/'concurrent'
        concurrent.mkdir()
        newer = self.component(concurrent, 'core:maintenance', '3.0.0', b'concurrent v3\n')
        original_apply = Engine.apply
        def racing_apply(instance, component, payload_root, **kwargs):
            original_apply(instance, newer, concurrent, environment=self.environment)
            return original_apply(instance, component, payload_root, **kwargs)
        with mock.patch.object(Engine, 'apply', racing_apply):
            with self.assertRaises(MaintenanceError):
                self.execute(plan, package)
        target = self.root/self.old_component['files'][0]['target']
        self.assertEqual(target.read_bytes(), b'concurrent v3\n')

    def test_executed_registry_cannot_roll_back_or_change_repository(self):
        first = self.package()
        self.sign_index(first)
        self.execute(self.plan(first), first)
        later = self.package(version='3.0.0')
        self.sign_index(later, sequence=9)
        with self.assertRaises(updates.UpdateError):
            self.plan(later)
        self.sign_index(later, sequence=11, repository='Different/Publisher')
        with self.assertRaises(updates.UpdateError):
            self.plan(later)


UpdateErrorTypes = (updates.UpdateError, ProtocolError)

if __name__ == '__main__':
    unittest.main()
