"""Actual fourteen-resource sources staged as data, with unknown hardware facts."""
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'guest'),str(ROOT/'components'),str(ROOT/'tests')]
from maintenance.engine import Engine,_json_write
from maintenance.resource_runtime import ResourcePool,ResourceSession,ResourceError
from maintenance.resource_staging import PURPOSE,CACHE,preflight_cache_stage,stage_cache,inspect_cache_stage
from maintenance.resource_entrypoints import prepare_cache_only_stage,commit_cache_only_stage,verify_cache_only_stage
from prepare_payload import build as prepare_payload

def snapshot(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

class FixtureOwner:
    purpose=PURPOSE;mode='explicit-file-fixture'
    def __init__(self):self.activated=False
    def revalidate(self,plan):pass
    def activate(self,plan):self.activated=True

class CacheStagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='cache-stage-sources-');cls.base=Path(cls.tmp.name)
        spec=importlib.util.spec_from_file_location('cache_stage_pool_builder',ROOT/'scripts/prepare-resource-pool.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        cls.pool_root=cls.base/'pool';builder.build(ROOT,cls.pool_root)
        cls.payload=cls.base/'payload';prepare_payload(cls.pool_root,cls.payload)
        cls.pool=ResourcePool.for_local_test(cls.payload/'integration/resource-pool',trust_local_test=True)
        assert len(cls.pool.catalog['resources'])==14
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='cache-stage-targets-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.home=self.root/'home';self.home.mkdir();self.slots={}
        for name in ('A','B'):
            root=self.root/name;root.mkdir()
            for n in ('var','home'):(root/n).mkdir()
            self.slots[name]=dict(root=root,environment={},hardware={})
    def selection(self,model):return [r['resource'] for r in next(m for m in self.pool.catalog['models'] if m['id']==model)['resources']]
    def plan(self,model='device:oxp3'):
        return prepare_cache_only_stage(self.payload,model,self.selection(model),slots=self.slots,shared_home=self.home,trust_local_test=True)
    def grant(self,owner=None):
        @contextlib.contextmanager
        def grant(plan):yield owner or FixtureOwner()
        return grant
    def test_default_and_wrong_purpose_are_write_free(self):
        before=snapshot(self.root);plan=self.plan();self.assertEqual(before,snapshot(self.root))
        with self.assertRaisesRegex(ResourceError,'purpose-bound'):commit_cache_only_stage(plan)
        owner=FixtureOwner();owner.purpose='resource-apply-v1'
        with self.assertRaisesRegex(ResourceError,'separate explicit development purpose'):stage_cache(plan,authorize=self.grant(owner))
        self.assertFalse(owner.activated);self.assertEqual(before,snapshot(self.root))
    def test_modified_plan_and_plain_json_grant_no_authority(self):
        plan=self.plan();plan.core['files'][0]['mode']=0o755;before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'review was modified'):stage_cache(plan,authorize=self.grant())
        with self.assertRaisesRegex(ResourceError,'live cache-only'):stage_cache({'purpose':PURPOSE},authorize=self.grant())
        self.assertEqual(before,snapshot(self.root))
    def test_self_consistent_extra_autostart_target_is_not_a_stageable_core(self):
        bad=self.root/'bad-payload';shutil.copytree(self.payload,bad)
        path=bad/'integration/manifest.json';value=json.loads(path.read_bytes());row=value['components']['core:maintenance']
        extra=dict(row['component']['files'][0],target='etc/systemd/system/must-not-stage.service')
        row['component']['files'].append(extra);row['files'].append(extra);path.write_text(json.dumps(value))
        before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'fixed runtime'):
            preflight_cache_stage(bad,self.pool,'device:oxp3',self.selection('device:oxp3'),slots=self.slots,shared_home=self.home)
        self.assertEqual(before,snapshot(self.root))
    def test_real_exit_after_core_commit_does_not_invent_a_staged_receipt(self):
        from test_fresh_resource_combined import pool_at
        tiny,_,selected=pool_at(self.root/'tiny-pool');payload=self.root/'tiny-payload';prepare_payload(tiny.root,payload)
        code='''import contextlib,os,json,sys
from pathlib import Path
source=Path(sys.argv[1]);sys.path[:0]=[str(source),str(source/'components')]
from maintenance.engine import Engine
from maintenance.resource_entrypoints import prepare_cache_only_stage,commit_cache_only_stage
from maintenance.resource_staging import PURPOSE
original=Engine.apply
def killed(self,component,*args,**kwargs):
 result=original(self,component,*args,**kwargs)
 if component['id']=='core:maintenance':os._exit(73)
 return result
Engine.apply=killed
class Owner:
 mode='explicit-file-fixture';purpose=PURPOSE
 def revalidate(self,p):pass
 def activate(self,p):pass
@contextlib.contextmanager
def grant(p):yield Owner()
plan=prepare_cache_only_stage(sys.argv[2],'device:fixture',['resource:fresh-fixture'],slots=json.loads(sys.argv[3]),shared_home=sys.argv[4],trust_local_test=True)
commit_cache_only_stage(plan,authorize=grant)
'''
        slots={s:dict(root=str(t['root']),environment={}) for s,t in self.slots.items()}
        child=subprocess.run([sys.executable,'-B','-c',code,str(ROOT),str(payload),json.dumps(slots),str(self.home)],capture_output=True,text=True)
        self.assertEqual(child.returncode,73,child.stderr)
        paths=list((self.home/'.boiler/fresh-resource-installs').glob('*.json'));self.assertEqual(len(paths),1)
        record=json.loads(paths[0].read_bytes());self.assertEqual(record['status'],'preparing');self.assertEqual(record['core_transactions'],[])
        before=snapshot(self.root);proof=inspect_cache_stage(paths[0],slots=self.slots,shared_home=self.home)
        self.assertFalse(proof['currently_staged']);self.assertEqual(proof['resources'],[]);self.assertEqual(before,snapshot(self.root))
        actual=Engine(self.slots['A']['root'],read_only=True)._state()['components']['core:maintenance']['transaction']
        Engine(self.slots['A']['root']).restore(actual)
    def test_both_models_stage_all_fourteen_without_installing_or_observing_hardware(self):
        for model in ('device:oxp3','device:x2-mini'):
            with self.subTest(model=model):
                if model.endswith('x2-mini'):
                    self.doCleanups();self.setUp()
                sentinels={self.slots['A']['root']/'var/lib/oxp3-cpu/config.json':b'{"enabled": true, "user": "preserve"}\n',
                    self.slots['B']['root']/'usr/lib/firmware/iwlwifi-sc-a0-fm-c0-c106.ucode':b'OS-owned firmware must remain unchanged\n',
                    self.home/'deck/.config/gamescope/scripts/98-boiler-gamma22.lua':b'-- existing user override\n'}
                for path,raw in sentinels.items():path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                plan=self.plan(model)
                with patch.object(ResourceSession,'install',side_effect=AssertionError('Resource installer must never execute')):
                    result=stage_cache(plan,authorize=self.grant())
                self.assertEqual(result['status'],'staged');self.assertEqual(result['activation'],'activation_blocked')
                self.assertIs(result['hardware_facts_consumed'],False);self.assertIs(result['resource_installation_performed'],False)
                self.assertEqual(len(result['intent']['resources']),14)
                self.assertTrue(all(r['status']=='staged' and r['activation']=='activation_blocked' for r in result['intent']['resources']))
                cpu=next(r for r in result['intent']['resources'] if r['id']=='resource:intel-g3e-public-cpu-controls')
                self.assertEqual(cpu['activation_requirements']['external_components'],['decky','native-tdp'])
                for resource in self.pool.catalog['resources']:
                    for row in resource['install']['files']:
                        paths=[self.home/row['target'].removeprefix('home/')] if row['scope']=='shared-home' else [t['root']/row['target'] for t in self.slots.values()]
                        for path in paths:
                            if path not in sentinels:self.assertFalse(path.exists(),str(path))
                for path,raw in sentinels.items():self.assertEqual(path.read_bytes(),raw)
                for target in self.slots.values():self.assertEqual(set(Engine(target['root'],read_only=True)._state()['components']),{'core:maintenance'})
                before=snapshot(self.root);proof=verify_cache_only_stage(result['receipt_path'],slots=self.slots,shared_home=self.home)
                self.assertTrue(proof['currently_staged']);self.assertEqual(before,snapshot(self.root))
                self.assertEqual(len(list(self.payload.rglob('catalog.toon'))),1)
                # Selected source/model data is committed in both actual core
                # journals, not accepted from a modified receipt alone.
                path=Path(result['receipt_path']);original=path.read_bytes();forged=json.loads(original)
                forged['intent']['source_model']='device:x2-mini' if model.endswith('oxp3') else 'device:oxp3';_json_write(path,forged)
                self.assertFalse(inspect_cache_stage(path,slots=self.slots,shared_home=self.home)['currently_staged']);path.write_bytes(original)
                entry=self.pool.catalog['resources'][0]['payload']['files'][0]
                cached=self.slots['A']['root']/CACHE/entry['path'];saved=cached.read_bytes();cached.write_bytes(b'corrupt staged source')
                before=snapshot(self.root);self.assertFalse(inspect_cache_stage(path,slots=self.slots,shared_home=self.home)['currently_staged'])
                self.assertEqual(before,snapshot(self.root));cached.write_bytes(saved)
                # The ordinary resource-only entry cannot reinterpret this
                # separate core intent as a completed resource installation.
                from resource_results import retained_core,require_matching_core
                with self.assertRaisesRegex(ResourceError,'Retained core differs'):
                    require_matching_core(ResourceSession(self.pool,self.slots,self.home,read_only=True),retained_core(self.payload))
                # Verify through the actually retained maintenance module in a
                # fresh process, without importing this source checkout.
                runtime=self.slots['A']['root']/'usr/local/lib/boiler'
                code="import sys,json;sys.path.insert(0,sys.argv[1]);from maintenance.resource_entrypoints import verify_cache_only_stage;v=verify_cache_only_stage(sys.argv[2],slots=json.loads(sys.argv[3]),shared_home=sys.argv[4]);print(json.dumps(v));assert v['currently_staged']"
                slots={s:dict(root=str(t['root']),environment={}) for s,t in self.slots.items()}
                child=subprocess.run([sys.executable,'-B','-c',code,str(runtime),str(path),json.dumps(slots),str(self.home)],capture_output=True,text=True)
                self.assertEqual(child.returncode,0,child.stderr)
                for operation in reversed(result['core_transactions']):Engine(self.slots[operation['scope']]['root']).restore(operation['transaction'])
                before=snapshot(self.root);after=inspect_cache_stage(path,slots=self.slots,shared_home=self.home)
                self.assertFalse(after['currently_staged']);self.assertEqual(after['status'],'binding_invalid');self.assertEqual(after['resources'],[])
                self.assertEqual(before,snapshot(self.root))
                for path,raw in sentinels.items():self.assertEqual(path.read_bytes(),raw)


