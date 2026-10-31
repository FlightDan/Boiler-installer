import unittest
import contextlib
import copy
import functools
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from maintenance import keyboard_activation as activation
from maintenance import resource_lifecycle as lifecycle
from maintenance import resource_firstboot as firstboot
from maintenance.keyboard_runtime import service
from test_x2_keyboard_dynamic import target
from maintenance.engine import _json_write


class KeyboardActivationTests(unittest.TestCase):
    def test_firstboot_records_before_write_and_binds_review(self):
        intent={'generation':'a'*32,'slot':'A','selection':{'components':[activation.bridge.IDENTITY]}}
        reviewed={'action':'apply','review_sha256':'digest','target_binding':{'label':'A'}}
        calls=[]
        with patch.object(activation,'_intent',return_value=intent), \
             patch.object(activation.bridge,'prepare',return_value=reviewed), \
             patch.object(activation.bridge,'activate',side_effect=lambda *a,**k:calls.append('write') or {}):
            result=activation.activate(activation.bridge.IDENTITY,root='/',pool=object(),facts={},
                selection=[activation.bridge.IDENTITY],record_before_write=lambda r:calls.append('record'),
                approve=lambda i:calls.append('approve'))
        self.assertEqual(calls,['record','approve','write'])
        self.assertEqual(result['status'],'configured')
        self.assertEqual(result['session_activation'],'not-validated')

    def test_verify_user_edits_are_reported_and_preserved(self):
        intent={'generation':'a'*32,'slot':'A','selection':{'components':[activation.bridge.IDENTITY]}}
        with patch.object(activation,'_intent',return_value=intent), \
             patch.object(activation.bridge,'verify',return_value={'installed':True,'ok':False,'preserved':['home/deck/.config/kwinrc']}):
            value=activation.verify(activation.bridge.IDENTITY,pool=object(),facts={},selection=[activation.bridge.IDENTITY],
                                    state={'generation':'a'*32,'slot':'A'})
        self.assertTrue(value['user_edits_preserved'])
        self.assertEqual(value['status'],'configured')

    def test_restore_resume_skips_completed_slot_after_interruption(self):
        identity=activation.bridge.IDENTITY
        intent={'generation':'a'*32,'slot':'A','selection':{'components':[identity]},'target_roles':{}}
        bindings={s:{'label':s} for s in ('A','B')}
        plan={'roles':{'fixture':True},'slots':{s:{'binding':bindings[s]} for s in bindings},'target_window':False}
        action={'kind':'BoilerKeyboardRestoreAction','generation':intent['generation'],'identity':identity,
                'status':'restoring','phase':'restoring-A','reviewed_sha256':lifecycle.digest(plan),
                'plan':plan,'completed':{},'document':{}}
        class Mounts:
            roots={'A':Path('/fixture-A'),'B':Path('/fixture-B')};bound={'fixture':True}
            def __enter__(self):return self
            def __exit__(self,*args):pass
        class Service:
            def options(self):return {'pending':False,'lifecycle_reason':None}
        def slot_intent(root):
            value=copy.deepcopy(intent);value['slot']='A' if str(root).endswith('A') else 'B';return value
        called=[]
        def perform(pool,other,reviewed,**kw):
            called.append(other['slot'])
            if other['slot']=='B' and called.count('B')==1:raise RuntimeError('simulated interruption')
            return {'slot':other['slot']}
        with patch.object(lifecycle,'TargetMounts',return_value=Mounts()), \
             patch.object(lifecycle,'Engine',return_value=object()), \
             patch('maintenance.live_root.write_scope',return_value=contextlib.nullcontext()), \
             patch.object(lifecycle.firstboot,'open_pool',return_value=contextlib.nullcontext((object(),[]))), \
             patch.object(lifecycle.firstboot,'checked_intent',side_effect=slot_intent), \
             patch.object(lifecycle.firstboot,'_new_state',side_effect=lambda value:{'resources':{},'status':'pending'}), \
             patch('maintenance.keyboard_restore_docs.load',return_value={'slots':{'A':{'after':{}},'B':{'after':{}}}}), \
             patch.object(lifecycle,'_keyboard_restore_preflight'), \
             patch.object(lifecycle,'_keyboard_edited_paths',return_value=[]), \
             patch.object(lifecycle,'_json_write'), \
             patch('maintenance.keyboard_target.binding',side_effect=lambda root,expected_label=None:bindings[expected_label]), \
             patch.object(activation.bridge,'Keyboard',return_value=Service()), \
             patch.object(activation.bridge,'prepare',side_effect=lambda pool,other,kind,root: {'review_sha256':'digest'}), \
             patch.object(activation.bridge,'restore',side_effect=perform):
            with self.assertRaisesRegex(RuntimeError,'interruption'):
                lifecycle._resume_keyboard_restore(intent,action)
            self.assertEqual(set(action['completed']),{'A'})
            result=lifecycle._resume_keyboard_restore(intent,action)
        self.assertEqual(called,['A','B','B'])
        self.assertEqual(result['status'],'restored')
        self.assertEqual(action['status'],'closed')

    def test_resource_firstboot_applies_canonical_store_and_preserves_later_edit(self):
        identity=activation.bridge.IDENTITY
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target(root)
            (root/'proc/sys/kernel/random').mkdir(parents=True)
            (root/'proc/sys/kernel/random/boot_id').write_text('12345678-1234-1234-1234-123456789abc')
            intent={'kind':firstboot.KIND,'schema_version':1,'mode':'firstboot-install-v1','generation':'a'*32,
                    'slot':'A','root_partuuid':'b'*36,'selection':{'trust':'local-test','components':[identity]},
                    'resource_receipt':{'id':'a'*32}}
            path=root/firstboot.INTENT;path.parent.mkdir(parents=True);_json_write(path,intent)
            state_path=root/firstboot.STATE
            class Pool:
                local_test=True
                def resource(self,name):
                    return dict(id=name,install=activation.bridge.INSTALL,verify=activation.bridge.VERIFY,
                        restore=activation.bridge.RESTORE,payload={'files':[]},dependencies=[],
                        external_component_dependencies=[],hardware_requires={})
            class Core:
                def __init__(self,*args,**kwargs):pass
                def verify_readonly(self,name):return {'ok':True}
            def bound(target,expected_label=None):
                return {'slot':'partuuid-11111111-aaaaaaaa','label':'A','partuuid':'11111111-aaaaaaaa',
                        'home':{'uuid':'fixture','device':1,'inode':1,'source':'fixture','major_minor':'0:1'}}
            with patch.object(firstboot,'Engine',Core),patch.object(firstboot,'environment',return_value={}), \
                 patch('maintenance.resource_activation.collect_facts',return_value={}), \
                 patch.object(service,'binding',side_effect=bound), \
                 patch.object(activation.bridge,'Keyboard',functools.partial(service.Keyboard,enforce_owner=False)):
                state=firstboot._run_pool(root,intent,state_path,firstboot._new_state(intent),Pool(),[{'resource':identity}])
                self.assertEqual(state['resources'][identity]['status'],'configured',state['resources'][identity])
                kwin=root/service.TARGET;edited=kwin.read_bytes()+b'# user edit\n';kwin.write_bytes(edited)
                state=firstboot._run_pool(root,intent,state_path,state,Pool(),[{'resource':identity}])
                self.assertEqual(kwin.read_bytes(),edited)
                self.assertEqual(state['resources'][identity]['runtime'],'user-edited-preserved')


if __name__=='__main__':unittest.main()
