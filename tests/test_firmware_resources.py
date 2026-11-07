"""Fixed handler integration against real Engine journals in temporary A/B roots."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maintenance.resource_runtime import ResourcePool, ResourceSession, ResourceError, PartialInstall, installer_install, maintenance_action
from maintenance.engine import MaintenanceError, _json_write, RUNTIME_FILES
from protocol.firmware_contract import KIND, IDENTITY, VERIFY, RESTORE, TARGETS, HARDWARE, validate_contract
from protocol.resource_pool import decoder, validate_pool

ENV = dict(architecture='x86_64', os_id='steamos', os_version='3.10', kernels=['fixture-kernel'])
FACTS = {name: values[0] for name, values in HARDWARE.items()}


def synthetic_pool(root):
    root.mkdir(); toon = decoder(); files = []
    def write(name, data):
        path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        return dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
    payload, targets = [], []
    for index, (target, missing) in enumerate(TARGETS.items()):
        entry = write('firmware/'+str(index), b'firmware-'+str(index).encode()); payload.append(entry)
        targets.append(dict(source=entry['path'], target=target, missing_only=missing, mode=420, scope='root-slots'))
    write('license', b'fixture only')
    resource = dict(schema_version=1, kind='BoilerResource', id=IDENTITY, version='1.0.0', dependencies=[], hardware_requires=HARDWARE,
        compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10'], kernels=['fixture-kernel']),
        payload=dict(files=payload), install=dict(kind=KIND, files=targets), verify=VERIFY, restore=RESTORE, license_files=['license'])
    write('resource.toon', (toon.dumps(resource)+'\n').encode())
    model = dict(schema_version=1, kind='BoilerResourceModel', id='device:fixture', name='Fixture', resources=[dict(resource=IDENTITY)])
    write('model.toon', (toon.dumps(model)+'\n').encode())
    for path in root.rglob('*'):
        if path.is_file(): files.append(dict(path=path.relative_to(root).as_posix(), size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    by_name = {r['path']: r for r in files}
    catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
        resources=[dict(id=IDENTITY, version='1.0.0', path='resource.toon', sha256=by_name['resource.toon']['sha256'])],
        models=[dict(id='device:fixture', path='model.toon', sha256=by_name['model.toon']['sha256'])], files=files)
    write('catalog.toon', (toon.dumps(catalog)+'\n').encode()); write('catalog.json', json.dumps(catalog).encode())
    return root


class FirmwareRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.base = Path(self.temp.name)
        path = Path(os.environ['BOILER_WIFI_RUNTIME_POOL']) if os.environ.get('BOILER_WIFI_RUNTIME_POOL') else synthetic_pool(self.base/'pool')
        self.pool = ResourcePool.for_local_test(path, trust_local_test=True); self.record = self.pool.resource(IDENTITY)
        self.refs = [dict(resource=IDENTITY)]; self.selected = [IDENTITY]; self.slots = {}
        environment = copy.deepcopy(ENV); environment['kernels'] = self.record['compatibility']['kernels']
        for slot in ('A', 'B'):
            root = self.base/slot; root.mkdir()
            self.slots[slot] = dict(root=root, environment=copy.deepcopy(environment), hardware=copy.deepcopy(FACTS))
        self.home = self.base/'home'; self.home.mkdir()
        self.rows = self.record['install']['files']; self.session = ResourceSession(self.pool, self.slots, self.home)

    def put(self, slot, name, data=b'original'):
        path = self.slots[slot]['root']/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); return path

    def install(self): return self.session.install(self.refs, self.selected)
    def verify(self): return maintenance_action(self.pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='verify')

    def test_noop_recovery_keeps_published_receipt_unchanged(self):
        receipt = self.install()
        group = next(self.home.rglob(receipt['id'] + '.json'))
        before = group.read_bytes()
        recovered = self.session.recover()
        self.assertEqual(recovered['resource_actions'][0], receipt)
        self.assertEqual(before, group.read_bytes())
        self.assertTrue(all(row['ok'] for row in self.verify()))

    def test_installer_and_retained_maintenance_share_handler_with_different_slot_presence(self):
        kept = self.put('A', self.rows[0]['target']); compressed = self.put('B', self.rows[1]['target']+'.zst')
        c106 = next(r['target'] for r in self.rows if not r['missing_only'])
        replaced = [self.put(slot, c106, (b'old-'+slot.encode())) for slot in self.slots]
        receipt = installer_install(self.pool, self.refs, self.selected, slots=self.slots, shared_home=self.home)
        self.assertEqual(len(receipt['transactions']), 2); self.assertTrue(all(r['ok'] for r in self.verify()))
        self.assertEqual(kept.read_bytes(), b'original'); self.assertEqual(compressed.read_bytes(), b'original')
        self.assertNotIn(self.rows[0]['target'], self.session.engines['A']._state()['owners'])
        self.assertIn(self.rows[0]['target'], self.session.engines['B']._state()['owners'])
        for slot in self.slots: maintenance_action(self.pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='restore', receipt=receipt, scope=slot)
        self.assertEqual([p.read_bytes() for p in replaced], [b'old-A', b'old-B']); self.assertEqual(kept.read_bytes(), b'original')

    def test_repeat_upgrade_and_two_generations_restore_keep_ownership(self):
        first = self.install(); before = (self.slots['A']['root']/self.rows[0]['target']).read_bytes()
        upgraded = copy.deepcopy(self.pool.catalog); upgraded['resources'][0]['version'] += '+upgrade'
        pool = ResourcePool(self.pool.root, upgraded, local_test=True)
        second = maintenance_action(pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='apply')
        self.assertTrue(all(r['ok'] for r in maintenance_action(pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='verify')))
        for slot in self.slots: self.session.restore(second, slot)
        self.assertEqual((self.slots['A']['root']/self.rows[0]['target']).read_bytes(), before)
        for slot in self.slots: self.session.restore(first, slot)
        self.assertFalse((self.slots['A']['root']/self.rows[0]['target']).exists())

    def test_b_c106_alias_rejects_before_a_or_group_write(self):
        c106 = next(r['target'] for r in self.rows if not r['missing_only']); self.put('B', c106+'.xz')
        with patch.object(self.session.engines['A'], 'apply') as write:
            with self.assertRaisesRegex(MaintenanceError, 'c106 alias'): self.install()
            write.assert_not_called()
        self.assertFalse((self.home/'.boiler/resource-maintenance/resource-actions').exists())
        self.assertFalse((self.slots['A']['root']/self.rows[0]['target']).exists())

    def test_b_missing_foreign_owned_rejects_before_a_or_group_write(self):
        row = self.rows[0]; source = next(p for p in self.record['payload']['files'] if p['path'] == row['source'])
        other = dict(schema_version=1, kind='ComponentManifest', id='resource:foreign', version='1.0.0', compatibility={},
            files=[dict(source=row['source'], target=row['target'], mode=420, size=source['size'], sha256=source['sha256'])])
        engine = self.session.engines['B']; engine.apply(other, self.pool.root); (engine.root/row['target']).unlink(); state = engine._state()
        with patch.object(self.session.engines['A'], 'apply') as write:
            with self.assertRaisesRegex(MaintenanceError, 'Foreign-owned firmware is missing'): self.install()
            write.assert_not_called()
        self.assertEqual(engine._state(), state); self.assertFalse((self.home/'.boiler/resource-maintenance/resource-actions').exists())

    def test_foreign_owned_missing_plain_with_zst_preserves_alias(self):
        self.test_b_missing_foreign_owned_rejects_before_a_or_group_write()
        row = self.rows[0]; alias = self.put('B', row['target']+'.zst', b'foreign-zst')
        receipt = self.install(); self.assertTrue(all(r['ok'] for r in self.verify()))
        self.session.restore(receipt, 'B'); self.session.restore(receipt, 'A'); self.assertEqual(alias.read_bytes(), b'foreign-zst')

    def test_modified_managed_missing_only_refuses_all_slots_preflight(self):
        self.install(); self.put('B', self.rows[0]['target'], b'external-change')
        self.assertFalse(all(r['ok'] for r in self.verify()))
        with patch.object(self.session.engines['A'], 'apply') as write:
            with self.assertRaises(MaintenanceError): self.install()
            write.assert_not_called()

    def test_system_preserved_update_survives_restore_and_missing_is_detected(self):
        path = self.put('A', self.rows[0]['target']+'.xz'); first = self.install(); path.write_bytes(b'OS-update')
        self.assertTrue(all(r['ok'] for r in self.verify())); path.unlink(); self.assertFalse(all(r['ok'] for r in self.verify()))
        second = self.install(); self.assertTrue(all(r['ok'] for r in self.verify()))
        for slot in self.slots: self.session.restore(second, slot)
        for slot in self.slots: self.session.restore(first, slot)
        self.assertFalse((self.slots['A']['root']/self.rows[0]['target']).exists())

    def test_wrong_hardware_or_kernel_blocks_both_slots(self):
        for changed in ('hardware', 'environment'):
            with self.subTest(changed=changed):
                original = self.session.slots['B'][changed]
                self.session.slots['B'][changed] = {} if changed == 'hardware' else dict(ENV, kernels=['wrong'])
                with self.assertRaises(MaintenanceError): self.install()
                self.session.slots['B'][changed] = original
        self.assertFalse((self.slots['A']['root']/self.rows[0]['target']).exists())

    def test_policy_cannot_flip_missing_only_or_expand_targets(self):
        for change in ('policy', 'target', 'hardware', 'compatibility', 'kind'):
            resource = copy.deepcopy(self.record)
            if change == 'policy': resource['install']['files'][0]['missing_only'] = False
            elif change == 'target': resource['install']['files'][0]['target'] = 'etc/other'
            elif change == 'hardware': resource['hardware_requires'] = {}
            elif change == 'compatibility': resource['compatibility'] = {}
            else: resource['install']['kind'] = 'pool-supplied-handler'
            with self.assertRaises(ValueError): validate_contract(resource)

    def test_verify_is_readonly_and_rejects_policy_journal_change(self):
        self.install(); engine = self.session.engines['A']; installed = engine._state()['components'][IDENTITY]
        before = {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.assertTrue(all(r['ok'] for r in self.verify())); self.assertEqual(before, {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()})
        path = engine._journal(installed['transaction']); journal = json.loads(path.read_bytes()); journal['component']['firmware_policy_sha256'] = '0'*64; _json_write(path, journal)
        self.assertFalse(all(r['ok'] for r in self.verify()))

    def test_new_modules_are_in_retained_runtime_whitelist(self):
        self.assertIn('maintenance/firmware_resources.py', RUNTIME_FILES); self.assertIn('protocol/firmware_contract.py', RUNTIME_FILES)

    def other_pool(self):
        source = self.record['payload']['files'][0]
        resource = dict(id='resource:not-selected', version='1.0.0', dependencies=[], payload=dict(files=[source]),
            install=dict(kind='files-v1', files=[dict(source=source['path'], target='etc/not-selected', mode=420, scope='root-slots')]),
            verify=dict(kind='sha256-v1'), restore=dict(kind='transaction-backup-v1'))
        catalog = copy.deepcopy(self.pool.catalog); catalog['resources'].append(resource)
        return ResourcePool(self.pool.root, catalog, local_test=True), resource

    def test_restore_raw_non_resource_receipt_cannot_restore_unselected_component(self):
        pool, resource = self.other_pool(); engine = self.session.engines['A']; entry = resource['payload']['files'][0]
        raw = engine.apply(dict(schema_version=1, kind='ComponentManifest', id=resource['id'], version='1.0.0',
            files=[dict(source=entry['path'], target='etc/not-selected', size=entry['size'], sha256=entry['sha256'], mode=420)]), pool.root)
        supplied = dict(kind='ResourceInstallReceipt', transactions=[dict(raw, scope='A')])
        with self.assertRaisesRegex(ResourceError, 'retained resource group'):
            maintenance_action(pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='restore', receipt=supplied, scope='A')
        self.assertTrue((engine.root/'etc/not-selected').is_file()); self.assertIn(resource['id'], engine._state()['components'])

    def test_restore_different_durable_group_selection_rejected_without_mutation(self):
        pool, resource = self.other_pool(); other = ResourceSession(pool, self.slots, self.home)
        receipt = other.install([dict(resource=resource['id'])], [resource['id']])
        before = {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ResourceError, 'selection differs'):
            maintenance_action(pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='restore', receipt=receipt, scope='A')
        self.assertEqual(before, {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_supplied_rows_do_not_extend_a_valid_durable_wifi_group(self):
        receipt = self.install(); pool, resource = self.other_pool(); other = ResourceSession(pool, self.slots, self.home)
        other_receipt = other.install([dict(resource=resource['id'])], [resource['id']])
        forged = dict(receipt, transactions=other_receipt['transactions'], resources=[resource['id']])
        maintenance_action(pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='restore', receipt=forged, scope='A')
        self.assertNotIn(IDENTITY, other.engines['A']._state()['components'])
        self.assertIn(resource['id'], other.engines['A']._state()['components']); self.assertTrue((self.slots['A']['root']/'etc/not-selected').exists())

    def test_b_interrupted_write_rolls_back_and_partial_a_receipt_restores(self):
        c106 = next(r['target'] for r in self.rows if not r['missing_only']); original = self.put('B', c106, b'B-original')
        engine = self.session.engines['B']; put = engine._put_snapshot; calls = 0
        def fail_once(target, snapshot, **kwargs):
            nonlocal calls
            calls += 1; put(target, snapshot, **kwargs)
            if calls == 2: raise OSError('injected interrupted firmware write')
        with patch.object(engine, '_put_snapshot', side_effect=fail_once):
            with self.assertRaises(PartialInstall) as caught: self.install()
        self.assertEqual([r['scope'] for r in caught.exception.completed], ['A']); self.assertEqual(original.read_bytes(), b'B-original')
        restarted = ResourceSession(self.pool, self.slots, self.home); restarted.recover(); restarted.restore(caught.exception.receipt, 'A')
        self.assertFalse((self.slots['A']['root']/self.rows[0]['target']).exists())
        self.assertFalse((self.slots['B']['root']/self.rows[0]['target']).exists())

    def test_pending_transaction_is_recovered_before_missing_only_decision(self):
        self.install(); engine = self.session.engines['A']; state = engine._state(); transaction = state['components'][IDENTITY]['transaction']
        path = engine._journal(transaction); journal = json.loads(path.read_bytes())
        # Retain actual Engine backups/snapshots; model a kill after file writes
        # and before state commit by rewinding only the durable markers.
        _json_write(engine.state_dir/'state.json', journal['before_state']); journal['status'] = 'applying'; _json_write(path, journal)
        restarted = ResourceSession(self.pool, self.slots, self.home); receipt = restarted.install(self.refs, self.selected)
        self.assertTrue(all(r['ok'] for r in maintenance_action(self.pool, self.refs, self.selected, slots=self.slots, shared_home=self.home, action='verify')))
        current = json.loads(engine._journal(engine._state()['components'][IDENTITY]['transaction']).read_bytes())
        self.assertEqual(current['component']['firmware_preserved'], [])
        self.assertEqual(len(current['operations']), 9)

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux leaf-link semantics required')
    def test_plain_compressed_and_dangling_leaf_links_are_preserved(self):
        for slot, index, suffix in (('A', 0, ''), ('B', 1, '.zst')):
            path = self.slots[slot]['root']/(self.rows[index]['target']+suffix); path.parent.mkdir(parents=True, exist_ok=True); path.symlink_to('original-missing')
        receipt = self.install(); self.assertTrue(all(r['ok'] for r in self.verify()))
        for slot in self.slots: self.session.restore(receipt, slot)
        self.assertTrue((self.slots['A']['root']/self.rows[0]['target']).is_symlink())

    @unittest.skipUnless(os.name == 'posix', 'Actual Linux ancestor-link semantics required')
    def test_ancestor_link_refused_before_other_slot_write(self):
        root = self.slots['B']['root']; (root/'usr/lib').mkdir(parents=True); outside = self.base/'outside'; outside.mkdir(); (root/'usr/lib/firmware').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(list(outside.iterdir()), []); self.assertFalse((self.slots['A']['root']/self.rows[0]['target']).exists())


if __name__ == '__main__': unittest.main()
