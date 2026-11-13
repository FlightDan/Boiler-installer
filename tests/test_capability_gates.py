"""Capability gates: one policy table, applied by chains and asserted by validators.

The table holds only capabilities whose handler stays in the package while policy
withholds availability. ``oxp3:wifi`` is deliberately absent: the Intel BE201
firmware resource owns that firmware, and ``protocol.manifest`` rejects a package
that makes both the resource and the legacy handler available.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))
from protocol import capability_gates as gates


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


applier = module('capability_gate_applier', ROOT/'scripts/apply-capability-gates.py')

GATED = 'oxp3:gm7-resume'
POLICY = gates.GATES[GATED]['policy']


def capability(identity, *, available=True, default=False, reason=None):
    row = dict(id=identity, category='repairs', dependencies=[],
               available=available, default=default)
    if reason is not None:
        row['reason'] = reason
    return row


def package(*capabilities, resources=None):
    value = dict(schema_version=1, kind='DevicePackage', id='device:oxp3', version='1.0.0-test.1',
                 source={'kind': 'local', 'path': 'fixture'}, compatibility={}, files=[],
                 components=[{'schema_version': 1, 'kind': 'ComponentManifest',
                              'id': 'oxp3:legacy-adaptation', 'version': '1.0.0-test.1', 'files': []}],
                 legacy_capabilities=list(capabilities),
                 selection_contract={'kind': 'oxp3-legacy-options-v1', 'component': 'oxp3:legacy-adaptation'},
                 shared_adaptations={'kind': 'boiler-shared-static-v1', 'components': [], 'warnings': []})
    if resources is not None:
        value['resources'] = list(resources)
    return value


class GateTableTests(unittest.TestCase):
    def test_release_table_covers_only_withheld_handler_policies(self):
        self.assertEqual(set(gates.GATES), {GATED})
        for identity, policy in gates.GATES.items():
            self.assertTrue(policy['policy'])
            self.assertIn('en', policy['reason'])
            self.assertIn('zh', policy['reason'])

    def test_resource_owned_capability_is_not_a_gate(self):
        # oxp3:wifi moved to the resource model. Leaving it in the table would be
        # a second, weaker definition of a decision the resource record owns, and
        # it would re-admit the fail-open path this table replaced.
        self.assertNotIn('oxp3:wifi', gates.GATES)
        self.assertEqual(gates.gated_ids(package(capability('oxp3:wifi'))), [])

    def test_apply_disables_availability_and_default_and_records_evidence(self):
        value = package(capability('oxp3:input', available=True, default=True),
                        capability(GATED, available=True, default=True))
        gated, choice = gates.apply_gates(value, {'repairs': ['oxp3:input', GATED], 'plugins': []})
        row = next(item for item in gated['legacy_capabilities'] if item['id'] == GATED)
        self.assertIs(row['available'], False)
        self.assertIs(row['default'], False)
        self.assertEqual(row['reason'], gates.GATES[GATED]['reason'])
        self.assertEqual(choice['repairs'], ['oxp3:input'])
        self.assertEqual(gated['capability_gates'],
                         [{'id': GATED, 'policy': POLICY, 'version': gates.POLICY_VERSION}])
        # The un-gated capability is untouched.
        self.assertTrue(gated['legacy_capabilities'][0]['default'])

    def test_apply_is_idempotent(self):
        value = package(capability(GATED), capability('oxp3:input'))
        once, _ = gates.apply_gates(value)
        twice, _ = gates.apply_gates(once)
        self.assertEqual(once, twice)

    def test_apply_preserves_an_existing_reviewed_reason(self):
        reviewed = {'en': 'Reviewed wording.', 'zh': '已审阅。'}
        value = package(capability(GATED, reason=reviewed))
        gated, _ = gates.apply_gates(value)
        self.assertEqual(gated['legacy_capabilities'][0]['reason'], reviewed)

    def test_apply_never_adds_a_handler_a_model_does_not_carry(self):
        value = package(capability('oxp3:input'))
        gated, choice = gates.apply_gates(value, {'repairs': ['oxp3:input'], 'plugins': []})
        self.assertEqual([row['id'] for row in gated['legacy_capabilities']], ['oxp3:input'])
        self.assertNotIn('capability_gates', gated)
        self.assertEqual(choice['repairs'], ['oxp3:input'])

    def test_apply_does_not_mutate_its_input(self):
        value = package(capability(GATED, available=True, default=True))
        original = copy.deepcopy(value)
        gates.apply_gates(value)
        self.assertEqual(value, original)


class GateViolationTests(unittest.TestCase):
    def test_a_fully_gated_package_reports_nothing(self):
        gated, _ = gates.apply_gates(package(capability(GATED)))
        self.assertEqual(gates.violations(gated), [])
        self.assertTrue(gates.assert_gates(gated))

    def test_ungated_availability_is_reported_by_name(self):
        value = package(capability(GATED, available=True))
        problems = gates.violations(value)
        self.assertTrue(any('still available: ' + GATED in problem for problem in problems), problems)

    def test_selected_by_default_is_reported_separately(self):
        gated, _ = gates.apply_gates(package(capability(GATED)))
        gated['legacy_capabilities'][0]['default'] = True
        problems = gates.violations(gated)
        self.assertEqual(len([p for p in problems if 'selected by default' in p]), 1, problems)

    def test_missing_or_wrong_policy_evidence_is_rejected(self):
        gated, _ = gates.apply_gates(package(capability(GATED)))
        missing = copy.deepcopy(gated); missing.pop('capability_gates')
        self.assertTrue(any('lacks recorded policy evidence' in p
                            for p in gates.violations(missing, require_evidence=True)))
        wrong = copy.deepcopy(gated)
        wrong['capability_gates'][0]['policy'] = 'some-other-policy'
        self.assertTrue(any('different policy' in p for p in gates.violations(wrong)))
        stale = copy.deepcopy(gated)
        stale['capability_gates'][0]['version'] = gates.POLICY_VERSION + 1
        self.assertTrue(any('predates' in p for p in gates.violations(stale)))

    def test_a_package_published_before_the_table_still_loads(self):
        # Its bytes cannot be rewritten, so demanding evidence would make every
        # historical package unloadable. The values must still hold and any
        # evidence it does carry must still be correct.
        legacy = package(capability(GATED, available=False, default=False))
        self.assertEqual(gates.violations(legacy), [])
        self.assertEqual(gates.violations(legacy, require_evidence=True),
                         ['Gated capability lacks recorded policy evidence: ' + GATED])
        legacy['capability_gates'] = [{'id': GATED, 'policy': 'invented', 'version': 1}]
        self.assertTrue(any('different policy' in p for p in gates.violations(legacy)))

    def test_evidence_for_an_absent_capability_is_stale(self):
        gated, _ = gates.apply_gates(package(capability(GATED)))
        gated['legacy_capabilities'] = [capability('oxp3:input')]
        self.assertTrue(any('Stale gate evidence' in p for p in gates.violations(gated)))

    def test_assert_gates_names_the_specific_failure(self):
        value = package(capability(GATED, available=True))
        with self.assertRaisesRegex(ValueError, 'still available: ' + GATED):
            gates.assert_gates(value, 'the rebuilt X2 base')

    def test_structural_evidence_validation_rejects_malformed_rows(self):
        for bad in ([], {}, [{'id': GATED}], [{'id': GATED, 'policy': 'p', 'version': 0}],
                    [{'id': GATED, 'policy': 'p', 'version': True}],
                    [{'id': GATED, 'policy': 'p', 'version': 1},
                     {'id': GATED, 'policy': 'p', 'version': 1}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                gates.validate_evidence(bad)


class ManifestValidatorTests(unittest.TestCase):
    def test_validator_rejects_manifest_that_names_a_gate_but_stays_available(self):
        from protocol.manifest import ProtocolError, validate_manifest
        value = package(capability(GATED, available=True))
        value['capability_gates'] = [{'id': GATED, 'policy': POLICY, 'version': 1}]
        with self.assertRaisesRegex(ProtocolError, 'still available'):
            validate_manifest(value)

    def test_validator_accepts_a_gated_manifest(self):
        from protocol.manifest import validate_manifest
        gated, _ = gates.apply_gates(package(capability(GATED)))
        self.assertEqual(validate_manifest(gated)['id'], 'device:oxp3')

    def test_validator_still_accepts_packages_without_gate_evidence(self):
        from protocol.manifest import validate_manifest
        self.assertEqual(validate_manifest(package(capability('oxp3:input')))['id'], 'device:oxp3')

    def test_validator_keeps_resource_and_legacy_handler_mutually_exclusive(self):
        # With oxp3:wifi gone from the gate table this is the only remaining
        # boundary against two writers for the same firmware targets.
        from protocol.manifest import ProtocolError, validate_manifest
        resource = [{'id': 'resource:intel-be201-firmware',
                     'path': 'common/network/intel/be201/resource.toon'}]
        value = package(capability('oxp3:wifi', available=True), resources=resource)
        with self.assertRaisesRegex(ProtocolError, 'conflicts with legacy Wi-Fi handler'):
            validate_manifest(value)
        value = package(capability('oxp3:wifi', available=False, default=False), resources=resource)
        self.assertEqual(validate_manifest(value)['id'], 'device:oxp3')


class ApplierTests(unittest.TestCase):
    def build(self):
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temporary, True)
        directory = Path(temporary)/'package'; directory.mkdir()
        manifest = package(capability('oxp3:input', available=True, default=True),
                           capability(GATED, available=True, default=False))
        (directory/'selection.json').write_bytes(
            (json.dumps({'schema_version': 1, 'repairs': ['oxp3:input', GATED], 'plugins': [],
                         'installer_language': 'en-US', 'kde_language': 'system',
                         'acknowledge_legacy_update_hold': True}, indent=2)+'\n').encode())
        manifest['files'] = applier.inventory(directory)
        (directory/'manifest.json').write_bytes(applier.json_bytes(manifest))
        return directory, applier.sha((directory/'manifest.json').read_bytes())

    def test_enforce_gates_a_candidate_and_rewrites_inventory(self):
        directory, anchor = self.build()
        result = applier.enforce(directory, expected_manifest_sha256=anchor)
        self.assertTrue(result['changed'])
        self.assertEqual(result['gates'], [GATED])
        self.assertTrue(result['selection_changed'])
        manifest = json.loads((directory/'manifest.json').read_bytes())
        self.assertEqual(manifest['files'], applier.inventory(directory))
        self.assertEqual(gates.violations(manifest), [])
        selection = json.loads((directory/'selection.json').read_bytes())
        self.assertEqual(selection['repairs'], ['oxp3:input'])

    def test_enforce_is_a_no_op_on_an_already_gated_candidate(self):
        directory, anchor = self.build()
        first = applier.enforce(directory, expected_manifest_sha256=anchor)
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        second = applier.enforce(directory, expected_manifest_sha256=first['manifest_sha256'])
        after = {path.name: path.read_bytes() for path in directory.iterdir()}
        self.assertFalse(second['changed'])
        self.assertEqual(before, after)

    def test_enforce_refuses_an_unanchored_package(self):
        directory, anchor = self.build()
        with self.assertRaises(ValueError):
            applier.enforce(directory, expected_manifest_sha256='0'*64)

    def test_enforce_rejects_inconsistent_evidence_on_a_claimed_gated_package(self):
        directory, anchor = self.build()
        first = applier.enforce(directory, expected_manifest_sha256=anchor)
        manifest = json.loads((directory/'manifest.json').read_bytes())
        manifest['capability_gates'][0]['policy'] = 'invented'
        (directory/'manifest.json').write_bytes(applier.json_bytes(manifest))
        tampered = applier.sha((directory/'manifest.json').read_bytes())
        with self.assertRaisesRegex(ValueError, 'different policy'):
            applier.enforce(directory, expected_manifest_sha256=tampered)
        self.assertTrue(first['changed'])


class SnapshotBoundaryTests(unittest.TestCase):
    """The adaptation snapshot is generated; gate policy lives here, not there."""

    def test_snapshot_matches_its_own_inventory(self):
        # Editing the snapshot by hand is how a generated tree silently forks
        # from the canonical repository. The export check enforces this; the
        # unit test states it so a future edit fails close to the edit.
        directory = ROOT/'adaptations'
        inventory = json.loads((directory/'model-source-files.json').read_bytes())
        differing = [row['path'] for row in inventory['files']
                     if hashlib.sha256((directory/row['path']).read_bytes()).hexdigest() != row['sha256']]
        self.assertEqual(differing, [], 'adaptations/ must be an exact canonical snapshot')

    def test_gate_policy_is_not_duplicated_in_the_snapshot(self):
        # A second copy in the snapshot would drift from protocol/capability_gates.
        self.assertFalse((ROOT/'adaptations/protocol/capability_gates.py').exists())
        self.assertIn('capability_gates', (ROOT/'protocol/manifest.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
