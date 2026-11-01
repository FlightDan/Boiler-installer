"""Full staged-verification fixtures for the known Decky journal lineage."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('journal_test_adapter', ROOT/'adaptations/brands/onexplayer/oxp3/bridge/adapter.py')
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
LOADER = 'var/lib/oxp3-installer/decky/runtime/settings/loader.json'
BASE = 'home/.oxp3-installer/integration/'

def encoded(data): return base64.b64encode(data).decode()
def original(data, mode=0o644): return dict(exists=True, data=encoded(data), mode=mode, uid=0, gid=0)

@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Requires Linux root-owned filesystem fixture')
class JournalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.options = dict(repairs=['decky'], plugins=[])
        reports = {name: dict(status='files_installed', files=[]) for name in ('decky', 'system-update-hold', 'serial-console-policy', 'maintenance-tool')}
        reports['decky']['files'] = [LOADER]
        self.write_json(BASE+'A/result.json', dict(schema=1, slot='A', options=self.options, components=reports))
        self.write('etc/passwd', b'deck:x:1000:1000::/home/deck:/bin/bash\n')
        for component in ('runtime', 'release-note'):
            path = 'etc/'+component
            self.write(path, b'installed')
            self.journal(component, path, dict(exists=False), b'installed')
        self.write(LOADER, b'first')
        self.journal('decky-order', LOADER, dict(exists=False), b'first')
        link = self.root/A.LINK
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(A.LINK_TARGET)

    def write(self, path, data, mode=0o644):
        target = self.root/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
        return target
    def write_json(self, path, value):
        return self.write(path, json.dumps(value, sort_keys=True).encode(), 0o600)
    def journal(self, component, path, before, applied, *, metadata=None):
        record = dict(phase='installed', allowed=[path], original={path: before}, applied={path: encoded(applied)})
        if metadata is not None: record['metadata'] = {path: metadata}
        return self.write_json(BASE+'A/'+component+'/state.json', record)
    def verify(self): return A.verify_staged(self.root, 'A', self.options)
    def chain(self):
        self.journal('decky-order-light', LOADER, original(b'first'), b'final')
        self.write(LOADER, b'final')
    def test_fresh_and_chained_deterministic_readonly_proofs(self):
        fresh = self.verify()
        self.assertIn(LOADER, [entry['path'] for entry in fresh['files']])
        self.chain()
        before = {p.relative_to(self.root).as_posix(): (os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in self.root.rglob('*') if p.is_file() or p.is_symlink()}
        proof = self.verify()
        self.assertEqual(proof, self.verify())
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): (os.readlink(p) if p.is_symlink() else p.read_bytes()) for p in self.root.rglob('*') if p.is_file() or p.is_symlink()})
        paths = [entry['path'] for entry in proof['evidence']]
        self.assertIn(BASE+'A/decky-order/state.json', paths)
        self.assertIn(BASE+'A/decky-order-light/state.json', paths)
    def test_missing_truncated_and_incomplete_journals_fail(self):
        path = self.root/(BASE+'A/decky-order/state.json')
        path.unlink()
        with self.assertRaises(RuntimeError): self.verify()
        path = self.journal('decky-order', LOADER, dict(exists=False), b'first')
        path.write_text('{')
        with self.assertRaises((ValueError, RuntimeError)): self.verify()
        self.write_json(BASE+'A/decky-order/state.json', dict(phase='applying'))
        with self.assertRaises(RuntimeError): self.verify()
    def test_lineage_bytes_and_metadata_mismatch_fail(self):
        self.chain()
        self.journal('decky-order-light', LOADER, original(b'wrong'), b'final')
        with self.assertRaisesRegex(RuntimeError, 'lineage'): self.verify()
        self.journal('decky-order-light', LOADER, original(b'first', 0o600), b'final')
        with self.assertRaisesRegex(RuntimeError, 'lineage'): self.verify()
    def test_final_bytes_and_unexpected_overlap_fail(self):
        self.chain()
        self.write(LOADER, b'changed')
        with self.assertRaises(RuntimeError): self.verify()
        self.write(LOADER, b'final')
        self.journal('desktop-session', LOADER, original(b'final'), b'final')
        with self.assertRaisesRegex(RuntimeError, 'Overlapping'): self.verify()
    def test_modified_predecessor_and_successor_fail(self):
        self.chain()
        self.journal('decky-order', LOADER, dict(exists=False), b'altered')
        with self.assertRaisesRegex(RuntimeError, 'lineage'): self.verify()
        self.journal('decky-order', LOADER, dict(exists=False), b'first')
        self.journal('decky-order-light', LOADER, original(b'first'), b'altered')
        with self.assertRaises(RuntimeError): self.verify()
    def test_missing_chain_journals_fail(self):
        self.chain()
        (self.root/(BASE+'A/decky-order/state.json')).unlink()
        with self.assertRaisesRegex(RuntimeError, 'predecessor'): self.verify()
        self.journal('decky-order', LOADER, dict(exists=False), b'first')
        (self.root/(BASE+'A/decky-order-light/state.json')).unlink()
        with self.assertRaises(RuntimeError): self.verify()

if __name__ == '__main__': unittest.main()
