"""Publication and input-boundary checks; source export tests use a fresh process."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
def model_source_names():
    return ['adaptations/devices/onexplayer/oxp3.toon','adaptations/devices/onexplayer/x2-mini.toon']

spec = importlib.util.spec_from_file_location('project_resource_build', ROOT/'scripts/prepare-resource-pool.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)


class ProjectResourceBuildTests(unittest.TestCase):
    def copy_locked_inputs(self, project, lock, omit=None):
        mapping=json.loads((ROOT/builder.build_wifi_pool.INPUT_MAP).read_bytes())
        paths={row['legacy']:row['canonical'] for row in mapping['files']}
        for row in lock['files']:
            if row['path']==omit: continue
            name=paths.get(row['path'],row['path']); target=project/name
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
        name=builder.build_wifi_pool.INPUT_MAP;target=project/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
        target=project/builder.LOCK;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(lock))
        return paths

    def test_kmod_transform_is_a_locked_resource_input(self):
        _, lock, _ = builder.check_inputs(ROOT)
        names = {'adaptations/common/xevma/kmod_transform.py', 'adaptations/xevma-kmod-source-lock.json'}
        self.assertTrue(names <= {row['path'] for row in lock['files']})
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.copy_locked_inputs(project,lock)
            for name in names:
                original = (project/name).read_bytes()
                (project/name).write_bytes(original+b'\n')
                with patch.object(builder.model_source,'sources',return_value=model_source_names()), self.assertRaisesRegex(ValueError, 'Locked project input differs'):
                    builder.check_inputs(project)
                (project/name).write_bytes(original)

    def test_xevma_recipe_binds_original_sources_and_complete_official_toolchain(self):
        helper = builder.xevma_proof
        raw, recipe = helper.verified_recipe(ROOT)
        self.assertEqual(len(recipe['sources']), 4)
        self.assertEqual(len(recipe['packages']), 60)
        for changed in ('source', 'package', 'module'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                for name in [helper.RECIPE, *(helper.SOURCE+name for name in ('xevmafix.c', 'Makefile', 'LICENSE', 'UPSTREAM_LICENSE', 'module-lock.json'))]:
                    target = project/name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, target)
                value = json.loads(raw)
                if changed == 'source': (project/helper.SOURCE/'xevmafix.c').write_bytes(b'changed')
                elif changed == 'package': value['packages'][0]['sha256'] = '0'*64
                else: value['expected_module']['sha256'] = '0'*64
                (project/helper.RECIPE).write_text(json.dumps(value))
                with self.assertRaises(ValueError): helper.verified_recipe(project)

    def test_explicit_single_input_lock_has_no_cache_archive_or_private_paths(self):
        project, lock, _ = builder.check_inputs(ROOT)
        self.assertEqual(len(lock['static_resources']), 11)
        self.assertEqual(lock['wifi_resource'], 'resource:intel-be201-firmware')
        self.assertEqual(lock['publication_blockers'], [])
        for row in lock['files']:
            name = row['path'].lower()
            self.assertTrue(name.startswith('adaptations/'))
            self.assertNotIn('cache/', name); self.assertNotIn('.local/', name)
            self.assertNotIn('.zip', name); self.assertNotIn('model-bundles', name)
        recipe = json.loads((ROOT/builder.build_wifi_pool.SOURCE).read_bytes())
        self.assertEqual(len(recipe['files']), 12)
        self.assertTrue(all(r['path'].startswith('adaptations/common/resource-inputs/intel-be201/') for r in recipe['files']))

    def test_unproven_public_source_blocks_normal_build_before_any_output(self):
        project, lock, raw = builder.check_inputs(ROOT)
        lock['publication_blockers'] = ['A deliberately unverified source remains']
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'blocked'
            with patch.object(builder, 'check_inputs', return_value=(project, lock, raw)), self.assertRaisesRegex(ValueError, 'Public resource build blocked'):
                builder.build(ROOT, output)
            self.assertFalse(output.exists())

    def test_current_public_source_proof_has_immutable_commit_and_exact_input_bytes(self):
        raw, proof, rows = builder.assemble_source_pool.verified_current_sources(ROOT)
        self.assertEqual(proof['repository'], 'FlightDan/Boiler-installer')
        self.assertEqual(proof['commit'], '00d26ea7ed0aa4f1d1002216242199bc72736e80')
        self.assertEqual(len(rows), 16)
        self.assertIn('adaptations/LICENSE', rows)
        self.assertIn('adaptations/brands/onexplayer/common/microphone/microphone.py', rows)
        _, lock, _ = builder.check_inputs(ROOT)
        self.assertIn(builder.assemble_source_pool.CURRENT_PROOF, {r['path'] for r in lock['files']})
        self.assertEqual(lock['publication_blockers'], [])

    def test_xevma_proof_rejects_changed_scope_artifact_or_script(self):
        helper = builder.xevma_proof
        raw, proof, recipe = helper.verified_rebuild(ROOT)
        self.assertFalse(proof['module_loaded']); self.assertFalse(proof['hardware_accepted'])
        names = [helper.PROOF, helper.RECIPE, helper.SCRIPT, helper.LOG, helper.ROOT+'xevmafix.ko',
            *(helper.SOURCE+name for name in ('xevmafix.c', 'Makefile', 'LICENSE', 'UPSTREAM_LICENSE', 'module-lock.json'))]
        for changed in ('scope', 'artifact', 'script'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                for name in names:
                    target = project/name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, target)
                value = json.loads(raw)
                if changed == 'scope': value['old_module_input'] = True
                elif changed == 'artifact': value['artifact']['sha256'] = '0'*64
                else: (project/helper.SCRIPT).write_bytes(b'changed')
                (project/helper.PROOF).write_text(json.dumps(value))
                with self.assertRaises(ValueError): helper.verified_rebuild(project)

    def test_verified_source_build_does_not_grant_release_readiness_or_fresh_compile(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'normal-build'
            result = builder.build(ROOT, output)
            self.assertTrue(result['source_provenance_complete'])
            for field in ('publishable', 'release_ready', 'official_release', 'fresh_kernel_module_build'):
                self.assertFalse(result[field])
            catalog = builder.validate_pool(output)
            resource = next(r for r in catalog['resources'] if r['id'] == 'resource:intel-xevmafix-724')
            self.assertTrue(resource['acceptance']['build_chain_reproduced'])
            self.assertFalse(resource['acceptance']['fresh_kernel_module_build'])
            proof = json.loads((output/resource['provenance']['independent_source_rebuild_proof']).read_bytes())
            self.assertEqual(proof['artifact']['sha256'], '10a473fca8031ab43206f7a9146413cf781355ddedd3ff5a66cf59bb5369461b')

    def test_changed_or_missing_single_source_refused(self):
        lock_raw = (ROOT/builder.LOCK).read_bytes(); lock = json.loads(lock_raw)
        target = next(r for r in lock['files'] if r['path'].endswith('c101.ucode'))
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths=self.copy_locked_inputs(root,lock,omit=target['path'] if missing else None)
                if not missing: (root/paths[target['path']]).write_bytes(b'changed')
                with patch.object(builder.model_source,'sources',return_value=model_source_names()), self.assertRaises((ValueError, OSError)):
                    builder.check_inputs(root)

    def test_generated_project_license_has_accurate_agpl_name_and_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'components'
            _, lock, _ = builder.check_inputs(ROOT)
            builder.split_components.build(ROOT, output, xevma_module=ROOT/lock['xevma_module'])
            catalog = builder.validate_pool(output)
            xevma=next(row for row in catalog['resources'] if row['id']=='resource:intel-xevmafix-724')
            resource=xevma
            entry=next(row for row in resource['install']['files'] if row['target'].endswith('/xevmafix/load.py'))
            generated=(output/entry['source']).read_bytes()
            self.assertIn(b"subprocess.run(['modprobe', 'xe'], executable='/usr/bin/kmod', check=True)",generated)
            self.assertIn(b"subprocess.run(['insmod', str(module)], executable='/usr/bin/kmod', check=True)",generated)
            self.assertIn(b"fields.get('DRIVER') == 'xe'",generated)
            proof=resource['provenance']['transforms'][0]
            self.assertEqual(proof['output_sha256'],hashlib.sha256(generated).hexdigest())
            license_path = 'licenses/project-AGPL-3.0'
            self.assertEqual((output/license_path).read_bytes(), (ROOT/'adaptations/LICENSE').read_bytes())
            self.assertIn(b'GNU AFFERO GENERAL PUBLIC LICENSE', (output/license_path).read_bytes())
            self.assertFalse((output/'licenses/project-GPL-3.0').exists())
            references = [name for resource in catalog['resources'] for name in resource['license_files']]
            self.assertIn(license_path, references)
            self.assertNotIn('licenses/project-GPL-3.0', references)

    def test_build_code_has_no_old_workspace_or_package_discovery(self):
        for name in builder.CODE:
            text = (ROOT/name).read_text(encoding='utf-8')
            self.assertNotIn('C:'+chr(92)+'steamosmod', text)
            self.assertNotIn('cache/model-bundles', text)
        context = (ROOT/'scripts/resources/model_context.py').read_text()
        self.assertNotIn('manifest.json', context)
        self.assertNotIn('zipfile', context)


if __name__ == '__main__': unittest.main()
