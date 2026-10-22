"""Private binary receipt migration, bounded file IO and durable retry barriers."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from maintenance import Engine, MaintenanceError
from maintenance import adoption_blobs as implementation
from protocol import ProtocolError
import test_adoption_bridge as intake


class AdoptionBlobTests(unittest.TestCase):
    def setUp(self):
        intake.AdoptionReceiptIntakeTests.setUp(self)
        self.value = json.loads(self.raw)
        self.value.update(schema_version=2, legacy_slot='partuuid-12345678-abcd',
                          source_format='oxp3-maintenance-format-1', source_release={'version':'1.0.6'},
                          migration_complete=False, blob_directory=self.receipt.name+'.blobs', blobs=[])
        self.bundle = self.work/self.value['blob_directory']
        self.bundle.mkdir(mode=0o700)
        for component in self.value['components']:
            for operation in component['operations']:
                for phase in ('before', 'after'):
                    snapshot = operation[phase]
                    data = base64.b64decode(snapshot.pop('data'))
                    snapshot.update(blob=snapshot['sha256'], size=len(data))
                    self.blob(data)
        store = self.root/'home/.oxp3-maintenance'
        store.mkdir(parents=True, mode=0o700)
        (store/'lock').write_bytes(b'')
        (store/'lock').chmod(0o600)
        (store/'index.json').write_bytes(b'{"format":1}')
        (store/'index.json').chmod(0o600)
        self.value['evidence'].append(dict(path='home/.oxp3-maintenance/index.json', role='maintenance-index',
                                         size=12, sha256=hashlib.sha256(b'{"format":1}').hexdigest()))
        self.save()

    def blob(self, data):
        digest = hashlib.sha256(data).hexdigest()
        if not (self.bundle/digest).exists():
            (self.bundle/digest).write_bytes(data)
            (self.bundle/digest).chmod(0o600)
            self.value['blobs'].append(dict(sha256=digest, size=len(data)))
        return digest

    def snapshot(self, data, mode=0o644):
        digest = self.blob(data)
        info = (self.root/'etc/model.conf').stat()
        return dict(exists=True, blob=digest, sha256=digest, size=len(data), mode=mode, uid=info.st_uid, gid=info.st_gid)

    def save(self):
        self.raw = json.dumps(self.value, sort_keys=True).encode()
        self.receipt.write_bytes(self.raw)
        self.receipt.chmod(0o600)

    def assert_unowned(self):
        self.assertEqual(self.engine._state()['components'], {})
        self.assertFalse((self.engine.state_dir/implementation.PENDING).exists())
        self.assertEqual(list(self.engine.journals.glob('*.json')), [])

    def second_component(self):
        target = self.root/'etc/second.conf'
        target.write_bytes(b'second applied\n')
        target.chmod(0o644)
        self.value['components'].append(dict(id='oxp3:gyro', version='1.0.6', operations=[dict(
            target='etc/second.conf', before={'exists':False}, after=self.snapshot(target.read_bytes()))]))
        self.save()

    def upgrade(self, data=b'upgraded configuration\n'):
        payload = self.work/'upgrade'
        payload.mkdir(exist_ok=True)
        source = payload/'new'
        source.write_bytes(data)
        source.chmod(0o644)
        component = dict(schema_version=1,kind='ComponentManifest',id='oxp3:input',version='1.0.7',files=[dict(
            source='new',target='etc/model.conf',size=len(data),sha256=hashlib.sha256(data).hexdigest(),mode=0o644)])
        return component,payload

    def retained_fixture(self):
        name = 'usr/local/lib/legacy/component-receipts/partuuid-12345678-abcd/input.json'
        path = self.root/name
        path.parent.mkdir(parents=True)
        data = b'{"legacy_revision":"historical-content-digest"}\n'
        path.write_bytes(data)
        path.chmod(0o600)
        self.value['components'][0]['operations'].extend([
            dict(target=name,before={'exists':False},after=self.snapshot(data,0o600)),
            dict(target='etc/old-absent',before={'exists':False},after={'exists':False})])
        self.save()
        return name,path

    def test_binary_adopt_retry_verify_and_restore_without_source_bundle(self):
        original_index = (self.root/'home/.oxp3-maintenance/index.json').read_bytes()
        result = self.engine.adopt(self.receipt)
        self.assertTrue(result['migration_complete'])
        self.assertEqual(self.engine.adopt(self.receipt)['transactions'], result['transactions'])
        journal_path = self.engine._journal(result['transactions'][0])
        journal = json.loads(journal_path.read_bytes())
        self.assertEqual(journal['adoption_receipt'], hashlib.sha256(self.raw).hexdigest())
        self.assertTrue(all(snapshot.get('storage') == 'blob-v1' and 'data' not in snapshot
                            for operation in journal['operations'] for snapshot in (operation['before'],operation['after'])))
        shutil.rmtree(self.bundle)
        self.receipt.unlink()
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.engine.restore(result['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(), b'original configuration\n')
        self.assertEqual((self.root/'home/.oxp3-maintenance/index.json').read_bytes(), original_index)
        self.assertLess(journal_path.stat().st_size, 16384)

    def test_adopted_tiny_upgrade_preserves_both_original_generations(self):
        adopted = self.engine.adopt(self.receipt)
        original = self.value['components'][0]['operations'][0]['before']
        component,payload = self.upgrade()
        result = self.engine.apply(component,payload)
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'upgraded configuration\n')
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        journal = json.loads(self.engine._journal(result['transaction']).read_bytes())
        self.assertTrue(journal['legacy_lock_required'])
        self.assertEqual(journal['operations'][0]['before']['storage'],'blob-v1')
        self.assertNotIn('data',journal['operations'][0]['after'])
        shutil.rmtree(self.bundle)
        self.engine.restore(result['transaction'])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'applied configuration\n')
        self.assertEqual(self.engine._state()['components']['oxp3:input']['transaction'],adopted['transactions'][0])
        self.engine.restore(adopted['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'original configuration\n')
        self.assertTrue((implementation.blob_cache(self.engine)/original['sha256']).is_file())

    def test_binary_adoption_requires_old_lock_even_for_early_journals_without_flag(self):
        adopted=self.engine.adopt(self.receipt)
        path=self.engine._journal(adopted['transactions'][0])
        journal=json.loads(path.read_bytes())
        self.assertTrue(journal.pop('legacy_lock_required'))
        path.write_text(json.dumps(journal))
        (self.root/'home/.oxp3-maintenance/lock').unlink()
        state=self.engine._state();target=self.root/'etc/model.conf';contents=target.read_bytes()
        component,payload=self.upgrade()
        for action in (lambda:self.engine.restore(adopted['transactions'][0]),lambda:self.engine.apply(component,payload)):
            with self.assertRaises((MaintenanceError,OSError)):
                action()
            self.assertEqual(self.engine._state(),state)
            self.assertEqual(target.read_bytes(),contents)
        journal['status']='restoring';path.write_text(json.dumps(journal))
        with self.assertRaises((MaintenanceError,OSError)):
            self.engine.recover()
        self.assertEqual(target.read_bytes(),contents)
        self.assertFalse((self.root/'home/.oxp3-maintenance/lock').exists())

    def test_retained_receipt_and_absence_are_reviewable_without_rewrite(self):
        name,path = self.retained_fixture()
        adopted = self.engine.adopt(self.receipt)
        component,payload = self.upgrade()
        component['retain_owned_paths'] = True
        before = path.stat()
        contents = path.read_bytes()
        retained = self.engine.retained_owned(component)
        self.assertEqual([item['target'] for item in retained],['etc/old-absent',name])
        self.assertEqual(retained[0]['snapshot'],{'exists':False})
        self.assertEqual(retained[1]['transaction'],adopted['transactions'][0])
        self.assertEqual(set(retained[1]['snapshot']),{'exists','size','sha256','mode','uid','gid'})
        result = self.engine.apply(component,payload)
        self.assertEqual(path.read_bytes(),contents)
        self.assertEqual((path.stat().st_ino,path.stat().st_mtime_ns),(before.st_ino,before.st_mtime_ns))
        self.assertFalse((self.root/'etc/old-absent').exists())
        journal = json.loads(self.engine._journal(result['transaction']).read_bytes())
        self.assertEqual(len([item for item in journal['operations'] if item.get('retained')]),2)
        for item in journal['operations']:
            if item.get('retained'):
                self.assertEqual(item['before'],item['after'])
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.engine.restore(result['transaction'])
        self.assertEqual((path.stat().st_ino,path.stat().st_mtime_ns),(before.st_ino,before.st_mtime_ns))
        self.assertEqual(self.engine._state()['components']['oxp3:input']['transaction'],adopted['transactions'][0])

    def test_retention_refuses_new_install_hooks_nonboolean_and_changed_owned_paths(self):
        name,path = self.retained_fixture()
        component,payload = self.upgrade()
        component['retain_owned_paths'] = True
        with self.assertRaisesRegex(MaintenanceError,'existing component'):
            self.engine.apply(component,payload)
        self.engine.adopt(self.receipt)
        for value in (1,'true',None):
            invalid=copy.deepcopy(component);invalid['retain_owned_paths']=value
            with self.assertRaises((MaintenanceError,ProtocolError)): self.engine.apply(invalid,payload)
        invalid=copy.deepcopy(component);invalid['hooks']={'apply':'scripts/apply.sh'}
        with self.assertRaisesRegex(MaintenanceError,'static file'):
            self.engine.retained_owned(invalid)
        path.write_bytes(b'edited historical receipt')
        with self.assertRaisesRegex(MaintenanceError,'has changed'):
            self.engine.retained_owned(component)
        with self.assertRaisesRegex(MaintenanceError,'has changed'):
            self.engine.apply(component,payload)
        self.assertEqual(path.read_bytes(),b'edited historical receipt')

    def test_retention_requires_exact_current_owner_transaction_and_journal_coverage(self):
        name,path = self.retained_fixture()
        self.engine.adopt(self.receipt)
        component,payload = self.upgrade();component['retain_owned_paths']=True
        state = self.engine._state()
        for mutation in ('transaction','coverage','alias','prefix'):
            changed = copy.deepcopy(state)
            candidate = copy.deepcopy(component)
            if mutation == 'transaction':
                changed['owners'][name]['transaction']='b'*32
            elif mutation == 'coverage':
                del changed['owners'][name]
            elif mutation == 'alias':
                candidate['files'][0]['target']=name.upper()
            else:
                candidate['files'][0]['target']=name+'/child'
            with self.subTest(mutation=mutation), self.assertRaises(MaintenanceError):
                self.engine.retained_owned(candidate,changed)

    def test_retention_cannot_forget_old_script_lifecycle_or_declare_no_files(self):
        self.retained_fixture();adopted=self.engine.adopt(self.receipt)
        component,payload=self.upgrade();component['retain_owned_paths']=True
        invalid=copy.deepcopy(component);invalid['files']=[]
        with self.assertRaisesRegex(MaintenanceError,'requires declared files'):
            self.engine.retained_owned(invalid)
        path=self.engine._journal(adopted['transactions'][0])
        prior=json.loads(path.read_bytes())
        prior['component']['hooks']={'apply':'scripts/old-lifecycle.sh'}
        path.write_text(json.dumps(prior))
        with self.assertRaisesRegex(MaintenanceError,'scripted component lifecycle'):
            self.engine.retained_owned(component)

    def test_retained_paths_count_toward_binary_snapshot_quota(self):
        self.retained_fixture();self.engine.adopt(self.receipt)
        component,payload = self.upgrade();component['retain_owned_paths']=True
        state=self.engine._state()
        with mock.patch.object(implementation,'MAX_TOTAL',80):
            with self.assertRaisesRegex(MaintenanceError,'backup limit'):
                self.engine.apply(component,payload)
        self.assertEqual(self.engine._state(),state)

    def test_interrupted_retaining_update_does_not_rewrite_historical_receipt(self):
        name,path=self.retained_fixture();adopted=self.engine.adopt(self.receipt)
        component,payload=self.upgrade();component['retain_owned_paths']=True
        before=(path.stat().st_ino,path.stat().st_mtime_ns)
        original=self.engine._put_snapshot
        def dying(target,snapshot):
            original(target,snapshot)
            raise RuntimeError('interrupted static update')
        with mock.patch.object(self.engine,'_put_snapshot',side_effect=dying), mock.patch.object(self.engine,'_recover',return_value=[]):
            with self.assertRaises(RuntimeError): self.engine.apply(component,payload)
        Engine(self.root).recover()
        self.assertEqual((path.stat().st_ino,path.stat().st_mtime_ns),before)
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'applied configuration\n')
        self.assertEqual(self.engine._state()['components']['oxp3:input']['transaction'],adopted['transactions'][0])

    def test_interrupted_adopted_update_recovers_to_adopted_bytes_and_ownership(self):
        adopted = self.engine.adopt(self.receipt)
        component,payload = self.upgrade()
        original = self.engine._put_snapshot
        def dying(path,snapshot):
            original(path,snapshot)
            raise RuntimeError('simulated process death after streamed write')
        with mock.patch.object(self.engine,'_put_snapshot',side_effect=dying), mock.patch.object(self.engine,'_recover',return_value=[]):
            with self.assertRaisesRegex(RuntimeError,'process death'):
                self.engine.apply(component,payload)
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'upgraded configuration\n')
        recovered = Engine(self.root).recover()
        self.assertEqual(len(recovered),1)
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'applied configuration\n')
        self.assertEqual(self.engine._state()['components']['oxp3:input']['transaction'],adopted['transactions'][0])
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])

    def test_binary_update_limits_and_changed_target_fail_before_new_ownership(self):
        self.engine.adopt(self.receipt)
        component,payload = self.upgrade()
        before_state = self.engine._state()
        count = len(list(self.engine.journals.glob('*.json')))
        for limit in ('MAX_FILE','MAX_TOTAL'):
            with self.subTest(limit=limit), mock.patch.object(implementation,limit,4):
                with self.assertRaisesRegex(MaintenanceError,'limit'):
                    self.engine.apply(component,payload)
            self.assertEqual(self.engine._state(),before_state)
            self.assertEqual(len(list(self.engine.journals.glob('*.json'))),count)
            self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'applied configuration\n')
        (self.root/'etc/model.conf').write_bytes(b'user change preserved')
        with self.assertRaisesRegex(MaintenanceError,'modified managed file'):
            self.engine.apply(component,payload)
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'user change preserved')
        self.assertEqual(self.engine._state(),before_state)

    def test_target_changed_during_preparation_is_preserved_by_failure_barrier(self):
        self.engine.adopt(self.receipt)
        component,payload = self.upgrade()
        old_payload = implementation.payload_snapshot
        def changed(*args,**kwargs):
            result = old_payload(*args,**kwargs)
            (self.root/'etc/model.conf').write_bytes(b'concurrent user edit')
            return result
        state = self.engine._state()
        with mock.patch.object(implementation,'payload_snapshot',side_effect=changed):
            with self.assertRaises(MaintenanceError):
                self.engine.apply(component,payload)
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'concurrent user edit')
        self.assertEqual(self.engine._state(),state)
        with self.assertRaisesRegex(MaintenanceError,'Recovery refused modified target'):
            self.engine.recover()

    def test_independent_slot_states_share_only_verified_content_objects(self):
        first = self.engine.adopt(self.receipt)
        cache = implementation.blob_cache(self.engine)
        self.assertEqual(cache,(self.root/'home/.boiler/maintenance-blobs').resolve())
        inodes = {path.name:path.stat().st_ino for path in cache.iterdir()}
        other = Engine(self.root,self.root/'var/lib/boiler/second-slot-maintenance')
        self.value.update(slot='A',legacy_slot='partuuid-abcdef01-2345')
        self.save()
        second = other.adopt(self.receipt)
        self.assertTrue(first['migration_complete'] and second['migration_complete'])
        self.assertEqual(implementation.blob_cache(other),cache)
        self.assertEqual({path.name:path.stat().st_ino for path in cache.iterdir()},inodes)
        self.assertNotEqual(first['transactions'],second['transactions'])
        shutil.rmtree(self.bundle)
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.assertTrue(other.verify('oxp3:input')['ok'])
        other.restore(second['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'original configuration\n')
        self.assertEqual({path.name:path.stat().st_ino for path in cache.iterdir()},inodes)

    def test_all_inputs_and_live_targets_are_preflighted_before_ownership(self):
        self.second_component()
        (self.root/'etc/second.conf').write_bytes(b'changed after export')
        with self.assertRaisesRegex(MaintenanceError, 'target changed'):
            self.engine.adopt(self.receipt)
        self.assert_unowned()

    def test_existing_owner_case_alias_is_refused_before_migration_intent(self):
        state = self.engine._state()
        state['owners']['etc/model.conf'] = dict(component='core:other',transaction='a'*32,sha256=None)
        state['components']['core:other'] = dict(version='1.0.0',transaction='a'*32,requires=[],conflicts=[])
        implementation._json_write(self.engine.state_dir/'state.json',state)
        self.value['components'][0]['operations'][0]['target'] = 'etc/MODEL.conf'
        alias = self.root/'etc/MODEL.conf'
        if not alias.exists():
            alias.write_bytes(b'applied configuration\n')
            alias.chmod(0o644)
        self.save()
        with self.assertRaisesRegex(MaintenanceError,'overlaps existing ownership'):
            self.engine.adopt(self.receipt)
        self.assertEqual(self.engine._state(),state)
        self.assertFalse((self.engine.state_dir/implementation.PENDING).exists())
        self.assertEqual(list(self.engine.journals.glob('*.json')),[])

    def test_components_cannot_own_shared_backup_store_or_its_ancestors(self):
        payload = self.work/'payload'
        payload.mkdir()
        (payload/'source').write_bytes(b'ok')
        for target in ('home/.boiler/maintenance-blobs/'+('a'*64),'home/.boiler/maintenance-blobs','home/.boiler','home'):
            component = dict(schema_version=1,kind='ComponentManifest',id='core:other',version='1.0.0',files=[dict(
                source='source',target=target,size=2,sha256=hashlib.sha256(b'ok').hexdigest())])
            with self.subTest(target=target), self.assertRaisesRegex(MaintenanceError,'cannot own maintenance backups'):
                self.engine.apply(component,payload)
        self.assertEqual(self.engine._state()['components'],{})
        self.assertEqual(self.engine._target('home/.boiler/decky/config'),self.engine.root/'home/.boiler/decky/config')

    def test_inventory_and_blob_integrity_fail_closed(self):
        original = copy.deepcopy(self.value)
        for mutation in ('extra', 'missing', 'hash', 'size', 'unreferenced'):
            with self.subTest(mutation=mutation):
                self.value = copy.deepcopy(original)
                before = self.value['components'][0]['operations'][0]['before']
                path = self.bundle/before['blob']
                data = path.read_bytes()
                if mutation == 'extra':
                    extra = self.bundle/'unexpected'
                    extra.write_bytes(b'no')
                elif mutation == 'missing':
                    path.unlink()
                elif mutation == 'hash':
                    path.write_bytes(b'x'*len(data))
                elif mutation == 'size':
                    before['size'] += 1
                else:
                    self.blob(b'unreferenced')
                self.save()
                with self.assertRaises(MaintenanceError):
                    self.engine.adopt(self.receipt)
                self.assert_unowned()
                for entry in self.bundle.iterdir():
                    if entry.name not in {item['sha256'] for item in original['blobs']}:
                        entry.unlink()
                path.write_bytes(data)
                path.chmod(0o600)

    def test_failed_batch_requires_same_receipt_and_resumes_without_duplicate_generation(self):
        self.second_component()
        original_write = implementation._json_write
        failed = []
        def writing(path, value):
            original_write(path, value)
            if (Path(path).name == implementation.PENDING and value.get('status') == 'applying' and
                    len(value.get('transactions',[])) == 1 and not failed):
                failed.append(True)
                raise OSError('simulated power loss after first component commit')
        with mock.patch.object(implementation, '_json_write', side_effect=writing):
            with self.assertRaisesRegex(OSError, 'power loss'):
                self.engine.adopt(self.receipt)
        first = self.engine._state()['components']['oxp3:input']['transaction']
        self.assertEqual(len(self.engine._state()['components']), 1)
        pending = self.engine.state_dir/implementation.PENDING
        self.assertFalse(json.loads(pending.read_bytes())['migration_complete'])
        for action in (self.engine.recover, lambda:self.engine.verify('oxp3:input'), lambda:self.engine.restore(first)):
            with self.assertRaisesRegex(MaintenanceError, 'explicit receipt resume'):
                action()
        old_raw = self.receipt.read_bytes()
        self.value['source_release']['changed'] = True
        self.save()
        with self.assertRaisesRegex(MaintenanceError, 'existing adoption receipt'):
            self.engine.adopt(self.receipt)
        self.assertEqual(len(self.engine._state()['components']), 1)
        self.receipt.write_bytes(old_raw)
        result = self.engine.adopt(self.receipt)
        self.assertTrue(result['migration_complete'])
        self.assertEqual(result['transactions'][0], first)
        self.assertEqual(len(list(self.engine.journals.glob('*.json'))), 2)
        self.assertFalse(pending.exists())

    def test_interrupted_ownership_commit_recovers_only_metadata_then_resumes(self):
        original_write = implementation._json_write
        failed = []
        def writing(path, value):
            if value.get('status') == 'committed' and value.get('adoption_receipt') and not failed:
                failed.append(True)
                raise OSError('simulated power loss before journal commit')
            original_write(path,value)
        with mock.patch.object(implementation,'_json_write',side_effect=writing):
            with self.assertRaisesRegex(OSError,'power loss'):
                self.engine.adopt(self.receipt)
        self.assertEqual(len(self.engine._state()['components']),1)
        live = (self.root/'etc/model.conf').read_bytes()
        result = Engine(self.root).adopt(self.receipt)
        self.assertTrue(result['migration_complete'])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),live)
        journals = [json.loads(path.read_bytes()) for path in self.engine.journals.glob('*.json')]
        self.assertEqual(sorted(value['status'] for value in journals),['committed','rolled_back'])

    def test_source_changed_between_validation_and_cache_copy_is_refused(self):
        original_hash = implementation._hash_file
        changed = []
        def hashing(path,*args,**kwargs):
            result = original_hash(path,*args,**kwargs)
            if Path(path).parent.resolve() == self.bundle.resolve() and not changed:
                changed.append(True)
                Path(path).write_bytes(b'x'*Path(path).stat().st_size)
            return result
        with mock.patch.object(implementation,'_hash_file',side_effect=hashing):
            with self.assertRaisesRegex(MaintenanceError,'digest changed'):
                self.engine.adopt(self.receipt)
        self.assertTrue(changed)
        self.assert_unowned()

    def test_damaged_private_cache_refuses_restore_without_writing_target(self):
        result = self.engine.adopt(self.receipt)
        before = self.value['components'][0]['operations'][0]['before']
        cache = implementation.blob_cache(self.engine)/before['sha256']
        cache.write_bytes(b'x'*before['size'])
        target = self.root/'etc/model.conf'
        live = target.read_bytes()
        with self.assertRaisesRegex(MaintenanceError,'digest changed'):
            self.engine.restore(result['transactions'][0])
        self.assertEqual(target.read_bytes(),live)
        self.assertEqual(self.engine.recover(),[])
        with self.assertRaisesRegex(MaintenanceError,'digest changed'):
            self.engine.restore(result['transactions'][0])
        self.assertEqual(target.read_bytes(),live)

    def test_restore_and_recovery_validate_all_original_blobs_before_any_write(self):
        target = self.root/'etc/second.conf'
        target.write_bytes(b'second applied\n')
        target.chmod(0o644)
        self.value['components'][0]['operations'].append(dict(target='etc/second.conf',
            before=self.snapshot(b'second original\n'),after=self.snapshot(target.read_bytes())))
        self.save()
        result = self.engine.adopt(self.receipt)
        first = self.value['components'][0]['operations'][0]['before']
        cache = implementation.blob_cache(self.engine)/first['sha256']
        cache.write_bytes(b'x'*first['size'])
        with self.assertRaisesRegex(MaintenanceError,'digest changed'):
            self.engine.restore(result['transactions'][0])
        self.assertEqual(target.read_bytes(),b'second applied\n')
        journal_path = self.engine._journal(result['transactions'][0])
        journal = json.loads(journal_path.read_bytes())
        self.assertEqual(journal['status'],'committed')
        journal['status'] = 'restoring'
        journal_path.write_text(json.dumps(journal))
        with self.assertRaisesRegex(MaintenanceError,'digest changed'):
            self.engine.recover()
        self.assertEqual(target.read_bytes(),b'second applied\n')

    def test_explicit_absence_survives_verify_and_streamed_restore(self):
        operation = self.value['components'][0]['operations'][0]
        old_after = operation['after']['blob']
        operation['after'] = {'exists':False}
        (self.root/'etc/model.conf').unlink()
        (self.bundle/old_after).unlink()
        self.value['blobs'] = [item for item in self.value['blobs'] if item['sha256'] != old_after]
        self.save()
        result = self.engine.adopt(self.receipt)
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.engine.restore(result['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(), b'original configuration\n')

    def test_missing_user_parent_is_refused_before_any_restore_write(self):
        (self.root/'etc/passwd').write_bytes(b'root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/sh\n')
        (self.root/'etc/passwd').chmod(0o644)
        operation = self.value['components'][0]['operations'][0]
        operation['target'] = 'home/deck/missing/config'
        applied = operation['after']['blob']
        operation['after'] = {'exists':False}
        (self.bundle/applied).unlink()
        self.value['blobs'] = [item for item in self.value['blobs'] if item['sha256'] != applied]
        self.save()
        result = self.engine.adopt(self.receipt)
        with self.assertRaisesRegex(MaintenanceError,'existing protected user directory'):
            self.engine.restore(result['transactions'][0])
        self.assertFalse((self.root/'home/deck').exists())
        journal = json.loads(self.engine._journal(result['transactions'][0]).read_bytes())
        self.assertEqual(journal['status'],'committed')

    def test_absent_original_is_removed_and_changed_current_file_refuses_restore(self):
        operation = self.value['components'][0]['operations'][0]
        before = operation['before']['blob']
        operation['before'] = {'exists':False}
        (self.bundle/before).unlink()
        self.value['blobs'] = [item for item in self.value['blobs'] if item['sha256'] != before]
        self.save()
        result = self.engine.adopt(self.receipt)
        target = self.root/'etc/model.conf'
        target.write_bytes(b'modified by another owner')
        with self.assertRaisesRegex(MaintenanceError, 'modified target'):
            self.engine.restore(result['transactions'][0])
        target.write_bytes(b'applied configuration\n')
        self.engine.restore(result['transactions'][0])
        self.assertFalse(target.exists())

    def test_legacy_changes_or_new_lifecycle_refuse_stale_receipt(self):
        index = self.root/'home/.oxp3-maintenance/index.json'
        original = index.read_bytes()
        index.write_bytes(b'{"format":2}')
        with self.assertRaisesRegex(MaintenanceError, 'digest changed'):
            self.engine.adopt(self.receipt)
        self.assert_unowned()
        index.write_bytes(original)
        lifecycle = index.parent/'live-operation.json'
        lifecycle.write_bytes(b'{"phase":"applying"}')
        lifecycle.chmod(0o600)
        with self.assertRaisesRegex(MaintenanceError, 'lifecycle appeared'):
            self.engine.adopt(self.receipt)
        self.assert_unowned()

    @unittest.skipIf(os.name == 'nt', 'Native no-follow, nonblocking and private ancestry checks')
    def test_symlink_fifo_and_writable_blob_ancestor_are_refused(self):
        name = self.value['blobs'][0]['sha256']
        path = self.bundle/name
        data = path.read_bytes()
        path.unlink()
        path.symlink_to(self.root/'etc/model.conf')
        with self.assertRaises((MaintenanceError,OSError,ProtocolError)):
            self.engine.adopt(self.receipt)
        path.unlink()
        os.mkfifo(path,0o600)
        with self.assertRaises(MaintenanceError):
            self.engine.adopt(self.receipt)
        path.unlink()
        path.write_bytes(data)
        path.chmod(0o600)
        self.work.chmod(0o777)
        with self.assertRaises(MaintenanceError):
            self.engine.adopt(self.receipt)
        self.work.chmod(0o700)
        self.assert_unowned()

    @unittest.skipIf(os.name == 'nt', 'Native flock lock ordering test')
    def test_old_store_is_locked_during_preflight_and_ownership_commit(self):
        import fcntl
        old_hash = implementation._hash_file
        old_write = implementation._json_write
        checks = []
        def locked():
            with (self.root/'home/.oxp3-maintenance/lock').open('rb') as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            checks.append(True)
        def checking(*args, **kwargs):
            locked()
            return old_hash(*args, **kwargs)
        def writing(*args,**kwargs):
            locked()
            return old_write(*args,**kwargs)
        with mock.patch.object(implementation,'_hash_file',side_effect=checking), mock.patch.object(implementation,'_json_write',side_effect=writing):
            self.engine.adopt(self.receipt)
        self.assertGreater(len(checks),2)

    @unittest.skipIf(os.name == 'nt', 'Native protected state ancestry')
    def test_all_engine_mutations_refuse_state_below_writable_ancestor(self):
        unsafe = self.work/'unsafe'
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        with self.assertRaisesRegex(MaintenanceError,'Writable adoption directory'):
            Engine(self.root,unsafe/'protected-state')
        self.assertFalse((unsafe/'protected-state').exists())

    @unittest.skipIf(os.name == 'nt', 'Native root sticky state leaf and untrusted journal regression')
    def test_sticky_state_leaf_and_user_owned_journal_directory_are_not_trusted(self):
        state = self.work/'sticky-state'
        state.mkdir(mode=0o1777)
        state.chmod(0o1777)
        journals = state/'transactions'
        journals.mkdir()
        marker = journals/'crafted.json'
        raw = b'untrusted recovery journal must never be parsed'
        marker.write_bytes(raw)
        os.chown(journals,1000,1000)
        with self.assertRaisesRegex(MaintenanceError,'must not be writable'):
            Engine(self.root,state)
        self.assertEqual(marker.read_bytes(),raw)
        self.assertFalse((state/'lock').exists())
        os.chown(journals,0,0)
        with self.assertRaisesRegex(MaintenanceError,'must not be writable'):
            Engine(self.root,state)
        self.assertEqual(marker.read_bytes(),raw)

    @unittest.skipIf(os.name == 'nt', 'Large native sparse source exercises bounded streaming')
    def test_original_larger_than_old_inline_limit_restores_from_small_journal(self):
        size = 129*1024**2
        original = self.bundle/'incoming'
        with original.open('wb') as stream:
            stream.truncate(size)
        digest = hashlib.sha256()
        with original.open('rb') as stream:
            while block := stream.read(1024*1024):
                digest.update(block)
        name = digest.hexdigest()
        original.rename(self.bundle/name)
        (self.bundle/name).chmod(0o600)
        for item in list(self.bundle.iterdir()):
            if item.name != name:
                item.unlink()
        operation = self.value['components'][0]['operations'][0]
        operation['before'] = dict(exists=True,blob=name,sha256=name,size=size,mode=0o644,uid=0,gid=0)
        operation['after'] = {'exists':False}
        self.value['blobs'] = [dict(sha256=name,size=size)]
        (self.root/'etc/model.conf').unlink()
        self.save()
        result = self.engine.adopt(self.receipt)
        self.assertLess(self.engine._journal(result['transactions'][0]).stat().st_size,16384)
        self.engine.restore(result['transactions'][0])
        target = self.root/'etc/model.conf'
        self.assertEqual(target.stat().st_size,size)
        self.assertEqual(implementation.observe(self.engine,target)['sha256'],name)

    @unittest.skipIf(os.name == 'nt', 'Native 138 MB adopted upgrade and recovery chain')
    def test_138mb_adopted_file_upgrades_and_restores_with_bounded_binary_journals(self):
        size = 138485536
        target = self.root/'etc/model.conf'
        with target.open('wb') as stream:
            stream.truncate(size)
        target.chmod(0o644)
        digest = hashlib.sha256()
        with target.open('rb') as stream:
            while block := stream.read(1024*1024):
                digest.update(block)
        name = digest.hexdigest()
        with (self.bundle/name).open('wb') as stream:
            stream.truncate(size)
        (self.bundle/name).chmod(0o600)
        operation = self.value['components'][0]['operations'][0]
        old_after = operation['after']['blob']
        (self.bundle/old_after).unlink()
        self.value['blobs'] = [item for item in self.value['blobs'] if item['sha256'] != old_after]
        self.value['blobs'].append(dict(sha256=name,size=size))
        operation['after'] = dict(exists=True,blob=name,sha256=name,size=size,mode=0o644,uid=0,gid=0)
        self.save()
        adopted = self.engine.adopt(self.receipt)
        component,payload = self.upgrade()
        source = payload/'new'
        with source.open('wb') as stream:
            stream.write(b'N')
            stream.truncate(size)
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            while block := stream.read(1024*1024):
                digest.update(block)
        component['files'][0].update(size=size,sha256=digest.hexdigest())
        result = self.engine.apply(component,payload)
        self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.assertEqual(implementation.observe(self.engine,target)['sha256'],digest.hexdigest())
        self.assertLess(self.engine._journal(result['transaction']).stat().st_size,16384)
        self.engine.restore(result['transaction'])
        self.assertEqual(implementation.observe(self.engine,target)['sha256'],name)
        self.engine.restore(adopted['transactions'][0])
        self.assertEqual(target.read_bytes(),b'original configuration\n')


class InlineAdoptionCompatibilityTests(unittest.TestCase):
    def setUp(self):
        intake.AdoptionReceiptIntakeTests.setUp(self)

    def component(self):
        payload=self.work/'payload';payload.mkdir()
        (payload/'new').write_bytes(b'new configuration\n')
        component=dict(schema_version=1,kind='ComponentManifest',id='oxp3:input',version='1.0.7',files=[dict(
            source='new',target='etc/model.conf',size=18,sha256=hashlib.sha256(b'new configuration\n').hexdigest(),mode=0o644)])
        return component,payload

    def test_inline_adoption_upgrade_and_both_restore_generations_need_no_old_store(self):
        adopted=self.engine.adopt(self.receipt)
        component,payload=self.component()
        with mock.patch.object(implementation,'_legacy_lock',side_effect=AssertionError('V1 had no legacy lock contract')):
            upgraded=self.engine.apply(component,payload)
            self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'new configuration\n')
            self.engine.restore(upgraded['transaction'])
            self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'applied configuration\n')
            self.engine.restore(adopted['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'original configuration\n')
        self.assertFalse((self.root/'home/.oxp3-maintenance').exists())

    def test_inline_adoption_interrupted_binary_upgrade_recovers_without_old_store(self):
        adopted=self.engine.adopt(self.receipt)
        component,payload=self.component();original=self.engine._put_snapshot
        def dying(path,snapshot):
            original(path,snapshot)
            raise RuntimeError('simulated interruption')
        with mock.patch.object(implementation,'_legacy_lock',side_effect=AssertionError('V1 had no legacy lock contract')):
            with mock.patch.object(self.engine,'_put_snapshot',side_effect=dying), mock.patch.object(self.engine,'_recover',return_value=[]):
                with self.assertRaises(RuntimeError): self.engine.apply(component,payload)
            self.assertEqual(len(Engine(self.root).recover()),1)
            self.assertTrue(self.engine.verify('oxp3:input')['ok'])
        self.assertEqual(self.engine._state()['components']['oxp3:input']['transaction'],adopted['transactions'][0])
        self.assertEqual((self.root/'etc/model.conf').read_bytes(),b'applied configuration\n')
        self.assertFalse((self.root/'home/.oxp3-maintenance').exists())


if __name__ == '__main__':
    unittest.main()
