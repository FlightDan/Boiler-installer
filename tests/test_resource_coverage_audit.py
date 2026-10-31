import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resource_coverage_audit', ROOT/'scripts/audit-resource-coverage.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class CoverageAuditTests(unittest.TestCase):
    def test_coverage_requires_lifecycle_and_identical_payload(self):
        base = dict(id='device:fixture', files=[dict(path='old.bin', size=4, sha256='a'*64)],
                    legacy_capabilities=[dict(id='oxp3:input'), dict(id='oxp3:hdr')])
        pool = dict(resources=[dict(id='resource:input', payload=dict(files=[dict(path='files/new.bin', size=4, sha256='a'*64)]),
                                    capabilities=['input'])],
                    models=[dict(id='device:fixture', resources=[dict(resource='resource:input')],
                                 pending=[dict(component='hdr')])])
        with patch.object(audit, 'validate_package', return_value=base):
            report = audit.audit('old', 'b'*64, 'pool', 'device:fixture', lambda _: pool)
        self.assertEqual(report['baseline_identical_files'], 1)
        self.assertEqual(report['capabilities_uncovered'], ['hdr'])
        self.assertEqual(report['capabilities_not_explicitly_pending'], [])
        self.assertFalse(report['complete'])

    def test_missing_payload_and_model_mismatch_are_not_accepted(self):
        base = dict(id='device:fixture', files=[dict(path='old.bin', size=4, sha256='a'*64)], legacy_capabilities=[])
        pool = dict(resources=[dict(id='resource:input', payload=dict(files=[]), capabilities=[])],
                    models=[dict(id='device:fixture', resources=[dict(resource='resource:input')])])
        with patch.object(audit, 'validate_package', return_value=base):
            self.assertFalse(audit.audit('old', 'b'*64, 'pool', 'device:fixture', lambda _: pool)['complete'])
            with self.assertRaisesRegex(ValueError, 'identity differ'):
                audit.audit('old', 'b'*64, 'pool', 'device:other', lambda _: pool)


if __name__ == '__main__':
    unittest.main()
