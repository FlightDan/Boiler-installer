"""Synthetic first-install model retention; run as root on Linux."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'components'), str(ROOT/'guest')]
from integration import stage
from maintenance import models
from maintenance.engine import Engine


def sha(data): return hashlib.sha256(data).hexdigest()
def record(source, data, **extra): return dict(source=source, size=len(data), sha256=sha(data), **extra)
def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True)+'\n', encoding='utf-8')
    if os.name == 'posix': path.chmod(0o600)

@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'requires genuine Linux root and synthetic target')
class FirstInstallModelRetention(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='boiler-retain-test-'); self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name); self.root=self.work/'root'; self.root.mkdir()
        (self.root/'etc').mkdir(); (self.root/'etc/os-release').write_text('ID=steamos\nVERSION_ID=3.10\nBUILD_ID=fixture\n')
        (self.root/'usr/lib/modules/kernel-fixture').mkdir(parents=True)
        (self.root/'usr/bin').mkdir(parents=True); elf=bytearray(20); elf[:6]=b'\x7fELF\x02\x01'; elf[18:20]=(62).to_bytes(2,'little'); (self.root/'usr/bin/env').write_bytes(elf)
        self.payload=self.work/'payload'; self.package=self.payload/'device-packages/oxp3'; (self.package/'scripts').mkdir(parents=True)
        self.capabilities=[dict(id='oxp3:base',category='repairs',name={'zh':'基础','en':'Base'},available=True,default=True,dependencies=[])]
        inspect=dict(executable=True,current=None,capabilities=self.capabilities,target={'slot':'A'})
        plan=dict(executable=True,plan_sha256='a'*64,summary={'action':'verify','slot':'A','file_count':1},warnings=[])
        shell=("#!/bin/bash\ncase \"$1\" in\ninspect) echo 'BOILER_MODEL_RESULT="+json.dumps(inspect,ensure_ascii=False)+"' ;;\nplan) echo 'BOILER_MODEL_RESULT="+json.dumps(plan)+"' ;;\n*) exit 1 ;;\nesac\n").encode()
        (self.package/'scripts/maintenance.sh').write_bytes(shell)
        (self.package/'data').write_bytes(b'fixture-device')
        devfile=record('data',b'fixture-device',target='etc/boiler-fixture-device',mode=0o644)
        component=dict(schema_version=1,kind='ComponentManifest',id='oxp3:fixture',version='0.1.0',files=[devfile],requires=[],conflicts=[],hooks={})
        self.manifest=dict(schema_version=1,kind='DevicePackage',id='device:oxp3',version='0.1.0-test.1',source={'kind':'local','path':'fixture'},compatibility={'architectures':['x86_64'],'os_ids':['steamos'],'kernels':['kernel-fixture']},components=[component],files=[dict(path='data',size=len(b'fixture-device'),sha256=sha(b'fixture-device')),dict(path='scripts/maintenance.sh',size=len(shell),sha256=sha(shell))],maintenance_contract=dict(models.CONTRACT),legacy_kde_languages=['system'],legacy_capabilities=self.capabilities)
        self.save_manifest()
        coredata=b'fixture-maintenance'; (self.payload/'integration').mkdir(parents=True); (self.payload/'integration/core').write_bytes(coredata)
        corefile=record('core',coredata,target='usr/lib/boiler-fixture-core',mode=0o644)
        core=dict(schema_version=1,kind='ComponentManifest',id='core:maintenance',version='0.1.0',files=[corefile],requires=[],conflicts=[],hooks={})
        write_json(self.payload/'integration/manifest.json',dict(schema=1,components={'core:maintenance':dict(available=True,files=[corefile],component=core)}))
        self.options=dict(schema=1,repairs=[],plugins=['core:maintenance'],device_components=['oxp3:fixture'],device_package='oxp3',device_trust='developer-session',device_manifest_sha256=self.anchor)
        self.state=self.root/'var/lib/boiler/maintenance'; self.pointer=self.state/'models/package-oxp3.json'
    def save_manifest(self):
        write_json(self.package/'manifest.json',self.manifest); self.anchor=sha((self.package/'manifest.json').read_bytes())
    def install(self, **changes): return stage(self.root,self.payload,dict(self.options,**changes),'A')
    def test_actual_stage_retains_authenticated_package_for_offline_maintenance(self):
        with self.assertRaises(Exception): models._package('device:oxp3',Engine(self.root,read_only=True),self.work/'empty',trust_local_test=True)
        self.assertEqual('files_installed',self.install()['status'])
        pointer=json.loads(self.pointer.read_bytes()); self.assertEqual(self.anchor,pointer['manifest_sha256'])
        retained=self.state/'models/packages'/self.anchor
        self.assertEqual((self.package/'scripts/maintenance.sh').read_bytes(),(retained/'scripts/maintenance.sh').read_bytes())
        shutil.rmtree(self.package)
        self.assertEqual(self.anchor,models._package('device:oxp3',Engine(self.root,read_only=True),self.work/'empty',trust_local_test=True)['manifest_sha256'])
        with self.assertRaises(Exception): models._package('device:oxp3',Engine(self.root,read_only=True),self.work/'empty',trust_local_test=False)
        self.assertTrue(models.options(self.root,'device:oxp3',trust_local_test=True,runtime_root=ROOT)['executable'])
        self.assertEqual('verify',models.plan_model(self.root,'device:oxp3',[],[],action='verify',trust_local_test=True,runtime_root=ROOT)['action'])
    def test_no_core_no_device_or_contract_does_not_retain(self):
        self.install(plugins=[]); self.assertFalse(self.pointer.exists())
        # Use independent roots to avoid previous ownership affecting these cases.
    def test_no_selected_device_does_not_retain(self):
        self.install(device_components=[]); self.assertFalse(self.pointer.exists())
    def test_be201_provider_is_retained_without_other_model_choices(self):
        from test_be201_model_resource import ModelFirmwareTests
        fixture = ModelFirmwareTests(methodName='test_unknown_facts_stop_before_slot_writes')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for row in fixture.manifest['files']:
            source = fixture.root / row['path']
            destination = self.package / row['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            self.manifest['files'].append(row)
        self.manifest['be201_firmware'] = fixture.manifest['be201_firmware']
        self.save_manifest()
        self.install(device_components=[], device_manifest_sha256=self.anchor)
        self.assertTrue(self.pointer.is_file())
        retained = self.state / 'models/packages' / self.anchor
        self.assertTrue((retained / fixture.manifest['be201_firmware']['resource']).is_file())

    def test_unrecognized_contract_does_not_retain(self):
        self.manifest.pop('maintenance_contract'); self.save_manifest(); self.install(device_manifest_sha256=self.anchor); self.assertFalse(self.pointer.exists())
    def test_tampered_input_fails_before_component_writes(self):
        (self.package/'data').write_bytes(b'tampered')
        with self.assertRaises(Exception): self.install()
        self.assertFalse((self.root/'usr/lib/boiler-fixture-core').exists())
    def test_source_changed_after_device_write_fails_before_success(self):
        original = Engine.apply
        def mutate_after_device(engine, component, *args, **kwargs):
            result = original(engine, component, *args, **kwargs)
            if component['id'] == 'oxp3:fixture':
                (self.package/'data').write_bytes(b'changed-after-install')
            return result
        with patch.object(Engine, 'apply', mutate_after_device):
            with self.assertRaises(Exception): self.install()
        self.assertFalse(self.pointer.exists())
        self.assertFalse((self.root/'home/.boiler/integration/A/result.json').exists())
    def test_existing_different_pointer_blocks_before_component_writes(self):
        write_json(self.pointer,dict(kind='RetainedModelPackage',model_id='device:oxp3',manifest_sha256='b'*64))
        with self.assertRaisesRegex(Exception,'different retained'): self.install()
        self.assertFalse((self.root/'usr/lib/boiler-fixture-core').exists())
    def test_pending_blocks_before_component_writes(self):
        write_json(self.state/'models/pending.json',dict(kind='PendingModelAction',model_id='device:oxp3',id='a'*32))
        with self.assertRaisesRegex(Exception,'Interrupted model'): self.install()
        self.assertFalse((self.root/'usr/lib/boiler-fixture-core').exists())
    def test_same_pointer_can_be_reused(self):
        self.install(); before=self.pointer.read_bytes(); self.install(); self.assertEqual(before,self.pointer.read_bytes())
    def test_tampered_retained_payload_blocks_before_component_writes(self):
        self.install(); (self.state/'models/packages'/self.anchor/'data').write_bytes(b'changed')
        core=self.root/'usr/lib/boiler-fixture-core'; core.unlink()
        with self.assertRaises(Exception): self.install()
        self.assertFalse(core.exists())

if __name__ == '__main__': unittest.main()
