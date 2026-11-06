import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('index_multi',ROOT/'scripts/build-index.py')
builder=importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)

class MultiIndexTests(unittest.TestCase):
    def setUp(self):
        saved_protocol = {name: module for name, module in sys.modules.items()
                          if name == 'protocol' or name.startswith('protocol.')}
        for name in saved_protocol:
            del sys.modules[name]

        def restore_protocol():
            for name in list(sys.modules):
                if name == 'protocol' or name.startswith('protocol.'):
                    del sys.modules[name]
            sys.modules.update(saved_protocol)

        self.addCleanup(restore_protocol)
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.work=Path(self.tmp.name); self.repo=self.work/'repo'; (self.repo/'devices').mkdir(parents=True)
        inventory=json.loads((ROOT/'model-source-files.json').read_bytes())
        for name in ['model-source-files.json']+[row['path'] for row in inventory['files']]:
            target=self.repo/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/name,target)
        def git(*args): return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.PIPE).decode().strip()
        git('init','-q'); git('config','core.autocrlf','false'); git('config','user.name','Fixture'); git('config','user.email','fixture@example.invalid')
        git('remote','add','origin','https://github.com/FlightDan/Boiler-adaptations.git'); git('add','.'); git('commit','-qm','fixture')
        self.commit=git('rev-parse','HEAD')
        self.installer=self.work/'installer'; (self.installer/'protocol').mkdir(parents=True)
        (self.installer/'protocol/__init__.py').write_text('')
        (self.installer/'protocol/manifest.py').write_text('def validate_manifest(value, kind): return value\ndef validate_package(path, expected_manifest_sha256): return None\n')
        (self.installer/'scripts').mkdir()
        (self.installer/'scripts/source-files.json').write_text(json.dumps({'kind':'BoilerSourceWhitelist','files':['protocol/__init__.py','protocol/manifest.py','scripts/source-files.json']}))
        def installer_git(*args): return subprocess.check_output(['git','-C',str(self.installer),*args],stderr=subprocess.PIPE).decode().strip()
        installer_git('init','-q'); installer_git('config','core.autocrlf','false'); installer_git('config','user.name','Fixture'); installer_git('config','user.email','fixture@example.invalid'); installer_git('remote','add','origin','https://github.com/FlightDan/Boiler-installer.git'); installer_git('add','.'); installer_git('commit','-qm','fixture')
        self.installer_commit=installer_git('rev-parse','HEAD')
        self.old={'id':'device:oxp3','version':'0.1.0-preview.1','source':{'kind':'local','path':'old'},'size':1,'sha256':'c'*64,'compatibility':{}}
        self.previous={'schema_version':1,'kind':'DeviceIndex','id':'registry:boiler-devices','version':'0.1.0-preview.1','sequence':1,'generated_at':'2026-01-01T00:00:00Z','revocations':['d'*64],'packages':[self.old],'devices':['devices/oxp3.json']}
        self.previous_path=self.work/'previous.json'; self.previous_path.write_text(json.dumps(self.previous))
        self.models=[]
        for identity in ('device:oxp3','device:x2-mini'):
            folder=self.work/identity.split(':')[1]; folder.mkdir(); (folder/'data').write_bytes(b'data')
            manifest={'schema_version':1,'kind':'DevicePackage','id':identity,'version':'0.1.1','source':{'kind':'github','repository':'FlightDan/Boiler-adaptations','commit':self.commit},'compatibility':{},'minimum_engine_version':'0.1.5','components':[],'files':[{'path':'data','size':4,'sha256':hashlib.sha256(b'data').hexdigest()}]}
            rule='oxp3' if identity=='device:oxp3' else 'x2'
            vendor='oxp3' if rule=='oxp3' else 'x2-mini'
            source='fingerprint/profile/brands/onexplayer/'+vendor+'/current/fingerprint/69-boiler-'+rule+'-fingerprint.rules'
            path=folder/source; path.parent.mkdir(parents=True); path.write_bytes(b'rule')
            item={'path':source,'size':4,'sha256':hashlib.sha256(b'rule').hexdigest()}; manifest['files'].append(item)
            component={'schema_version':1,'kind':'ComponentManifest','id':'oxp3:fingerprint-unlock','version':'0.1.1','default':True,'optional':True,'minimum_engine_version':'0.1.5','compatibility':{'device_ids':[identity.removeprefix('device:')]},'files':[{'source':source,'target':'etc/udev/rules.d/69-boiler-'+rule+'-fingerprint.rules','size':4,'sha256':item['sha256']}]}
            plugin='var/lib/oxp3-installer/decky/runtime/plugins/Boiler-Fingerprint-Unlock/'
            targets=['etc/modprobe.d/boiler-oxp3-fingerprint.conf','etc/systemd/system/boiler-fprintd.service','usr/share/dbus-1/system-services/net.reactivated.Fprint.service',plugin+'profile.json',plugin+'main.py',plugin+'dist/index.js',plugin+'plugin.json',plugin+'package.json','etc/dbus-1/system.d/net.reactivated.Fprint.conf','usr/share/polkit-1/actions/net.reactivated.fprint.device.policy','var/lib/oxp3-installer/fingerprint/libexec/fprintd',*('var/lib/oxp3-installer/fingerprint/bin/fprintd-'+x for x in ('verify','enroll','list','delete'))]
            for n,target in enumerate(targets):
                source='fingerprint/runtime-'+str(n); path=folder/source; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(b'runtime')
                checksum=hashlib.sha256(b'runtime').hexdigest(); manifest['files'].append({'path':source,'size':7,'sha256':checksum}); component['files'].append({'source':source,'target':target,'size':7,'sha256':checksum})
            manifest.update(trust={'status':'index-signature-required','official_release':False},components=[component,{'schema_version':1,'kind':'ComponentManifest','id':'oxp3:legacy-adaptation','version':'0.1.1'}],legacy_capabilities=[{'id':'oxp3:decky','available':True},{'id':'oxp3:wifi','available':False,'default':False}],selection_contract={'kind':'oxp3-legacy-options-v1','component':'oxp3:legacy-adaptation'},shared_adaptations={'components':['oxp3:fingerprint-unlock']})
            (folder/'manifest.json').write_text(json.dumps(manifest))
            archive=self.work/(folder.name+'.zip'); self.zip(folder,archive)
            self.models.append((folder,archive,'https://github.com/FlightDan/Boiler-adaptations/releases/download/v0.1.1/'+archive.name))
    def zip(self, folder, archive):
        with zipfile.ZipFile(archive,'w') as z:
            for path in folder.rglob('*'):
                if path.is_file(): z.write(path,path.relative_to(folder).as_posix())
    def run_build(self,**overrides):
        args=dict(previous_index=self.previous_path,previous_sha256=builder.digest(self.previous_path),sequence=2,source_commit=self.commit,version='0.1.1',installer_root=self.installer,installer_commit=self.installer_commit); args.update(overrides)
        with mock.patch.object(builder,'ROOT',self.repo): return builder.build_models(self.models,self.work/'out.json',**args)
    def change(self, index, callback):
        folder,archive,_=self.models[index]; path=folder/'manifest.json'; obj=json.loads(path.read_text()); callback(obj); path.write_text(json.dumps(obj)); self.zip(folder,archive)
    def test_preloaded_protocol_is_rejected(self):
        sys.modules['protocol'] = types.ModuleType('protocol')
        with self.assertRaisesRegex(ValueError, 'Protocol already loaded'):
            self.run_build()
        self.assertFalse((self.work/'out.json').exists())

    def test_two_models_preserve_history_and_bind_bytes(self):
        result=self.run_build(); self.assertEqual(result['devices'],['devices/oxp3.json','devices/x2-mini.json']); self.assertEqual(result['packages'][0],self.old); self.assertEqual(result['revocations'],self.previous['revocations']); self.assertEqual(result['sequence'],2)
        oxp3,x2=result['packages'][1:]
        self.assertEqual(x2['name']['en'],'ONEXPLAYER X2 Mini / Mini PRO')
        self.assertEqual(x2['hardware_matching'],{'mode':'advisory-exact-dmi-v1','pairs':[{'manufacturer':'ONE-NETBOOK','model':'ONEXPLAYER X2 Mini'},{'manufacturer':'ONE-NETBOOK','model':'ONEXPLAYER X2 Mini PRO'}]})
        self.assertIn('physical hardware',x2['notes'][0])
        for entry,model in ((oxp3,'oxp3'),(x2,'x2-mini')):
            self.assertEqual(entry['source_url'],'https://github.com/FlightDan/Boiler-adaptations/tree/'+self.commit+'/brands/onexplayer/'+model)
        for row,(folder,archive,_) in zip(result['packages'][1:],self.models):
            self.assertEqual(row['sha256'],builder.digest(folder/'manifest.json')); self.assertEqual(row['source']['sha256'],builder.digest(archive)); self.assertEqual(row['minimum_engine_version'],'0.1.5')
    def test_rejects_rollback_and_source_identity(self):
        for kwargs in ({'sequence':1},{'source_commit':'a'*40},{'version':'0.1.2'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.run_build(**kwargs)
        self.change(1,lambda m:m['source'].update(repository='FlightDan/Boiler-devices'))
        with self.assertRaises(ValueError): self.run_build()
    def test_rejects_low_engine_and_tampering(self):
        self.change(1,lambda m:m.update(minimum_engine_version='0.1.4'))
        with self.assertRaises(ValueError): self.run_build()
        self.change(1,lambda m:m.update(minimum_engine_version='0.1.5'))
        self.models[1][1].write_bytes(b'bad')
        with self.assertRaises((ValueError,zipfile.BadZipFile)): self.run_build()
        self.zip(self.models[1][0],self.models[1][1]); (self.models[1][0]/'data').write_bytes(b'evil')
        # The actual protocol validator checks directory bytes; independently test ZIP inventory mismatch.
        with zipfile.ZipFile(self.models[1][1],'a') as z: z.writestr('extra',b'evil')
        with self.assertRaises(ValueError): self.run_build()
    def test_previous_digest_binds_verified_bytes(self):
        old=builder.digest(self.previous_path)
        with self.assertRaises(ValueError): self.run_build(previous_sha256='0'*64)
        self.previous_path.write_text(json.dumps(dict(self.previous, sequence=9)))
        with self.assertRaises(ValueError): self.run_build(previous_sha256=old,sequence=10)

    def test_devices_preserved_deduplicated_and_invalid_rejected(self):
        for value in (None, 'devices/oxp3.json', [1], ['../devices/old.json'], ['devices/../old.json'], ['devices/old.txt']):
            with self.subTest(value=value):
                self.previous['devices']=value; self.previous_path.write_text(json.dumps(self.previous))
                with self.assertRaises(ValueError): self.run_build()
                self.assertFalse((self.work/'out.json').exists())
        self.previous['devices']=['devices/old.json','devices/oxp3.json','devices/old.json','devices/x2-mini.json']
        self.previous_path.write_text(json.dumps(self.previous))
        result=self.run_build()
        self.assertEqual(result['devices'],['devices/old.json','devices/oxp3.json','devices/x2-mini.json'])
        self.assertEqual(result['packages'][0],self.old)
        self.assertEqual(result['sequence'],2)
    def test_rejects_fixed_url_rebinding(self):
        p,a,url=self.models[0]
        self.previous['packages'].append({'id':'device:old','version':'1.0.0','source':{'kind':'https','url':url,'sha256':'1'*64,'size':1},'size':1,'sha256':'a'*64,'compatibility':{}})
        self.previous_path.write_text(json.dumps(self.previous))
        with self.assertRaisesRegex(ValueError,'conflicting archive'): self.run_build()
        self.assertFalse((self.work/'out.json').exists())
    def test_rejects_missing_fingerprint_contract(self):
        changes=[lambda m:m.update(purpose='maintenance'),lambda m:m.pop('trust'),lambda m:m.update(components=[]),lambda m:m['components'][0].update(default=False),lambda m:m['components'][0].update(optional=False),lambda m:m['components'][0].update(selection_policy='explicit-opt-in'),lambda m:m['components'][0].update(requires=['oxp3:legacy-adaptation']),lambda m:m['components'][0].update(hooks={'apply':'scripts/missing.sh'}),lambda m:m['components'][0]['compatibility'].update(device_ids=['oxp3','x2-mini']),lambda m:m['components'][0]['files'][1].update(target='etc/unexpected'),lambda m:m['components'][0].update(minimum_engine_version='0.1.4'),lambda m:m['components'][0]['files'][0].update(target='etc/udev/rules.d/wrong.rules'),lambda m:m['components'][0]['files'].pop(),lambda m:m['legacy_capabilities'][0].update(available=False),lambda m:m.update(selection_contract={})]
        for change in changes:
            with self.subTest(change=change):
                folder,archive,_=self.models[0]; original=(folder/'manifest.json').read_text()
                self.change(0,change)
                with self.assertRaises(ValueError): self.run_build()
                self.assertFalse((self.work/'out.json').exists())
                (folder/'manifest.json').write_text(original); self.zip(folder,archive)
    def test_rejects_untracked_and_modified_protocol(self):
        extra=self.installer/'protocol/other.py'; extra.write_text('untracked')
        with self.assertRaisesRegex(ValueError,'untracked'): self.run_build()
        extra.unlink(); (self.installer/'protocol/manifest.py').write_text('changed')
        with self.assertRaisesRegex(ValueError,'committed bytes'): self.run_build()
        self.assertFalse((self.work/'out.json').exists())
    def test_rejects_floating_url_and_revoked_manifest(self):
        p,a,_=self.models[0]; self.models[0]=(p,a,'https://github.com/FlightDan/Boiler-adaptations/releases/latest/download/model.zip')
        with self.assertRaises(ValueError): self.run_build()
        self.models[0]=(p,a,'https://github.com/FlightDan/Boiler-adaptations/releases/download/tag/model.zip')
        self.previous['revocations'].append(builder.digest(p/'manifest.json')); self.previous_path.write_text(json.dumps(self.previous))
        with self.assertRaises(ValueError): self.run_build()

if __name__=='__main__': unittest.main()
