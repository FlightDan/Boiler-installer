import contextlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import models, model_efi, ssd_boot

class RetainedModelActionTests(unittest.TestCase):
    def test_retained_oxp3_contract_selects_its_own_provider_for_verify_and_restore(self):
        selection={'schema_version':1,'slot':'guest','repairs':['oxp3:split-lock'],'plugins':[],
                   'installer_language':'en-US','kde_language':'system','acknowledge_legacy_update_hold':True}
        bound={'slot':'A','root_partuuid':'aaaaaaaa-1111','esp_partuuid':'bbbbbbbb-2222',
               'esp_type':ssd_boot.ESP_TYPE,'home_uuid':'home-uuid'}
        source=dict(package_id='device:oxp3',manifest_sha256='a'*64,selection_sha256=ssd_boot.digest(selection))
        record=dict(source=source,status='installed',companions_original={})
        pair={'slots':{'A':{'target':model_efi._model_target(bound)}}}
        store=mock.Mock();store._state_read.side_effect=lambda key: pair if key=='pair.json' else (record if key.startswith('model-esp-') else None)
        plan=mock.Mock();plan.plan.side_effect=lambda action: {'action':action}
        for key,kind,provider_module,lock_name in (
            ('oxp3_efi_contract','oxp3-split-lock-efi-v1','components.oxp3_efi_provider','oxp3_efi_provider.lock.json'),
            ('oxp3_full_efi_contract','oxp3-full-split-lock-efi-v1','components.oxp3_full_efi_provider','oxp3_full_efi_provider.lock.json')):
            package={'id':'device:oxp3',key:{'kind':kind,'component':'oxp3:legacy-adaptation'}}
            item={'package':package,'directory':'fixture-package','manifest_sha256':'a'*64}
            with self.subTest(key=key), mock.patch(provider_module+'.verify') as verify, \
                 mock.patch.object(models.os.path,'lexists',return_value=True), \
                 mock.patch.object(ssd_boot,'binding',return_value=bound), \
                 mock.patch('maintenance.keyboard_runtime.vendor.transactions.Store',return_value=store), \
                 mock.patch.object(models,'_private_json',return_value={'selection':selection,'phase':'applied'}), \
                 mock.patch.object(models,'validated_selection',return_value={k:v for k,v in selection.items() if k!='slot'}), \
                 mock.patch.object(model_efi,'_check_pair'),mock.patch.object(model_efi,'_record'), \
                 mock.patch.object(model_efi,'_verify_adapter_proof'),mock.patch.object(model_efi,'ModelEFI',return_value=plan), \
                 mock.patch.object(models,'_review_model_companions',return_value={}):
                for action in ('verify','restore'):
                    result=models._retained_model_efi(Path('fixture-root'),item,action)
                    self.assertEqual(result['review']['action'],action)
                    self.assertEqual(result.get('provider'),key if key=='oxp3_full_efi_contract' else None)
                self.assertEqual(verify.call_count,2)
                self.assertEqual(verify.call_args.args[1].name,lock_name)

    @unittest.skipUnless(os.name == 'posix', 'POSIX flock fixture requires Linux')
    def test_real_legacy_flock_blocks_competing_descriptor_and_releases(self):
        if os.geteuid() != 0:
            self.skipTest('Protected maintenance fixture requires controlled Linux root')
        import fcntl
        with tempfile.TemporaryDirectory(prefix='boiler-legacy-lock-') as directory:
            root=Path(directory)
            legacy=root/'home/.oxp3-maintenance'
            legacy.mkdir(parents=True,mode=0o700)
            path=legacy/'lock'
            path.touch(mode=0o600)
            engine=SimpleNamespace(root=root)
            with path.open('rb') as old_maintainer:
                fcntl.flock(old_maintainer.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                with self.assertRaisesRegex(models.MaintenanceError,'Legacy maintenance is active'):
                    with models._retained_legacy_guard(engine):
                        self.fail('Competing legacy owner was ignored')
                fcntl.flock(old_maintainer.fileno(),fcntl.LOCK_UN)
                with models._retained_legacy_guard(engine):
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(old_maintainer.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                fcntl.flock(old_maintainer.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                fcntl.flock(old_maintainer.fileno(),fcntl.LOCK_UN)

    def test_existing_legacy_lock_is_required_and_busy_fails_before_work(self):
        engine=mock.Mock();engine.root=Path('fixture')
        events=[]
        @contextlib.contextmanager
        def held(*args,**kwargs):
            self.assertEqual(kwargs,{'blocking':False})
            events.append('legacy-acquired')
            try: yield
            finally: events.append('legacy-released')
        with mock.patch.object(models.os.path,'lexists',side_effect=[True,False]):
            with self.assertRaisesRegex(models.MaintenanceError,'no lock'):
                with models._retained_legacy_guard(engine): events.append('write')
        self.assertEqual(events,[])
        with mock.patch.object(models.os.path,'lexists',return_value=True), \
             mock.patch('maintenance.adoption_blobs._legacy_lock',side_effect=models.MaintenanceError('Legacy maintenance is active')):
            with self.assertRaisesRegex(models.MaintenanceError,'active'):
                with models._retained_legacy_guard(engine): events.append('write')
        self.assertEqual(events,[])
        with mock.patch.object(models.os.path,'lexists',return_value=True), \
             mock.patch('maintenance.adoption_blobs._legacy_lock',side_effect=held):
            with models._retained_legacy_guard(engine): events.append('work')
        self.assertEqual(events,['legacy-acquired','work','legacy-released'])

    def test_parent_acquires_engine_then_legacy_before_recheck_or_writes(self):
        events=[]
        @contextlib.contextmanager
        def engine_lock(**kwargs):
            events.append('engine');yield
        @contextlib.contextmanager
        def legacy_lock(engine):
            events.append('legacy');yield
        from types import SimpleNamespace
        engine=SimpleNamespace(_lock=mock.Mock(side_effect=engine_lock),_recover=mock.Mock(),
                               _state=mock.Mock(return_value={}),state_dir=Path('fixture/state'))
        review={'status':'installed'}
        plan=dict(kind='ReviewedModelPlan',model_id='device:oxp3',action='restore',selection={},
                  state_sha256=models._digest({}),pending=None,package={},
                  backend=dict(kind='RetainedModelEfiAction',model_efi=review,ordinary=None))
        plan['review_sha256']=models._digest(plan)
        def retained(*args,**kwargs): events.append('recheck');return review
        def perform(*args,**kwargs): events.append('boot-and-ssd-work');return dict(ok=True,verified=True,status='restored')
        with mock.patch.object(models,'_privileged'),mock.patch.object(models,'plan_model',return_value=plan), \
             mock.patch.object(models,'Engine',return_value=engine),mock.patch.object(models,'pending',return_value=None), \
             mock.patch.object(models,'_retained_legacy_guard',side_effect=legacy_lock), \
             mock.patch.object(models,'_package',return_value={'manifest_sha256':'a'*64}),mock.patch.object(models,'_retain',return_value=Path('package')), \
             mock.patch.object(models,'_retained_model_efi',side_effect=retained),mock.patch.object(models,'_execute_retained_model_efi',side_effect=perform), \
             mock.patch.object(models,'_directory',return_value=contextlib.nullcontext()),mock.patch.object(models,'_json_write'), \
             mock.patch.object(models,'_sync_dir'),mock.patch.object(Path,'unlink'):
            models.execute_model('fixture',plan,reviewed_sha256=plan['review_sha256'],trust_local_test=True)
        self.assertEqual(events,['engine','legacy','recheck','boot-and-ssd-work'])
        engine._recover.reset_mock()
        with mock.patch.object(models,'_privileged'),mock.patch.object(models,'plan_model',return_value=plan), \
             mock.patch.object(models,'Engine',return_value=engine),mock.patch.object(models,'pending',return_value=None), \
             mock.patch.object(models,'_retained_legacy_guard',side_effect=models.MaintenanceError('Legacy maintenance is active')), \
             mock.patch.object(models,'_execute_retained_model_efi') as execute, mock.patch.object(models,'_json_write') as write:
            with self.assertRaisesRegex(models.MaintenanceError,'active'):
                models.execute_model('fixture',plan,reviewed_sha256=plan['review_sha256'],trust_local_test=True)
            execute.assert_not_called();write.assert_not_called();engine._recover.assert_not_called()

    def test_retained_boot_lock_precedes_ssd_lock(self):
        events=[]
        @contextlib.contextmanager
        def held(name):
            events.append(name+'-enter')
            try: yield
            finally: events.append(name+'-exit')
        bound={'slot':'A'}; plan={'action':'verify','review_sha256':'a'*64}
        review={'status':'installed','source':{},'selection':{},'review':plan,'companions':{}}
        obj=mock.Mock();obj.store.lock.side_effect=lambda:held('boot')
        obj.store._state_read.return_value={'companions_original':{}};obj._name.return_value='record';obj.plan.return_value=plan
        store=mock.Mock();store.state=Path('fixture/ssd');store.lock.side_effect=lambda:held('ssd')
        with mock.patch.object(model_efi,'ModelEFI',return_value=obj),mock.patch('maintenance.keyboard_runtime.vendor.transactions.Store',return_value=store), \
             mock.patch.object(models.os.path,'lexists',return_value=True),mock.patch.object(ssd_boot,'binding',return_value=bound), \
             mock.patch.object(models,'_review_model_companions',return_value={}):
            result=models._execute_retained_model_efi(Path('fixture'),Path('package'),review)
        self.assertTrue(result['verified'])
        self.assertEqual(events,['boot-enter','ssd-enter','ssd-exit','boot-exit'])

    def test_ordinary_store_cannot_overlap_fixed_boot_targets(self):
        store=mock.Mock()
        store._state_read.return_value=None
        store._index.return_value={'slots':{'partuuid-a':{'current':{'paths':{'etc/default/grub.d/oxp3-splitlock.cfg':{}}}}}}
        with mock.patch('maintenance.keyboard_runtime.vendor.transactions.Store',return_value=store):
            with self.assertRaises(models.MaintenanceError):
                models._ordinary_model_action(Path('fixture'),{'id':'partuuid-a'})
            store._index.return_value={'slots':{'partuuid-a':{'current':{'paths':{'etc/other.conf':{}}}}}}
            self.assertTrue(models._ordinary_model_action(Path('fixture'),{'id':'partuuid-a'}))

    def test_interrupted_efi_recovers_only_after_ordinary_and_companions(self):
        events=[]
        @contextlib.contextmanager
        def lock():
            events.append('lock')
            try: yield
            finally: events.append('unlock')
        bound={'slot':'A'}; pending={'companions_original':{'first':'snapshot'}}
        inner={'action':'recover','review_sha256':'a'*64}
        review={'status':'installed','source':{'package_id':'device:x2-mini'},'selection':{},'review':inner,'companions':{'snapshot':'unchanged'}}
        obj=mock.Mock();obj.store.lock.side_effect=lock;obj.store._state_read.return_value=pending
        obj._name.return_value='record';obj.plan.return_value=inner
        obj._intent.return_value=(b'old',b'new',None,b'old',b'new')
        obj._execute_locked.side_effect=lambda *a,**kw:(events.append('recover-efi') or {'changed':True})
        store=mock.Mock();store.state=Path('absent-ssd-state')
        with mock.patch.object(model_efi,'ModelEFI',return_value=obj),mock.patch('maintenance.keyboard_runtime.vendor.transactions.Store',return_value=store), \
             mock.patch.object(models.os.path,'lexists',return_value=False),mock.patch.object(ssd_boot,'binding',return_value=bound), \
             mock.patch.object(model_efi,'_ssd_restore_barrier'),mock.patch.object(ssd_boot,'_target',return_value=b'new'), \
             mock.patch.object(models,'_review_model_companions',return_value={'snapshot':'unchanged'}), \
             mock.patch.object(models,'_rollback_model_companions',side_effect=lambda *a:events.append('companions')):
            result=models._execute_retained_model_efi(Path('fixture'),Path('package'),review,lambda:events.append('ordinary-recover'))
        self.assertTrue(result['recovered'])
        self.assertLess(events.index('ordinary-recover'),events.index('companions'))
        self.assertLess(events.index('companions'),events.index('recover-efi'))

    def test_restore_persists_efi_intent_before_ordinary_and_companions(self):
        events=[]; locked=[False]
        @contextlib.contextmanager
        def lock():
            self.assertFalse(locked[0]);locked[0]=True;events.append('lock')
            try: yield
            finally: locked[0]=False;events.append('unlock')
        bound={'slot':'A'}; pending={'companions_original':{'first':'snapshot'}}
        review_plan={'action':'restore','review_sha256':'a'*64}
        review={'status':'installed','source':{'package_id':'device:oxp3'},'provider':'oxp3_full_efi_contract',
                'selection':{},'review':review_plan,'companions':{'snapshot':'unchanged'}}
        obj=mock.Mock();obj.store.lock.side_effect=lock;obj.store._state_read.side_effect=lambda key: pending
        obj._name.return_value='record';obj.plan.return_value=review_plan
        obj._execute_locked.side_effect=lambda *a,**kw:(events.append('efi-intent-and-write') or {'changed':True})
        obj._intent.return_value=(b'old',b'new',None,b'old',b'new')
        obj._finalize_locked.side_effect=lambda:events.append('finalize')
        store=mock.Mock();store.state=Path('absent-ssd-state')
        def ordinary():
            self.assertFalse(locked[0]);events.append('ordinary-store');return {'ok':True}
        with mock.patch.object(model_efi,'ModelEFI',return_value=obj),mock.patch('maintenance.keyboard_runtime.vendor.transactions.Store',return_value=store), \
             mock.patch.object(models.os.path,'lexists',return_value=False),mock.patch.object(ssd_boot,'binding',return_value=bound), \
             mock.patch.object(model_efi,'_ssd_restore_barrier'),mock.patch.object(ssd_boot,'_target',return_value=b'new'), \
             mock.patch.object(models,'_review_model_companions',return_value={'snapshot':'unchanged'}), \
             mock.patch.object(models,'_rollback_model_companions',side_effect=lambda *a:events.append(('companions',a[-1]))):
            result=models._execute_retained_model_efi(Path('fixture'),Path('package'),review,ordinary)
        self.assertTrue(result['ok'])
        self.assertLess(events.index('efi-intent-and-write'),events.index('ordinary-store'))
        self.assertLess(events.index('ordinary-store'),events.index(('companions','oxp3_full_efi_contract')))
        self.assertLess(events.index(('companions','oxp3_full_efi_contract')),events.index('finalize'))
        events.clear()
        def interrupted():
            self.assertFalse(locked[0]); events.append('ordinary-interrupted')
            raise RuntimeError('simulated interruption')
        with mock.patch.object(model_efi,'ModelEFI',return_value=obj),mock.patch('maintenance.keyboard_runtime.vendor.transactions.Store',return_value=store), \
             mock.patch.object(models.os.path,'lexists',return_value=False),mock.patch.object(ssd_boot,'binding',return_value=bound), \
             mock.patch.object(model_efi,'_ssd_restore_barrier'),mock.patch.object(ssd_boot,'_target',return_value=b'new'), \
             mock.patch.object(models,'_review_model_companions',return_value={'snapshot':'unchanged'}), \
             mock.patch.object(models,'_rollback_model_companions',side_effect=lambda *a:events.append('companions')):
            with self.assertRaisesRegex(RuntimeError,'simulated interruption'):
                models._execute_retained_model_efi(Path('fixture'),Path('package'),review,interrupted)
        self.assertEqual([e for e in events if e not in ('lock','unlock')],
                         ['efi-intent-and-write','ordinary-interrupted'])

if __name__=='__main__': unittest.main()
