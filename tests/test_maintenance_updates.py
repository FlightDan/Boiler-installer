import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
except ImportError:
    Ed25519PrivateKey = None
from maintenance import Engine, MaintenanceError
from maintenance import updates
from maintenance.engine import build_hook_environment
from protocol.manifest import ProtocolError


class OpenSSLTestSigner:
    """Disposable test key only; preserve real Ed25519 checks without Python extras."""
    def __init__(self, work, executable, public_key):
        self.work, self.executable = work, executable
        self.private_key = work/'test-only-private.pem'
        self.run('genpkey', '-algorithm', 'ED25519', '-out', str(self.private_key))
        self.run('pkey', '-in', str(self.private_key), '-pubout', '-out', str(public_key))

    def run(self, *args):
        subprocess.run([self.executable, *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def sign(self, data):
        with tempfile.TemporaryDirectory(prefix='test-signature-', dir=self.work) as temporary:
            message, signature = Path(temporary)/'index.json', Path(temporary)/'index.sig'
            message.write_bytes(data)
            self.run('pkeyutl', '-sign', '-inkey', str(self.private_key), '-rawin', '-in', str(message), '-out', str(signature))
            return signature.read_bytes()


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
        self.public_key = self.work/'test-only-public.pem'
        self.openssl = os.environ.get('BOILER_TEST_OPENSSL') or shutil.which('openssl')
        if Ed25519PrivateKey is not None and not os.environ.get('BOILER_TEST_OPENSSL'):
            self.key = Ed25519PrivateKey.generate()
            self.public_key.write_bytes(self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        elif self.openssl:
            self.key = OpenSSLTestSigner(self.work, self.openssl, self.public_key)
        else:
            self.skipTest('Ed25519 tests require system OpenSSL or the optional cryptography module')
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
        return updates.plan_update(self.root, self.cache, package[0], 'bundle:maintenance', package[1]['version'], public_key=self.public_key, openssl=self.openssl or 'openssl', **kwargs)

    def execute(self, plan, package):
        return updates.execute_plan(self.root, plan, self.cache, package[0], reviewed_sha256=plan['review_sha256'], public_key=self.public_key, openssl=self.openssl or 'openssl')

    def target_bytes(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}

    def install_owned_receipt(self):
        import copy
        component = copy.deepcopy(self.old_component)
        data = b'historical component revision\n'
        (self.old/'receipt.bin').write_bytes(data)
        component['files'].append({'source': 'receipt.bin', 'target': 'usr/local/lib/boiler-test/historical-receipt.json',
                                   'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': 0o600})
        self.engine.apply(component, self.old, environment=self.environment)
        return self.root/'usr/local/lib/boiler-test/historical-receipt.json'

    def test_signed_retained_ownership_plan_upgrade_and_restore(self):
        receipt = self.install_owned_receipt()
        prior = self.engine._state()['components']['core:maintenance']['transaction']
        old_receipt = receipt.read_bytes()
        def components(payload):
            value = self.component(payload, 'core:maintenance', '2.0.0', b'new runtime\n')
            value['retain_owned_paths'] = True
            return [value]
        package = self.package(components=components)
        self.sign_index(package)
        plan = self.plan(package)
        retained = plan['actions'][0]['retained_owned_paths']
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]['transaction'], prior)
        self.assertEqual(retained[0]['target'], receipt.relative_to(self.root).as_posix())
        self.assertEqual(retained[0]['snapshot']['sha256'], hashlib.sha256(old_receipt).hexdigest())
        self.assertNotIn('data', retained[0]['snapshot'])
        self.execute(plan, package)
        self.assertEqual(receipt.read_bytes(), old_receipt)
        transaction = self.engine._state()['components']['core:maintenance']['transaction']
        self.assertNotEqual(transaction, prior)
        self.assertEqual(self.engine._state()['owners'][retained[0]['target']]['transaction'], transaction)
        self.engine.restore(transaction)
        self.assertEqual(receipt.read_bytes(), old_receipt)
        self.assertEqual(self.engine._state()['components']['core:maintenance']['transaction'], prior)
        self.assertTrue(self.engine.verify('core:maintenance')['ok'])

    def test_retained_ownership_is_explicit_and_changed_files_invalidate_review(self):
        receipt = self.install_owned_receipt()
        ordinary = self.package()
        self.sign_index(ordinary)
        with self.assertRaisesRegex(MaintenanceError, 'drops managed paths'):
            self.plan(ordinary)
        def components(payload):
            value = self.component(payload, 'core:maintenance', '2.0.0', b'new runtime\n')
            value['retain_owned_paths'] = True
            return [value]
        package = self.package(components=components)
        self.sign_index(package, sequence=11)
        plan = self.plan(package)
        receipt.write_bytes(b'user modification')
        runtime = self.root/self.old_component['files'][0]['target']
        with self.assertRaises(MaintenanceError):
            self.execute(plan, package)
        self.assertEqual(runtime.read_bytes(), b'old runtime\n')
        self.assertEqual(receipt.read_bytes(), b'user modification')

    def test_equal_version_static_retention_is_unchanged_not_a_fabricated_upgrade(self):
        self.install_owned_receipt()
        def components(payload):
            value = self.component(payload, 'core:maintenance', '1.0.0', b'old runtime\n')
            value['retain_owned_paths'] = True
            return [value]
        package = self.package(version='1.0.0', components=components)
        self.sign_index(package)
        plan = self.plan(package)
        self.assertEqual(plan['actions'][0]['action'], 'unchanged')
        before = self.engine._state()
        self.execute(plan, package)
        self.assertEqual(self.engine._state(), before)

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

    def engine_hook_package(self, *, extra_components=False, script=b'#!/bin/bash\nexit 0\n'):
        def components(payload):
            items = [self.component(payload, 'core:maintenance', '2.0.0', b'new runtime\n')]
            if extra_components:
                existing = self.work/'second-old'
                existing.mkdir()
                old = self.component(existing, 'core:second', '1.0.0', b'old second\n')
                self.engine.apply(old, existing, environment=self.environment)
                items.append(self.component(payload, 'core:second', '2.0.0', b'new second\n'))
            return items
        package = self.package(components=components, scripted=True)
        payload, manifest = package
        manifest['maintenance_entry'] = {'kind': 'engine-hooks-v1'}
        (payload/'scripts/apply.sh').write_bytes(script)
        manifest['files'] = [dict(path=path.relative_to(payload).as_posix(), size=path.stat().st_size,
                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                             for path in sorted(payload.rglob('*')) if path.is_file() and path.name != 'manifest.json']
        (payload/'manifest.json').write_text(json.dumps(manifest))
        self.sign_index(package)
        return package

    def test_engine_hook_contract_authorizes_only_private_signed_snapshot(self):
        package = self.engine_hook_package()
        plan = self.plan(package)
        self.assertTrue(plan['executable'])
        self.assertEqual(plan['execution'], 'engine-hooks-v1')
        original_apply = Engine.apply
        calls = []
        def checked_apply(engine, component, payload, **kwargs):
            calls.append((component, Path(payload), kwargs))
            self.assertNotEqual(Path(payload), package[0])
            self.assertEqual((Path(payload)/'scripts/apply.sh').read_bytes(), b'#!/bin/bash\nexit 0\n')
            self.assertTrue(kwargs['allow_hooks'])
            self.assertNotIn('hook_environment', kwargs)
            self.assertEqual(kwargs['expected_state_sha256'], plan['state_sha256'])
            if os.name != 'nt':
                self.assertEqual(Path(payload).stat().st_mode & 0o777, 0o700)
            # Windows cannot execute Linux hooks. Exercise the authentication
            # bridge and real file transaction; native hook coverage is below.
            files_only = dict(component)
            files_only.pop('hooks')
            return original_apply(engine, files_only, payload, environment=kwargs['environment'],
                                  expected_state_sha256=kwargs['expected_state_sha256'])
        with mock.patch.object(Engine, 'apply', checked_apply):
            result = self.execute(plan, package)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0][1].exists())

    def test_scripted_review_rejects_tampered_identity_source_revocation_and_payload(self):
        for change in ('identity', 'source', 'revocation', 'payload'):
            with self.subTest(change=change):
                package = self.engine_hook_package()
                plan = self.plan(package)
                if change == 'payload':
                    (package[0]/'scripts/apply.sh').write_bytes(b'#!/bin/bash\nexit 1\n')
                elif change == 'identity':
                    package[1]['id'] = 'bundle:other'
                    (package[0]/'manifest.json').write_text(json.dumps(package[1]))
                    self.sign_index(package, sequence=11)
                elif change == 'source':
                    self.sign_index(package, sequence=11, repository='Different/Publisher')
                else:
                    self.sign_index(package, sequence=11, revoked=True)
                with mock.patch.object(Engine, 'apply') as apply:
                    with self.assertRaises(UpdateErrorTypes):
                        self.execute(plan, package)
                    apply.assert_not_called()

    def test_same_version_hook_bytes_and_unknown_contract_fail_closed(self):
        package = self.engine_hook_package()
        package[1]['components'][0]['version'] = '1.0.0'
        (package[0]/'manifest.json').write_text(json.dumps(package[1]))
        self.sign_index(package)
        with self.assertRaisesRegex(updates.UpdateError, 'scripted component'):
            self.plan(package)
        package[1]['components'][0]['version'] = '2.0.0'
        package[1]['maintenance_entry'] = {'kind': 'run-arbitrary-script'}
        (package[0]/'manifest.json').write_text(json.dumps(package[1]))
        self.sign_index(package)
        with self.assertRaisesRegex((updates.UpdateError, ProtocolError), 'entry|contract'):
            self.plan(package)

    def test_registry_change_after_first_commit_stops_remaining_components(self):
        package = self.engine_hook_package(extra_components=True)
        plan = self.plan(package)
        original_apply = Engine.apply
        calls = []
        def revoking_apply(engine, component, payload, **kwargs):
            calls.append(component['id'])
            files_only = dict(component)
            files_only.pop('hooks', None)
            result = original_apply(engine, files_only, payload, environment=kwargs['environment'],
                                    expected_state_sha256=kwargs['expected_state_sha256'])
            self.sign_index(package, sequence=11, revoked=True)
            return result
        with mock.patch.object(Engine, 'apply', revoking_apply):
            with self.assertRaisesRegex(updates.UpdateError, 'registry changed'):
                self.execute(plan, package)
        self.assertEqual(calls, ['core:maintenance'])
        self.assertEqual((self.root/'usr/local/lib/boiler-test/core-second.txt').read_bytes(), b'old second\n')
        receipt = json.loads((self.engine.state_dir/'updates'/(plan['review_sha256']+'.json')).read_text())
        self.assertEqual(receipt['status'], 'failed-preserved')
        self.assertEqual(len(receipt['transactions']), 1)

    def test_hook_failure_barrier_stops_later_components_and_subsequent_execution(self):
        package = self.engine_hook_package(extra_components=True)
        plan = self.plan(package)
        calls = []
        def failed_hook(engine, component, payload, **kwargs):
            calls.append(component['id'])
            # Mirror the Engine's durable failure boundary, also covered by the
            # native Linux test; do not infer that arbitrary effects rolled back.
            (engine.journals/('f'*32+'.json')).write_text(json.dumps(dict(status='manual_recovery_required')))
            raise MaintenanceError('simulated failed authenticated hook')
        with mock.patch.object(Engine, 'apply', failed_hook):
            with self.assertRaises(MaintenanceError):
                self.execute(plan, package)
        self.assertEqual(calls, ['core:maintenance'])
        with mock.patch.object(Engine, 'apply') as apply:
            with self.assertRaisesRegex(updates.UpdateError, 'pending'):
                self.execute(plan, package)
            apply.assert_not_called()
        self.assertEqual((self.root/'usr/local/lib/boiler-test/core-second.txt').read_bytes(), b'old second\n')

    def test_hook_environment_is_minimal_and_requires_explicit_context(self):
        with mock.patch.dict(os.environ, {'BASH_ENV': '/bad', 'ENV': '/bad', 'LD_PRELOAD': '/bad',
                                          'PYTHONPATH': '/bad', 'PATH': '/bad', 'BOILER_GUEST': 'spoof'}):
            env = build_hook_environment(self.root, 'transaction')
        self.assertEqual(set(env), {'PATH', 'LANG', 'LC_ALL', 'HOME', 'BOILER_TARGET_ROOT', 'BOILER_TRANSACTION_ID'})
        self.assertEqual(env['PATH'], '/usr/sbin:/usr/bin:/sbin:/bin')
        self.assertEqual(build_hook_environment(self.root, 'transaction', {'BOILER_INSTALL_SLOT': 'A'})['BOILER_INSTALL_SLOT'], 'A')
        for extra in ({'BASH_ENV': '/bad'}, {'BOILER_TARGET_ROOT': '/other'}, {'BOILER_EXTRA': 'bad\x00value'}):
            with self.assertRaises(MaintenanceError):
                build_hook_environment(self.root, 'transaction', extra)

    @unittest.skipUnless(os.name != 'nt' and os.geteuid() == 0, 'Ancestor ownership checks require Linux root')
    def test_native_snapshot_rejects_unprotected_ancestor_and_accepts_root_sticky_parent(self):
        package = self.engine_hook_package()
        parent = self.work/'unprotected-parent'
        parent.mkdir()
        parent.chmod(0o777)
        state = parent/'root-owned-state'
        state.mkdir(mode=0o700)
        digest = hashlib.sha256((package[0]/'manifest.json').read_bytes()).hexdigest()
        with self.assertRaisesRegex(updates.UpdateError, 'writable ancestor'):
            with updates._authenticated_payload(package[0], digest, state):
                self.fail('Writable ancestor must be refused before executing signed code')
        parent.chmod(0o1777)
        with updates._authenticated_payload(package[0], digest, state) as snapshot:
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
        os.chown(parent, 65534, 65534)
        try:
            with self.assertRaisesRegex(updates.UpdateError, 'untrusted ancestor'):
                updates._protected_staging_path(state)
        finally:
            os.chown(parent, 0, 0)

    @unittest.skipIf(os.name == 'nt', 'No-follow/nonblocking source checks require Linux')
    def test_native_source_open_rejects_fifo_and_swapped_symbolic_link(self):
        fifo = self.work/'fifo'
        os.mkfifo(fifo)
        with self.assertRaisesRegex(updates.UpdateError, 'regular file'):
            with updates._regular_stream(fifo):
                self.fail('FIFO sources must not wait for a writer')
        ordinary = self.work/'ordinary'
        ordinary.write_bytes(b'authenticated bytes')
        link = self.work/'link'
        link.symlink_to(ordinary)
        with self.assertRaises(OSError):
            with updates._regular_stream(link):
                self.fail('Swapped symbolic links must not be followed')

    @unittest.skipIf(os.name == 'nt', 'Native Engine Bash hooks require Linux')
    def test_native_hook_success_runs_apply_verify_with_minimal_environment(self):
        package = self.engine_hook_package(script=b'#!/bin/bash\nset -eu\nprintf apply > "$BOILER_TARGET_ROOT/hook-result"\n')
        payload, manifest = package
        (payload/'scripts/verify.sh').write_text('set -eu\ntest "$(cat "$BOILER_TARGET_ROOT/hook-result")" = apply\nprintf verified >> "$BOILER_TARGET_ROOT/hook-result"\n')
        manifest['components'][0]['hooks']['verify'] = 'scripts/verify.sh'
        manifest['files'].append(dict(path='scripts/verify.sh', size=(payload/'scripts/verify.sh').stat().st_size,
                                      sha256=hashlib.sha256((payload/'scripts/verify.sh').read_bytes()).hexdigest()))
        (payload/'manifest.json').write_text(json.dumps(manifest))
        self.sign_index(package)
        poison = self.work/'bash-env'
        poison.write_text('touch "'+str(self.work/'poison-ran')+'"\n')
        with mock.patch.dict(os.environ, {'BASH_ENV': str(poison), 'BOILER_GUEST': 'spoof'}):
            result = self.execute(self.plan(package), package)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual((self.root/'hook-result').read_text(), 'applyverified')
        self.assertFalse((self.work/'poison-ran').exists())

    @unittest.skipIf(os.name == 'nt', 'Native Engine Bash hooks require Linux')
    def test_native_failed_hook_preserves_manual_recovery_and_blocks_later_component(self):
        package = self.engine_hook_package(extra_components=True, script=b'#!/bin/bash\nprintf side-effect > "$BOILER_TARGET_ROOT/failed-hook-effect"\nexit 7\n')
        plan = self.plan(package)
        with self.assertRaises(Exception):
            self.execute(plan, package)
        pending = [json.loads(path.read_text()) for path in self.engine.journals.glob('*.json')
                   if json.loads(path.read_text()).get('status') == 'manual_recovery_required']
        self.assertEqual(len(pending), 1)
        self.assertEqual((self.root/'failed-hook-effect').read_text(), 'side-effect')
        self.assertEqual((self.root/'usr/local/lib/boiler-test/core-second.txt').read_bytes(), b'old second\n')
        with self.assertRaises(MaintenanceError):
            self.engine.recover()
        with self.assertRaises(updates.UpdateError):
            self.execute(plan, package)

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
        later[1]['source']['repository'] = 'Different/DeclaredSource'
        (later[0]/'manifest.json').write_text(json.dumps(later[1]))
        self.sign_index(later, sequence=11)
        with self.assertRaisesRegex(updates.UpdateError, 'declared source'):
            self.plan(later)


UpdateErrorTypes = (updates.UpdateError, ProtocolError)

if __name__ == '__main__':
    unittest.main()
