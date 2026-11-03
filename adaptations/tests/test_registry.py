import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('registry_builder', ROOT/'scripts/build-index.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class RegistryTests(unittest.TestCase):
    def test_readonly_hardware_advice_contract(self):
        value={'mode':'advisory-exact-dmi-v1','pairs':[{'manufacturer':'ONE-NETBOOK','model':'ONEXPLAYER 3'}]}
        self.assertEqual(builder.hardware_advice(value),value)
        for invalid in (dict(value,script='scripts/run.sh'),dict(value,mode='regex'),dict(value,pairs=[]),
                        dict(value,pairs=value['pairs']*33),dict(value,pairs=[{'manufacturer':'x','model':'bad\n'}])):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): builder.hardware_advice(invalid)

    def fixture(self, base):
        base.mkdir()
        (base/'payload').mkdir()
        (base/'payload/data').write_bytes(b'configuration\n')
        entry = {'path': 'payload/data', 'sha256': hashlib.sha256(b'configuration\n').hexdigest(), 'size': 14}
        value = {'schema_version': 1, 'kind': 'DevicePackage', 'id': 'device:fixture', 'version': '0.1.0',
                 'source': {'kind': 'local', 'path': 'fixture'}, 'compatibility': {}, 'components': [], 'files': [entry]}
        (base/'manifest.json').write_text(json.dumps(value))

    def release_fixture(self, work):
        package, registry = work/'package', work/'registry'
        self.fixture(package)
        manifest = json.loads((package/'manifest.json').read_text())
        manifest.update(id='device:oxp3', source={'kind':'github','repository':'FlightDan/OneXPlayer3-SteamOS','commit':'a'*40})
        (package/'manifest.json').write_text(json.dumps(manifest))
        registry.mkdir(); (registry/'devices').mkdir()
        for name in ('.gitattributes','device-index.json','devices/oxp3.json'):
            shutil.copyfile(ROOT/name, registry/name)
        def git(*args):
            return subprocess.check_output(['git','-C',str(registry),*args],stderr=subprocess.PIPE)
        git('init','-q'); git('config','core.autocrlf','false')
        git('config','user.name','Registry fixture'); git('config','user.email','registry-fixture@example.invalid')
        git('remote','add','origin','https://github.com/FlightDan/Boiler-devices.git')
        git('add','.'); git('commit','-qm','Disposable committed registry metadata')
        archive = work/'model.zip'
        self.zip_package(package, archive)
        return package, registry, archive

    def zip_package(self, package, archive):
        with zipfile.ZipFile(archive,'w') as stream:
            for path in sorted(package.rglob('*')):
                if path.is_file(): stream.write(path,path.relative_to(package).as_posix())

    def candidate(self, package, registry, archive, output, **kwargs):
        values = dict(archive=archive,asset_url='https://github.com/FlightDan/OneXPlayer3-SteamOS/releases/download/boiler-device-v0.1.0/model.zip',sequence=1)
        values.update(kwargs)
        with mock.patch.object(builder,'ROOT',registry):
            return builder.build(package,output,**values)

    def test_production_draft_advertises_no_unsigned_package(self):
        index = json.loads((ROOT/'device-index.json').read_text())
        self.assertEqual(index['packages'], [])
        self.assertEqual(index['sequence'], 0)
        device = json.loads((ROOT/'devices/oxp3.json').read_text())
        self.assertEqual({item['status'] for item in device['compatibility_records']}, {'supported', 'experimental', 'blocked'})
        self.assertTrue(all(item['evidence'] and item['note'] for item in device['compatibility_records']))

    def test_local_index_uses_actual_manifest_bytes(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work)/'package'
            self.fixture(source)
            result = builder.build(source, Path(work)/'out/index.json')
            entry = result['packages'][0]
            self.assertEqual(entry['sha256'], hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest())
            self.assertEqual(entry['size'], (source/'manifest.json').stat().st_size)
            self.assertEqual(entry['source']['kind'], 'local')
            self.assertFalse(result['trust']['official_release'])

    def test_tampered_inventory_is_not_indexed(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work)/'package'
            self.fixture(source)
            (source/'payload/data').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                builder.build(source, Path(work)/'out/index.json')

    def test_release_candidate_binds_zip_manifest_and_committed_experimental_metadata(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            package, registry, archive = self.release_fixture(work)
            index = self.candidate(package,registry,archive,work/'candidate.json')
            entry = index['packages'][0]
            self.assertEqual(index['sequence'],1)
            self.assertEqual(index['trust'],{'status':'index-signature-required','official_release':False})
            self.assertEqual(entry['sha256'],builder.digest(package/'manifest.json'))
            self.assertEqual(entry['source']['sha256'],builder.digest(archive))
            self.assertEqual(entry['source']['size'],archive.stat().st_size)
            self.assertEqual(entry['source']['kind'],'https')
            self.assertEqual(entry['status'],'experimental')
            self.assertTrue(entry['known_issues'] and entry['compatibility_records'])
            self.assertIn('/tree/'+'a'*40+'/boiler-device',entry['source_url'])
            self.assertEqual(entry['verification_scope'],'local-manifest-and-archive-inventory')

    def test_release_candidate_rejects_corrupt_zip_mismatched_manifest_and_floating_source(self):
        for change in ('corrupt','manifest','floating','extra','link'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as work:
                work = Path(work)
                package, registry, archive = self.release_fixture(work)
                if change == 'corrupt': archive.write_bytes(b'not a ZIP')
                elif change in ('manifest','floating'):
                    value = json.loads((package/'manifest.json').read_text())
                    if change == 'manifest': value['version']='0.1.1'
                    else: value['source']['commit']='main'
                    (package/'manifest.json').write_text(json.dumps(value))
                else:
                    with zipfile.ZipFile(archive,'a') as stream:
                        info = zipfile.ZipInfo('unexpected' if change == 'extra' else 'payload/link')
                        if change == 'link': info.external_attr = (0o120777 << 16)
                        stream.writestr(info,b'unreviewed')
                with self.assertRaises((ValueError,zipfile.BadZipFile)):
                    self.candidate(package,registry,archive,work/'candidate.json')
                self.assertFalse((work/'candidate.json').exists())

    def test_release_candidate_rejects_uncommitted_metadata_bad_urls_and_nonmonotonic_sequence(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            package, registry, archive = self.release_fixture(work)
            for url in ('https://user@github.com/FlightDan/OneXPlayer3-SteamOS/releases/download/tag/model.zip',
                        'https://github.com/Other/Model/releases/download/tag/model.zip',
                        'https://github.com/FlightDan/OneXPlayer3-SteamOS/releases/latest/download/model.zip',
                        'https://github.com/FlightDan/OneXPlayer3-SteamOS/releases/download/tag/model.zip?token=secret',
                        '\nhttps://github.com/FlightDan/OneXPlayer3-SteamOS/releases/download/tag/model.zip',
                        'https://github.com/FlightDan/OneXPlayer3-SteamOS/releases/download/tag/model name.zip'):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    self.candidate(package,registry,archive,work/'candidate.json',asset_url=url)
            previous = json.loads((registry/'device-index.json').read_text()); previous['sequence']=3
            (work/'previous.json').write_text(json.dumps(previous))
            for sequence in (0,1,3):
                with self.subTest(sequence=sequence), self.assertRaises(ValueError):
                    self.candidate(package,registry,archive,work/'candidate.json',sequence=sequence,previous_index=work/'previous.json')
            (registry/'devices/oxp3.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'committed bytes'):
                self.candidate(package,registry,archive,work/'candidate.json')

    def test_fresh_public_checkout_with_autocrlf_keeps_release_metadata_exact(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            package, registry, archive = self.release_fixture(work)
            checkout = work/'fresh-public-clone'
            subprocess.check_output(['git','-c','core.autocrlf=true','clone','-q',str(registry),str(checkout)],stderr=subprocess.PIPE)
            subprocess.check_output(['git','-C',str(checkout),'remote','set-url','origin','https://github.com/FlightDan/Boiler-devices.git'])
            index = self.candidate(package,checkout,archive,work/'candidate.json')
            self.assertEqual(index['packages'][0]['status'],'experimental')

    def test_release_preflight_rejects_unfetchable_component_contracts_before_output(self):
        for change in ('hook','source','digest','target','entry','identity','version','compatibility'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as work:
                work=Path(work)
                package,registry,archive=self.release_fixture(work)
                manifest=json.loads((package/'manifest.json').read_text())
                source=manifest['files'][0]
                component=dict(schema_version=1,kind='ComponentManifest',id='oxp3:test',version='0.1.0',files=[
                    dict(source=source['path'],target='etc/fixture.conf',size=source['size'],sha256=source['sha256'])])
                manifest['components']=[component]
                if change == 'hook': component['hooks']={'apply':'scripts/missing.sh'}
                elif change == 'source': component['files'][0]['source']='payload/missing'
                elif change == 'digest': component['files'][0]['sha256']='0'*64
                elif change == 'target': component['files'][0]['target']='etc/CON'
                elif change == 'entry': manifest['maintenance_entry']='scripts/missing.sh'
                elif change == 'identity': component['id']='raw-legacy-id'
                elif change == 'version': component['version']='main'
                else: component['compatibility']={'unknown_field':['any']}
                (package/'manifest.json').write_text(json.dumps(manifest))
                self.zip_package(package,archive)
                with self.assertRaises(ValueError):
                    self.candidate(package,registry,archive,work/'candidate.json')
                self.assertFalse((work/'candidate.json').exists())

    def test_registry_metadata_reads_one_pinned_commit_snapshot(self):
        with tempfile.TemporaryDirectory() as work:
            work=Path(work)
            package,registry,archive=self.release_fixture(work)
            commands=[]
            original=builder.subprocess.check_output
            def capture(command,**kwargs):
                commands.append(command)
                return original(command,**kwargs)
            with mock.patch.object(builder.subprocess,'check_output',side_effect=capture):
                self.candidate(package,registry,archive,work/'candidate.json')
            shows=[command[-1] for command in commands if 'show' in command]
            self.assertEqual(len(shows),2)
            self.assertEqual(len({value.split(':',1)[0] for value in shows}),1)
            self.assertTrue(all(len(value.split(':',1)[0])==40 for value in shows))


if __name__ == '__main__':
    unittest.main()
