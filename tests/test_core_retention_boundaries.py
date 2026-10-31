"""Retention probing must preserve generic safety and corrupt-state refusal."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from maintenance.engine import Engine,MaintenanceError,_json_write

def snapshot(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}

class CoreRetentionBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'image';self.root.mkdir();self.engine=Engine(self.root)
    def state_with(self,identity):
        state=self.engine._state();state['components'][identity]=dict(version='1.0.0',transaction='a'*32,requires=[],conflicts=[])
        _json_write(self.engine.state_dir/'state.json',state)
        return self.engine._journal('a'*32)
    def test_missing_core_or_resource_journal_is_maintenance_error_and_write_free(self):
        for identity in ('core:maintenance','resource:broken'):
            with self.subTest(identity=identity):
                self.state_with(identity);before=snapshot(self.root)
                with self.assertRaisesRegex(MaintenanceError,'journal is unavailable or corrupt'):self.engine.recover()
                self.assertEqual(before,snapshot(self.root))
                (self.engine.state_dir/'state.json').unlink()
    def test_corrupt_or_misbound_candidate_journal_is_not_ignored(self):
        path=self.state_with('resource:broken')
        for raw in (b'{',json.dumps(dict(root='different-root',component={'id':'resource:broken'},status='committed')).encode()):
            with self.subTest(raw=raw):
                path.write_bytes(raw);before=snapshot(self.root)
                with self.assertRaises(MaintenanceError):self.engine.recover()
                self.assertEqual(before,snapshot(self.root))
    def test_readonly_restore_rejects_without_reading_any_journal(self):
        reader=Engine(self.root,read_only=True);before=snapshot(self.root)
        with patch('maintenance.engine._read',side_effect=AssertionError('Readonly restore read journal')):
            with self.assertRaisesRegex(MaintenanceError,'Read-only'):reader.restore('0'*32)
        self.assertEqual(before,snapshot(self.root))
    def test_marked_apply_does_not_leak_missing_prior_journal_error(self):
        from maintenance.core_retention import seed_root,RESOURCE_MARKER
        seed_root(self.engine);self.state_with('resource:broken')
        component=dict(schema_version=1,kind='ComponentManifest',id='resource:broken',version='1.0.0',
            files=[],requires=['core:maintenance'],conflicts=[],compatibility={},core_retention=RESOURCE_MARKER)
        with self.engine._lock(retention_component=component):pass
        before=snapshot(self.root)
        with self.assertRaisesRegex(MaintenanceError,'journal is unavailable or corrupt'):
            self.engine.apply(component,self.root)
        self.assertEqual(before,snapshot(self.root))

if __name__=='__main__':unittest.main()
