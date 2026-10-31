"""Bounded-memory verification of protected directory runtime journals."""
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ADAPTER = load('adapter_runtime_test', ROOT/'adaptations/brands/onexplayer/oxp3/bridge/adapter.py')
DECODER = None


def decoder_fixture():
    supplied = os.environ.get('BOILER_LEGACY_DECODER')
    if supplied:
        candidates = [Path(supplied)]
    else:
        suffix = 'payload/legacy/maintenance/app/oxp3_maintenance/adoption.py'
        candidates = [ROOT/'cache/model-bundles/packages/oxp3'/suffix,
                      ROOT/'adaptations/inputs/oxp3-base'/suffix]
    return next((path for path in candidates if path.is_file()), None)

@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Requires Linux root-owned protected filesystem fixture')
class RuntimeVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global DECODER
        path = decoder_fixture()
        if path is None:
            raise unittest.SkipTest('Legacy decoder fixture unavailable; set BOILER_LEGACY_DECODER to the pinned decoder')
        DECODER = load('decoder_runtime_test', path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.prefix = DECODER._RUNTIMES['opti2xmx']
        self.destination = self.root/self.prefix
        self.state = self.destination.parent/'.installer-state'
        self.installed = self.state/'installed'
        for tree in (self.destination, self.installed):
            tree.mkdir(parents=True)
            (tree/'lib').mkdir()
            (tree/'lib/a').write_bytes(b'fixture')
        self.record = {'destination': self.prefix, 'phase': 'installed', 'original_exists': False,
                       'installed_metadata': self.metadata(self.installed), 'original_metadata': None}

    def metadata(self, path):
        result = {}
        for item in (path, *path.rglob('*')):
            info = item.lstat()
            rel = item.relative_to(path).as_posix()
            result[rel] = dict(directory=stat.S_ISDIR(info.st_mode), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        return result

    def verify(self):
        return ADAPTER._directory_summaries(self.root, 'opti2xmx', self.record, DECODER)

    def test_summary_matches_legacy_snapshots_without_calling_full_tree(self):
        expected = DECODER._tree(self.installed, self.record['installed_metadata'], self.root)
        old = expected['lib/a']
        with mock.patch.object(DECODER, '_tree', side_effect=AssertionError('full snapshot')), \
             mock.patch.object(DECODER, '_directory_originals', side_effect=AssertionError('full snapshot')):
            result = self.verify()[self.prefix+'/lib/a']
        data = base64.b64decode(old['data'])
        self.assertEqual(result, dict(sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                                      **{key: old[key] for key in ('mode', 'uid', 'gid')}))

    def test_large_member_and_limit(self):
        for tree in (self.destination, self.installed):
            with (tree/'lib/a').open('wb') as stream:
                stream.truncate(128*1024*1024+1)
        self.assertEqual(self.verify()[self.prefix+'/lib/a']['size'], 128*1024*1024+1)
        with (self.installed/'lib/a').open('wb') as stream:
            stream.truncate(256*1024*1024+1)
        with self.assertRaises(RuntimeError): self.verify()

    def test_changed_bytes_metadata_path_and_links_rejected(self):
        target = self.destination/'lib/a'
        target.write_bytes(b'changed')
        with self.assertRaises(RuntimeError): self.verify()
        target.write_bytes(b'fixture')
        target.chmod(0o600)
        with self.assertRaises(RuntimeError): self.verify()
        target.chmod(0o644)
        target.rename(target.parent/'b')
        with self.assertRaises(RuntimeError): self.verify()
        target.parent.joinpath('b').unlink()
        target.symlink_to(self.installed/'lib/a')
        with self.assertRaises(RuntimeError): self.verify()
        target.unlink()
        os.link(self.installed/'lib/a', target)
        with self.assertRaises(RuntimeError): self.verify()

    def test_backup_policies(self):
        self.record['original_exists'] = True
        self.record['original_metadata'] = self.record['installed_metadata']
        with self.assertRaisesRegex(RuntimeError, 'backup is missing'): self.verify()
        import shutil
        shutil.copytree(self.installed, self.state/'original')
        shutil.copytree(self.installed, self.state/'original-reference')
        self.verify()
        (self.state/'original/lib/a').write_bytes(b'different')
        with self.assertRaisesRegex(RuntimeError, 'disagree'): self.verify()
        (self.state/'original/lib/a').write_bytes(b'fixture')
        for name in ('original', 'original-reference'):
            (self.state/name/'lib/extra').write_bytes(b'extra')
        self.record['original_metadata'] = self.metadata(self.state/'original')
        with self.assertRaisesRegex(RuntimeError, 'original-only'): self.verify()
        self.record.update(original_exists=False, original_metadata=None)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected original'): self.verify()

    def test_trust_and_journal_validation(self):
        self.destination.chmod(0o777)
        with self.assertRaises(RuntimeError): self.verify()
        self.destination.chmod(0o755)
        self.record['destination'] = 'opt/other'
        with self.assertRaises(RuntimeError): self.verify()
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'): ADAPTER._pairs([('x', 1), ('x', 2)])

    def test_entry_cap_stops_streaming_directory_enumeration(self):
        directory_info = self.installed.lstat()
        file_info = (self.installed/'lib/a').lstat()
        count = []
        @contextmanager
        def entries(_path):
            def generate():
                for index in range(20000):
                    count.append(index)
                    yield types.SimpleNamespace(name='file'+str(index))
            yield generate()
        summary = dict(sha256='0'*64, size=0, mode=0o644, uid=0, gid=0)
        with mock.patch.object(DECODER, '_trusted'), \
             mock.patch.object(ADAPTER.os, 'scandir', side_effect=entries), \
             mock.patch.object(Path, 'lstat', autospec=True,
                               side_effect=lambda path: directory_info if path == self.installed else file_info), \
             mock.patch.object(ADAPTER, '_runtime_file_summary', return_value=summary):
            with self.assertRaisesRegex(RuntimeError, 'Oversized legacy runtime tree'):
                self.verify()
        self.assertEqual(len(count), 12000)

class SnapshotTests(unittest.TestCase):
    def test_adoption_default_limit_unchanged(self):
        # Use a mocked decoder to avoid allocating a large base64 fixture.
        value = dict(exists=True, data='unused', mode=0o644, uid=0, gid=0)
        with mock.patch.object(ADAPTER.base64, 'b64decode', return_value=b'x'*(128*1024*1024+1)):
            with self.assertRaisesRegex(RuntimeError, 'snapshot exceeds'): ADAPTER._normal_snapshot(value)

if __name__ == '__main__': unittest.main()
