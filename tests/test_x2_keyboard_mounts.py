"""Native root/owner tests on one explicitly tagged blank QEMU scratch disk.

Opt-in only. Refuse every device except the 512 MiB virtio test disk carrying
the exact fixed serial. No host disk is exposed by the corresponding runner.
This is a synthetic SteamOS filesystem fixture, not KDE/hardware acceptance.
"""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'components'),str(ROOT/'guest'),str(ROOT/'tests')]
from test_x2_keyboard_dynamic import module, target, write, bundle, source_release
from maintenance import keyboard_models as api
from maintenance.keyboard_runtime import service
from maintenance.keyboard_target import binding
from maintenance import live_root
import integration


@unittest.skipUnless(os.name=='posix' and os.environ.get('BOILER_KEYBOARD_NATIVE_FIXTURE')=='1',
                     'Requires the explicitly tagged disposable QEMU disk')
class NativeKeyboardMounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid()!=0:raise RuntimeError('Native keyboard fixture requires guest root')
        device=Path('/dev/vdb')
        print('NATIVE_SCRATCH_DIAGNOSTIC='+json.dumps(dict(device=str(device),serials={str(p):p.read_text().strip() for p in (Path('/sys/class/block/vdb/serial'),Path('/sys/class/block/vdb/device/serial')) if p.is_file()},sectors=Path('/sys/class/block/vdb/size').read_text().strip())),flush=True)
        if (not stat.S_ISBLK(device.stat().st_mode)
                or {p.read_text().strip() for p in (Path('/sys/class/block/vdb/serial'),Path('/sys/class/block/vdb/device/serial')) if p.is_file()} != {'boiler-kbd-fixture'}
                or int(Path('/sys/class/block/vdb/size').read_text())*512!=512*1024**2):
            raise RuntimeError('Refusing an untagged or unexpected scratch disk')
        cls.work=Path('/audit/keyboard-native-fixture')
        if cls.work.exists():raise RuntimeError('Native fixture output must be new')
        cls.work.mkdir(mode=0o755)
        subprocess.run(['sfdisk','--label','gpt',str(device)],input='label:gpt\nsize=327680,name=rootfs-A\nsize=327680,name=rootfs-B\nname=home\n',text=True,check=True,capture_output=True)
        subprocess.run(['mdev','-s'],check=True)
        for number in (1,2):
            subprocess.run(['mkfs.btrfs','-f','-q',str(device)+str(number)],check=True)
        subprocess.run(['mkfs.ext4','-F','-q',str(device)+'3'],check=True)
        cls.roots={}
        cls.mounts=[]
        try:
            for label,number in (('A',1),('B',2)):
                root=cls.work/label;root.mkdir()
                subprocess.run(['mount','-t','btrfs',str(device)+str(number),str(root)],check=True);cls.mounts.append(root)
                (root/'home').mkdir()
                subprocess.run(['mount','-t','ext4',str(device)+'3',str(root/'home')],check=True);cls.mounts.append(root/'home')
                target(root);cls.roots[label]=root
            cls.package_build=cls.work/'package-build'
            module('native_x2_keyboard_builder',ROOT/'adaptations/brands/onexplayer/x2-mini/bridge/scripts/build.py').build(ROOT,cls.package_build)
            cls.package=cls.package_build/'package'
            cls.component=next(row for row in json.loads((cls.package/'manifest.json').read_bytes())['components'] if row['id']==service.IDENTITY)
            cls.runtime=cls.work/'runtime';bundle(cls.package,cls.runtime)
            cls.payload=cls.work/'payload'
            preparer=module('native_keyboard_core_payload',ROOT/'scripts/prepare-integration.py')
            preparer.prepare(cls.payload/'integration')
            shutil.copytree(cls.package,cls.payload/'device-packages/x2-mini')
            cls.options=dict(schema=1,repairs=[],plugins=[],device_components=[service.IDENTITY],device_package='x2-mini',
                device_manifest_sha256=hashlib.sha256((cls.package/'manifest.json').read_bytes()).hexdigest(),device_trust='developer-session')
        except BaseException:
            cls.unmount();raise

    @classmethod
    def unmount(cls):
        for root in reversed(getattr(cls,'mounts',[])):
            subprocess.run(['mount','-o','remount,bind,rw',str(root)],check=False,capture_output=True)
            subprocess.run(['umount',str(root)],check=False,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.unmount()

    def setUp(self):
        # All locations are on the serial-verified scratch partitions above.
        # Remove only previous test-owned transaction state, never real data.
        for root in self.roots.values():
            state=root/'var/lib/boiler'
            if state.exists():shutil.rmtree(state)
        home=self.roots['A']/'home'
        state=home/'.boiler'
        if state.exists():shutil.rmtree(state)
        self.original=b'# native original\r\n[Wayland]\r\nInputMethod=custom\r\nScale=1.25\r\n  [Unrelated]\r\nKey=kept\r\n'
        self.path=write(home/'deck/.config/kwinrc',self.original,0o600,1000)
        os.chown(home/'deck/.config',1000,1000)

    def plan(self,action='apply',label='A'):
        return api.plan(self.roots[label],'device:x2-mini',[service.IDENTITY] if action=='apply' else [],
            action=action,trust_local_test=True,runtime_root=self.runtime)

    def execute(self,action='apply',label='A'):
        review=self.plan(action,label)
        return api.execute(self.roots[label],review,reviewed_sha256=review['review_sha256'],trust_local_test=True,runtime_root=self.runtime)

    def test_actual_new_install_two_mounted_roots_share_one_original_and_readonly_verify(self):
        a,b=self.roots['A'],self.roots['B']
        self.assertNotEqual(a.stat().st_dev,b.stat().st_dev)
        self.assertEqual((a/'home').stat().st_dev,(b/'home').stat().st_dev)
        self.assertEqual((a/'home').stat().st_ino,(b/'home').stat().st_ino)
        real_run=subprocess.run
        calls=[]
        def fixed_only(command,*args,**kwargs):
            if Path(command[0]).name not in ('findmnt','lsblk'):
                raise AssertionError('No guest/package executable may run: '+str(command))
            calls.append(command)
            return real_run(command,*args,**kwargs)
        with patch.object(integration,'release',source_release),patch('subprocess.run',fixed_only):
            for label in ('A','B'):
                result=integration.stage(self.roots[label],self.payload,self.options,label)
                self.assertIn('core:maintenance',result['components'])
                self.assertIn(service.IDENTITY,result['components'])
                self.assertNotIn(service.TARGET,json.loads((self.roots[label]/'var/lib/boiler/maintenance/state.json').read_bytes())['owners'])
                # The actual prepared core payload retains the fixed handler.
                self.assertEqual((ROOT/'maintenance/keyboard_runtime/service.py').read_bytes(),
                    (self.roots[label]/'usr/local/lib/boiler/maintenance/keyboard_runtime/service.py').read_bytes())
        self.assertTrue(calls)
        for root in (a,a/'home'):
            subprocess.run(['mount','-o','remount,bind,ro',str(root)],check=True)
        try:
            with patch.object(integration,'release',source_release):
                self.assertEqual({service.IDENTITY},integration.verify_device_components(a,self.payload,self.options,'A',{service.IDENTITY}))
        finally:
            for root in (a,a/'home'):subprocess.run(['mount','-o','remount,bind,rw',str(root)],check=True)
        first=self.execute('restore','A')['result']['components'][0]
        self.assertFalse(first['configuration_restored']);self.assertEqual([binding(b)['slot']],first['managed_slots'])
        self.assertIn(b'VirtualKeyboardMode=1',self.path.read_bytes())
        self.assertTrue(self.execute('verify','B')['result']['verified'])
        last=self.execute('restore','B')['result']['components'][0]
        self.assertTrue(last['configuration_restored']);self.assertEqual(self.original,self.path.read_bytes())
        self.assertEqual((1000,1000,0o600),(self.path.stat().st_uid,self.path.stat().st_gid,stat.S_IMODE(self.path.stat().st_mode)))

    def test_production_owner_no_links_dependency_and_model_advisory(self):
        self.path.chmod(0o622)
        unavailable=api.options(self.roots['A'],'device:x2-mini',trust_local_test=True,runtime_root=self.runtime)
        self.assertFalse(unavailable['install_available'])
        self.assertIn('ownership',str(unavailable['capabilities'][0]['reason']).lower())
        with self.assertRaises(RuntimeError):self.plan()
        self.path.chmod(0o600)
        # A redirected target and an untrusted user home both refuse plan.
        self.path.unlink();self.path.symlink_to('/etc/passwd')
        with self.assertRaises((OSError,RuntimeError,ValueError)):self.plan()
        self.path.unlink();write(self.path,self.original,0o600,1000)
        home=self.roots['A']/'home/deck';os.chown(home,1234,1234)
        try:
            unavailable=api.options(self.roots['A'],'device:x2-mini',trust_local_test=True,runtime_root=self.runtime)
            self.assertFalse(unavailable['install_available'])
            self.assertIn('untrusted',str(unavailable['capabilities'][0]['reason']).lower())
            with self.assertRaises(RuntimeError):self.plan()
        finally:
            os.chown(home,1000,1000)
        desktop=self.roots['A']/service.DESKTOP;raw=desktop.read_bytes();desktop.unlink()
        try:
            with self.assertRaises((OSError,RuntimeError,ValueError)):self.plan()
        finally:
            write(desktop,raw)
        # No DMI files exist in either synthetic root. This is advisory, not a gate.
        result=self.execute()['result']['components'][0]
        self.assertFalse(result['runtime_validated'])
        self.execute('restore')

    def test_review_target_account_and_partition_binding_rechecked_under_native_locks(self):
        review=self.plan();self.path.write_bytes(self.original+b'# changed after review\n')
        with self.assertRaisesRegex(RuntimeError,'review again'):
            api.execute(self.roots['A'],review,reviewed_sha256=review['review_sha256'],trust_local_test=True,runtime_root=self.runtime)
        write(self.path,self.original,0o600,1000)
        review=self.plan()
        with self.assertRaisesRegex(RuntimeError,'review again'):
            api.execute(self.roots['B'],review,reviewed_sha256=review['review_sha256'],trust_local_test=True,runtime_root=self.runtime)
        with self.assertRaisesRegex(RuntimeError,'marker disagrees'):
            service.Keyboard(self.roots['A']).plan('apply','B')
        self.assertEqual(self.original,self.path.read_bytes())

    def test_interrupted_native_transaction_refuses_wrong_slot_and_conflicting_file(self):
        review=self.plan()
        with patch.object(service.Store,'_after_write',side_effect=RuntimeError('fixture power loss')):
            with self.assertRaisesRegex(RuntimeError,'power loss'):
                api.execute(self.roots['A'],review,reviewed_sha256=review['review_sha256'],trust_local_test=True,runtime_root=self.runtime)
        pending=self.roots['A']/service.STATE/'pending.json';before=pending.read_bytes()
        with self.assertRaisesRegex(RuntimeError,'slot'):
            self.execute('recover','B')
        self.assertEqual(before,pending.read_bytes())
        other=api.options(self.roots['B'],'device:x2-mini',trust_local_test=True,runtime_root=self.runtime)
        self.assertFalse(other['pending']['recover_available'])
        self.assertEqual(binding(self.roots['A'])['slot'],other['pending']['slot'])
        applied=self.path.read_bytes();self.path.write_bytes(b'# user edit after power loss\n')
        with self.assertRaisesRegex(RuntimeError,'Recovery conflict'):self.execute('recover','A')
        self.assertEqual(before,pending.read_bytes())
        self.path.write_bytes(applied)
        self.execute('recover','A');self.execute('restore','A')
        self.assertEqual(self.original,self.path.read_bytes())

    def test_cross_root_store_lock_and_home_only_platform_separation(self):
        self.execute('apply','A')
        a,b=self.roots['A'],self.roots['B']
        one,two=a/service.STATE/'lock',b/service.STATE/'lock'
        self.assertEqual((one.stat().st_dev,one.stat().st_ino),(two.stat().st_dev,two.stat().st_ino))
        code="import sys;sys.path.insert(0,sys.argv[1]);from maintenance.keyboard_runtime.vendor.transactions import Store;from pathlib import Path;r=Path(sys.argv[2]);print('ready',flush=True);s=Store(r,r/'home/.boiler/keyboard-v1');\nwith s.lock(): print('entered',flush=True)"
        store=service.Store(a,a/service.STATE)
        with store.lock():
            child=subprocess.Popen([sys.executable,'-I','-B','-c',code,str(ROOT),str(b)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertEqual('ready',child.stdout.readline().strip());time.sleep(0.2)
                self.assertIsNone(child.poll())
            except BaseException:
                child.kill();child.wait();raise
        stdout,stderr=child.communicate(timeout=30)
        self.assertEqual(0,child.returncode,stderr);self.assertIn('entered',stdout)
        with patch.object(live_root,'write_scope',side_effect=AssertionError('Keyboard must not open root window')):
            self.execute('verify','A');self.execute('restore','A')


if __name__=='__main__':unittest.main(verbosity=2)

