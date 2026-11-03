import contextlib
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from maintenance import shared_models as shared
from maintenance import ssd_boot as boot
from maintenance.engine import MaintenanceError

PROVIDER='vendor:gm7-resume'
OWNER={'component_id':PROVIDER,'manifest_sha256':'a'*64}
POLICY={'id':'ssd:gm7-boot-repair','version':1}


class ProviderMaintenanceTests(unittest.TestCase):
    def test_provider_verify_and_restore_use_fixed_ssd_handler(self):
        component=dict(id=PROVIDER,ssd_boot_policy=POLICY,files=[],hooks={},requires=[],conflicts=[],optional=True)
        item=dict(components=[component],manifest_sha256=OWNER['manifest_sha256'],package={'id':'device:example'})
        current=dict(executable=True,install_available=True,pending=None,
                     current=dict(repairs=[PROVIDER],plugins=[]),
                     capabilities=[dict(id=PROVIDER,available=True)],package=dict(id='device:example',manifest_sha256='a'*64),
                     environment={},target={},warnings=[])
        engine=mock.Mock(); engine._state.return_value={'components':{},'owners':{}}
        handler=mock.Mock(); handler.plan.side_effect=lambda action:dict(action=action,review_sha256='b'*64)
        handler.execute.return_value=dict(ok=True,changed=False,restart_required=None)
        with tempfile.TemporaryDirectory(prefix='boiler-provider-fixture-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as root, \
             mock.patch.object(shared.models,'_privileged'), mock.patch.object(shared,'options',return_value=current), \
             mock.patch.object(shared,'package',return_value=item), mock.patch.object(shared,'Engine',return_value=engine), \
             mock.patch.object(boot,'SSD',return_value=handler) as factory:
            for action in ('verify','restore'):
                with self.subTest(action=action):
                    plan=shared.plan(root,'device:example',[],action=action,trust_local_test=True)
                    self.assertEqual(plan['selection']['repairs'],[PROVIDER]); self.assertEqual(plan['ssd_plan']['action'],action)
                    result=shared.execute(root,plan,reviewed_sha256=plan['review_sha256'],trust_local_test=True)
                    self.assertEqual(result['result']['components'][0]['id'],PROVIDER)
                    self.assertTrue(result['result']['verified'])
            self.assertTrue(all(call.kwargs.get('provider_owner') == OWNER for call in factory.call_args_list))
            engine._apply_locked.assert_not_called()

    def test_common_existing_state_cannot_be_relabelled_as_provider(self):
        obj=object.__new__(boot.SSD); obj.root=Path('fixture'); obj.context=None; obj.model_preflight=None; obj.provider_owner=OWNER
        obj.store=mock.Mock(); obj.store.state=Path('missing-fixture-state')
        obj.store._state_read.side_effect=lambda name: None if name=='pending.json' else {'version':2}
        bound=dict(disposition='match',esp_partuuid='aaaaaaaa-1111')
        with mock.patch.object(boot,'binding',return_value=bound), mock.patch.object(boot,'Engine'), \
             mock.patch.object(boot.os.path,'lexists',return_value=False):
            with self.assertRaises(MaintenanceError): obj.plan('apply')

    def test_package_provider_owner_rejects_different_id_or_manifest(self):
        obj=object.__new__(boot.SSD); obj.root=Path('fixture'); obj.context=None; obj.model_preflight=None
        obj.store=mock.Mock(); obj.store.state=Path('missing-fixture-state')
        obj.store._state_read.side_effect=lambda name: None if name=='pending.json' else {'version':4,'provider_owner':OWNER}
        bound=dict(disposition='match',esp_partuuid='aaaaaaaa-1111')
        for owner in (dict(OWNER,component_id='other:ssd'),dict(OWNER,manifest_sha256='b'*64)):
            obj.provider_owner=owner
            with self.subTest(owner=owner), mock.patch.object(boot,'binding',return_value=bound), \
                 mock.patch.object(boot,'Engine'), mock.patch.object(boot.os.path,'lexists',return_value=False):
                with self.assertRaises(MaintenanceError): obj.plan('verify')

    def test_provider_id_may_match_common_id_but_owner_remains_explicit(self):
        owner=dict(component_id='ssd:gm7-boot-repair',manifest_sha256='a'*64)
        self.assertEqual(boot._provider_owner(owner),owner)

    def test_v4_provider_record_preserves_owner_and_exact_original(self):
        bound=dict(root_partuuid='aaaaaaaa-1111',slot='A',esp_partuuid='bbbbbbbb-2222',esp_type=boot.ESP_TYPE,
                   home_uuid='home-uuid',physical={'bus':'NVMe','model':'Predator SSD GM7 1TB','serial':'serial1'})
        original=dict(efi=boot._encode(b'linux /boot/vmlinuz-test quiet\n'),dropin=None)
        record=dict(version=4,target=boot._durable(bound),original=original,applied=boot._desired(original),
                    status='installed',provider_owner=OWNER)
        before,after=boot._record(record,bound)
        self.assertEqual(before,original); self.assertEqual(record['provider_owner'],OWNER)
        self.assertIn(b'nvme.noacpi=1',boot._decode(after['efi']))
        bad=dict(record,provider_owner=dict(OWNER,manifest_sha256='not-a-digest'))
        with self.assertRaises(MaintenanceError): boot._record(bad,bound)

    def test_authenticated_model_package_needs_no_shared_metadata(self):
        component=dict(id=PROVIDER,ssd_boot_policy=POLICY,files=[],hooks={},requires=[],conflicts=[],optional=True)
        item=dict(directory='fixture',manifest_sha256='a'*64,package=dict(id='device:example',components=[component]))
        engine=mock.Mock(); engine.state_dir=Path('missing-fixture-state')
        with mock.patch.object(shared,'bundled_model_package',return_value=item), \
             mock.patch.object(shared.os.path,'lexists',return_value=False):
            result=shared.package('device:example',engine,Path('runtime'),True)
        self.assertEqual([part['id'] for part in result['components']],[PROVIDER])
        self.assertEqual(shared._ssd_provider(result,PROVIDER),OWNER)

    def test_fixed_handler_verify_and_restore_preserve_provider_owner(self):
        bound=dict(root_partuuid='aaaaaaaa-1111',slot='A',esp_partuuid='bbbbbbbb-2222',esp_type=boot.ESP_TYPE,
                   home_uuid='home-uuid',physical={'bus':'NVMe','model':'Predator SSD GM7 1TB','serial':'serial1'},
                   disposition='match')
        original=dict(efi=boot._encode(b'linux /boot/vmlinuz-test quiet\n'),dropin=None)
        applied=boot._desired(original)
        record=dict(version=4,target=boot._durable(bound),original=original,applied=applied,status='installed',provider_owner=OWNER)
        obj=object.__new__(boot.SSD); obj.root=Path('fixture'); obj.context=None; obj.model_preflight=None; obj.provider_owner=OWNER
        obj.store=mock.Mock(); obj.store.state=Path('fixture/missing'); obj.store.lock.return_value=contextlib.nullcontext()
        obj.store._state_read.side_effect=lambda name:None if name=='pending.json' else record
        obj._remove_pending=mock.Mock()
        engine=mock.Mock(); engine._lock=mock.Mock(return_value=contextlib.nullcontext())
        lock_store=mock.Mock(); lock_store.lock.return_value=contextlib.nullcontext()
        with mock.patch.object(boot.os,'name','posix'), mock.patch.object(boot.os,'geteuid',return_value=0,create=True), \
             mock.patch.object(boot,'binding',return_value=bound), mock.patch.object(boot,'Engine',return_value=engine), \
             mock.patch.object(boot,'Store',return_value=lock_store), mock.patch.object(boot,'_legacy_conflicts'), \
             mock.patch.object(boot,'_snapshot',return_value=applied), mock.patch.object(boot.os.path,'lexists',return_value=False), \
             mock.patch.object(boot,'_replace') as replace, mock.patch.object(boot,'_activation',return_value='unknown'):
            verified=obj.plan('verify'); self.assertEqual(verified['provider_owner'],OWNER)
            self.assertTrue(obj.execute(verified,reviewed_sha256=verified['review_sha256'])['ok'])
            plan=obj.plan('restore'); result=obj.execute(plan,reviewed_sha256=plan['review_sha256'])
            self.assertEqual(result['status'],'restored'); replace.assert_called_once()
        saved=[call.args[1] for call in obj.store._state_write.call_args_list if call.args[0].startswith('esp-')]
        self.assertEqual(len(saved),1); self.assertEqual(saved[0]['provider_owner'],OWNER)
        self.assertEqual(saved[0]['original'],original); self.assertEqual(saved[0]['status'],'restored')

    def test_same_id_provider_stays_selected_for_installer_verification(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'components'))
        try:
            import integration
            component=dict(id='ssd:gm7-boot-repair',ssd_boot_policy=POLICY,files=[],hooks={})
            descriptor=dict(ssd_providers=[component['id']],manifest_sha256='a'*64)
            options=dict(device_package='device:example',device_components=[component['id']])
            with mock.patch.object(integration,'validate_options'), mock.patch.object(integration.graphics,'selected',return_value=None), \
                 mock.patch.object(integration,'release',return_value={}), \
                 mock.patch.object(integration,'device_plan',return_value=(Path('fixture'),[component],{},descriptor)), \
                 mock.patch.object(integration,'maintenance_api',return_value=mock.Mock()):
                identities=integration.verify_device_components('fixture','fixture',options,'A',{component['id']:{}})
            self.assertEqual(identities,{component['id']})
            self.assertEqual(integration.selected_ssd_provider_owner(component['id'],[component],descriptor),
                             dict(component_id=component['id'],manifest_sha256='a'*64))
            with self.assertRaises(ValueError): integration.selected_ssd_provider_owner(component['id'],[],descriptor)
        finally:
            sys.path.pop(0)

    def test_provider_unavailable_for_nonmatching_ssd_and_default_off(self):
        component=dict(id=PROVIDER,ssd_boot_policy=POLICY,files=[],hooks={},requires=[],conflicts=[],optional=True,default=True)
        item=dict(components=[component],manifest_sha256='a'*64,package=dict(id='device:example',version='1.0.0'))
        engine=mock.Mock(); engine._state.return_value={'components':{},'owners':{}}; engine.journals.glob.return_value=[]
        handler=mock.Mock(); handler.inspect.return_value=dict(available=False,reason='not_applicable')
        with tempfile.TemporaryDirectory(prefix='boiler-provider-options-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as root, \
             mock.patch.object(shared.models,'_privileged'), mock.patch.object(shared,'Engine',return_value=engine), \
             mock.patch.object(shared,'package',return_value=item), mock.patch.object(shared,'target_environment',return_value={}), \
             mock.patch.object(shared,'compatible',return_value=True), mock.patch.object(boot,'SSD',return_value=handler), \
             mock.patch('maintenance.live_root.pending',return_value=None):
            result=shared.options(root,'device:example',trust_local_test=True)
        self.assertFalse(result['capabilities'][0]['available']); self.assertFalse(result['capabilities'][0]['default'])

    def test_interrupted_provider_restore_plans_fixed_recovery(self):
        component=dict(id=PROVIDER,ssd_boot_policy=POLICY,files=[],hooks={},requires=[],conflicts=[],optional=True)
        item=dict(components=[component],manifest_sha256='a'*64,package={'id':'device:example'})
        current=dict(executable=True,install_available=True,pending=dict(source='ssd-provider',component=PROVIDER),current=None,
                     capabilities=[dict(id=PROVIDER,available=True)],package=dict(id='device:example',manifest_sha256='a'*64),
                     environment={},target={},warnings=[])
        engine=mock.Mock(); engine._state.return_value={'components':{},'owners':{}}
        handler=mock.Mock(); handler.plan.return_value=dict(action='recover',review_sha256='b'*64)
        with tempfile.TemporaryDirectory(prefix='boiler-provider-recovery-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as root, \
             mock.patch.object(shared.models,'_privileged'), mock.patch.object(shared,'options',return_value=current), \
             mock.patch.object(shared,'package',return_value=item), mock.patch.object(shared,'Engine',return_value=engine), \
             mock.patch.object(boot,'SSD',return_value=handler):
            plan=shared.plan(root,'device:example',[],action='restore',trust_local_test=True)
        self.assertEqual(plan['selection']['repairs'],[PROVIDER]); handler.plan.assert_called_once_with('recover')

    def test_explicit_provider_restore_when_other_components_are_installed(self):
        component=dict(id=PROVIDER,ssd_boot_policy=POLICY,files=[],hooks={},requires=[],conflicts=[],optional=True)
        other=dict(id='vendor:other',files=[],hooks={})
        item=dict(components=[component,other],manifest_sha256='a'*64,package={'id':'device:example'})
        current=dict(executable=True,install_available=True,pending=None,
                     current=dict(repairs=[PROVIDER,'vendor:other'],plugins=[]),
                     capabilities=[dict(id=PROVIDER,available=True),dict(id='vendor:other',available=True)],
                     package=dict(id='device:example',manifest_sha256='a'*64),environment={},target={},warnings=[])
        engine=mock.Mock(); engine._state.return_value={'components':{'vendor:other':{}},'owners':{}}
        handler=mock.Mock(); handler.plan.return_value=dict(action='restore',review_sha256='b'*64)
        with tempfile.TemporaryDirectory(prefix='boiler-provider-mixed-',dir=os.environ.get('BOILER_SSD_TEST_TMP')) as root, \
             mock.patch.object(shared.models,'_privileged'), mock.patch.object(shared,'options',return_value=current), \
             mock.patch.object(shared,'package',return_value=item), mock.patch.object(shared,'Engine',return_value=engine), \
             mock.patch.object(boot,'SSD',return_value=handler):
            plan=shared.plan(root,'device:example',[PROVIDER],action='restore',trust_local_test=True)
            self.assertEqual(plan['selection']['repairs'],[PROVIDER]); handler.plan.assert_called_once_with('restore')
            with self.assertRaises(MaintenanceError): shared.plan(root,'device:example',[],action='restore',trust_local_test=True)
            current['pending']=dict(source='ssd-provider',component=PROVIDER)
            handler.plan.return_value=dict(action='recover',review_sha256='c'*64)
            recovered=shared.plan(root,'device:example',[],action='restore',trust_local_test=True)
            self.assertEqual(recovered['selection']['repairs'],[PROVIDER]); self.assertEqual(recovered['ssd_plan']['action'],'recover')

    def test_installer_completion_keeps_explicit_same_id_provider_owner(self):
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'components'))
        try:
            import integration
            identity='ssd:gm7-boot-repair'; owner=dict(component_id=identity,manifest_sha256='a'*64)
            plan=dict(provider_owner=owner,target=dict(root_partuuid='a',esp_partuuid='b'),review_sha256='c'*64)
            result=dict(components={},install_plan=dict(device_package=dict(ssd_providers=[identity],manifest_sha256='a'*64)))
            with mock.patch.object(boot,'installer_execute',return_value=dict(ok=True,changed=False)) as run:
                integration.complete_ssd('fixture',result,{'kind':'physical'},plan,identity)
            self.assertEqual(run.call_args.kwargs['provider_owner'],owner)
            self.assertEqual(result['install_plan']['ssd_repair']['provider_owner'],owner)
            self.assertIn(identity,result['components'])
        finally:
            sys.path.pop(0)


if __name__ == '__main__': unittest.main()
