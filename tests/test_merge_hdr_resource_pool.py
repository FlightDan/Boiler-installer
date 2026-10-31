"""Shared HDR, exact model parameters and real A/B/home Engine integration."""
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
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'guest'),str(ROOT/'components')]
from protocol.resource_pool import validate_pool
from protocol.hdr_contract import IDENTITY as HDR,PROFILES,FACTS
from protocol.firmware_contract import IDENTITY as WIFI,HARDWARE as WIFI_FACTS
from protocol.resource_selection import resource_catalog,select_resources
from maintenance.resource_runtime import ResourcePool,ResourceSession,component,ResourceError
from maintenance.hdr_resources import bind,install,action
from maintenance import resource_cli as cli
from resources import merge_hdr_resource_pool as merger
import resource_results as results
import test_merge_cpu_resource_pool as cpu_fixture
spec=importlib.util.spec_from_file_location('hdr_project_builder',ROOT/'scripts/prepare-resource-pool.py');builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def snapshot(root):
    # Mounted RW ext4 backing images include kernel journal/atime writeback.
    # Compare logical target files/directories; never call their disk image
    # container bytes an API-level read-only fingerprint.
    return {p.relative_to(root).as_posix():sha(p.read_bytes()) if p.is_file() else '<directory>' for p in root.rglob('*')
            if not (p.parent==root and p.name in ('A.img','B.img','home.img'))}
class HdrPoolMergeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='hdr-pool-');cls.base=Path(cls.temp.name)
        _,lock,_=builder.check_inputs(ROOT)
        c=cls.base/'components';builder.split_components.build(ROOT,c,xevma_module=ROOT/lock['xevma_module'])
        source=cls.base/'source';builder.assemble_source_pool.build(ROOT,c,source)
        wifi=cls.base/'wifi';builder.build_wifi_pool.build(ROOT,wifi)
        twelve=cls.base/'twelve';builder.merge_firmware_resource_pool.build(source,wifi,twelve,base_sha256=sha((source/'catalog.toon').read_bytes()),wifi_sha256=sha((wifi/'catalog.toon').read_bytes()))
        cpu=cls.base/'cpu';builder.cpu_builder(ROOT).build(ROOT,cpu)
        cls.thirteen=cls.base/'thirteen';builder.merge_cpu_resource_pool.build(twelve,cpu,cls.thirteen,base_sha256=sha((twelve/'catalog.toon').read_bytes()),cpu_sha256=sha((cpu/'catalog.toon').read_bytes()))
        cls.hdr=cls.base/'hdr';builder.hdr_game.build(ROOT,cls.hdr)
        cls.output=cls.base/'merged';cls.catalog=cls.merge(cls.output)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    @classmethod
    def merge(cls,output):return merger.build(cls.thirteen,cls.hdr,output,base_sha256=sha((cls.thirteen/'catalog.toon').read_bytes()),hdr_sha256=sha((cls.hdr/'catalog.toon').read_bytes()))
    def test_thirteen_records_defaults_parameters_and_unrelated_pending_preserved(self):
        old=validate_pool(self.thirteen);self.assertEqual(len(self.catalog['resources']),14)
        for row in old['resources']:self.assertEqual(row,next(r for r in self.catalog['resources'] if r['id']==row['id']))
        for model in old['models']:
            new=next(m for m in self.catalog['models'] if m['id']==model['id']);self.assertEqual(new['resources'][:-1],model['resources'])
            self.assertEqual([p for p in model['pending'] if p['component']!='hdr'],[p for p in new['pending'] if p['component'] not in ('hdr',HDR)])
            self.assertEqual(new['parameters']['hdr']['sdr_white_nits'],model['parameters']['hdr']['sdr_white_nits'])
            self.assertEqual(new['parameters']['hdr']['peak_nits'],model['parameters']['hdr']['peak_nits'])
            self.assertEqual(new['resources'][-1],dict(resource=HDR,enabled=False,default=False,parameters={'profile':model['id'].split(':')[1]}))
    def test_one_common_payload_and_no_duplicate_payload_or_mit_license(self):
        payload={r['path']:(r['sha256'],r['size']) for item in self.catalog['resources'] for r in item['payload']['files']}
        self.assertEqual(len(payload),77);self.assertEqual(len(payload),len(set(payload.values())))
        resource=next(r for r in self.catalog['resources'] if r['id']==HDR)
        previous=validate_pool(self.thirteen)
        for name in resource['license_files']:
            raw=(self.output/name).read_bytes()
            self.assertEqual(sum(r['sha256']==sha(raw) for r in self.catalog['files']),sum(r['sha256']==sha(raw) for r in previous['files']))
        self.assertEqual(sum(p.endswith('/common.lua') for p in payload),1)
    def test_merge_is_deterministic_and_keeps_inputs_unchanged(self):
        before=(snapshot(self.thirteen),snapshot(self.hdr));output=self.base/'repeat';self.merge(output)
        self.assertEqual(snapshot(output),snapshot(self.output));self.assertEqual(before,(snapshot(self.thirteen),snapshot(self.hdr)))
    def test_two_model_selection_exposes_only_validated_mapping_and_no_execution_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'cache').mkdir();shutil.copytree(self.output,root/'cache/resource-pool')
            catalog=resource_catalog(root);self.assertTrue(catalog['deployment_available'])
            for model in catalog['models']:
                choice=next(c for c in model['components'] if c['id']==HDR);p=PROFILES[model['id'].split(':')[1]]
                self.assertTrue(choice['selectable']);self.assertFalse(choice['default'])
                self.assertEqual((choice['parameter_data']['full_frame'],choice['parameter_data']['peak']),(p['full_frame'],p['peak']))
                with self.assertRaisesRegex(ValueError,'consent'):
                    select_resources(model['id'],[HDR],catalog['catalog_sha256'],root=root)
                result=select_resources(model['id'],[HDR],catalog['catalog_sha256'],trust_local_test=True,root=root)
                self.assertEqual(result['components'],[HDR]);self.assertEqual(result['trust'],'local-test')
    @unittest.skipIf(os.name=='nt','actual ext4/fcntl/UID lifecycle requires disposable Linux')
    def test_ext4_oxp3_hdr_wifi_results_readonly_and_reviewed_cli_restore(self):
        with tempfile.TemporaryDirectory(prefix='hdr-targets-') as temporary:
            work=Path(temporary);fixture=work/(cli.FIXTURE_PREFIX+uuid.uuid4().hex);fixture.mkdir(mode=0o700)
            roots,home,mounts=cpu_fixture.CpuPoolMergeTests.mounted_fixture(self,fixture)
            try:
                print('HDR_REAL_EXT4='+str(bool(mounts)),flush=True)
                marker=json.dumps(dict(kind=cli.FIXTURE_KIND,id=fixture.name.removeprefix(cli.FIXTURE_PREFIX))).encode()
                for p in (fixture,*roots.values(),home):p.chmod(0o700);(p/'.boiler-resource-fixture.json').write_bytes(marker);(p/'.boiler-resource-fixture.json').chmod(0o600)
                (fixture/'operation.lock').write_bytes(b'0');(fixture/'operation.lock').chmod(0o600)
                facts={k:v[0] for k,v in FACTS.items()};facts.update(gamescope_display_vendor='SDC',gamescope_display_model='AMS881KB01-0',hdr_panel_policy='oxp3-public')
                facts.update({k:v[0] for k,v in WIFI_FACTS.items()})
                slots={}
                for name,target in roots.items():
                    (target/'etc').mkdir();(target/'etc/passwd').write_text('deck:x:1000:1000:deck:/home/deck:/bin/bash\n')
                    (target/'home').mkdir(exist_ok=True)
                    slots[name]=dict(root=target,hardware=copy.deepcopy(facts),environment=dict(architecture='x86_64',os_id='steamos',os_version='3.10',kernels=['7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9']))
                pool=ResourcePool.for_local_test(self.output,trust_local_test=True)
                model=next(m for m in pool.catalog['models'] if m['id']=='device:oxp3');selected=[WIFI,HDR]
                wifi=pool.resource(WIFI);kept=roots['A']/next(r['target'] for r in wifi['install']['files'] if r['missing_only']);kept.parent.mkdir(parents=True,exist_ok=True);kept.write_bytes(b'OS-A-firmware')
                effective,refs=bind(pool,model['resources'],selected);session=ResourceSession(effective,slots,home)
                core=component(effective.resource(HDR),'root-slots');core.pop('hdr_profile');core.update(id='core:maintenance',files=[dict(component(effective.resource(HDR),'shared-home')['files'][0],target='usr/local/lib/boiler/core-fixture')])
                for e in session.engines.values():e.apply(core,pool.root,environment=slots['A']['environment'])
                prepared=session.prepare(refs,selected);receipt=install(pool,model['resources'],selected,slots=slots,shared_home=home)
                self.assertEqual([r['scope'] for r in receipt['transactions'] if r['component']==HDR],['shared-home'])
                results.require_matching_core(session,core)
                options=dict(plugins=['core:maintenance'],resource_selection=dict(schema_version=1,model_id=model['id'],catalog_sha256=sha((self.output/'catalog.toon').read_bytes()),trust='local-test',components=selected))
                sources={s:dict(ID='steamos',VERSION_ID='3.10',KERNELS=slots[s]['environment']['kernels']) for s in slots}
                templates={s:dict(schema_version=1,kind='InstallPlan',slot=s,components=[],maintenance={'id':'core:maintenance','version':core['version']}) for s in slots}
                rows=results.documents(options,sources,core,prepared,receipt,templates,session=session);results.publish(roots,rows)
                for row in rows.values():
                    self.assertEqual(row['components'][HDR]['hdr_profile']['peak'],1100)
                    self.assertEqual(row['components'][HDR]['transactions'],rows['A']['components'][HDR]['transactions'])
                ro=ResourceSession(effective,slots,home,read_only=True);before=snapshot(fixture)
                actual=results.receipt_readonly(ro,roots,prepared);results.verify_records(roots,results.documents(options,sources,core,prepared,actual,templates,session=ro));self.assertEqual(before,snapshot(fixture))
                action(pool,model['resources'],selected,slots=slots,shared_home=home,action='recover')
                results.verify_records(roots,results.documents(options,sources,core,prepared,results.receipt_readonly(ro,roots,prepared),templates,session=ro))
                common=dict(fixture=fixture,root=roots['A'],trust_local_test=True)
                self.assertTrue(all(r['ok'] for r in cli.verify(self.output,options['resource_selection']['catalog_sha256'],model['id'],selected,**common)['verification']))
                with self.assertRaisesRegex(ResourceError,'Restore profile'):
                    cli.plan_restore(self.output,options['resource_selection']['catalog_sha256'],'device:x2-mini',selected,group_id=receipt['id'],scope='A',**common)
                for scope in ('A','B','shared-home'):
                    review=cli.plan_restore(self.output,options['resource_selection']['catalog_sha256'],model['id'],selected,group_id=receipt['id'],scope=scope,**common)
                    cli.execute_restore(self.output,options['resource_selection']['catalog_sha256'],model['id'],selected,group_id=receipt['id'],scope=scope,plan=review['plan'],reviewed_sha256=review['reviewed_sha256'],confirm_fixture_restore=True,**common)
                self.assertEqual(kept.read_bytes(),b'OS-A-firmware');self.assertFalse((home/'deck/.config/gamescope/scripts/98-boiler-gamma22.lua').exists())
                with self.assertRaisesRegex(ResourceError,'generation'):results.receipt_readonly(ro,roots,prepared)
                x2=next(m for m in pool.catalog['models'] if m['id']=='device:x2-mini');before=snapshot(fixture)
                with self.assertRaisesRegex(ResourceError,'panel policy not established'):install(pool,x2['resources'],[HDR],slots=slots,shared_home=home)
                self.assertEqual(before,snapshot(fixture))
            finally:
                for p in reversed(mounts):subprocess.run(['umount',str(p)],check=True)
if __name__=='__main__':unittest.main()
