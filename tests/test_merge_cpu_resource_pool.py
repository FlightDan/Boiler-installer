"""Merge only locked public sources; execute the real fixed handlers on both models."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from protocol.resource_pool import validate_pool,decoder
from protocol.cpu_contract import IDENTITY as CPU,CONFIG,HARDWARE as CPU_FACTS,KERNEL
from protocol.firmware_contract import IDENTITY as WIFI,HARDWARE as WIFI_FACTS
from maintenance.resource_runtime import ResourcePool,installer_install,maintenance_action
from resources import merge_cpu_resource_pool as merger
spec=importlib.util.spec_from_file_location('combined_project_builder',ROOT/'scripts/prepare-resource-pool.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

def sha(raw):return hashlib.sha256(raw).hexdigest()
def snapshot(root):return {p.relative_to(root).as_posix():sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}

class CpuPoolMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work=tempfile.TemporaryDirectory(prefix='merge-public-cpu-');cls.base=Path(cls.work.name);_,cls.lock,_=builder.check_inputs(ROOT)
        components=cls.base/'components';builder.split_components.build(ROOT,components,xevma_module=ROOT/cls.lock['xevma_module'])
        sources=cls.base/'source-base';builder.assemble_source_pool.build(ROOT,components,sources)
        wifi=cls.base/'wifi';builder.build_wifi_pool.build(ROOT,wifi)
        cls.twelve=cls.base/'twelve';builder.merge_firmware_resource_pool.build(sources,wifi,cls.twelve,base_sha256=sha((sources/'catalog.toon').read_bytes()),wifi_sha256=sha((wifi/'catalog.toon').read_bytes()))
        cls.cpu=cls.base/'cpu';builder.cpu_builder(ROOT).build(ROOT,cls.cpu)
        cls.base_sha=sha((cls.twelve/'catalog.toon').read_bytes());cls.cpu_sha=sha((cls.cpu/'catalog.toon').read_bytes())
        cls.output=cls.base/'merged';cls.catalog=cls.merge(cls.output)
    @classmethod
    def tearDownClass(cls):cls.work.cleanup()
    @classmethod
    def merge(cls,output,cpu=None):
        cpu=cpu or cls.cpu
        return merger.build(cls.twelve,cpu,output,base_sha256=cls.base_sha,cpu_sha256=sha((cpu/'catalog.toon').read_bytes()))
    def rewrite(self,root):
        catalog=json.loads((root/'catalog.json').read_bytes());toon=decoder()
        for row in catalog['resources']:row['sha256']=sha((root/row['path']).read_bytes())
        catalog['files']=[dict(path=p.relative_to(root).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(root.rglob('*')) if p.is_file() and p.name not in ('catalog.toon','catalog.json')]
        (root/'catalog.toon').write_bytes((toon.dumps(catalog)+'\n').encode());(root/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def test_twelve_records_and_every_model_field_default_parameter_pending_preserved(self):
        base=validate_pool(self.twelve);merged=self.catalog
        self.assertEqual(len(merged['resources']),13);self.assertEqual(len(merged['models']),2)
        for old in base['resources']:self.assertEqual(old,next(r for r in merged['resources'] if r['id']==old['id']))
        for old in base['models']:
            new=next(m for m in merged['models'] if m['id']==old['id']);self.assertEqual(new['resources'][:-1],old['resources']);self.assertEqual(new['pending'][:len(old['pending'])],old['pending'])
            self.assertTrue(all(new.get(k)==v for k,v in old.items() if k not in ('resources','pending')))
            ref=new['resources'][-1];self.assertEqual(ref['resource'],CPU);self.assertIs(ref['enabled'],False);self.assertIs(ref['default'],False)
            self.assertEqual(len(new['pending'])-len(old['pending']),3)
    def test_no_shared_payload_or_license_duplicate_and_fixed_cpu_modes(self):
        rows={r['path']:(r['size'],r['sha256']) for item in self.catalog['resources'] for r in item['payload']['files']}
        self.assertEqual(len(rows),len(set(rows.values())));self.assertEqual(len(rows),74)
        resource=next(r for r in self.catalog['resources'] if r['id']==CPU)
        self.assertEqual(next(r['mode'] for r in resource['install']['files'] if r['target']==CONFIG),0o600)
        self.assertEqual((self.output/'licenses/project-AGPL-3.0').read_bytes(),(self.twelve/'licenses/project-AGPL-3.0').read_bytes())
        self.assertEqual(len([r for r in self.catalog['files'] if r['path']=='licenses/project-AGPL-3.0']),1)
        self.assertEqual(resource['activation'],dict(kind='blocked-unverified-service-provider-v1'))
    def test_deterministic_merge_and_inputs_are_unchanged(self):
        before=(snapshot(self.twelve),snapshot(self.cpu));repeated=self.base/'repeat';self.merge(repeated)
        self.assertEqual(snapshot(repeated),snapshot(self.output));self.assertEqual(before,(snapshot(self.twelve),snapshot(self.cpu)))
    def test_conflicting_license_rejects_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bad=root/'cpu';shutil.copytree(self.cpu,bad);(bad/'licenses/project-AGPL-3.0').write_bytes(b'conflicting license');self.rewrite(bad);validate_pool(bad)
            output=root/'rejected'
            with self.assertRaisesRegex(ValueError,'Conflicting CPU/base'):self.merge(output,bad)
            self.assertFalse(output.exists())
    def test_wrong_cpu_private_mode_rejects_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bad=root/'cpu';shutil.copytree(self.cpu,bad);catalog=json.loads((bad/'catalog.json').read_bytes());path=bad/catalog['resources'][0]['path'];toon=decoder();resource=toon.loads(path.read_text(encoding='utf-8'),strict=True)
            next(r for r in resource['install']['files'] if r['target']==CONFIG)['mode']=0o644;path.write_bytes((toon.dumps(resource)+'\n').encode());self.rewrite(bad)
            output=root/'rejected'
            with self.assertRaises(ValueError):self.merge(output,bad)
            self.assertFalse(output.exists())
    def test_existing_or_nested_output_rejects(self):
        with tempfile.TemporaryDirectory() as temporary:
            output=Path(temporary)/'existing';output.mkdir();(output/'keep').write_bytes(b'original')
            with self.assertRaises(FileExistsError):self.merge(output)
            self.assertEqual((output/'keep').read_bytes(),b'original')
        with self.assertRaises(ValueError):self.merge(self.twelve/'must-not-create')
        self.assertFalse((self.twelve/'must-not-create').exists())
    def mounted_fixture(self,root):
        roots={name:root/name for name in ('A','B')};home=root/'home';mounts=[]
        for path in [*roots.values(),home]:path.mkdir()
        if os.environ.get('BOILER_CPU_EXT4_FIXTURES')=='1':
            if os.name!='posix' or os.geteuid()!=0:raise RuntimeError('Real ext4 fixtures require root in the disposable Linux VM')
            try:
                for name,target in [*roots.items(),('home',home)]:
                    image=root/(name+'.img');assert image.resolve().is_relative_to(root.resolve())
                    with image.open('xb') as stream:stream.truncate(64*1024*1024)
                    if not stat.S_ISREG(image.stat().st_mode):raise RuntimeError('Fixture image is not an ordinary file')
                    subprocess.run(['mkfs.ext4','-q','-F',str(image)],check=True);subprocess.run(['mount','-t','ext4','-o','loop',str(image),str(target)],check=True);mounts.append(target)
                for target in roots.values():
                    (target/'home').mkdir();subprocess.run(['mount','--bind',str(home),str(target/'home')],check=True);mounts.append(target/'home')
            except BaseException:
                for target in reversed(mounts):subprocess.run(['umount',str(target)],check=True)
                raise
        return roots,home,mounts
    def test_two_models_select_and_execute_cpu_wifi_both_slots_verify_upgrade_restore(self):
        runtime=ResourcePool.for_local_test(self.output,trust_local_test=True);wifi=next(r for r in self.catalog['resources'] if r['id']==WIFI)
        facts={k:copy.deepcopy(v[0]) for k,v in CPU_FACTS.items()};facts.update({k:copy.deepcopy(v[0]) for k,v in WIFI_FACTS.items()})
        for model in self.catalog['models']:
            with self.subTest(model=model['id']),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);roots,home,mounts=self.mounted_fixture(root)
                if mounts:print('CPU_COMBINED_REAL_EXT4='+model['id'],flush=True)
                try:
                    slots={name:dict(root=target,hardware=copy.deepcopy(facts),environment=dict(architecture='x86_64',os_id='steamos',os_version='3.10',kernels=[KERNEL])) for name,target in roots.items()}
                    config=roots['A']/CONFIG;config.parent.mkdir(parents=True);config.write_bytes(b'original user config');config.chmod(0o640)
                    if os.name=='posix':os.chown(config,0,123)
                    config_meta=config.stat();preserved=roots['A']/next(r['target'] for r in wifi['install']['files'] if r['missing_only']);preserved.parent.mkdir(parents=True,exist_ok=True);preserved.write_bytes(b'OS-firmware')
                    replacement=roots['B']/next(r['target'] for r in wifi['install']['files'] if not r['missing_only']);replacement.parent.mkdir(parents=True,exist_ok=True);replacement.write_bytes(b'B-original-c106')
                    selected=[WIFI,CPU];first=installer_install(runtime,model['resources'],selected,slots=slots,shared_home=home);receipt_path=Path(first['receipt_path']);receipt_bytes=receipt_path.read_bytes()
                    maintenance_action(runtime,model['resources'],selected,slots=slots,shared_home=home,action='recover');self.assertEqual(receipt_path.read_bytes(),receipt_bytes)
                    config.write_bytes(b'edited user config');upgraded=copy.deepcopy(runtime.catalog)
                    for resource in upgraded['resources']:
                        if resource['id'] in selected:resource['version']+='+generation2'
                    newer=ResourcePool(runtime.root,upgraded,local_test=True);second=maintenance_action(newer,model['resources'],selected,slots=slots,shared_home=home,action='apply')
                    proof=maintenance_action(newer,model['resources'],selected,slots=slots,shared_home=home,action='verify');self.assertEqual(len(proof),4);self.assertTrue(all(row['ok'] for row in proof));self.assertTrue(all(row['activation_verified'] is False for row in proof if row['component']==CPU))
                    for receipt,pool,order in ((second,newer,('B','A')),(first,runtime,('A','B'))):
                        for scope in order:maintenance_action(pool,model['resources'],selected,slots=slots,shared_home=home,action='restore',receipt=receipt,scope=scope)
                    self.assertEqual(config.read_bytes(),b'edited user config');self.assertFalse((roots['B']/CONFIG).exists());self.assertEqual(preserved.read_bytes(),b'OS-firmware');self.assertEqual(replacement.read_bytes(),b'B-original-c106')
                    if os.name=='posix':self.assertEqual((config.stat().st_mode,config.stat().st_uid,config.stat().st_gid),(config_meta.st_mode,config_meta.st_uid,config_meta.st_gid))
                    for target in slots.values():target['hardware']={};target['environment']['kernels']=['changed-after-uninstall']
                    maintenance_action(runtime,model['resources'],[],slots=slots,shared_home=home,action='recover')
                finally:
                    for target in reversed(mounts):subprocess.run(['umount',str(target)],check=True)

if __name__=='__main__':unittest.main()
