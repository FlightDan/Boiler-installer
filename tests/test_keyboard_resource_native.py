"""Real Linux files and mounts on an explicitly serial-tagged disposable disk."""
import contextlib, copy, json, os, shutil, stat, subprocess, unittest
from pathlib import Path
from unittest.mock import patch
from maintenance import keyboard_resource as bridge
from maintenance.keyboard_runtime import service
from maintenance.keyboard_target import binding
from maintenance import resource_lifecycle as lifecycle, resource_firstboot as firstboot, keyboard_restore_docs
from maintenance.engine import _json_write


class Pool:
    def resource(self, identity):
        return dict(id=identity,install=bridge.INSTALL,verify=bridge.VERIFY,restore=bridge.RESTORE,
                    payload={'files':[]},dependencies=[],external_component_dependencies=[],hardware_requires={})


@unittest.skipUnless(os.name=='posix' and os.environ.get('BOILER_KEYBOARD_NATIVE_FIXTURE')=='1','disposable disk required')
class NativeResourceKeyboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        device=Path('/dev/vdb')
        serials={p.read_text().strip() for p in (Path('/sys/class/block/vdb/serial'),Path('/sys/class/block/vdb/device/serial')) if p.is_file()}
        if os.geteuid()!=0 or not stat.S_ISBLK(device.stat().st_mode) or serials!={'boiler-kbd-fixture'} or int(Path('/sys/class/block/vdb/size').read_text())*512!=512*1024**2:
            raise RuntimeError('Refusing nonfixture disk')
        subprocess.run(['sfdisk','--label','gpt',str(device)],input='label:gpt\nsize=327680,name=rootfs-A\nsize=327680,name=rootfs-B\nname=home\n',text=True,check=True,capture_output=True)
        subprocess.run(['mdev','-s'],check=True)
        for number in (1,2):subprocess.run(['mkfs.btrfs','-f','-q',str(device)+str(number)],check=True)
        subprocess.run(['mkfs.ext4','-F','-q',str(device)+'3'],check=True)
        cls.base=Path('/audit/kbd-resource');cls.base.mkdir();cls.roots={};cls.mounts=[]
        for slot,number in (('A',1),('B',2)):
            root=cls.base/slot;root.mkdir();subprocess.run(['mount','-t','btrfs',str(device)+str(number),str(root)],check=True);cls.mounts.append(root)
            (root/'home').mkdir();subprocess.run(['mount','-t','ext4',str(device)+'3',str(root/'home')],check=True);cls.mounts.append(root/'home')
            for name in ('etc','usr/share/applications','usr/lib/modules/fixture','home/deck','var'): (root/name).mkdir(parents=True,exist_ok=True)
            (root/'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\ndeck:x:1000:1000:deck:/home/deck:/bin/sh\n')
            (root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\n')
            (root/'usr/bin').mkdir(parents=True,exist_ok=True)
            elf=bytearray(20);elf[:4]=b'\x7fELF';elf[5]=1;elf[18:20]=(62).to_bytes(2,'little');(root/'usr/bin/env').write_bytes(elf)
            (root/service.DESKTOP).write_text('[Desktop Entry]\nName=Keyboard fixture\n')
            os.chown(root/'home/deck',1000,1000);cls.roots[slot]=root
    @classmethod
    def tearDownClass(cls):
        for path in reversed(cls.mounts):subprocess.run(['umount',str(path)],check=False)
    def setUp(self):
        for root in self.roots.values():
            state=root/'var/lib/boiler'
            if state.exists():shutil.rmtree(state)
        home=self.roots['A']/'home';state=home/'.boiler'
        if state.exists():shutil.rmtree(state)
        config=home/'deck/.config';config.mkdir(exist_ok=True);os.chown(config,1000,1000)
        self.path=config/'kwinrc';self.original=b'# original\n[Wayland]\nScale=1\n';self.path.write_bytes(self.original);os.chmod(self.path,0o600);os.chown(self.path,1000,1000)
        self.pool=Pool()
    def intent(self,slot):return {'slot':slot,'selection':{'components':[bridge.IDENTITY]}}
    def perform(self,slot,action):
        root=self.roots[slot];intent=self.intent(slot);plan=bridge.prepare(self.pool,intent,action,root=root)
        method={'apply':bridge.activate,'restore':bridge.restore,'recover':bridge.retry}[action]
        return method(self.pool,intent,plan,reviewed_sha256=plan['review_sha256'],root=root)
    def test_two_slots_share_ledger_and_restore_exact_original(self):
        for slot in ('A','B'):self.perform(slot,'apply')
        self.assertEqual((self.roots['A']/service.STATE/'lock').stat().st_ino,(self.roots['B']/service.STATE/'lock').stat().st_ino)
        self.perform('A','restore');result=self.perform('B','restore')
        self.assertEqual(self.path.read_bytes(),self.original);self.assertTrue(result['configuration_restored'])
    def test_user_edit_is_preserved_across_both_restores(self):
        for slot in ('A','B'):self.perform(slot,'apply')
        edited=self.path.read_bytes().replace(b'Scale=1',b'Scale=2');self.path.write_bytes(edited)
        self.perform('A','restore');result=self.perform('B','restore')
        self.assertEqual(self.path.read_bytes(),edited);self.assertTrue(result['user_edits_preserved'])
    def test_interrupted_transaction_requires_original_slot(self):
        root=self.roots['A'];intent=self.intent('A');plan=bridge.prepare(self.pool,intent,'apply',root=root)
        with patch.object(service.Store,'_after_write',side_effect=RuntimeError('fixture interruption')):
            with self.assertRaisesRegex(RuntimeError,'interruption'):bridge.activate(self.pool,intent,plan,reviewed_sha256=plan['review_sha256'],root=root)
        with self.assertRaisesRegex(RuntimeError,'slot'):self.perform('B','recover')
        self.perform('A','recover');self.perform('A','restore');self.assertEqual(self.path.read_bytes(),self.original)

    def action_fixture(self,edited=False):
        for slot in ('A','B'):self.perform(slot,'apply')
        if edited:self.path.write_bytes(self.path.read_bytes()+b'# preserved user edit\n')
        identity=bridge.IDENTITY
        roles={'rootfs-'+s:{'partuuid':binding(root,expected_label=s)['partuuid']} for s,root in self.roots.items()}
        intent={'kind':firstboot.KIND,'schema_version':1,'mode':'firstboot-install-v1','generation':'a'*32,
                'slot':'A','root_partuuid':roles['rootfs-A']['partuuid'],'selection':{'trust':'local-test','components':[identity]},'target_roles':roles}
        intents={s:dict(intent,slot=s,root_partuuid=roles['rootfs-'+s]['partuuid']) for s in self.roots}
        for slot,root in self.roots.items():
            _json_write(root/firstboot.STATE,dict(kind='BoilerResourceFirstbootState',schema_version=1,
                generation='a'*32,slot=slot,root_partuuid=roles['rootfs-'+slot]['partuuid'],status='complete',resources={identity:{'status':'configured'}}))
        class Mounts:
            bound={'fixture':True}
            def __init__(self,*args,**kwargs):self.roots=NativeResourceKeyboard.roots
            def __enter__(self):return self
            def __exit__(self,*args):pass
        mounts=Mounts();service_a=service.Keyboard(self.roots['A']);store,_,_=service_a._preflight()
        with store.lock():
            index,pending=service_a._state(store);assert pending is None
            ledger=lifecycle.digest(index);target=lifecycle.digest(store._read(service.TARGET,user=True))
        slots={s:{'binding':binding(root,expected_label=s),'state_sha256':lifecycle.digest(firstboot.read_private(root/firstboot.STATE)),
                  'gate_sha256':lifecycle.digest(None)}
               for s,root in self.roots.items()}
        plan=dict(roles=mounts.bound,slots=slots,ledger_sha256=ledger,target_sha256=target,target_window=False)
        review=lifecycle.digest(plan);marker=self.roots['A']/'home/.boiler/resource-maintenance/lifecycle-action.json'
        marker.parent.mkdir(parents=True,exist_ok=True)
        def checked(root):return intents[Path(root).name]
        with patch.object(firstboot,'checked_intent',side_effect=checked),patch.object(firstboot,'validate_restore_binding'):
            ref=keyboard_restore_docs.create(intent,plan,review,mounts,marker)
        action=dict(kind='BoilerKeyboardRestoreAction',generation='a'*32,identity=identity,document=ref,
                    status='restoring',phase='prepared',reviewed_sha256=review,plan=plan,completed={})
        _json_write(marker,action)
        return intent,intents,Mounts,marker

    def test_foreign_inactive_state_refused_before_any_additional_write(self):
        intent,intents,Mounts,marker=self.action_fixture();foreign=firstboot.read_private(self.roots['B']/firstboot.STATE)
        foreign['generation']='f'*32;_json_write(self.roots['B']/firstboot.STATE,foreign)
        def snapshot():
            files=[self.roots['A']/firstboot.STATE,self.roots['B']/firstboot.STATE]
            files += [p for p in (self.roots['A']/'home').rglob('*') if p.is_file()]
            return {str(p):p.read_bytes() for p in files}
        before=snapshot()
        with patch.object(lifecycle,'_keyboard_marker',return_value=marker),patch.object(lifecycle,'TargetMounts',Mounts), \
             patch.object(firstboot,'checked_intent',side_effect=lambda root:intents[Path(root).name]), \
             patch.object(bridge,'Keyboard',side_effect=AssertionError('ledger touched before state preflight')):
            with self.assertRaisesRegex(RuntimeError,'outside this action'):lifecycle._resume_keyboard_restore(intent,firstboot.read_private(marker))
        self.assertEqual(snapshot(),before)

    def test_foreign_or_changed_gate_refused_before_document_and_home_write(self):
        identity=bridge.IDENTITY
        roles={'rootfs-'+s:{'partuuid':binding(root,expected_label=s)['partuuid']} for s,root in self.roots.items()}
        intent={'kind':firstboot.KIND,'schema_version':1,'mode':'firstboot-install-v1','generation':'a'*32,
                'slot':'A','root_partuuid':roles['rootfs-A']['partuuid'],'selection':{'trust':'local-test','components':[identity]},'target_roles':roles}
        intents={s:dict(intent,slot=s,root_partuuid=roles['rootfs-'+s]['partuuid']) for s in self.roots}
        for slot,root in self.roots.items():
            _json_write(root/firstboot.STATE,dict(kind='BoilerResourceFirstbootState',schema_version=1,generation='a'*32,
                slot=slot,root_partuuid=roles['rootfs-'+slot]['partuuid'],resources={}))
        slots={s:{'binding':binding(root,expected_label=s),'state_sha256':lifecycle.digest(firstboot.read_private(root/firstboot.STATE)),
                  'gate_sha256':lifecycle.digest(None)} for s,root in self.roots.items()}
        plan={'slots':slots};marker=self.roots['A']/'home/.boiler/resource-maintenance/lifecycle-action.json'
        class Mounts:pass
        mounts=Mounts();mounts.roots=self.roots
        gate=self.roots['B']/'var/lib/boiler-resource-approved/common-kde-keyboard.json'
        def snapshot():return {str(p):p.read_bytes() for p in (self.roots['A']/'home').rglob('*') if p.is_file()}
        for value,message in (({'generation':'f'*32,'identity':identity},'another installation'),
                              ({'generation':'a'*32,'identity':identity},'changed after review')):
            _json_write(gate,value);before=snapshot()
            with patch.object(firstboot,'checked_intent',side_effect=lambda root:intents[Path(root).name]), \
                 patch.object(firstboot,'validate_restore_binding'):
                with self.assertRaisesRegex(RuntimeError,message):keyboard_restore_docs.create(intent,plan,'review',mounts,marker)
            self.assertEqual(snapshot(),before)
            self.assertFalse((marker.parent/'keyboard-restore-documents').exists())

    def test_os_exit_at_restore_and_publication_boundaries(self):
        for point in ('restore-A','restore-B','state-A','state-B','before-close','restore-B-edited'):
            with self.subTest(point=point):
                # Each iteration starts from a fresh, test-owned ledger.
                if point!='restore-A':self.setUp()
                edited=point=='restore-B-edited'
                intent,intents,Mounts,marker=self.action_fixture(edited=edited);real_keyboard=bridge.Keyboard
                expected=self.path.read_bytes() if edited else self.original
                factory=lambda root:real_keyboard(self.roots['A'] if Path(root)==Path('/') else root)
                real_restore=bridge.restore;real_write=lifecycle._json_write
                def kill_restore(pool,other,reviewed,**kwargs):
                    result=real_restore(pool,other,reviewed,**kwargs)
                    if point=='restore-'+other['slot'] or (point=='restore-B-edited' and other['slot']=='B'):os._exit(71)
                    return result
                def kill_write(path,value):
                    if point=='before-close' and Path(path)==marker and value.get('status')=='closed':os._exit(71)
                    real_write(path,value)
                    for slot,root in self.roots.items():
                        if point=='state-'+slot and Path(path)==root/firstboot.STATE and value.get('maintenance_restore',{}).get('status')=='restored':os._exit(71)
                with patch.object(lifecycle,'_keyboard_marker',return_value=marker),patch.object(lifecycle,'TargetMounts',Mounts), \
                     patch.object(firstboot,'checked_intent',side_effect=lambda root:intents[Path(root).name]), \
                     patch.object(firstboot,'open_pool',side_effect=lambda *a,**k:contextlib.nullcontext((self.pool,[]))), \
                     patch.object(bridge,'Keyboard',side_effect=factory):
                    pid=os.fork()
                    if pid==0:
                        with patch.object(bridge,'restore',side_effect=kill_restore),patch.object(lifecycle,'_json_write',side_effect=kill_write):
                            lifecycle._resume_keyboard_restore(intent,firstboot.read_private(marker))
                        os._exit(72)
                    _,status=os.waitpid(pid,0);self.assertEqual(os.waitstatus_to_exitcode(status),71)
                    result=lifecycle._resume_keyboard_restore(intent,firstboot.read_private(marker))
                    self.assertEqual(result['status'],'restored');self.assertEqual(self.path.read_bytes(),expected)
                    if edited:self.assertTrue(result['slots']['B']['user_edits_preserved'])


if __name__=='__main__':unittest.main(verbosity=2)
