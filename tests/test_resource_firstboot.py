import copy
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance.resource_runtime import ResourcePool, ResourceSession, ResourceError
from maintenance import resource_firstboot as firstboot
from maintenance.engine import _json_write
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'guest'))
from resource_firstboot_install import bootstrap_manifest, BOOTSTRAP

class OfflineDeploymentTests(unittest.TestCase):
    def fixture(self,base):
        payload=base/'pool';payload.mkdir();data=b'fixed resource\n';(payload/'body').write_bytes(data)
        import hashlib
        resource=dict(id='resource:sample',version='1.0.0',dependencies=[],compatibility={},
            hardware_requires={'actual_interface':[True]},payload={'files':[dict(path='body',size=len(data),sha256=hashlib.sha256(data).hexdigest())]},
            install=dict(kind='files-v1',files=[dict(source='body',target='etc/boiler-sample',scope='root-slots',mode=420)]),
            verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
        slots={}
        for slot in ('A','B'):
            root=base/slot;root.mkdir();(root/'etc').mkdir();(root/'home').mkdir();(root/'var').mkdir()
            slots[slot]=dict(root=root,environment={},hardware={},opt_offload=False)
        home=base/'home';home.mkdir()
        return ResourcePool(payload,{'resources':[resource]},local_test=True),slots,home,[{'resource':resource['id']}]
    def test_regular_path_still_requires_positive_live_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            pool,slots,home,refs=self.fixture(Path(directory))
            session=ResourceSession(pool,slots,home,read_only=True)
            with self.assertRaisesRegex(ResourceError,'actual_interface'):session.prepare(refs,['resource:sample'])
            self.assertFalse((slots['A']['root']/'var/lib/boiler').exists())
    def test_offline_mode_installs_real_files_without_claiming_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            pool,slots,home,refs=self.fixture(Path(directory))
            session=ResourceSession(pool,slots,home,offline_image=True)
            receipt=session.install(refs,['resource:sample'])
            self.assertEqual(receipt['status'],'committed')
            for slot in slots:self.assertEqual((slots[slot]['root']/'etc/boiler-sample').read_bytes(),b'fixed resource\n')
            self.assertTrue(all(p['ok'] for p in session.verify(['resource:sample'])))
            self.assertTrue(all(not t['hardware'] for t in slots.values()))
            self.assertFalse(hasattr(pool,'_offline_deployment'))
    def test_offline_mode_cannot_authorize_current_live_root_or_single_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            pool,slots,home,refs=self.fixture(Path(directory))
            with self.assertRaisesRegex(ResourceError,'both owned'):ResourceSession(pool,{'A':slots['A']},home,offline_image=True)
            slots['A']['root']=Path(Path(directory).anchor)
            with self.assertRaisesRegex(ResourceError,'both owned'):ResourceSession(pool,slots,home,offline_image=True)
    def test_bootstrap_contains_real_service_ordering_gates_and_private_intent(self):
        intent=dict(selection={'components':['resource:intel-g3e-public-cpu-controls']})
        with tempfile.TemporaryDirectory() as directory:
            manifest=bootstrap_manifest(Path(directory),intent)
            rows={r['target']:r for r in manifest['files']}
            self.assertEqual(manifest['id'],BOOTSTRAP)
            self.assertEqual(rows[firstboot.INTENT]['mode'],0o600)
            gate='etc/systemd/system/oxp3-cpu.service.d/90-boiler-resource-gate.conf'
            self.assertIn(gate,rows)
            self.assertIn(b'ConditionPathExists=/var/lib/boiler-resource-approved/intel-g3e-public-cpu-controls.json',(Path(directory)/rows[gate]['source']).read_bytes())
            self.assertIn('etc/systemd/system/plugin_loader.service.d/90-boiler-resource-gate.conf',rows)

class FirstbootOrderingTests(unittest.TestCase):
    def run_case(self,directory,provider,initial=None,*,retry_failed=False,probe=None):
        root=Path(directory);state=root/'state.json';identity='resource:sample'
        (root/'proc/sys/kernel/random').mkdir(parents=True,exist_ok=True)
        (root/'proc/sys/kernel/random/boot_id').write_text('12345678-1234-1234-1234-123456789abc')
        intent=dict(schema_version=1,kind=firstboot.KIND,generation='a'*32,slot='A',root_partuuid='b'*36,
            selection={'components':[identity]},resource_receipt={'id':'a'*32})
        if initial:_json_write(state,initial)
        class Pool:
            def resource(self,identity):return dict(id=identity,install={'kind':'files-v1'},hardware_requires={'actual_interface':[True]})
        class Session:
            def __init__(self,*a,**kw):self.read_only=kw['read_only']
            def verify(self,ids):return [dict(ok=True,component=identity)]
        class Core:
            def __init__(self,*a,**kw):pass
            def verify_readonly(self,identity):return dict(ok=True)
        with patch.object(firstboot,'open_pool',return_value=contextlib.nullcontext((Pool(),[]))),patch.object(firstboot,'environment',return_value={}),patch.object(firstboot,'ResourceSession',Session),patch.object(firstboot,'Engine',Core),patch.object(firstboot,'resolve',return_value=[{'id':identity}]),patch.object(firstboot,'_provider',return_value=provider),patch('maintenance.resource_activation.collect_facts',return_value={'actual_interface':True},side_effect=probe):
            result=firstboot._run_locked(root,intent,state,retry_failed=retry_failed)
        return result,state
    def test_recovery_is_durable_before_provider_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            class Provider:
                def activate(self,identity,**kw):
                    kw['record_before_write']({'owned-service':'inactive'})
                    state=json.loads((Path(directory)/'state.json').read_text())
                    assert state['resources'][identity]['activation_recovery']=={'owned-service':'inactive'}
                    return dict(status='active',runtime='validated')
            result,_=self.run_case(directory,Provider())
            self.assertEqual(result['status'],'complete')
    def test_activation_failure_retains_writeahead_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            class Provider:
                def activate(self,identity,**kw):
                    kw['record_before_write']({'owned-service':'inactive'})
                    raise RuntimeError('driver rejected actual interface')
            result,_=self.run_case(directory,Provider())
            row=result['resources']['resource:sample']
            self.assertEqual(row['status'],'failed');self.assertIn('activation_recovery',row)
            self.assertEqual(result['status'],'attention_required')
    def test_interrupted_activation_never_replays_automatically(self):
        with tempfile.TemporaryDirectory() as directory:
            initial=dict(schema_version=1,kind='BoilerResourceFirstbootState',generation='a'*32,slot='A',root_partuuid='b'*36,status='running',resources={'resource:sample':{'status':'activating','activation_recovery':{'owner':'before'}}})
            class Provider:
                def activate(self,*a,**kw):raise AssertionError('interrupted action replayed')
            result,_=self.run_case(directory,Provider(),initial)
            self.assertEqual(result['resources']['resource:sample']['status'],'recovery_required')

    def test_failed_retry_retains_original_ownership_and_requires_explicit_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            recovery={'services':[{'unit':'owned.service','before':'inactive','start_pending':True}]}
            initial=dict(schema_version=1,kind='BoilerResourceFirstbootState',generation='a'*32,slot='A',root_partuuid='b'*36,status='attention_required',resources={'resource:sample':{'status':'failed','activation_recovery':copy.deepcopy(recovery)}})
            class Provider:
                def activate(self,*a,**kw):raise AssertionError('unsafe automatic retry')
            result,_=self.run_case(directory,Provider(),initial,retry_failed=True)
            self.assertEqual(result['resources']['resource:sample']['status'],'recovery_required')
            self.assertEqual(result['resources']['resource:sample']['activation_recovery'],recovery)
    def test_facts_are_refreshed_immediately_before_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            class Provider:
                def activate(self,identity,**kw):
                    assert kw['facts']['actual_interface'] is True
                    return dict(status='active',runtime='validated')
            result,_=self.run_case(directory,Provider(),probe=[{'actual_interface':False},{'actual_interface':True}])
            self.assertEqual(result['status'],'complete')
            self.assertFalse(result['initial_probe']['actual_interface'])
    def test_active_sameboot_is_verified_without_reactivation(self):
        with tempfile.TemporaryDirectory() as directory:
            initial=dict(schema_version=1,kind='BoilerResourceFirstbootState',generation='a'*32,slot='A',root_partuuid='b'*36,status='complete',resources={'resource:sample':{'status':'active','runtime':'validated','boot_id':'12345678-1234-1234-1234-123456789abc'}})
            class Provider:
                def activate(self,*a,**kw):raise AssertionError('sameboot resource reactivated')
                def verify(self,identity,**kw):return dict(status='active',runtime='validated')
            result,_=self.run_case(directory,Provider(),initial)
            self.assertEqual(result['status'],'complete')
    def test_newboot_reactivates_and_preserves_original_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            original={'identity':'resource:sample','services':[]}
            initial=dict(schema_version=1,kind='BoilerResourceFirstbootState',generation='a'*32,slot='A',root_partuuid='b'*36,status='complete',resources={'resource:sample':{'status':'active','runtime':'validated','boot_id':'old-boot','activation_recovery':copy.deepcopy(original)}})
            class Provider:
                def activate(self,identity,**kw):
                    kw['record_before_write']({'identity':identity,'services':[],'new_boot':True})
                    return dict(status='active',runtime='validated')
            result,_=self.run_case(directory,Provider(),initial)
            self.assertEqual(result['status'],'complete')
            self.assertEqual(result['resources']['resource:sample']['activation_recovery'],original)
            self.assertTrue(result['resources']['resource:sample']['activation_recovery_updates'])
    def test_restored_resource_is_not_resurrected_by_timer(self):
        with tempfile.TemporaryDirectory() as directory:
            initial=dict(schema_version=1,kind='BoilerResourceFirstbootState',generation='a'*32,slot='A',root_partuuid='b'*36,status='attention_required',resources={'resource:sample':{'status':'restored'}})
            class Provider:
                def activate(self,*a,**kw):raise AssertionError('restored resource reactivated')
            result,_=self.run_case(directory,Provider(),initial)
            self.assertEqual(result['resources']['resource:sample']['status'],'restored')

class RestoreBindingTests(unittest.TestCase):
    def test_last_plugin_restore_stops_only_owned_loader_after_previous_complete_restore(self):
        from maintenance import resource_activation
        ids=['resource:common-windows-reboot','resource:onexplayer-light']
        for owned,changed in ((True,False),(False,False),(True,True)):
            with self.subTest(owned=owned,changed=changed),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path=root/firstboot.STATE;path.parent.mkdir(parents=True)
                intent=dict(generation='a'*32,slot='A',root_partuuid='b'*36,selection={'components':ids})
                state=dict(kind='BoilerResourceFirstbootState',generation='a'*32,slot='A',root_partuuid='b'*36,
                    resources={ids[0]:dict(status='restored'),ids[1]:dict(status='active',activation_recovery={})},
                    decky=dict(started=owned,invocation='ours'))
                _json_write(path,state)
                gate=root/'var/lib/boiler-resource-approved/decky.json';gate.parent.mkdir(parents=True)
                _json_write(gate,{'generation':'a'*32})
                commands=[]
                class System:
                    def __init__(self,*args):pass
                    def service(self,*args):return 'active'
                    def invocation(self,*args):return 'external' if changed else 'ours'
                    def run(self,*args):commands.append(args)
                class Provider:
                    def restore(self,*args,**kwargs):return {}
                class Core:
                    def __init__(self,*args,**kwargs):pass
                    def verify_readonly(self,*args):return {'ok':True}
                with patch.object(firstboot,'Engine',Core),patch.object(firstboot,'_provider',return_value=Provider()),patch.object(resource_activation,'LiveSystem',System):
                    if changed:
                        with self.assertRaisesRegex(ResourceError,'changed outside'):firstboot._restore_locked(ids[1],root,intent)
                        self.assertTrue(gate.exists());self.assertEqual(commands,[])
                        continue
                    result=firstboot._restore_locked(ids[1],root,intent)
                self.assertFalse(gate.exists())
                self.assertEqual(commands,[('systemctl','stop','plugin_loader.service')] if owned else [])
                self.assertEqual(result['resources'][ids[1]]['status'],'activation_restored')

    def test_wrong_generation_or_gate_cannot_mutate_state_or_stop_provider(self):
        for wrong in ('state','gate'):
            with self.subTest(wrong=wrong),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path=root/firstboot.STATE;path.parent.mkdir(parents=True)
                intent=dict(generation='a'*32,slot='A',root_partuuid='b'*36,selection={'components':['resource:sample']})
                state=dict(kind='BoilerResourceFirstbootState',generation=('x'*32 if wrong=='state' else 'a'*32),slot='A',root_partuuid='b'*36,resources={'resource:sample':dict(status='active',activation_recovery={'owner':'before'})})
                _json_write(path,state);original=path.read_bytes()
                if wrong=='gate':
                    gate=root/'var/lib/boiler-resource-approved/sample.json';gate.parent.mkdir(parents=True);_json_write(gate,{'generation':'x'*32,'identity':'resource:sample'})
                class Core:
                    def __init__(self,*a,**kw):pass
                    def verify_readonly(self,*a):return {'ok':True}
                with patch.object(firstboot,'Engine',Core),patch.object(firstboot,'_provider') as provider:
                    with self.assertRaises(ResourceError):firstboot._restore_locked('resource:sample',root,intent)
                    provider.assert_not_called()
                self.assertEqual(path.read_bytes(),original)

if __name__=='__main__':unittest.main()

