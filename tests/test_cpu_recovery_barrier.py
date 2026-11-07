import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_cpu_resource as cpu_tests
ROOT,REFS,snapshot=cpu_tests.ROOT,cpu_tests.REFS,cpu_tests.snapshot
from protocol.cpu_contract import IDENTITY,CONFIG,HARDWARE
from maintenance.engine import Engine,MaintenanceError
from maintenance.resource_runtime import ResourcePool,ResourceSession,installer_install,maintenance_action,component

class CpuRecoveryBarrierTests(unittest.TestCase):
    fixture=cpu_tests.CpuResourceTests.fixture
    install=cpu_tests.CpuResourceTests.install
    action=cpu_tests.CpuResourceTests.action
    @classmethod
    def setUpClass(cls):cpu_tests.CpuResourceTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):cpu_tests.CpuResourceTests.tearDownClass.__func__(cls)

    def kill_cpu_group(self,slots,home):
        command='\n'.join(['import json,os,sys','sys.path.insert(0,sys.argv[1])','from maintenance.engine import Engine','from maintenance.resource_runtime import ResourcePool,installer_install',"pool=ResourcePool.for_local_test(sys.argv[2],trust_local_test=True)",'slots=json.loads(sys.argv[3]);original=Engine._put_snapshot','def killed(self,path,value,**kwargs):','    original(self,path,value,**kwargs)',"    if path.name=='config.json':os._exit(73)",'Engine._put_snapshot=killed',"installer_install(pool,[dict(resource='resource:intel-g3e-public-cpu-controls',parameters={})],['resource:intel-g3e-public-cpu-controls'],slots=slots,shared_home=sys.argv[4])"])
        value={s:dict(v,root=str(v['root'])) for s,v in slots.items()}
        run=subprocess.run([sys.executable,'-B','-c',command,str(ROOT),str(self.pool.root),json.dumps(value),str(home)],capture_output=True,text=True)
        self.assertEqual(run.returncode,73,run.stderr);self.assertTrue((slots['A']['root']/CONFIG).exists())

    def unrelated_pool(self):
        catalog=copy.deepcopy(self.pool.catalog);entry=copy.deepcopy(catalog['resources'][0]['payload']['files'][-1])
        other=dict(schema_version=1,kind='BoilerResource',id='resource:unrelated',version='1.0.0',dependencies=[],payload=dict(files=[entry]),
            install=dict(kind='files-v1',files=[dict(source=entry['path'],target='etc/unrelated',scope='root-slots',mode=0o644)]),
            verify=dict(kind='sha256-v1'),restore=dict(kind='transaction-backup-v1'))
        catalog['resources'].append(other);return ResourcePool(self.pool.root,catalog,local_test=True),other

    def test_non_cpu_entries_cannot_implicitly_recover_pending_cpu_even_when_active(self):
        for entry in ('resource-apply','engine-apply','engine-verify','engine-restore','engine-recover','retained-other'):
            with self.subTest(entry=entry),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);slots,home=self.fixture(root);pool,other=self.unrelated_pool();refs=[dict(resource=other['id'],parameters={})]
                receipt=installer_install(pool,refs,[other['id']],slots=slots,shared_home=home);self.kill_cpu_group(slots,home)
                slots['A']['hardware']['cpu_service_state']='active';engine=Engine(slots['A']['root']);before=snapshot(root)
                txn=next(r['transaction'] for r in receipt['transactions'] if r['scope']=='A')
                with self.assertRaisesRegex(MaintenanceError,'CPU'):
                    if entry=='resource-apply':installer_install(pool,refs,[other['id']],slots=slots,shared_home=home)
                    elif entry=='engine-apply':engine.apply(component(other,'root-slots'),pool.root)
                    elif entry=='engine-verify':engine.verify(other['id'])
                    elif entry=='engine-restore':engine.restore(txn)
                    elif entry=='engine-recover':engine.recover()
                    else:ResourceSession(pool,slots,home).retained_receipt(receipt['id'])
                self.assertEqual(snapshot(root),before);self.assertTrue((slots['A']['root']/CONFIG).exists())

    def test_raw_cpu_apply_and_restore_require_fixed_host_authorization(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);engine=Engine(slots['A']['root']);before=snapshot(root)
            with self.assertRaisesRegex(MaintenanceError,'explicit fixed'):
                engine.apply(component(self.pool.resource(IDENTITY),'root-slots'),self.pool.root,environment=slots['A']['environment'])
            self.assertEqual(snapshot(root),before)
            receipt=self.install(slots,home);txn=next(r['transaction'] for r in receipt['transactions'] if r['scope']=='A');before=snapshot(root)
            with self.assertRaisesRegex(MaintenanceError,'explicit fixed'):engine.restore(txn)
            self.assertEqual(snapshot(root),before)

    def test_fixed_recovery_succeeds_with_broken_apply_interfaces_and_new_kernel(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);self.kill_cpu_group(slots,home)
            for value in slots.values():value['hardware']['intel_pstate']=False;value['hardware']['cpu_core_groups']=[];value['environment']['kernels']=['changed-kernel']
            result=self.action(slots,home,'recover');self.assertTrue(result['engines']['A']);self.assertFalse((slots['A']['root']/CONFIG).exists())

    def test_fixed_restore_succeeds_with_broken_apply_interfaces_or_new_kernel(self):
        for changed in ('pstate','kernel'):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);slots,home=self.fixture(root);receipt=self.install(slots,home)
                if changed=='pstate':slots['A']['hardware']['intel_pstate']=False
                else:slots['A']['environment']['kernels']=['changed-kernel']
                result=self.action(slots,home,'restore',receipt=receipt,scope='A');self.assertTrue(result);self.assertFalse((slots['A']['root']/CONFIG).exists())

    def test_explicit_fixed_recovery_still_refuses_active_service_before_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);self.kill_cpu_group(slots,home);slots['A']['hardware']['cpu_service_state']='active';before=snapshot(root)
            with self.assertRaises(MaintenanceError):self.action(slots,home,'recover')
            self.assertEqual(snapshot(root),before)


    def test_completed_cpu_history_does_not_block_unrelated_recovery(self):
        for terminal in ('restored','rolled-back'):
            with self.subTest(terminal=terminal),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);slots,home=self.fixture(root);pool,other=self.unrelated_pool();refs=[dict(resource=other['id'],parameters={})]
                installer_install(pool,refs,[other['id']],slots=slots,shared_home=home)
                if terminal=='restored':
                    receipt=self.install(slots,home)
                    with self.assertRaisesRegex(MaintenanceError,'CPU journals require'):
                        maintenance_action(pool,refs,[other['id']],slots=slots,shared_home=home,action='recover')
                    for scope in ('A','B'):self.action(slots,home,'restore',receipt=receipt,scope=scope)
                    group_path=Path(receipt['receipt_path']);before=group_path.read_bytes()
                else:
                    self.kill_cpu_group(slots,home);self.action(slots,home,'recover',pool);group_path=None
                for value in slots.values():value['hardware']={};value['environment']['kernels']=['unrelated-new-kernel']
                result=maintenance_action(pool,refs,[other['id']],slots=slots,shared_home=home,action='recover')
                self.assertIn('engines',result)
                for value in slots.values():self.assertNotIn(IDENTITY,Engine(value['root'])._state()['components'])
                if group_path:
                    self.assertEqual(group_path.read_bytes(),before)
                    ResourceSession(pool,slots,home).retained_receipt(receipt['id'])
                    self.assertEqual(group_path.read_bytes(),before)

if __name__=='__main__':unittest.main()
