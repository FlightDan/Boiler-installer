import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.cpu_contract import IDENTITY,CONFIG,HARDWARE,KERNEL,validate_contract
from protocol.resource_pool import validate_pool
from maintenance.resource_runtime import ResourcePool,installer_install,maintenance_action,component
from maintenance.engine import Engine,MaintenanceError
spec=importlib.util.spec_from_file_location('cpu_builder',ROOT/'scripts/prepare-cpu-resource.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
REFS=[dict(resource=IDENTITY,enabled=False,parameters={})]

def snapshot(root):
    return {p.relative_to(root).as_posix(): ('link',os.readlink(p)) if p.is_symlink() else
        ('file',hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode&0o777,p.stat().st_uid,p.stat().st_gid) if p.is_file() else ('dir',)
        for p in root.rglob('*')}

class CpuResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work=tempfile.TemporaryDirectory(prefix='cpu-resource-tests-');cls.pool_root=Path(cls.work.name)/'pool';builder.build(ROOT,cls.pool_root)
        cls.pool=ResourcePool.for_local_test(cls.pool_root,trust_local_test=True)
    @classmethod
    def tearDownClass(cls):cls.work.cleanup()
    def fixture(self,root):
        slots={}
        for name in ('A','B'):
            target=root/name;target.mkdir()
            slots[name]=dict(root=target,hardware={k:copy.deepcopy(v[0]) for k,v in HARDWARE.items()},environment=dict(architecture='x86_64',os_id='steamos',os_version='3.10',kernels=[KERNEL]))
        home=root/'home';home.mkdir();return slots,home
    def install(self,slots,home,pool=None):return installer_install(pool or self.pool,REFS,[IDENTITY],slots=slots,shared_home=home)
    def action(self,slots,home,action,pool=None,**kwargs):return maintenance_action(pool or self.pool,REFS,[IDENTITY],slots=slots,shared_home=home,action=action,**kwargs)

    def test_source_only_determinism_private_default_and_accurate_license(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'pool';result=builder.build(ROOT,output)
            self.assertEqual(snapshot(output),snapshot(self.pool_root));resource=validate_pool(output)['resources'][0];validate_contract(resource)
            row=next(r for r in resource['install']['files'] if r['target']==CONFIG);self.assertEqual(row['mode'],0o600)
            self.assertEqual(len(resource['install']['files']),8);self.assertFalse(json.loads((output/row['source']).read_bytes())['enabled'])
            self.assertFalse(result['private_research']);self.assertFalse(result['deployment_code_executed'])
            self.assertIn(b'GNU AFFERO GENERAL PUBLIC LICENSE',(output/'licenses/project-AGPL-3.0').read_bytes())
    def test_changed_public_lock_refuses_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'pool'
            with patch.object(builder,'LOCK_SHA','0'*64),self.assertRaises(ValueError):builder.build(ROOT,output)
            self.assertFalse(output.exists())
    def test_production_rejects_before_any_target_or_state_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);before=snapshot(root)
            production=ResourcePool(self.pool.root,self.pool.catalog,local_test=False)
            with self.assertRaisesRegex(MaintenanceError,'production deployment remains blocked'):self.install(slots,home,production)
            self.assertEqual(snapshot(root),before)
    def test_unknown_active_enabled_and_wrong_typed_facts_reject_before_all_writes(self):
        for key,value in (('cpu_service_state','active'),('cpu_service_enablement','enabled'),('intel_pstate',1),('cpu_core_groups',[2,8,3]),('steamos_build','unknown'),('cpu_staging_target','live-root')):
            with self.subTest(key=key),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);slots,home=self.fixture(root);slots['B']['hardware'][key]=value;before=snapshot(root)
                with self.assertRaises(MaintenanceError):self.install(slots,home)
                self.assertEqual(snapshot(root),before)
    def test_enabled_dependency_overrides_claimed_disabled_fact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root)
            target=slots['B']['root']/'etc/systemd/system/multi-user.target.wants/oxp3-cpu.service';target.parent.mkdir(parents=True);target.write_bytes(b'enabled dependency')
            before=snapshot(root)
            with self.assertRaisesRegex(ValueError,'enabled by an existing dependency'):self.install(slots,home)
            self.assertEqual(snapshot(root),before)
    def test_created_default_removed_only_if_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);receipt=self.install(slots,home)
            for slot in slots.values():
                path=slot['root']/CONFIG;self.assertFalse(json.loads(path.read_bytes())['enabled'])
                if os.name=='posix':self.assertEqual((path.stat().st_mode&0o777,path.stat().st_uid,path.stat().st_gid),(0o600,0,0))
            edited=slots['A']['root']/CONFIG;edited.write_bytes(b'user changes after install')
            for scope in ('B','A'):self.action(slots,home,'restore',receipt=receipt,scope=scope)
            self.assertFalse((slots['B']['root']/CONFIG).exists());self.assertEqual(edited.read_bytes(),b'user changes after install')
            self.assertNotIn(CONFIG,Engine(slots['A']['root'])._state()['owners'])
    def test_user_config_and_original_metadata_survive_two_generations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);config=slots['A']['root']/CONFIG;config.parent.mkdir(parents=True);config.write_bytes(b'original');config.chmod(0o640)
            old=config.parent/'cpu_control.py';old.write_bytes(b'prior runtime');old.chmod(0o600)
            if os.name=='posix':os.chown(config,0,123);os.chown(old,0,321)
            config_meta=config.stat();old_meta=old.stat();first=self.install(slots,home)
            config.write_bytes(b'user edited config');upgraded=copy.deepcopy(self.pool.catalog);upgraded['resources'][0]['version']+='+generation2';newer=ResourcePool(self.pool.root,upgraded,local_test=True)
            second=self.install(slots,home,newer);self.assertEqual(config.read_bytes(),b'user edited config')
            self.assertTrue(all(r['ok'] for r in self.action(slots,home,'verify',newer)))
            for receipt,pool,order in ((second,newer,('B','A')),(first,self.pool,('A','B'))):
                for scope in order:self.action(slots,home,'restore',pool,receipt=receipt,scope=scope)
            self.assertEqual(config.read_bytes(),b'user edited config');self.assertEqual(old.read_bytes(),b'prior runtime')
            if os.name=='posix':
                for path,info in ((config,config_meta),(old,old_meta)):self.assertEqual((path.stat().st_mode,path.stat().st_uid,path.stat().st_gid),(info.st_mode,info.st_uid,info.st_gid))
    def test_service_change_blocks_restore_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);receipt=self.install(slots,home);slots['B']['hardware']['cpu_service_state']='active';before=snapshot(root)
            with self.assertRaises(MaintenanceError):self.action(slots,home,'restore',receipt=receipt,scope='A')
            self.assertEqual(snapshot(root),before)
    def test_immutable_code_edit_still_blocks_restore_and_preserves_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);receipt=self.install(slots,home)
            self.action(slots,home,'recover');(slots['A']['root']/CONFIG).write_bytes(b'user config');(slots['A']['root']/'var/lib/oxp3-cpu/cpu_control.py').write_bytes(b'foreign edit');before=snapshot(root)
            with self.assertRaises(MaintenanceError):self.action(slots,home,'restore',receipt=receipt,scope='A')
            self.assertEqual(snapshot(root),before)
    def test_real_process_kill_after_config_write_recovers_all_originals(self):
        with tempfile.TemporaryDirectory() as temporary:
            target=Path(temporary)/'root';target.mkdir();manifest=component(self.pool.resource(IDENTITY),'root-slots')
            command='\n'.join(['import json,os,sys','from pathlib import Path','sys.path.insert(0,sys.argv[1])','from maintenance.engine import Engine','from maintenance.resource_runtime import ResourcePool',"pool=ResourcePool.for_local_test(sys.argv[3],trust_local_test=True)","slots={'A':dict(root=sys.argv[2],hardware=json.loads(sys.argv[6]),environment={'architecture':'x86_64','os_id':'steamos','os_version':'3.10','kernels':[sys.argv[5]]})}",'e=Engine(sys.argv[2]);original=e._put_snapshot','def killed(path,snapshot,**kwargs):','    original(path,snapshot,**kwargs)',"    if path.name=='config.json':os._exit(73)",'e._put_snapshot=killed',"e.apply_cpu(json.loads(sys.argv[4]),sys.argv[3],pool=pool,slots=slots,slot='A',environment={'architecture':'x86_64','os_id':'steamos','os_version':'3.10','kernels':[sys.argv[5]]})"])
            result=subprocess.run([sys.executable,'-B','-c',command,str(ROOT),str(target),str(self.pool.root),json.dumps(manifest),KERNEL,json.dumps({k:copy.deepcopy(v[0]) for k,v in HARDWARE.items()})],capture_output=True,text=True)
            self.assertEqual(result.returncode,73,result.stderr);self.assertTrue((target/CONFIG).exists());self.assertEqual(len(Engine(target).recover_cpu(pool=self.pool,slots={'A':dict(root=target,hardware={k:copy.deepcopy(v[0]) for k,v in HARDWARE.items()},environment={})},slot='A')),1)
            for row in manifest['files']:self.assertFalse((target/row['target']).exists())


    def test_actual_installer_and_maintenance_entrypoints_keep_activation_blocked(self):
        import shutil
        from maintenance.resource_entrypoints import stage_resources,resource_action
        from maintenance.resource_runtime import ResourceSession
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);payload=root/'payload';payload.mkdir();shutil.copytree(self.pool_root,payload/'resource-pool')
            runtime=root/'retained';(runtime/'cache').mkdir(parents=True);shutil.copytree(self.pool_root,runtime/'cache/resource-pool')
            options=dict(plugins=['core:maintenance'],resource_model='device:oxp3',resource_components=[IDENTITY])
            preview=ResourceSession(self.pool,slots,home,read_only=True);before=snapshot(root);preview.prepare(REFS,[IDENTITY]);self.assertEqual(snapshot(root),before)
            self.assertEqual(preview.cpu_config_plans[IDENTITY]['A']['action'],'create-disabled-private-default')
            receipt=stage_resources(payload,options,slots=slots,shared_home=home,trust_local_test=True)
            proof=resource_action(runtime,'device:oxp3',[IDENTITY],action='verify',slots=slots,shared_home=home,trust_local_test=True)
            self.assertTrue(all(p['ok'] and p['activation_verified'] is False and p['verification_scope']=='inactive-files-and-mutable-config-only' for p in proof))
            for scope in ('A','B'):resource_action(runtime,'device:oxp3',[IDENTITY],action='restore',slots=slots,shared_home=home,trust_local_test=True,receipt=receipt,scope=scope)

    @unittest.skipIf(os.name!='posix','Real ownership/link metadata requires Linux')
    def test_unsafe_existing_config_rejected_before_any_writes(self):
        for case in ('symlink','hardlink','foreign-owner','group-writable'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);slots,home=self.fixture(root);config=slots['B']['root']/CONFIG;config.parent.mkdir(parents=True)
                other=root/'user-original';other.write_bytes(b'user source')
                if case=='symlink':config.symlink_to(other)
                elif case=='hardlink':os.link(other,config)
                else:
                    config.write_bytes(b'user config')
                    if case=='foreign-owner':os.chown(config,123,123)
                    else:config.chmod(0o660)
                before=snapshot(root)
                with self.assertRaises((MaintenanceError,ValueError)):self.install(slots,home)
                self.assertEqual(snapshot(root),before)

    @unittest.skipIf(os.name!='posix','The exact public backend requires fcntl')
    def test_locked_public_cpu_backend_applies_restores_and_preserves_external_values(self):
        source=ROOT/'adaptations'/builder.PREFIX/'cpu/cpu_control.py'
        spec=importlib.util.spec_from_file_location('fixed_public_cpu_backend',source);cpu=importlib.util.module_from_spec(spec);spec.loader.exec_module(cpu)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cpu.CONFIG=root/'config.json';cpu.STATE=root/'state.json';cpu.POLICIES=root/'policies'
            for index,maximum in enumerate([4700000]*2+[3600000]*8+[3100000]*4):
                directory=cpu.POLICIES/('policy'+str(index));directory.mkdir(parents=True)
                for key,value in dict(scaling_driver='intel_pstate',cpuinfo_max_freq=maximum,scaling_min_freq=400000,scaling_max_freq=maximum,energy_performance_preference='balance_performance',energy_performance_available_preferences='performance balance_performance balance_power power',scaling_governor='powersave').items():(directory/key).write_text(str(value)+'\n')
            real_open=open
            def fixture_open(name,*args,**kwargs):return real_open(root/'cpu.lock' if str(name)=='/run/lock/oxp3-cpu.lock' else name,*args,**kwargs)
            with patch('builtins.open',fixture_open):
                config=dict(enabled=True,epp='balance_power',p_mhz=4000,e_mhz=3000,lp_mhz=2500);cpu.apply(config)
                first=cpu.POLICIES/'policy0/scaling_max_freq';self.assertEqual(first.read_text().strip(),'4000000');cpu.restore();self.assertEqual(first.read_text().strip(),'4700000')
                cpu.apply(config);first.write_text('3900000\n');restored=cpu.restore();self.assertIn(str(first),restored['external_changes']);self.assertEqual(first.read_text().strip(),'3900000')

    @unittest.skipIf(os.name!='posix','The exact public provider requires fcntl')
    def test_locked_public_rapl_provider_core_only_with_synthetic_interfaces(self):
        source=ROOT/'adaptations'/builder.PREFIX/'rapl/provider.py';spec=importlib.util.spec_from_file_location('fixed_public_rapl',source);rapl=importlib.util.module_from_spec(spec);spec.loader.exec_module(rapl)
        p1=(Path('/fixture/pl1-a'),Path('/fixture/pl1-b'));p2=(Path('/fixture/pl2-a'),Path('/fixture/pl2-b'));values={p:20000000 for p in p1};values.update({p:45000000 for p in p2});fault=[False]
        def writer(path,value):
            values[path]=value
            if fault[0] and path==p1[1] and value==40000000:fault[0]=False;raise OSError('synthetic failure after write')
        controller=rapl.RaplController(paths=p1,pl2_paths=p2,reader=lambda p:values[p],writer=writer,validator=lambda:None,busy_checker=lambda:None)
        controller.set_limit(30);controller.set_pl2(50);before=dict(values);fault[0]=True
        with self.assertRaises(rapl.ControllerError):controller.set_limit(40)
        self.assertEqual(values,before);self.assertEqual(controller.get_power_limits()['pl1_values'],[30,30])


    def test_real_process_kill_during_restore_preserves_user_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);slots,home=self.fixture(root);receipt=self.install(slots,home)
            config=slots['A']['root']/CONFIG;config.write_bytes(b'user config before interrupted restore')
            transaction=next(r['transaction'] for r in receipt['transactions'] if r['scope']=='A')
            command='\n'.join(['import json,os,sys','sys.path.insert(0,sys.argv[1])','from maintenance.engine import Engine','from maintenance.resource_runtime import ResourcePool',"pool=ResourcePool.for_local_test(sys.argv[4],trust_local_test=True)","slots={'A':dict(root=sys.argv[2],hardware=json.loads(sys.argv[5]),environment={})}",'e=Engine(sys.argv[2]);original=e._put_snapshot','def killed(path,snapshot,**kwargs):','    original(path,snapshot,**kwargs)',"    if path.name=='cpu_control.py':os._exit(74)",'e._put_snapshot=killed',"e.restore_cpu(sys.argv[3],pool=pool,slots=slots,slot='A')"])
            child=subprocess.run([sys.executable,'-B','-c',command,str(ROOT),str(slots['A']['root']),transaction,str(self.pool.root),json.dumps(slots['A']['hardware'])],capture_output=True,text=True)
            self.assertEqual(child.returncode,74,child.stderr);self.assertEqual(len(Engine(slots['A']['root']).recover_cpu(pool=self.pool,slots=slots,slot='A')),1)
            self.assertEqual(config.read_bytes(),b'user config before interrupted restore')
            self.assertNotIn(IDENTITY,Engine(slots['A']['root'])._state()['components'])

if __name__=='__main__':unittest.main()
