"""Abrupt child-process death against real temporary Engine journals."""
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from maintenance import resource_reinstall as reinstall, resource_lifecycle as lifecycle, resource_firstboot as firstboot
from maintenance.resource_runtime import ResourcePool,ResourceSession,ResourceError
from maintenance.engine import Engine,_json_write

ID='resource:sample'
GROUP='a'*32

def fixture(base,create=False,count=1,dependencies=False):
    pool=base/'pool';home=base/'home';slots={}
    if create:pool.mkdir();home.mkdir();(pool/'body').write_bytes(b'new file\n')
    data=b'new file\n';entry=dict(path='body',size=len(data),sha256=hashlib.sha256(data).hexdigest())
    resource=dict(id=ID,version='1.0.0',dependencies=[],compatibility={},hardware_requires={},payload={'files':[entry]},
        install=dict(kind='files-v1',files=[dict(source='body',target='etc/managed',scope='root-slots',mode=420),
            dict(source='body',target='home/deck/shared',scope='shared-home',mode=420)]),
        verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
    for slot in ('A','B'):
        root=base/slot
        if create:
            root.mkdir();(root/'etc').mkdir();(root/'home').mkdir();(root/'var').mkdir();(root/'etc/managed').write_bytes(b'old '+slot.encode())
            (root/'etc/passwd').write_text('deck:x:1000:1000::/home/deck:/bin/sh\n')
            (root/'etc/group').write_text('deck:x:1000:\n')
        slots[slot]=dict(root=root,environment={},hardware={},opt_offload=False)
    resources=[]
    for index in range(count):
        row=copy.deepcopy(resource);row['id']=ID if index==0 else ID+'-'+str(index)
        if index:
            for entry in row['install']['files']:entry['target']+='-'+str(index)
            if dependencies:row['dependencies']=[resources[-1]['id']]
        resources.append(row)
    return ResourceSession(ResourcePool(pool,{'resources':resources},local_test=True),slots,home,offline_image=True),[{'resource':row['id']} for row in resources]

def child(base,point):
    session,refs=fixture(base)
    import maintenance.engine as eng
    original=eng._json_write
    def write(path,value):
        original(path,value)
        if (point=='scope-applying' and value.get('status')=='applying' and value.get('component',{}).get('resource_group')==GROUP
                or point=='scope-committed-before-receipt' and value.get('status')=='committed' and value.get('component',{}).get('resource_group')==GROUP
                or point=='plan-published' and value.get('id')==GROUP and value.get('status')=='prepared'):
            os._exit(91)
    eng._json_write=write;reinstall._json_write=write
    session.install(refs,[ID],group=GROUP)

def lifecycle_fixture(base,create=False,count=1,dependencies=False):
    session,refs=fixture(base,create,count,dependencies)
    if create:
        identities=[row['resource'] for row in refs]
        receipt=session.install(refs,identities)
        for scope in ('A','B','shared-home'):session.restore(receipt,scope)
        for slot,entry in session.slots.items():
            root=entry['root']
            intent=dict(kind=firstboot.KIND,schema_version=1,mode='firstboot-install-v1',generation='c'*32,slot=slot,
                root_partuuid=slot*36,selection=dict(trust='local-test',components=identities),resource_receipt=receipt,target_roles={'synthetic':'fixture-only'})
            payload=base/('bootstrap-'+slot);payload.mkdir();data=(json.dumps(intent,sort_keys=True,separators=(',',':'))+'\n').encode()
            (payload/'intent').write_bytes(data);(payload/'unit').write_bytes(b'fixed bootstrap\n')
            manifest=dict(schema_version=1,kind='ComponentManifest',id='core:resource-firstboot',version='1.0.0',compatibility={},requires=[],conflicts=[],files=[
                dict(source='intent',target=firstboot.INTENT,size=len(data),sha256=hashlib.sha256(data).hexdigest(),mode=384),
                dict(source='unit',target='etc/fixed-bootstrap',size=16,sha256=hashlib.sha256(b'fixed bootstrap\n').hexdigest(),mode=420)])
            Engine(root).apply(manifest,payload)
            state=firstboot._new_state(intent)
            for identity in identities:state['resources'][identity]=dict(status='restored',original='retain-me')
            _json_write(root/firstboot.STATE,state)
    mounts=SimpleNamespace(roots={s:v['root'] for s,v in session.slots.items()},home=session.home,runtime=contextlib.ExitStack())
    def get_session(*args,readonly):
        return ResourceSession(session.pool,session.slots,session.home,offline_image=True,read_only=readonly),refs
    return session,mounts,get_session

def lifecycle_child(base,point):
    session,mounts,get_session=lifecycle_fixture(base)
    action=json.loads((base/'retry-action.json').read_text())
    import maintenance.engine as eng
    original=eng._json_write
    def write(path,value):
        original(path,value)
        if (point=='installing' and Path(path)==base/'retry-action.json' and value.get('phase')=='installing'
                or point=='bootstrap-applying' and value.get('status')=='applying' and value.get('component',{}).get('resource_retry')==action['token']
                or point=='bootstrap-A-committed' and value.get('status')=='committed' and value.get('component',{}).get('resource_retry')==action['token'] and value.get('root')==session.engines['A'].identity
                or point=='slot-A-state' and Path(path)==mounts.roots['A']/firstboot.STATE and value.get('resources',{}).get(ID,{}).get('retry_token')==action['token']
                or point=='closing-A-state' and Path(path)==mounts.roots['A']/firstboot.STATE and value.get('maintenance_retry',{}).get('status')=='closed'
                or point=='closing-shared' and Path(path)==mounts.home/'.boiler/resource-maintenance/lifecycle-action.json' and value.get('status')=='closed'):
            os._exit(92)
    eng._json_write=write;reinstall._json_write=write;lifecycle._json_write=write
    with patch.object(lifecycle,'_session',get_session):
        lifecycle._continue_retry(firstboot.checked_intent(mounts.roots['A']),action,mounts,lambda value:write(base/'retry-action.json',value))

class GroupResumeTests(unittest.TestCase):
    def test_real_process_kill_resumes_same_group_without_duplicate_commits(self):
        for point in ('plan-published','scope-applying','scope-committed-before-receipt'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);session,refs=fixture(base,True)
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'child',str(base),point],capture_output=True,text=True)
                self.assertEqual(result.returncode,91,result.stderr)
                receipt=session.install(refs,[ID],group=GROUP)
                self.assertEqual(receipt['status'],'committed');self.assertEqual(len(receipt['transactions']),3)
                self.assertNotIn('reinstall_plan',receipt);self.assertEqual(len(receipt['plan_sha256']),64)
                self.assertTrue(all(v['ok'] for v in session.verify([ID])))
                again=session.install(refs,[ID],group=GROUP);self.assertEqual(receipt,again)
                for scope,engine in reinstall.engines(session).items():
                    committed=[json.loads(p.read_text()) for p in engine.journals.glob('*.json') if json.loads(p.read_text()).get('status')=='committed']
                    self.assertEqual(len(committed),1)
    def test_tampered_separate_plan_blocks_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,refs=fixture(base,True)
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'child',str(base),'plan-published'],capture_output=True,text=True)
            self.assertEqual(result.returncode,91,result.stderr)
            path=session.shared.state_dir/'resource-actions'/(GROUP+'.plan')
            plan=json.loads(path.read_text());plan['binding']['selected']=[];_json_write(path,plan)
            before={str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ResourceError,'binding changed'):session.install(refs,[ID],group=GROUP)
            self.assertEqual(before,{str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()})
    def test_foreign_later_scope_pending_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,refs=fixture(base,True)
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'child',str(base),'scope-applying'],capture_output=True,text=True)
            self.assertEqual(result.returncode,91,result.stderr)
            engine=session.engines['B'];engine.journals.mkdir(parents=True,exist_ok=True)
            _json_write(engine._journal('b'*32),dict(id='b'*32,root=engine.identity,status='applying',component={'id':'resource:foreign'},operations=[]))
            before={str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ResourceError,'Unrelated pending'):session.install(refs,[ID],group=GROUP)
            self.assertEqual(before,{str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()})

