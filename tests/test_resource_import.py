import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.manifest import ProtocolError
from protocol.resource_import import import_resource_pool
from protocol.resource_pool import decoder


def fixture(root):
    toon=decoder(); files={}
    def write(name,data):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);files[name]=data
    def doc(name,value):write(name,(toon.dumps(value)+'\n').encode())
    write('licenses/example',b'license'); write('common/example/files/data',b'payload')
    payload=dict(path='common/example/files/data',size=7,sha256=hashlib.sha256(b'payload').hexdigest())
    resource=dict(schema_version=1,kind='BoilerResource',id='resource:example',version='1.0.0',dependencies=[],
        payload=dict(files=[payload]),install=dict(kind='files-v1',files=[dict(source=payload['path'],target='etc/example',mode=420,scope='root-slots')]),
        verify=dict(kind='sha256-v1'),restore=dict(kind='transaction-backup-v1'),license_files=['licenses/example'])
    path='common/example/resource.toon';doc(path,resource)
    refs=[dict(id=resource['id'],version=resource['version'],path=path,sha256=hashlib.sha256(files[path]).hexdigest())]
    model=dict(schema_version=1,kind='BoilerResourceModel',id='device:example',name='Example',resources=[dict(resource=resource['id'])])
    path='models/example.toon';doc(path,model)
    models=[dict(id=model['id'],path=path,sha256=hashlib.sha256(files[path]).hexdigest())]
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,
        resources=refs,models=models,files=[dict(path=n,size=len(d),sha256=hashlib.sha256(d).hexdigest()) for n,d in sorted(files.items())])
    doc('catalog.toon',catalog);write('catalog.json',(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n').encode())


class ResourceImportTests(unittest.TestCase):
    def test_explicit_session_directory_and_archive_import(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);pool=root/'pool';pool.mkdir();fixture(pool)
            with self.assertRaisesRegex(ProtocolError,'Explicit local-session'):
                import_resource_pool(pool,root/'denied')
            result=import_resource_pool(pool,root/'directory',trust_local_test=True)
            self.assertFalse(result['deployment_available']);self.assertEqual(result['resource_count'],1)
            archive=root/'resources.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for p in pool.rglob('*'):
                    if p.is_file():z.write(p,'wrapped/'+p.relative_to(pool).as_posix())
            second=import_resource_pool(archive,root/'archive-import',trust_local_test=True)
            self.assertEqual(result['catalog_sha256'],second['catalog_sha256'])

    def test_corruption_traversal_and_existing_destination_fail(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);pool=root/'pool';pool.mkdir();fixture(pool)
            (pool/'common/example/files/data').write_bytes(b'corrupt')
            with self.assertRaises(ValueError):import_resource_pool(pool,root/'bad',trust_local_test=True)
            self.assertFalse((root/'bad').exists())
            archive=root/'escape.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape',b'data')
            with self.assertRaises(ValueError):import_resource_pool(archive,root/'unsafe',trust_local_test=True)
            existing=root/'existing';existing.mkdir()
            with self.assertRaises(ProtocolError):import_resource_pool(pool,existing,trust_local_test=True)


if __name__=='__main__':unittest.main()
