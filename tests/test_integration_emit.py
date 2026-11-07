import ast
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'guest'))
from integration_emit import emit_slot


class IntegrationEmitTests(unittest.TestCase):
    def test_emit_slot_prints_one_result_one_plan_and_component_rows(self):
        result = {'install_plan': {'slot': 'A'}, 'components': {
            'core:maintenance': {'status': 'files_installed'},
            'oxp3:microphone': {'status': 'files_installed'},
        }}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            emit_slot('A', result)
        lines = output.getvalue().splitlines()
        self.assertTrue(lines[0].startswith('OXP_INTEGRATION_RESULT='))
        self.assertEqual(lines[1], 'BOILER_INSTALL_PLAN_JSON=' + json.dumps(result['install_plan']))
        rows = [json.loads(line.removeprefix('OXP_COMPONENT_RESULT=')) for line in lines[2:]]
        self.assertEqual([row['id'] for row in rows], ['core:maintenance', 'oxp3:microphone'])
        self.assertTrue(all(row['slot'] == 'A' for row in rows))
        self.assertEqual(len(lines), 4)

    def test_deferred_plan_and_ssd_rows_do_not_duplicate_other_components(self):
        identity = 'x2-mini:ssd-gm7-boot-repair'
        result = {'install_plan': {'slot': 'A'},
                  'components': {'core:maintenance': {'status': 'files_installed'}}}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            emit_slot('A', result, include_result=False, include_plan=False)
            # complete_ssd adds its row and destination-bound plan only later.
            result['install_plan']['ssd_repair'] = {'id': identity}
            result['components'][identity] = {'status': 'files_installed'}
            emit_slot('A', result, only=identity)
        lines = output.getvalue().splitlines()
        rows = [json.loads(line.split('=', 1)[1]) for line in lines
                if line.startswith('OXP_COMPONENT_RESULT=')]
        self.assertEqual([row['id'] for row in rows], ['core:maintenance', identity])
        self.assertEqual(sum(line.startswith('BOILER_INSTALL_PLAN_JSON=') for line in lines), 1)
        self.assertEqual(sum(line.startswith('OXP_INTEGRATION_RESULT=') for line in lines), 1)

    def run_model_orchestration(self, *, ssd=False, fail_b=False):
        # Execute the actual production orchestration statements, not a rewritten
        # simulation. Only mounts/review-file IO and component work are mocked.
        from types import SimpleNamespace
        source = (ROOT / 'guest/integration-image.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        branch = next(node for node in tree.body
                      if isinstance(node, ast.If) and ast.unparse(node.test) == 'model_plans')
        body = next(node.body for node in branch.body if isinstance(node, ast.Try))
        first = next(i for i, node in enumerate(body) if isinstance(node, ast.For)
                     and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                             and call.func.id == 'stage' for call in ast.walk(node)))
        program = ast.Module(body=body[first:], type_ignores=[])
        staged = []
        identity = 'x2-mini:ssd-gm7-boot-repair' if ssd else None

        def stage(root, payload, options, slot, password_hash, **kwargs):
            print('TEST_STAGE=' + slot)
            if fail_b and slot == 'B':
                raise RuntimeError('synthetic stage-B failure')
            self.assertEqual(kwargs['defer_ssd'], ssd)
            staged.append(slot)
            return {'install_plan': {'slot': slot}, 'components': {
                'core:maintenance': {'status': 'files_installed'},
                'oxp3:microphone': {'status': 'files_installed'}}}

        def complete(root, result, context, plan, component, **kwargs):
            self.assertEqual(staged, ['A', 'B'], 'SSD must not complete before both stages')
            slot = result['install_plan']['slot']
            print('TEST_COMPLETE=' + slot)
            result['install_plan']['ssd_repair'] = {'id': component}
            result['components'][component] = {'status': 'files_installed'}

        class FakePath:
            def __init__(self, path):
                pass
            def write_text(self, text):
                pass

        namespace = dict(roots=['root-A', 'root-B'], model_source={}, model_selection={},
                         model_context={}, model_plans={'A': {}, 'B': {}},
                         ssd_previews={'A': {}, 'B': {}}, ssd_identity=identity,
                         ssd_context={}, ssd_provider_owner=None, options={},
                         password_hash=None, target_disk=None, results={}, json=json,
                         Path=FakePath, stage=stage, emit_slot=emit_slot,
                         complete_ssd=complete, save_result=lambda *args: None,
                         installer_model_ssd_plan=lambda *args, **kwargs: {},
                         _mark=lambda name: print('BOILER_TIMING=integration:' + name + ':1.000'),
                         os=SimpleNamespace(O_WRONLY=1, O_CREAT=2, O_EXCL=4, O_NOFOLLOW=8,
                                            open=lambda *args: 1, fdopen=lambda *args: io.StringIO(),
                                            fsync=lambda *args: None))
        # StringIO has no real file descriptor; the production flush/fsync order
        # still runs against this inert review-file object.
        class ReviewFile(io.StringIO):
            def fileno(self):
                return 1
        namespace['os'].fdopen = lambda *args: ReviewFile()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            try:
                exec(compile(program, 'integration-image.py:model', 'exec'), namespace)
            except RuntimeError:
                if not fail_b:
                    raise
        return output.getvalue().splitlines()

    def assert_model_order(self, ssd):
        lines = self.run_model_orchestration(ssd=ssd)
        components = [(i, json.loads(line.split('=', 1)[1])) for i, line in enumerate(lines)
                      if line.startswith('OXP_COMPONENT_RESULT=')]
        plans = [(i, json.loads(line.split('=', 1)[1])) for i, line in enumerate(lines)
                 if line.startswith('BOILER_INSTALL_PLAN_JSON=')]
        self.assertEqual([row['slot'] for _, row in plans], ['A', 'B'])
        self.assertEqual(len({(row['slot'], row['id']) for _, row in components}), len(components))
        expected = {'core:maintenance', 'oxp3:microphone'}
        if ssd:
            expected.add('x2-mini:ssd-gm7-boot-repair')
        for slot in ('A', 'B'):
            self.assertEqual({row['id'] for _, row in components if row['slot'] == slot}, expected)
        first_b = lines.index('TEST_STAGE=B')
        self.assertTrue(all(i < first_b for i, row in components
                            if row['slot'] == 'A' and row['id'] != 'x2-mini:ssd-gm7-boot-repair'))
        if ssd:
            complete_b = lines.index('TEST_COMPLETE=B')
            self.assertTrue(all(i > complete_b and 'ssd_repair' in row for i, row in plans))
            self.assertTrue(all(i > complete_b for i, row in components
                                if row['id'] == 'x2-mini:ssd-gm7-boot-repair'))
        else:
            self.assertLess(plans[0][0], first_b)

    def test_production_model_orchestration_non_ssd_order(self):
        self.assert_model_order(False)

    def test_production_model_orchestration_deferred_ssd_order(self):
        self.assert_model_order(True)

    def test_stage_b_failure_never_emits_destination_bound_final_plan(self):
        lines = self.run_model_orchestration(ssd=True, fail_b=True)
        self.assertTrue(any(line.startswith('OXP_COMPONENT_RESULT=') for line in lines))
        self.assertFalse(any(line.startswith('BOILER_INSTALL_PLAN_JSON=') for line in lines))
        self.assertFalse(any(line.startswith('TEST_COMPLETE=') for line in lines))


if __name__ == '__main__':
    unittest.main()
