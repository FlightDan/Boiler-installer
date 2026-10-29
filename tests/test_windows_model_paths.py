"""Exercise real deep package intake, including the private verified generation."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import shutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.local import import_local
from protocol.manifest import _filesystem_path, validate_package, ProtocolError


@unittest.skipUnless(os.name=='nt','Win32 extended path handling')
class WindowsModelPaths(unittest.TestCase):
    def fixture(self,root):
        source=root/'source'
        relative='payload/legacy/integration/sources/input/hid-build-v1/'+'x'*65+'.patch'
        path=_filesystem_path(source/relative)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'reviewed source\n')
        value={'schema_version':1,'kind':'DevicePackage','id':'device:fixture','version':'0.1.0',
               'source':{'kind':'local','path':'fixture'},'compatibility':{},'components':[],
               'files':[{'path':relative,'size':16,'sha256':hashlib.sha256(b'reviewed source\n').hexdigest()}]}
        _filesystem_path(source/'manifest.json').write_text(json.dumps(value),encoding='utf-8')
        return source,relative

    def test_deep_local_import_preserves_anchor_and_normal_display_path(self):
        temporary=tempfile.mkdtemp(prefix='boiler-long-model-')
        try:
            root=Path(temporary)
            source,relative=self.fixture(root)
            destination=root/('task-'+'t'*70)/'payload/device-packages/selected'
            self.assertGreater(len(str(destination/relative)),260)
            result=import_local(source,destination,developer=True)
            self.assertEqual(Path(result['directory']),destination)
            value=validate_package(destination,expected_manifest_sha256=result['manifest_sha256'])
            self.assertEqual(value['files'][0]['path'],relative)
            self.assertEqual(_filesystem_path(destination/relative).read_bytes(),b'reviewed source\n')
            with self.assertRaisesRegex(ProtocolError,'already exists'):
                import_local(source,destination,developer=True)
            _filesystem_path(destination/relative).write_bytes(b'changed\n')
            with self.assertRaises(ProtocolError):
                validate_package(destination,expected_manifest_sha256=result['manifest_sha256'])
        finally:
            target=Path(temporary).resolve()
            temp_root=Path(tempfile.gettempdir()).resolve()
            assert target.is_relative_to(temp_root) and target!=temp_root and target.name.startswith('boiler-long-model-')
            shutil.rmtree(_filesystem_path(target))


if __name__=='__main__':unittest.main()
