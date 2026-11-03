"""Migration boundary checks: private exports and maintained static model inputs."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


exporter = module('standalone_source_export', ROOT/'scripts/export-source.py')
inputs = module('standalone_current_inputs', ROOT/'adaptations/scripts/prepare-current-inputs.py')


class StandaloneSourceTests(unittest.TestCase):
    def test_public_whitelist_excludes_agent_rules_and_private_state(self):
        files = exporter.reviewed_files(ROOT)
        self.assertNotIn('AGENTS.md', files)
        self.assertNotIn('adaptations/AGENTS.md', files)
        self.assertFalse(any('.local' in name.split('/') or name.startswith('adaptations/inputs/') for name in files))
        exporter.verify_adaptation_snapshots(ROOT, set(files))

    def test_export_policy_refuses_private_path_and_case_collisions(self):
        for names in (['.local/private.txt'], ['AGENTS.md', 'agents.md'],
                      ['adaptations/inputs/retained.zip'], ['private.pfx'],
                      ['.GIT/config'], ['.CoDeX/private.txt'], ['.LoCaL/private.txt'],
                      ['ADAPTATIONS/INPUTS/retained.zip']):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'scripts').mkdir()
                (root/'scripts/source-files.json').write_text(json.dumps(dict(kind='BoilerSourceWhitelist',
                    files=names, excluded=['adaptations/inputs/**'])), encoding='utf-8')
                for name in names:
                    candidate = root/name; candidate.parent.mkdir(parents=True, exist_ok=True)
                    candidate.write_bytes(b'private fixture')
                with self.assertRaisesRegex(ValueError, 'Private or generated|duplicate'):
                    exporter.reviewed_files(root)

    def copy_current_sources(self, root):
        original = ROOT/'adaptations'
        shutil.copyfile(original/'current-source-lock.json', root/'current-source-lock.json')
        for entry in json.loads((original/'current-source-lock.json').read_bytes())['files']:
            target = root/entry['path']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original/entry['path'], target)

    def test_current_static_regeneration_matches_frozen_records_without_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'sources'; root.mkdir()
            self.copy_current_sources(root)
            with mock.patch('subprocess.run', side_effect=AssertionError('Target process must not run')):
                result = inputs.prepare(Path(temporary)/'regenerated', root)
            self.assertEqual(result['files'], 24)
            self.assertFalse(result['deployment_executed'])
            self.assertFalse(result['old_workspace_required'])
            original = root/'records/shared-20261030'
            for part in json.loads((original/'inputs.json').read_bytes())['components'].values():
                for model in part.values():
                    for entry in model['files']:
                        self.assertEqual((Path(temporary)/'regenerated'/entry['source']).read_bytes(),
                                         (original/entry['source']).read_bytes())

    def test_changed_current_source_refuses_before_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'sources'; root.mkdir()
            self.copy_current_sources(root)
            (root/'brands/onexplayer/common/battery/battery_control.py').write_bytes(b'changed')
            output = Path(temporary)/'regenerated'
            with self.assertRaisesRegex(ValueError, 'reviewed inventory'):
                inputs.prepare(output, root)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
