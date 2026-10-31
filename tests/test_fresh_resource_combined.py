import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import subprocess

HERE=Path(__file__).resolve().parents[1];SRC=HERE
sys.path[:0]=[str(SRC),str(SRC/'guest'),str(SRC/'components'),str(HERE)]
from protocol.resource_pool import decoder,validate_pool
from maintenance.resource_runtime import ResourcePool,ResourceError,ResourceSession
from maintenance.engine import Engine,MaintenanceError
from protocol.manifest import ProtocolError
from fresh_resource_install import preflight,install,inspect_install
import prepare_payload

def sha(b):return hashlib.sha256(b).hexdigest()
def snapshot(root):return {p.relative_to(root).as_posix():sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}

def pool_at(root,targets=('etc/fresh-resource.conf',)):
    root.mkdir();data=b'fixture-only file data\n';source='payload/fixture.txt';p=root/source;p.parent.mkdir();p.write_bytes(data)
    license=root/'LICENSE';license.write_bytes(b'Fixture only; not a hardware resource.\n')
    entries=[dict(path=source,size=len(data),sha256=sha(data)),dict(path='LICENSE',size=license.stat().st_size,sha256=sha(license.read_bytes()))]
    resource=dict(schema_version=1,kind='BoilerResource',id='resource:fresh-fixture',version='1.0.0',
        dependencies=[],hardware_requires={},compatibility={},payload={'files':[entries[0]]},license_files=['LICENSE'],
        install=dict(kind='files-v1',files=[dict(source=source,target=t,mode=420,scope='shared-home' if t.startswith('home/') else 'root-slots') for t in targets]),
        verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'},synthetic_lifecycle_only=True)
    toon=decoder();rp=root/'resource.toon';rp.write_text(toon.dumps(resource)+'\n')
    model=dict(schema_version=1,kind='BoilerResourceModel',id='device:fixture',resources=[dict(resource=resource['id'],parameters={})]);mp=root/'model.toon';mp.write_text(toon.dumps(model)+'\n')
    entries += [dict(path=p.name,size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in (rp,mp)]
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,
        resources=[dict(id=resource['id'],version=resource['version'],path=rp.name,sha256=sha(rp.read_bytes()))],
        models=[dict(id=model['id'],path=mp.name,sha256=sha(mp.read_bytes()))],files=entries)
    (root/'catalog.toon').write_text(toon.dumps(catalog)+'\n');(root/'catalog.json').write_text(json.dumps(catalog)+'\n')
    return ResourcePool.for_local_test(root,trust_local_test=True),model['resources'],[resource['id']]

class FixtureAuthority:
    mode='explicit-file-fixture'
    def __init__(self):self.activated=False
    def revalidate(self,plan):pass
    def activate(self,plan):self.activated=True

class FreshInstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='fresh-real-core-');cls.base=Path(cls.tmp.name)
        cls.pool,cls.refs,cls.selected=pool_at(cls.base/'pool')
        cls.payload=cls.base/'payload';prepare_payload.build(cls.pool.root,cls.payload)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='fresh-target-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.home=self.root/'home';self.home.mkdir();self.slots={}
        for name in ('A','B'):
            p=self.root/name;p.mkdir();(p/'var').mkdir();(p/'etc').mkdir();(p/'opt').mkdir();(p/'home').mkdir()
            self.slots[name]=dict(root=p,environment={},hardware={})
    def plan(self,pool=None):return preflight(self.payload,pool or self.pool,self.refs,self.selected,slots=self.slots,shared_home=self.home)
    def test_real_core_and_pool_payload_rebuilt_from_current_source(self):
        manifest=json.loads((self.payload/'integration/manifest.json').read_bytes());files=manifest['components']['core:maintenance']['component']['files']
        self.assertTrue(any(r['target']=='usr/local/lib/boiler/maintenance/engine.py' for r in files))
        self.assertTrue(any(r['target']=='usr/local/lib/boiler/cache/resource-pool/catalog.toon' for r in files))
        self.assertEqual(len(list(self.payload.rglob('catalog.toon'))),1)
    def test_missing_session_pool_source_rejects_before_core_write(self):
        import shutil
        bad=self.root/'bad-payload';shutil.copytree(self.payload,bad)
        (bad/'integration/resource-pool/catalog.toon').unlink();before=snapshot(self.root)
        with self.assertRaises((OSError,ValueError)):
            preflight(bad,self.pool,self.refs,self.selected,slots=self.slots,shared_home=self.home)
        self.assertEqual(before,snapshot(self.root))
    def test_readonly_plan_and_default_authority_are_write_free(self):
        before=snapshot(self.root);p=self.plan();self.assertEqual(before,snapshot(self.root))
        with self.assertRaisesRegex(ResourceError,'authenticated source/target lease'):install(p)
        self.assertEqual(before,snapshot(self.root))
    def test_fresh_core_both_slots_then_real_resource_session(self):
        p=self.plan();authority=FixtureAuthority()
        @contextlib.contextmanager
        def grant(plan):yield authority
        result=install(p,authorize=grant);self.assertTrue(authority.activated);self.assertEqual(result['status'],'committed')
        self.assertEqual({r['scope'] for r in result['core']},{'A','B'})
        for slot in self.slots.values():
            self.assertTrue(Engine(slot['root'],read_only=True).verify_readonly('core:maintenance')['ok'])
            self.assertEqual((slot['root']/'etc/fresh-resource.conf').read_bytes(),b'fixture-only file data\n')
        before=snapshot(self.root);inspection=inspect_install(result['receipt_path'],slots=self.slots,shared_home=self.home,pool=self.pool)
        self.assertEqual(inspection['status'],'files-verified-readonly');self.assertEqual(before,snapshot(self.root))
        # The dependency must be enforced by the real Engine, not only the
        # coordinator's in-memory graph or a UI ordering convention.
        for operation in result['core']:
            before=snapshot(self.root)
            with self.assertRaisesRegex(MaintenanceError,'still requires'):
                Engine(self.slots[operation['scope']]['root']).restore(operation['transaction'])
            self.assertEqual(before,snapshot(self.root))
        engine=Engine(self.slots['A']['root']);state=engine._state()
        installed=state['components'][self.selected[0]]
        journal=json.loads(engine._journal(installed['transaction']).read_bytes())
        self.assertIn('core:maintenance',journal['component']['requires'])
        stripped=copy.deepcopy(journal['component']);stripped.pop('core_retention');stripped['requires']=[]
        before=snapshot(self.root)
        with self.assertRaisesRegex(MaintenanceError,'cannot be dropped'):
            engine.apply(stripped,self.pool.root)
        self.assertEqual(before,snapshot(self.root))
        # A missing home cannot silently redirect the shared guard to a new
        # namespace while removing a retained core.
        binding=engine.home_root/'.boiler/core-retention/identity.json';saved=binding.read_bytes();binding.unlink()
        before=snapshot(self.root)
        with self.assertRaisesRegex(MaintenanceError,'home binding is unavailable'):
            engine.restore(result['core'][0]['transaction'])
        self.assertEqual(before,snapshot(self.root));binding.write_bytes(saved);binding.chmod(0o600)
        from maintenance.core_retention import decorate
        from maintenance.resource_runtime import component
        manifest=decorate(component(self.pool.resource(self.selected[0]),'root-slots'),shared=True)
        manifest['requires']=[];manifest['files'][0]['target']='duplicate-slot-must-not-write'
        for roots,error in (([engine.root,engine.root],'distinct physical roots'),
                            ([engine.root,engine.home_root],'distinct physical roots'),
                            ([engine.root,self.slots['B']['root']],'physical home differs')):
            shared=Engine(engine.home_root,engine.home_root/'.boiler/resource-maintenance',target_domain='shared-home',account_roots=roots)
            before_journals=list(shared.journals.glob('*.json'))
            with self.assertRaisesRegex(MaintenanceError,error):shared.apply(manifest,self.pool.root)
            self.assertFalse((engine.home_root/'duplicate-slot-must-not-write').exists())
            self.assertEqual(before_journals,list(shared.journals.glob('*.json')))
        session=ResourceSession(self.pool,self.slots,self.home)
        for scope in ('A','B','shared-home'):session.restore(result['resource_receipt'],scope)
        for operation in reversed(result['core']):Engine(self.slots[operation['scope']]['root']).restore(operation['transaction'])
        for slot in self.slots.values():
            self.assertNotIn('core:maintenance',Engine(slot['root'],read_only=True)._state()['components'])
            self.assertFalse((slot['root']/'etc/fresh-resource.conf').exists())
        before=snapshot(self.root);self.assertEqual(inspect_install(result['receipt_path'],slots=self.slots,shared_home=self.home,pool=self.pool)['status'],'needs-explicit-owned-journal-review');self.assertEqual(before,snapshot(self.root))
    def test_missing_external_dependency_precedes_every_write(self):
        catalog=copy.deepcopy(self.pool.catalog);catalog['resources'][0]['external_component_dependencies']=['core:decky'];pool=ResourcePool(self.pool.root,catalog,local_test=True);before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'Missing unified dependency'):self.plan(pool)
        self.assertEqual(before,snapshot(self.root))
    def test_hardware_unknown_is_never_fabricated(self):
        catalog=copy.deepcopy(self.pool.catalog);catalog['resources'][0]['hardware_requires']={'physical_panel_ready':[True]};before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'Hardware requirement'):self.plan(ResourcePool(self.pool.root,catalog,local_test=True))
        self.assertEqual(before,snapshot(self.root))
    def test_physical_domain_alias_is_write_free(self):
        self.slots['B']['root']=self.slots['A']['root'];before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'physical domains alias'):self.plan()
        self.assertEqual(before,snapshot(self.root))
    def test_changed_review_does_not_activate_views(self):
        p=self.plan();p.slots['A']['environment']['changed-after-review']='yes';authority=FixtureAuthority();before=snapshot(self.root)
        @contextlib.contextmanager
        def grant(plan):yield authority
        with self.assertRaisesRegex(ResourceError,'review changed'):install(p,authorize=grant)
        self.assertFalse(authority.activated);self.assertEqual(before,snapshot(self.root))
    def test_real_kill_after_core_commit_is_reported_not_replayed(self):
        code='''import contextlib,json,os,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root),str(root/'guest'),str(root/'components')]
from maintenance.engine import Engine
from maintenance.resource_runtime import ResourcePool
from fresh_resource_install import preflight,install
slots=json.loads(sys.argv[4]);pool=ResourcePool.for_local_test(sys.argv[2],trust_local_test=True)
original=Engine.apply
def killed(self,component,*args,**kwargs):
 result=original(self,component,*args,**kwargs)
 if component['id']=='core:maintenance':os._exit(73)
 return result
Engine.apply=killed
class Owner:
 mode='explicit-file-fixture'
 def revalidate(self,plan):pass
 def activate(self,plan):pass
@contextlib.contextmanager
def grant(plan):yield Owner()
plan=preflight(sys.argv[3],pool,[dict(resource='resource:fresh-fixture',parameters={})],['resource:fresh-fixture'],slots=slots,shared_home=sys.argv[5])
install(plan,authorize=grant)
'''
        slots={s:dict(t,root=str(t['root'])) for s,t in self.slots.items()}
        run=subprocess.run([sys.executable,'-B','-c',code,str(SRC),str(self.pool.root),str(self.payload),json.dumps(slots),str(self.home)],capture_output=True,text=True)
        self.assertEqual(run.returncode,73,run.stderr)
        paths=list((self.home/'.boiler/fresh-resource-installs').glob('*.json'));self.assertEqual(len(paths),1)
        before=snapshot(self.root);result=inspect_install(paths[0],slots=self.slots,shared_home=self.home,pool=self.pool)
        self.assertEqual(result['status'],'needs-explicit-owned-journal-review');self.assertFalse(result['automatic_resume_available'])
        self.assertTrue(next(row for row in result['core'] if row['slot']=='A')['observed_transaction'])
        self.assertFalse((self.slots['B']['root']/'usr/local/bin/boiler-maintenance').exists());self.assertEqual(before,snapshot(self.root))
    def test_generic_resource_conflict_precedes_core_write(self):
        catalog=copy.deepcopy(self.pool.catalog);catalog['resources'][0]['install']['files'][0]['target']='usr/local/bin/boiler-maintenance';before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'Unified physical target conflict'):self.plan(ResourcePool(self.pool.root,catalog,local_test=True))
        self.assertEqual(before,snapshot(self.root))
    def test_official_missing_etc_and_opt_are_readonly_plannable(self):
        for slot in self.slots.values():
            d=slot['root']/'usr/lib/systemd/system';d.mkdir(parents=True)
            (d/'etc.mount').write_text('Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work\n')
            (d/'opt.mount').write_text('What=/home/.steamos/offload/opt\nWhere=/opt\nOptions=bind\n')
        before=snapshot(self.root);p=self.plan();self.assertTrue(all(v['fresh_etc'] and v['fresh_opt'] for v in p.policies.values()));self.assertEqual(before,snapshot(self.root))
        self.assertFalse((self.home/'.steamos').exists())
    def test_missing_upper_with_nonempty_work_rejects_before_writes(self):
        for slot in self.slots.values():
            d=slot['root']/'usr/lib/systemd/system';d.mkdir(parents=True)
            (d/'etc.mount').write_text('Where=/etc\nWhat=overlay\nType=overlay\nOptions=lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper,workdir=/var/lib/overlays/etc/work\n')
        work=self.slots['B']['root']/'var/lib/overlays/etc/work';work.mkdir(parents=True);(work/'preserve').write_bytes(b'unclean state')
        before=snapshot(self.root)
        with self.assertRaisesRegex(ResourceError,'nonempty work'):self.plan()
        self.assertFalse((work.parent/'upper').exists());self.assertEqual(before,snapshot(self.root))
    @unittest.skipIf(os.name=='nt','Native image UID/mode validation requires Linux')
    def test_missing_offload_checks_existing_ancestor_permissions(self):
        for slot in self.slots.values():
            d=slot['root']/'usr/lib/systemd/system';d.mkdir(parents=True)
            (d/'opt.mount').write_text('What=/home/.steamos/offload/opt\nWhere=/opt\nOptions=bind\n')
        ancestor=self.home/'.steamos';ancestor.mkdir();ancestor.chmod(0o1777);before=snapshot(self.root)
        try:
            with self.assertRaisesRegex(ResourceError,'mount backing ancestor'):self.plan()
            self.assertFalse((ancestor/'offload').exists());self.assertEqual(before,snapshot(self.root))
        finally:ancestor.chmod(0o700)
    def test_shared_opt_alias_conflict_precedes_every_write(self):
        for slot in self.slots.values():
            d=slot['root']/'usr/lib/systemd/system';d.mkdir(parents=True)
            (d/'opt.mount').write_text('What=/home/.steamos/offload/opt\nWhere=/opt\nOptions=bind\n')
        catalog=copy.deepcopy(self.pool.catalog);r=catalog['resources'][0];src=r['install']['files'][0]['source']
        r['install']['files']=[dict(source=src,target='opt/fixture/data',scope='root-slots',mode=420),dict(source=src,target='home/.steamos/offload/opt/fixture/data',scope='shared-home',mode=420)]
        before=snapshot(self.root)
        with self.assertRaisesRegex((ResourceError,ProtocolError),'overlapping targets|Unified physical target|Duplicate target'):self.plan(ResourcePool(self.pool.root,catalog,local_test=True))
        self.assertEqual(before,snapshot(self.root))

if __name__=='__main__':unittest.main()
