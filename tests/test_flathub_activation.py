import contextlib,copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from maintenance import flathub_activation as activation
from maintenance import flathub_transaction as transaction
from maintenance import resource_firstboot as firstboot


class Fake:
    def __init__(self):
        self.values={'system':{'exists':True,'url':'https://original/','key_digest':'key','configuration_digest':'before',
                     'configuration':{'gpg-verify':'true'},'flathub_key':False},'user':{'exists':False}}
        self.kill=False
    def observe(self,scope):return copy.deepcopy(self.values[scope])
    def set_url(self,scope,url):
        self.values[scope]['url']=url;self.values[scope]['configuration']['url-is-set']='true';self.values[scope]['configuration_digest']='changed'
        if self.kill:self.kill=False;raise RuntimeError('interrupted after native write')
    def restore_url_flag(self,scope,before):
        self.values[scope]['configuration']=copy.deepcopy(before['configuration']);self.values[scope]['configuration_digest']=before['configuration_digest']
    def create(self,scope,url):self.values[scope]={'exists':True,'url':url,'key_digest':'new','configuration_digest':'new',
                                                        'configuration':{'gpg-verify':'true'},'flathub_key':True}
    def created_matches(self,value):return value.get('flathub_key') and value.get('url')==transaction.MIRROR
    def delete_without_force(self,scope):self.values[scope]={'exists':False}


class FlathubActivationTests(unittest.TestCase):
    def test_interrupted_native_write_retains_before_and_resumes(self):
        identity=activation.IDENTITY;intent={'generation':'a'*32,'slot':'A','root_partuuid':'b'*36,'selection':{'components':[identity]}}
        fake=Fake();original=copy.deepcopy(fake.values);history=[];fake.kill=True
        class Shared:
            def acquire(self,backend,generation,partuuid,record):
                before=transaction.prepare(backend,scopes=('user',));rows=transaction.activate(backend,before,lambda _:None,scopes=('user',))
                self.before=before;self.rows=rows;state={'epoch':'e'*32,'status':'active'};record(state);return state
            def verify(self,*args):return {'ok':True,'epoch':'e'*32}
            def preflight_release(self,*args):return {}
            def release(self,backend,generation,partuuid,record):
                transaction.restore(backend,self.rows,lambda _:None,scopes=('user',));record({'epoch':'e'*32,'status':'closed'});return {'status':'restored'}
        shared=Shared()
        with patch.object(activation,'_intent',return_value=intent),patch.object(activation,'_backend',return_value=fake), \
             patch.object(activation.flathub_user_store,'Store',return_value=shared):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                activation.activate(identity,pool=object(),facts={},selection=[identity],record_before_write=lambda r:history.append(copy.deepcopy(r)),approve=lambda _:None)
            retained=history[-1]
            self.assertEqual(retained['rows']['system']['phase'],'writing')
            result=activation.resume(identity,pool=object(),facts={},selection=[identity],state=retained,
                                     record_before_write=lambda r:history.append(copy.deepcopy(r)),approve=lambda _:None)
            self.assertEqual(result['status'],'configured')
            self.assertFalse(result['network_validated'])
            self.assertEqual(result['recovery']['before'],{'system':original['system']})
            self.assertEqual(activation.verify(identity,pool=object(),facts={},selection=[identity],state=result['recovery'])['status'],'configured')
            with patch('maintenance.resource_firstboot.checked_intent',return_value=intent), \
                 patch('maintenance.resource_firstboot.open_pool',return_value=contextlib.nullcontext((object(),[]))):
                restored=activation.restore(identity,state=result['recovery'],record_before_write=lambda r:history.append(copy.deepcopy(r)))
            self.assertEqual(restored['status'],'restored');self.assertEqual(fake.values,original)

    def test_firstboot_persists_latest_before_and_user_ledger_reference(self):
        identity=activation.IDENTITY;backend=Fake()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'proc/sys/kernel/random').mkdir(parents=True)
            (root/'proc/sys/kernel/random/boot_id').write_text('12345678-1234-1234-1234-123456789abc')
            intent={'kind':firstboot.KIND,'schema_version':1,'mode':'firstboot-install-v1','generation':'a'*32,
                    'slot':'A','root_partuuid':'b'*36,'selection':{'trust':'local-test','components':[identity]},
                    'resource_receipt':{'id':'a'*32}}
            path=root/firstboot.INTENT;path.parent.mkdir(parents=True);path.write_text(json.dumps(intent))
            class Pool:
                local_test=True
                def resource(self,name):return dict(id=name,install={'kind':'boiler-flathub-mirror-v1'},hardware_requires={})
            class Session:
                def __init__(self,*a,**kw):pass
                def verify(self,ids):return [{'ok':True}]
            class Core:
                def __init__(self,*a,**kw):pass
                def verify_readonly(self,name):return {'ok':True}
            class Shared:
                def acquire(self,backend,generation,partuuid,record):
                    state={'epoch':'e'*32,'status':'active'};record(state);return state
            with patch.object(firstboot,'Engine',Core),patch.object(firstboot,'ResourceSession',Session), \
                 patch.object(firstboot,'environment',return_value={}),patch('maintenance.resource_activation.collect_facts',return_value={}), \
                 patch.object(firstboot,'resolve',return_value=[{'id':identity}]), \
                 patch.object(activation,'_backend',return_value=backend),patch.object(activation.flathub_user_store,'Store',return_value=Shared()):
                state=firstboot._run_pool(root,intent,root/firstboot.STATE,firstboot._new_state(intent),Pool(),[{'resource':identity}])
            row=state['resources'][identity]
            self.assertEqual(row['status'],'configured')
            self.assertEqual(row['activation_recovery']['rows']['system']['phase'],'applied')
            self.assertEqual(row['activation_recovery']['user_ledger']['epoch'],'e'*32)
            self.assertFalse(row['network_validated'])


if __name__=='__main__':unittest.main()