@unittest.skipUnless(os.name=='posix' and os.environ.get('BOILER_CACHE_STAGE_MOUNTS')=='1',
                     'Disposable Linux Btrfs/ext4 fixture required')
class MountedCacheStageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):CacheStagingTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):CacheStagingTests.tearDownClass.__func__(cls)
    def test_actual_a_b_cache_stage_preserves_all_activation_targets(self):
        with tempfile.TemporaryDirectory(prefix='mounted-cache-stage-') as temporary:
            base=Path(temporary);mounts=[];loops=[];roots={};var_devices={};unprotected=[]
            def command(*args):subprocess.run(list(args),check=True,stdout=subprocess.DEVNULL)
            def image(name,size):
                path=base/(name+'.img');assert path.resolve().is_relative_to(base.resolve())
                with path.open('xb') as stream:stream.truncate(size)
                return path
            def loop(path):
                device=subprocess.check_output(['losetup','-f'],text=True).strip()
                command('losetup',device,str(path));loops.append(device);return device
            def mount(source,target,*args):
                target.mkdir();command('mount',*args,str(source),str(target));mounts.append(target)
            try:
                for module in ('loop','btrfs','ext4'):command('modprobe',module)
                raw=image('home',128*1024**2);command('mkfs.ext4','-q','-F',str(raw));home_device=loop(raw)
                home=base/'home';mount(home_device,home,'-t','ext4','-o','ro,noload')
                for slot in ('A','B'):
                    raw=image('root-'+slot,256*1024**2);command('mkfs.btrfs','-q','-f',str(raw))
                    initial=base/('initial-'+slot);mount(raw,initial,'-t','btrfs','-o','loop');command('btrfs','subvolume','create',str(initial/'root'))
                    lower=initial/'root'
                    for name in ('var','home','etc','opt','usr/lib/systemd/system'):(lower/name).mkdir(parents=True,exist_ok=True)
                    (lower/'usr/lib/systemd/system/etc.mount').write_text('Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work\n')
                    (lower/'usr/lib/systemd/system/opt.mount').write_text('What=/home/.steamos/offload/opt\nWhere=/opt\nOptions=bind\n')
                    command('btrfs','property','set','-ts',str(lower),'ro','true');command('umount',str(initial));mounts.remove(initial)
                    root=base/slot;mount(raw,root,'-t','btrfs','-o','loop,subvol=root');roots[slot]=root
                    rawvar=image('var-'+slot,64*1024**2);command('mkfs.ext4','-q','-F',str(rawvar));var_devices[slot]=loop(rawvar)
                    command('mount','-t','ext4','-o','ro,noload',var_devices[slot],str(root/'var'));mounts.append(root/'var')
                    command('mount','--bind',str(home),str(root/'home'));mounts.append(root/'home')
                slots={s:dict(root=root,environment={},hardware={}) for s,root in roots.items()}
                selected=[r['resource'] for r in next(m for m in self.pool.catalog['models'] if m['id']=='device:oxp3')['resources']]
                plan=prepare_cache_only_stage(self.payload,'device:oxp3',selected,slots=slots,shared_home=home,trust_local_test=True)
                self.assertFalse((home/'.steamos').exists())
                class Owner:
                    purpose=PURPOSE;mode='development-session'
                    def revalidate(self,p):
                        for root in roots.values():
                            expected='ro=false' if root in unprotected else 'ro=true'
                            if subprocess.check_output(['btrfs','property','get','-ts',str(root),'ro'],text=True).strip()!=expected:
                                raise RuntimeError('Fixture root protection changed')
                    def activate(self,p):
                        for root in roots.values():command('umount',str(root/'home'));mounts.remove(root/'home')
                        command('umount',str(home));mounts.remove(home);command('mount','-t','ext4',home_device,str(home));mounts.insert(0,home)
                        for slot,root in roots.items():
                            command('btrfs','property','set','-ts',str(root),'ro','false');unprotected.append(root)
                            command('umount',str(root/'var'));mounts.remove(root/'var');command('mount','-t','ext4',var_devices[slot],str(root/'var'));mounts.append(root/'var')
                            command('mount','--bind',str(home),str(root/'home'));mounts.append(root/'home')
                @contextlib.contextmanager
                def grant(p):yield Owner()
                result=stage_cache(plan,authorize=grant)
                self.assertEqual(result['status'],'staged');self.assertTrue(inspect_cache_stage(result['receipt_path'],slots=slots,shared_home=home)['currently_staged'])
                self.assertFalse((home/'.steamos').exists())
                self.assertFalse((home/'deck/.config/gamescope/scripts/98-boiler-gamma22.lua').exists())
                for root in roots.values():
                    self.assertFalse((root/'var/lib/overlays').exists());self.assertFalse((root/'etc/systemd/system/oxp3-cpu.service').exists())
                    self.assertFalse((root/'usr/lib/firmware/iwlwifi-sc-a0-fm-c0-c106.ucode').exists())
                    self.assertFalse((root/'var/lib/oxp3-cpu/config.json').exists());self.assertTrue((root/CACHE/'catalog.toon').is_file())
                    self.assertEqual(set(Engine(root,read_only=True)._state()['components']),{'core:maintenance'})
                for operation in reversed(result['core_transactions']):Engine(roots[operation['scope']]).restore(operation['transaction'])
                self.assertFalse(inspect_cache_stage(result['receipt_path'],slots=slots,shared_home=home)['currently_staged'])
                print('CACHE_STAGE_REAL_BTRFS_EXT4_14_SOURCES_NO_ACTIVATION=passed',flush=True)
            finally:
                errors=[]
                for root in reversed(unprotected):
                    if subprocess.run(['btrfs','property','set','-ts',str(root),'ro','true'],capture_output=True).returncode:errors.append('root protection')
                for path in reversed(mounts):
                    if subprocess.run(['umount',str(path)],capture_output=True).returncode:errors.append('unmount '+str(path))
                for device in reversed(loops):
                    if subprocess.run(['losetup','-d',device],capture_output=True).returncode:errors.append('loop detach')
                if errors:raise RuntimeError('Stage fixture cleanup failed: '+str(errors))

if __name__=='__main__':unittest.main()
