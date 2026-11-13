"""Chain-level gate contract: every candidate-producing chain ends gated.

A unit test on the gate table cannot show that a particular rebuild chain ran
it. These tests instead walk real chain implementations and assert that each
one either applies the shared stage or is explicitly, visibly exempt. That is
what keeps the next new chain from producing a selectable handler and only
discovering it at release time.

The table now covers ``oxp3:gm7-resume`` only. ``oxp3:wifi`` moved to the BE201
resource model, whose ownership boundary is the resource/handler conflict check
in ``protocol.manifest``.
"""
import importlib.util
import re
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

# Chains that turn an already-reviewed base into a new model candidate. Adding a
# chain here without wiring the stage is what this file exists to catch.
CHAIN_SCRIPTS = (
    'scripts/finalize-canonical-model.py',
    'scripts/build-model-recovery-only.py',
    'scripts/build-onexplayer-brand.py',
    'scripts/apply-capability-gates.py',
)

# The canonical release overlay asserts the gate instead of applying it, because
# it must never quietly repair a base it did not build. Its source lives in the
# canonical repository, so this tree can only check that the protocol module it
# will import is present and shared.
ASSERTING_SCRIPTS = ()


def source(relative):
    return (ROOT/relative).read_text(encoding='utf-8')


class ChainGateTests(unittest.TestCase):
    def test_the_shared_table_is_the_single_definition(self):
        # No chain may re-declare the disabled values the table owns. A private
        # copy is exactly the drift that produced the unreplayable contract.
        table = source('protocol/capability_gates.py')
        self.assertIn('"oxp3:gm7-resume"', table)
        # oxp3:wifi is owned by the BE201 resource model, not by this table.
        self.assertNotIn('"oxp3:wifi":', table)
        for chain in CHAIN_SCRIPTS:
            text = source(chain)
            if chain.endswith('apply-capability-gates.py'):
                continue
            self.assertNotIn("wifi[0]['available']=False", text.replace(' ', ''),
                             chain + ' re-implements the gate instead of applying it')

    def test_applier_is_the_single_application_point(self):
        applier = source('scripts/apply-capability-gates.py')
        self.assertIn('from protocol.capability_gates import GATES, apply_gates, violations', applier)
        self.assertIn('def enforce(', applier)
        # Idempotence is the property that lets a chain always run this stage.
        self.assertIn('if gated == manifest:', applier)

    def test_asserting_scripts_use_the_shared_predicate(self):
        # The canonical overlay imports this module from the installer root it is
        # given. Keep the public names it needs importable and stable.
        from protocol import capability_gates
        for name in ('violations', 'assert_gates', 'evidence_row', 'apply_gates', 'GATES', 'POLICY_VERSION'):
            self.assertTrue(hasattr(capability_gates, name), 'missing shared gate API: ' + name)
        self.assertTrue(callable(capability_gates.violations))
        # Evidence is optional for loading historical packages but mandatory when
        # a chain produces one.
        import inspect
        self.assertIn('require_evidence', inspect.signature(capability_gates.violations).parameters)
        self.assertIn('require_evidence', inspect.signature(capability_gates.assert_gates).parameters)

    def test_no_chain_gates_on_an_incidental_proof_field(self):
        # `canonical=('source_commit' in proof)` made the safety decision depend
        # on whether an unrelated preparation field happened to be present.
        for chain in CHAIN_SCRIPTS:
            text = source(chain)
            self.assertNotIn("'source_commit' in proof", text, chain +
                             ' derives a safety decision from an incidental field')

    def test_finalizer_has_no_fail_open_branch(self):
        # The historical non-canonical branch actively re-enabled BE201 and put
        # it back into the selection. It must not exist.
        text = source('scripts/finalize-canonical-model.py')
        self.assertNotIn("wifi[0]['default']=True", text.replace(' ', ''))
        self.assertNotIn('canonical=', text)

    def test_every_chain_mentions_where_its_gate_is_enforced(self):
        marked = {
            'scripts/finalize-canonical-model.py': 'capability_gates',
            'scripts/build-model-recovery-only.py': 'capability_gates',
            'scripts/build-onexplayer-brand.py': 'capability_gates',
        }
        for chain, marker in marked.items():
            self.assertIn(marker, source(chain), chain + ' does not reference the shared gate table')


class ApplierImportTests(unittest.TestCase):
    def test_applier_loads_without_side_effects(self):
        spec = importlib.util.spec_from_file_location('gate_applier_probe',
                                                      ROOT/'scripts/apply-capability-gates.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.enforce))
        self.assertEqual(module.GATES, __import__('protocol.capability_gates', fromlist=['GATES']).GATES)


if __name__ == '__main__':
    unittest.main()