class LifecycleResumeTests(unittest.TestCase):
    def test_dependency_closure_retries_with_one_group(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,mounts,get_session=lifecycle_fixture(base,True,count=3,dependencies=True)
            intent=firstboot.checked_intent(mounts.roots['A'])
            with patch.object(lifecycle,'_session',get_session):
                action=lifecycle._prepare_retry(intent,firstboot.read_private(mounts.roots['A']/firstboot.STATE),mounts)
                done=lifecycle._continue_retry(intent,action,mounts,lambda value:_json_write(base/'retry-action.json',value))
            self.assertEqual(done['closure'],[ID,ID+'-1',ID+'-2'])
            self.assertEqual(len(done['receipt']['transactions']),9)
            self.assertTrue(all(row['ok'] for row in session.verify(done['closure'])))

    def test_provider_only_retry_publishes_both_slots_without_file_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,mounts,get_session=lifecycle_fixture(base,True)
            intent=firstboot.checked_intent(mounts.roots['A'])
            with patch.object(lifecycle,'_session',get_session):
                action=lifecycle._prepare_retry(intent,firstboot.read_private(mounts.roots['A']/firstboot.STATE),mounts)
                action.update(closure=[],inputs=[ID],original_receipts={},jobs={})
                with patch.object(type(session),'install',side_effect=AssertionError('Provider-only retry must not install files')):
                    done=lifecycle._continue_retry(intent,action,mounts,lambda value:_json_write(base/'retry-action.json',value))
            self.assertNotIn('receipt',done)
            for root in mounts.roots.values():
                state=firstboot.read_private(root/firstboot.STATE)
                self.assertEqual(state['resources'][ID]['runtime'],'pending_firstboot')
                self.assertEqual((root/'etc/managed').read_bytes(),b'old '+root.name.encode())

    def test_full_twentyfour_retry_uses_bounded_runtime_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,mounts,get_session=lifecycle_fixture(base,True,count=24)
            intent=firstboot.checked_intent(mounts.roots['A'])
            with patch.object(lifecycle,'_session',get_session):
                action=lifecycle._prepare_retry(intent,firstboot.read_private(mounts.roots['A']/firstboot.STATE),mounts)
                # Original diagnostics may be large without growing the runtime state.
                for slot in mounts.roots:action['original_resources'][slot][ID]['diagnostics']='x'*600000
                owner=base/'live-state.json'
                done=lifecycle._continue_retry(intent,action,mounts,lambda value:_json_write(owner,{'maintenance_retry':lifecycle.retry_documents.compact(value,owner)}))
            self.assertEqual(done['status'],'closed')
            self.assertEqual(len(done['receipt']['resources']),24)
            for root in mounts.roots.values():
                state=firstboot.read_private(root/firstboot.STATE)
                self.assertEqual(len(state['resources']),24)
                self.assertLess((root/firstboot.STATE).stat().st_size,100000)
                document=lifecycle.retry_documents.hydrate(state['maintenance_retry'],root/firstboot.STATE)
                self.assertEqual(len(document['original_resources']['A'][ID]['diagnostics']),600000)
                self.assertTrue(Engine(root,read_only=True).verify_readonly('core:resource-firstboot')['ok'])

    def test_real_kill_across_bootstrap_and_state_slot_boundaries(self):
        for point in ('installing','bootstrap-applying','bootstrap-A-committed','slot-A-state','closing-A-state','closing-shared'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);session,mounts,get_session=lifecycle_fixture(base,True)
                intent=firstboot.checked_intent(mounts.roots['A'])
                with patch.object(lifecycle,'_session',get_session):
                    action=lifecycle._prepare_retry(intent,firstboot.read_private(mounts.roots['A']/firstboot.STATE),mounts)
                _json_write(base/'retry-action.json',action)
                result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'lifecycle-child',str(base),point],capture_output=True,text=True)
                self.assertEqual(result.returncode,92,result.stderr)
                action=firstboot.read_private(base/'retry-action.json')
                with patch.object(lifecycle,'_session',get_session):
                    done=lifecycle._continue_retry(firstboot.checked_intent(mounts.roots['A']),action,mounts,lambda value:_json_write(base/'retry-action.json',value))
                self.assertEqual(done['status'],'closed')
                for slot,root in mounts.roots.items():
                    state=firstboot.read_private(root/firstboot.STATE)
                    self.assertEqual(state['resources'][ID]['status'],'pending')
                    prior=state['resources'][ID]['previous_restore']
                    document=lifecycle.retry_documents.hydrate(prior['document'],root/firstboot.STATE)
                    self.assertEqual(document['original_resources'][prior['slot']][prior['identity']]['original'],'retain-me')
                    self.assertEqual(state['maintenance_retry']['status'],'closed')
                    self.assertEqual(firstboot.checked_intent(root)['resource_receipts'][ID]['id'],action['group'])
                    self.assertTrue(Engine(root,read_only=True).verify_readonly('core:resource-firstboot')['ok'])
                    commits=[json.loads(p.read_text()) for p in session.engines[slot].journals.glob('*.json')
                        if json.loads(p.read_text()).get('status')=='committed' and json.loads(p.read_text()).get('component',{}).get('resource_retry')==action['token']]
                    self.assertEqual(len(commits),1)
                self.assertEqual(firstboot.read_private(mounts.home/'.boiler/resource-maintenance/lifecycle-action.json')['status'],'closed')
    def test_bootstrap_resume_refuses_foreign_shared_pending_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,mounts,get_session=lifecycle_fixture(base,True)
            intent=firstboot.checked_intent(mounts.roots['A'])
            with patch.object(lifecycle,'_session',get_session):
                action=lifecycle._prepare_retry(intent,firstboot.read_private(mounts.roots['A']/firstboot.STATE),mounts)
            _json_write(base/'retry-action.json',action)
            result=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'lifecycle-child',str(base),'bootstrap-A-committed'],capture_output=True,text=True)
            self.assertEqual(result.returncode,92,result.stderr)
            _json_write(session.shared._journal('e'*32),dict(id='e'*32,root=session.shared.identity,status='applying',component={'id':'resource:foreign'},operations=[]))
            action=firstboot.read_private(base/'retry-action.json')
            before={str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
            with patch.object(lifecycle,'_session',get_session):
                with self.assertRaisesRegex(ResourceError,'Unrelated pending'):
                    lifecycle._continue_retry(intent,action,mounts,lambda value:_json_write(base/'retry-action.json',value))
            self.assertEqual(before,{str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()})
    def test_committed_external_edit_does_not_reapply_or_replace_before(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);session,refs=fixture(base,True);session.install(refs,[ID],group=GROUP)
            (base/'A/etc/managed').write_bytes(b'external')
            before={str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()}
            with self.assertRaisesRegex(ResourceError,'Committed reinstall files changed'):session.install(refs,[ID],group=GROUP)
            self.assertEqual(before,{str(p):p.read_bytes() for p in base.rglob('*') if p.is_file()})

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='child':child(Path(sys.argv[2]),sys.argv[3])
    elif len(sys.argv)>1 and sys.argv[1]=='lifecycle-child':lifecycle_child(Path(sys.argv[2]),sys.argv[3])
    else:unittest.main()
