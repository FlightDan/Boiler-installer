import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from maintenance import resource_lifecycle as lifecycle
from maintenance import resource_firstboot as firstboot
from maintenance import keyboard_restore_docs
from maintenance.engine import _json_write


class KeyboardRestorePreflightTests(unittest.TestCase):
    def test_foreign_second_slot_refused_before_any_write(self):
        identity='resource:common-kde-keyboard'
        intent={'generation':'a'*32,'slot':'A','selection':{'components':[identity]},
                'target_roles':{'rootfs-A':{'partuuid':'A'},'rootfs-B':{'partuuid':'B'}}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);roots={s:root/s for s in ('A','B')}
            for path in roots.values():path.mkdir()
            states={s:dict(kind='BoilerResourceFirstbootState',generation='a'*32,slot=s,root_partuuid=s,resources={}) for s in roots}
            for slot,path in roots.items():
                state=path/firstboot.STATE;state.parent.mkdir(parents=True);_json_write(state,states[slot])
            after={s:dict(states[s],status='restored') for s in roots}
            document={'generation':'a'*32,'reviewed_sha256':'', 'slots':{s:{'before_exists':True,'before':states[s],
                      'after':after[s],'gate_before':None} for s in roots}}
            bindings={s:{'label':s} for s in roots}
            plan={'roles':{'owned':True},'slots':{s:{'binding':bindings[s]} for s in roots},'target_window':False}
            review=lifecycle.digest(plan);document['reviewed_sha256']=review
            action={'kind':'BoilerKeyboardRestoreAction','generation':'a'*32,'identity':identity,'status':'restoring',
                    'phase':'restoring-A','reviewed_sha256':review,'plan':plan,'completed':{},'document':{}}
            class Mounts:
                bound={'owned':True}
                def __init__(self,*args,**kwargs):self.roots=roots
                def __enter__(self):return self
                def __exit__(self,*args):pass
            def checked(path):
                slot=Path(path).name;return dict(intent,slot=slot,root_partuuid=slot)
            foreign=copy.deepcopy(states['B']);foreign['generation']='f'*32
            _json_write(roots['B']/firstboot.STATE,foreign)
            before={s:(p/firstboot.STATE).read_bytes() for s,p in roots.items()}
            with patch.object(lifecycle,'TargetMounts',Mounts), \
                 patch.object(firstboot,'checked_intent',side_effect=checked), \
                 patch('maintenance.keyboard_target.binding',side_effect=lambda root,expected_label=None:bindings[expected_label]), \
                 patch('maintenance.keyboard_restore_docs.load',return_value=document), \
                 patch.object(lifecycle,'_json_write') as write, \
                 patch('maintenance.keyboard_resource.Keyboard',side_effect=AssertionError('No ledger access before preflight')):
                with self.assertRaisesRegex(RuntimeError,'outside this action'):
                    lifecycle._resume_keyboard_restore(intent,action)
                write.assert_not_called()
            self.assertEqual(before,{s:(p/firstboot.STATE).read_bytes() for s,p in roots.items()})

    def test_foreign_or_review_changed_gate_rejected_before_document_write(self):
        identity='resource:common-kde-keyboard'
        intent={'generation':'a'*32,'slot':'A','selection':{'components':[identity]},
                'target_roles':{'rootfs-A':{'partuuid':'a'},'rootfs-B':{'partuuid':'b'}}}
        for value,message in (({'generation':'f'*32,'identity':identity},'another installation'),
                              ({'generation':'a'*32,'identity':identity},'changed after review')):
            with self.subTest(value=value),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);roots={s:root/s for s in ('A','B')}
                states={s:dict(kind='BoilerResourceFirstbootState',generation='a'*32,slot=s,root_partuuid=s.lower(),resources={}) for s in roots}
                for slot,path in roots.items():
                    path.mkdir();state=path/firstboot.STATE;state.parent.mkdir(parents=True);_json_write(state,states[slot])
                gate=roots['B']/'var/lib/boiler-resource-approved/common-kde-keyboard.json';gate.parent.mkdir(parents=True);_json_write(gate,value)
                class Mounts:pass
                mounts=Mounts();mounts.roots=roots
                plan={'slots':{s:{'binding':{'label':s},'state_sha256':lifecycle.digest(states[s]),'gate_sha256':lifecycle.digest(None)} for s in roots}}
                def checked(path):
                    slot=Path(path).name;return dict(intent,slot=slot,root_partuuid=slot.lower())
                with patch.object(firstboot,'checked_intent',side_effect=checked), \
                     patch.object(firstboot,'validate_restore_binding'), \
                     patch('maintenance.keyboard_target.binding',side_effect=lambda root,expected_label=None:{'label':expected_label}), \
                     patch.object(keyboard_restore_docs,'_directory',side_effect=AssertionError('document directory created')), \
                     patch.object(keyboard_restore_docs,'_json_write',side_effect=AssertionError('document written')):
                    with self.assertRaisesRegex(RuntimeError,message):
                        keyboard_restore_docs.create(intent,plan,'review',mounts,root/'home/marker.json')

if __name__=='__main__':unittest.main()
