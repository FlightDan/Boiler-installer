"""Complete X2 facade reuse for a fresh project, on temporary targets."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'components'))
from maintenance import models
from maintenance.engine import Engine,MaintenanceError
from protocol import compatible
from protocol.model_options import validated_selection
from protocol.manifest import ProtocolError
import integration


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class CompleteX2Facade(unittest.TestCase):
    def setUp(self):
        self.base=load('_x2_reuse_original_model_fixture',ROOT/'tests/test_maintenance_models.py')
        self.base.MODEL='device:x2-mini'
        self.f=self.base.MaintenanceModelsTests(methodName='runTest')
        self.f.setUp();self.addCleanup(self.f.doCleanups)
        old=self.f.package
        self.f.package=old.parent/'x2-mini'
        shutil.move(old,self.f.package)
        original=self.f.write_package
        def write_package():
            original()
            descriptor=self.f.runtime/'cache/model-bundles/manifest.json'
            value=json.loads(descriptor.read_text());value['packages'][0]['directory']='cache/model-bundles/packages/x2-mini'
            descriptor.write_text(json.dumps(value))
        self.f.write_package=write_package
        self.f.write_package()

    def test_fixed_external_identity_accepts_shared_options(self):
        value=validated_selection(self.f.manifest,[self.base.BASE],[],acknowledge_update_hold=True)
        self.assertEqual(value['repairs'],[self.base.BASE])
        bad=dict(self.f.manifest,id='device:unreviewed')
        with self.assertRaises(ProtocolError):
            validated_selection(bad,[self.base.BASE],[],acknowledge_update_hold=True)

    def test_full_x2_maintenance_apply_retains_exact_package_then_verifies_and_restores_without_cache(self):
        f=self.f
        view=models.options(f.root,'device:x2-mini',trust_local_test=True,runtime_root=f.runtime)
        self.assertTrue(view['executable']);self.assertEqual(view['model']['id'],'device:x2-mini')
        self.assertEqual(view['capabilities'],f.capabilities)
        applied=f.execute(f.plan())
        self.assertEqual(applied['status'],'verified')
        self.assertTrue((f.state/'models/package-x2-mini.json').is_file())
        (f.package/'manifest.json').write_bytes(b'corrupt-current-cache')
        self.assertTrue(f.execute(f.plan(action='verify'))['result']['verified'])
        shutil.rmtree(f.runtime/'cache/model-bundles')
        verified=f.execute(f.plan(action='verify'))
        self.assertTrue(verified['result']['verified'])
        restored=f.execute(f.plan(action='restore'))
        self.assertEqual(restored['result']['status'],'restored')
        self.assertIsNone(f.current)

    def test_without_trust_or_fixed_contract_model_code_is_not_authorized(self):
        f=self.f
        before=f.files()
        view=models.options(f.root,'device:x2-mini',runtime_root=f.runtime)
        self.assertFalse(view['executable']);f.adapter.assert_not_called()
        f.manifest.pop('maintenance_contract');f.write_package()
        with self.assertRaisesRegex(MaintenanceError,'maintenance entry'):
            models.options(f.root,'device:x2-mini',trust_local_test=True,runtime_root=f.runtime)
        f.adapter.assert_not_called();self.assertEqual(before,f.files())

    def test_legacy_installer_selection_uses_external_x2_with_shared_internal_ids(self):
        f=self.f
        options=dict(device_legacy_options=dict(repairs=[self.base.BASE],plugins=[],acknowledge_legacy_update_hold=True),
            installer_language='en-US',kde_language='system',repairs=[],plugins=[])
        value=integration.device_legacy_selection(f.manifest,options,[dict(id='oxp3:legacy-adaptation')])
        self.assertEqual(value['repairs'],[self.base.BASE]);self.assertEqual(value['slot'],'guest')
        self.assertEqual(integration.device_hook_environment(dict(legacy_selection=value),dict(id='oxp3:legacy-adaptation')),
            {'BOILER_OXP3_SELECTION':json.dumps(value,sort_keys=True,separators=(',',':'))})





    def test_engine_capability_rejects_older_hosts_for_complete_package(self):
        value=dict(minimum_engine_version='0.1.2')
        self.assertTrue(compatible(value,{}))
        with patch('protocol.manifest.ENGINE_VERSION','0.1.1'):
            self.assertFalse(compatible(value,{}))






class RuntimeModelAdvice(unittest.TestCase):
    def setUp(self):
        self.overlay=Path(os.environ.get('X2_COMPLETE_BRIDGE_OVERLAY',str(ROOT/'adaptations/brands/onexplayer/common/complete-bridge')))
        if not (self.overlay/'overlay-lock.json').is_file():
            self.skipTest('Explicit reviewed complete bridge overlay is required')

    def test_bridge_both_selection_boundaries_accept_fixed_x2(self):
        adapter=load('_x2_model_adapter',self.overlay/'adapter.py')
        maintenance=load('_x2_model_maintenance_adapter',self.overlay/'maintenance_adapter.py')
        catalog=dict(languages=['en-US'],kde_languages=['system'],repairs=[dict(id='input',dependencies=[])],plugins=[])
        choice=dict(schema_version=1,slot='guest',repairs=['oxp3:input'],plugins=[],installer_language='en-US',kde_language='system',acknowledge_legacy_update_hold=True)
        with tempfile.TemporaryDirectory() as tmp:
            package=Path(tmp);(package/'payload/legacy/components').mkdir(parents=True)
            (package/'payload/legacy/components/options.json').write_text(json.dumps(catalog))
            shutil.copy2(self.overlay/'adapter.py',package/'adapter.py')
            manifest=dict(id='device:x2-mini',selection_contract={'kind':'oxp3-legacy-options-v1','component':'oxp3:legacy-adaptation'},
                maintenance_contract=maintenance.CONTRACT,legacy_capabilities=[dict(id='oxp3:input',category='repairs',available=True,dependencies=[])])
            (package/'manifest.json').write_text(json.dumps(manifest))
            with patch.object(adapter,'PACKAGE',package),patch.dict(os.environ,BOILER_OXP3_SELECTION=json.dumps(choice)):
                actual,_=adapter.runtime_selection(catalog)
            self.assertEqual(actual['repairs'],['oxp3:input'])
            wire={key:value for key,value in choice.items() if key!='slot'}
            actual,*_=maintenance.selection(dict(schema_version=1,selection=wire),package)
            self.assertEqual(actual['repairs'],['oxp3:input'])

    def test_speaker_model_mismatch_is_advice_while_codec_checks_remain(self):
        module=load('_x2_speaker_advice',self.overlay/'upstream-repairs/speaker/speaker_route.py')
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stderr(io.StringIO()) as output:
            root=Path(tmp);self.assertTrue(module.supported(root))
            self.assertIn('continuing',output.getvalue())
            self.assertFalse(module.local_codec('0',root))

    def test_xevma_model_mismatch_is_advice_but_kernel_and_module_hash_still_required(self):
        module=load('_x2_xevma_advice',self.overlay/'upstream-repairs/xevmafix/load.py')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,data in {'proc/sys/kernel/osrelease':module.KERNEL,'etc/os-release':'ID=steamos\nVERSION_ID=3.10\nBUILD_ID=20260924.1000\n',module.MODULE.lstrip('/'):'module'}.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data)
            with patch.object(module,'MODULE_SHA256',hashlib.sha256(b'module').hexdigest()),contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(module.validate(root),root/module.MODULE.lstrip('/'))
            self.assertIn('continuing',output.getvalue())
            (root/'proc/sys/kernel/osrelease').write_text('wrong-kernel')
            with self.assertRaisesRegex(RuntimeError,'exact.*kernel'):module.validate(root)


class CompleteBridgeBuilder(unittest.TestCase):
    def setUp(self):
        self.builder=load('_x2_complete_bridge_builder',ROOT/'adaptations/scripts/build-complete-bridge.py')

    def test_wrong_base_is_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'base';base.mkdir();(base/'manifest.json').write_text('{}')
            output=Path(tmp)/'output'
            with self.assertRaises(ValueError):self.builder.build(base,output)
            self.assertFalse(output.exists())

    def test_inside_input_or_existing_output_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)/'base';base.mkdir()
            for output in (base/'child',base):
                with self.assertRaisesRegex(ValueError,'Fresh output'):self.builder.build(base,output)
            self.assertEqual(list(base.iterdir()),[])

    def test_source_transform_refuses_unreviewed_bytes(self):
        for name in self.builder.INPUTS:
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'base source changed'):
                self.builder.transform(name,b'not authenticated source')

    def test_explicit_complete_base_reproduces_reviewed_overlay(self):
        supplied=os.environ.get('X2_COMPLETE_BASE')
        if not supplied:self.skipTest('Explicit authenticated complete OXP3 package required')
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'overlay';result=self.builder.build(Path(supplied),output)
            self.assertEqual(result['lock_sha256'],self.builder.LOCK_SHA256)
            actual={path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()}
            self.assertEqual(actual,set(self.builder.INPUTS)|{'overlay-lock.json'})
            for name,pin in self.builder.OUTPUTS.items():
                self.assertEqual(hashlib.sha256((output/name).read_bytes()).hexdigest(),pin)


if __name__=='__main__':unittest.main()
