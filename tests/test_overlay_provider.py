import copy,unittest
from types import SimpleNamespace
from unittest.mock import patch
from maintenance import overlay_activation as a
from test_overlay_contract import ContractTests

class FakeSystem:
    def __init__(self):self.states={'boiler-overlay-read.service':'inactive','oxp3-overlay-telemetry.service':None};self.enabled={};self.calls=[];self.boot='boot'
    def boot_id(self):return self.boot
    def service(self,unit,user=False):return self.states[unit]
    def invocation(self,unit,user=False):return '1'*32
    def run(self,name,*args,check=True):
        self.calls.append((name,*args))
        if name=='systemctl':return self.command(*args)
        return (0,'')
    def userctl(self,*args,check=True):
        if self.states['oxp3-overlay-telemetry.service'] is None:return None
        self.calls.append(('user',*args));return self.command(*args)
    def command(self,action,unit=None,*rest):
        if action=='is-enabled':return (1,self.enabled.get(unit,'disabled'))
        if action=='enable':self.enabled[unit]='enabled'
        if action=='disable':self.enabled[unit]='disabled'
        if action=='start':self.states[unit]='active'
        if action=='stop':self.states[unit]='inactive'
        return (0,'')
class FakeStore:
    def __init__(self,*args):self.owners=set()
    def acquire(self,g,h,record,user):self.owners.add(h);record({'epoch':'e'*32})
    def verify(self,g,owners):return {'epoch':'e'*32,'preserved':False}
class Pool:
    def resource(self,identity):return ContractTests().resource()
class ProviderTests(unittest.TestCase):
    def test_pending_then_fresh_session(self):
        system=FakeSystem();store=FakeStore();intent={'generation':'a'*32,'slot':'A','selection':{'components':[a.IDENTITY]},'target_roles':{'rootfs-A':{'partuuid':'1'*36},'rootfs-B':{'partuuid':'2'*36}}}
        saved=[]
        with patch.object(a,'intent_for',return_value=intent),patch.object(a,'account',return_value=SimpleNamespace(pw_uid=1000,pw_gid=1000)),patch.object(a.resource_activation,'LiveSystem',return_value=system),patch.object(a.overlay_home,'Store',return_value=store),patch.object(a,'_fresh_as_deck',return_value=True):
            first=a._apply(a.IDENTITY,'/',Pool(),[a.IDENTITY],lambda x:saved.append(copy.deepcopy(x)),lambda x:None)
            self.assertEqual(first['runtime'],'pending-user-session');self.assertEqual(len(store.owners),2)
            system.states['oxp3-overlay-telemetry.service']='inactive'
            second=a._apply(a.IDENTITY,'/',Pool(),[a.IDENTITY],lambda x:saved.append(copy.deepcopy(x)),lambda x:None,previous=first['recovery'])
            self.assertEqual(second['runtime'],'collector-active');self.assertEqual(system.states['oxp3-overlay-telemetry.service'],'active')
            self.assertEqual(sum(1 for x in system.calls if x==('systemctl','start','boiler-overlay-read.service')),1)
            system.boot='next-boot'
            third=a._apply(a.IDENTITY,'/',Pool(),[a.IDENTITY],lambda x:saved.append(copy.deepcopy(x)),lambda x:None,previous=second['recovery'])
            self.assertTrue(all(row['started'] for row in third['recovery']['services']))

    def test_external_enablement_change_is_reported(self):
        import contextlib
        from maintenance import resource_firstboot,overlay_permissions
        system=FakeSystem()
        intent={'generation':'a'*32,'slot':'A','target_roles':{'rootfs-A':{'partuuid':'1'*36},'rootfs-B':{'partuuid':'2'*36}}}
        state={'kind':'BoilerOverlayRecovery','generation':intent['generation'],'slot':'A','holders':['1'*36,'2'*36],'services':[
          dict(user=False,unit='boiler-overlay-read.service',started=False,start_pending=False,enabled=True,enable_pending=False,invocation=None)]}
        with patch.object(resource_firstboot,'checked_intent',return_value=intent),patch.object(a.resource_activation,'LiveSystem',return_value=system),patch.object(overlay_permissions,'private_lock',return_value=contextlib.nullcontext()),patch.object(overlay_permissions,'restore',return_value=[]):
            result=a.restore(a.IDENTITY,state=state,record_before_write=lambda x:None)
        self.assertTrue(result['service_edits_preserved'])
    def test_restore_stops_grant_before_missing_user_manager(self):
        import contextlib
        from maintenance import resource_firstboot,overlay_permissions
        system=FakeSystem();system.states['boiler-overlay-read.service']='active'
        intent={'generation':'a'*32,'slot':'A','target_roles':{'rootfs-A':{'partuuid':'1'*36},'rootfs-B':{'partuuid':'2'*36}}}
        state={'kind':'BoilerOverlayRecovery','generation':intent['generation'],'slot':'A','holders':['1'*36,'2'*36],'services':[
          dict(user=False,unit='boiler-overlay-read.service',started=True,start_pending=False,enabled=False,enable_pending=False,invocation='1'*32),
          dict(user=True,unit='oxp3-overlay-telemetry.service',started=True,start_pending=False,enabled=True,enable_pending=False,invocation='1'*32)]}
        with patch.object(resource_firstboot,'checked_intent',return_value=intent),patch.object(a.resource_activation,'LiveSystem',return_value=system),patch.object(overlay_permissions,'private_lock',return_value=contextlib.nullcontext()),patch.object(overlay_permissions,'restore',return_value=[]):
            with self.assertRaisesRegex(a.ResourceError,'User session required'):a.restore(a.IDENTITY,state=state,record_before_write=lambda x:None)
        self.assertEqual(system.states['boiler-overlay-read.service'],'inactive')
if __name__=='__main__':unittest.main()
