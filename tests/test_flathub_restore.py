import contextlib,copy,hashlib,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from maintenance import flathub_restore as restore
from maintenance import resource_firstboot as firstboot,resource_lifecycle as lifecycle
from maintenance.resource_runtime import ResourcePool,ResourceSession
from protocol.flathub_contract import IDENTITY,TARGET,KIND
from maintenance.engine import _json_write


class Backend:
    def __init__(self,current):self.current=current
    def observe(self,scope):return copy.deepcopy(self.current[scope])


class FlathubRestorePlanTests(unittest.TestCase):
    def test_foreign_or_changed_gate_blocks_document_publication(self):
        from maintenance import flathub_restore_docs as docs
        intent={'generation':'a'*32,'slot':'A','selection':{'components':[IDENTITY]},
                'target_roles':{'rootfs-A':{'partuuid':'a'},'rootfs-B':{'partuuid':'b'}}}
        for gate_value,message in (({'generation':'f'*32,'identity':IDENTITY},'another installation'),
                                   ({'generation':'a'*32,'identity':IDENTITY},'changed after review')):
            with self.subTest(gate_value=gate_value),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);roots={s:root/s for s in ('A','B')}
                states={s:dict(kind='BoilerResourceFirstbootState',generation='a'*32,slot=s,root_partuuid=s.lower(),resources={}) for s in roots}
                for slot,path in roots.items():
                    path.mkdir();_path=path/firstboot.STATE;_path.parent.mkdir(parents=True);_json_write(_path,states[slot])
                gate=roots['B']/'var/lib/boiler-resource-approved/common-flathub-mirror.json';gate.parent.mkdir(parents=True);_json_write(gate,gate_value)
                plan={'slots':{s:{'state_sha256':lifecycle.digest(states[s]),'gate_sha256':lifecycle.digest(None)} for s in roots}}
                def checked(path):
                    slot=Path(path).name;return dict(intent,slot=slot,root_partuuid=slot.lower())
                with patch.object(firstboot,'checked_intent',side_effect=checked),patch.object(firstboot,'validate_restore_binding'), \
                     patch.object(docs,'_directory',side_effect=AssertionError('document directory created')), \
                     patch.object(docs,'_json_write',side_effect=AssertionError('document written')):
                    with self.assertRaisesRegex(RuntimeError,message):docs.create(intent,plan,'review',SimpleNamespace(roots=roots),root/'home/marker')

    def test_plan_binds_both_systems_and_reports_shared_user_edit(self):
        initial={'exists':True,'url':'https://mirror/','key_digest':'key','configuration_digest':'cfg','configuration':{}}
        edited=dict(initial,url='https://user-change/')
        live=Backend({'system':initial,'user':edited});inactive=Backend({'system':initial,'user':edited})
        row={'phase':'applied','after':initial}
        plan={'slots':{slot:{'activation':{'status':'configured','activation_recovery':{
            'generation':'a'*32,'slot':slot,'rows':{'system':row}}}} for slot in ('A','B')}}
        intent={'generation':'a'*32,'slot':'A'};mounts=SimpleNamespace(roots={'A':Path('/a'),'B':Path('/b')})
        shared={'ledger':{'generation':'a'*32,'status':'active','rows':{'user':row}},'remote':edited}
        store=SimpleNamespace(snapshot=lambda backend:shared)
        with patch.object(restore.flathub_activation,'_backend',return_value=live), \
             patch.object(restore.flathub_activation,'offline_backend',return_value=contextlib.nullcontext(inactive)), \
             patch.object(restore.flathub_user_store,'Store',return_value=store):
            value=restore.attach(plan,intent,mounts,SimpleNamespace(pool=object()))
        self.assertTrue(value['flathub']['shared_user_changed'])
        self.assertEqual(set(value['flathub']['system_observations']),{'A','B'})

    def test_changed_inactive_system_refuses_before_restore(self):
        original={'exists':True,'url':'https://mirror/','key_digest':'key','configuration_digest':'cfg','configuration':{}}
        changed=dict(original,url='https://other/')
        live=Backend({'system':original,'user':original});inactive=Backend({'system':changed,'user':original})
        plan={'slots':{s:{'activation':{'status':'configured','activation_recovery':{
            'generation':'a'*32,'slot':s,'rows':{'system':{'phase':'applied','after':original}}}}} for s in ('A','B')}}
        with patch.object(restore.flathub_activation,'_backend',return_value=live), \
             patch.object(restore.flathub_activation,'offline_backend',return_value=contextlib.nullcontext(inactive)):
            with self.assertRaisesRegex(RuntimeError,'system remote changed'):
                restore.attach(plan,{'generation':'a'*32,'slot':'A'},SimpleNamespace(roots={'B':Path('/b')}),SimpleNamespace(pool=object()))

    def test_two_slot_remote_and_real_file_journals_resume_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);payload=root/'pool';payload.mkdir()
            raw=(Path(__file__).resolve().parent/'fixtures/flathub.flatpakrepo').read_bytes()
            (payload/'definition').write_bytes(raw)
            resource=dict(id=IDENTITY,version='1.0.0',dependencies=[],external_component_dependencies=[],hardware_requires={},compatibility={},
                payload={'files':[dict(path='definition',size=len(raw),sha256=hashlib.sha256(raw).hexdigest())]},
                install={'kind':KIND,'files':[dict(source='definition',target=TARGET,mode=420,scope='root-slots')]},
                verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'})
            pool=ResourcePool(payload,{'resources':[resource]},local_test=True);slots={};roots={}
            for slot in ('A','B'):
                target=root/slot;target.mkdir();(target/'home').mkdir();(target/'var').mkdir();roots[slot]=target
                slots[slot]=dict(root=target,environment={},hardware={},opt_offload=False)
            home=root/'home';home.mkdir();session=ResourceSession(pool,slots,home,offline_image=True)
            receipt=session.install([{'resource':IDENTITY}],[IDENTITY])
            roles={s:{'partuuid':s.lower()*36} for s in ('A','B')}
            target_roles={'rootfs-'+s:roles[s] for s in roles}
            intent={'generation':'a'*32,'slot':'A','root_partuuid':roles['A']['partuuid'],
                    'selection':{'components':[IDENTITY]},'target_roles':target_roles,'resource_receipt':receipt}
            intents={s:dict(intent,slot=s,root_partuuid=roles[s]['partuuid']) for s in roots}
            before_remote={'exists':True,'url':'https://before/','key_digest':'key','configuration_digest':'before','configuration':{}}
            after_remote={'exists':True,'url':'https://mirror/','key_digest':'key','configuration_digest':'after','configuration':{}}
            backends={s:Backend({'system':copy.deepcopy(after_remote),'user':{'exists':False}}) for s in roots}
            recoveries={s:dict(generation='a'*32,slot=s,rows={'system':{'phase':'applied','before':copy.deepcopy(before_remote),'after':copy.deepcopy(after_remote)}}) for s in roots}
            states={s:dict(kind='BoilerResourceFirstbootState',generation='a'*32,slot=s,root_partuuid=roles[s]['partuuid'],
                           resources={IDENTITY:{'status':'configured','activation_recovery':copy.deepcopy(recoveries[s])}}) for s in roots}
            after={s:dict(states[s],status='restored') for s in roots}
            from maintenance.engine import _json_write
            for s in roots:_json_write(roots[s]/firstboot.STATE,states[s])
            shared={'ledger':None,'remote':{'exists':False}}
            plan={'roles':{'fixture':True},'receipt_sha256':lifecycle.digest(receipt),'target_window':False,
                  'flathub':{'system_observations':{s:lifecycle.digest(after_remote) for s in roots},'shared_observation_sha256':lifecycle.digest(shared)}}
            review=lifecycle.digest(plan);document={'generation':'a'*32,'reviewed_sha256':review,
                'slots':{s:{'before_exists':True,'before':states[s],'after':after[s],'gate_before':None} for s in roots}}
            action={'kind':'BoilerFlathubRestoreAction','generation':'a'*32,'identity':IDENTITY,'status':'restoring','phase':'prepared',
                    'reviewed_sha256':review,'plan':plan,'document':{},'recovery':copy.deepcopy(recoveries),'remote_done':{}}
            marker=home/'marker.json';_json_write(marker,action)
            class Mounts:
                bound={'fixture':True}
                def __init__(self,*a,**kw):self.roots=roots
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def pending_window(self):return None
            held=[False]
            class Shared:
                def snapshot(self,backend):
                    if held[0]:raise RuntimeError('recursive shared ledger lock')
                    return copy.deepcopy(shared)
            def checked(path):return intents[Path(path).name]
            def backend_for(slot,*a):return contextlib.nullcontext(backends[slot])
            calls=[]
            def restore_bound(backend,recovery,record_before_write,*,partuuid,record_user_before_write=None):
                slot=recovery['slot'];calls.append(slot);backend.current['system']=copy.deepcopy(before_remote)
                result=copy.deepcopy(recovery);result['rows']['system']['phase']='restored';record_before_write(result)
                if record_user_before_write:
                    held[0]=True
                    try:record_user_before_write(result)
                    finally:held[0]=False
                if calls==['B']:raise RuntimeError('fixture interruption after remote commit')
                return result
            with patch.object(restore,'_marker',return_value=marker),patch.object(restore,'Engine',return_value=object()),patch('maintenance.resource_targets.TargetMounts',Mounts), \
                 patch.object(lifecycle,'_session',return_value=(session,[{'resource':IDENTITY}])), \
                 patch('maintenance.live_root.write_scope',return_value=contextlib.nullcontext()), \
                 patch.object(firstboot,'checked_intent',side_effect=checked), \
                 patch('maintenance.flathub_restore_docs.load',return_value=document), \
                 patch.object(restore,'_backend_for',side_effect=backend_for), \
                 patch.object(restore.flathub_activation,'_backend',return_value=backends['A']), \
                 patch.object(restore.flathub_activation,'restore_bound',side_effect=restore_bound), \
                 patch.object(restore.flathub_user_store,'Store',return_value=Shared()):
                with self.assertRaisesRegex(RuntimeError,'fixture interruption'):restore.resume(intent,firstboot.read_private(marker))
                foreign=copy.deepcopy(states['B']);foreign['generation']='b'*32
                _json_write(roots['B']/firstboot.STATE,foreign)
                marker_before=marker.read_bytes();remote_before=copy.deepcopy(backends['B'].current)
                files_before={s:(roots[s]/TARGET).read_bytes() for s in roots}
                with self.assertRaisesRegex(RuntimeError,'slot state changed'):restore.resume(intent,firstboot.read_private(marker))
                self.assertEqual(marker.read_bytes(),marker_before)
                self.assertEqual(backends['B'].current,remote_before)
                self.assertEqual({s:(roots[s]/TARGET).read_bytes() for s in roots},files_before)
                _json_write(roots['B']/firstboot.STATE,states['B'])
                gate=roots['B']/'var/lib/boiler-resource-approved/common-flathub-mirror.json'
                _json_write(gate,{'generation':'b'*32,'identity':IDENTITY})
                with self.assertRaisesRegex(RuntimeError,'approval gate changed'):restore.resume(intent,firstboot.read_private(marker))
                self.assertEqual(marker.read_bytes(),marker_before)
                self.assertEqual(backends['B'].current,remote_before)
                self.assertEqual({s:(roots[s]/TARGET).read_bytes() for s in roots},files_before)
                gate.unlink()
                if hasattr(os,'fork'):
                    pid=os.fork()
                    if pid==0:
                        point=os.environ.get('BOILER_FLATHUB_KILL','state_a');write=restore._json_write
                        from maintenance.engine import Engine
                        original_restore=Engine.restore
                        def crash_file(engine,*args,**kwargs):
                            result=original_restore(engine,*args,**kwargs)
                            if point=='file_'+engine.root.name.lower():os._exit(79)
                            return result
                        def crash_state(path,value):
                            if point=='before_close' and Path(path)==marker and value.get('status')=='closed':os._exit(79)
                            write(path,value)
                            if point in ('state_a','state_b') and Path(path)==roots[point[-1].upper()]/firstboot.STATE:os._exit(79)
                        with patch.object(restore,'_json_write',side_effect=crash_state),patch.object(Engine,'restore',crash_file):
                            restore.resume(intent,firstboot.read_private(marker))
                        os._exit(80)
                    _,status=os.waitpid(pid,0)
                    self.assertEqual(os.waitstatus_to_exitcode(status),79)
                    # The fixture backend is process-local; reflect the child's committed fake remote.
                    backends['A'].current['system']=copy.deepcopy(before_remote)
                result=restore.resume(intent,firstboot.read_private(marker))
            self.assertEqual(result['status'],'restored')
            self.assertEqual(calls,['B'] if hasattr(os,'fork') else ['B','B','A'])
            for s in roots:self.assertFalse((roots[s]/TARGET).exists())


if __name__=='__main__':unittest.main()


