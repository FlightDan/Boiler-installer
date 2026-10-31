import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance.resource_runtime import ResourceError, ResourcePool, ResourceSession, component, check_hardware


class ResourceTargetConflicts(unittest.TestCase):
    def test_hardware_values_require_exact_types(self):
        resource = {'hardware_requires': {'fan_driver_ready': [True], 'device_id': [1]}}
        check_hardware(resource, {'fan_driver_ready': True, 'device_id': 1})
        for facts in ({'fan_driver_ready': 1, 'device_id': 1},
                      {'fan_driver_ready': True, 'device_id': True}):
            with self.assertRaises(ResourceError):
                check_hardware(resource, facts)

    def fixture(self, base):
        pool_root = base/'pool'; pool_root.mkdir()
        (pool_root/'payload').write_bytes(b'payload')
        file = dict(path='payload', size=7, sha256=hashlib.sha256(b'payload').hexdigest())
        resource = dict(id='resource:first', version='1.0.0', dependencies=[], payload=dict(files=[file]),
            install=dict(kind='files-v1', files=[
                dict(source='payload', target='etc/thing', scope='root-slots'),
                dict(source='payload', target='home/shared-file', scope='shared-home')]),
            verify=dict(kind='sha256-v1'), restore=dict(kind='transaction-backup-v1'))
        slots = {}
        for label in ('A', 'B'):
            root = base/label; root.mkdir()
            slots[label] = dict(root=root, environment={}, hardware={})
        home = base/'home'; home.mkdir()
        pool = ResourcePool(pool_root, dict(resources=[resource]), local_test=True)
        return pool, slots, home, [dict(resource=resource['id'], parameters={})]

    def test_overlap_is_rejected_before_any_target_or_journal_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            pool_root = base/'pool'; pool_root.mkdir()
            (pool_root/'payload').write_bytes(b'payload')
            file = dict(path='payload', size=7, sha256=hashlib.sha256(b'payload').hexdigest())
            first = dict(id='resource:first', version='1.0.0', dependencies=[], payload=dict(files=[file]),
                         install=dict(kind='files-v1', files=[dict(source='payload', target='etc/thing', scope='root-slots')]),
                         verify=dict(kind='sha256-v1'), restore=dict(kind='transaction-backup-v1'))
            second = copy.deepcopy(first); second['id'] = 'resource:second'
            slots = {}
            for label in ('A', 'B'):
                root = base/label; root.mkdir()
                slots[label] = dict(root=root, environment={}, hardware={})
            home = base/'home'; home.mkdir()
            session = ResourceSession(ResourcePool(pool_root, dict(resources=[first, second]), local_test=True), slots, home)
            refs = [dict(resource=row['id'], parameters={}) for row in (first, second)]
            with self.assertRaisesRegex(ResourceError, 'overlapping targets'):
                session.install(refs, ['resource:first', 'resource:second'])
            self.assertFalse((base/'A/etc/thing').exists())
            self.assertFalse((base/'B/etc/thing').exists())
            self.assertFalse((home/'.boiler/resource-maintenance/resource-actions').exists())

    def test_shared_restore_uses_receipt_not_new_resource_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            pool, slots, home, refs = self.fixture(Path(temporary))
            session = ResourceSession(pool, slots, home)
            receipt = session.install(refs, ['resource:first'])
            pool.catalog['resources'][0]['install']['files'] = [
                row for row in pool.catalog['resources'][0]['install']['files'] if row['scope'] == 'shared-home']
            with self.assertRaisesRegex(ResourceError, 'prior generations'):
                session.restore(receipt, 'shared-home')
            self.assertEqual((home/'shared-file').read_bytes(), b'payload')

    def test_existing_owner_in_second_slot_is_detected_before_any_other_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            pool, slots, home, refs = self.fixture(Path(temporary))
            session = ResourceSession(pool, slots, home)
            blocker = copy.deepcopy(pool.catalog['resources'][0]); blocker['id'] = 'resource:blocker'
            session.engines['B'].apply(component(blocker, 'root-slots'), pool.root, environment={})
            with self.assertRaisesRegex(ResourceError, 'already owned'):
                session.install(refs, ['resource:first'])
            self.assertFalse((home/'shared-file').exists())
            self.assertFalse((slots['A']['root']/'etc/thing').exists())
            self.assertEqual((slots['B']['root']/'etc/thing').read_bytes(), b'payload')

    def test_verify_rejects_same_version_changed_targets_or_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            pool, slots, home, refs = self.fixture(Path(temporary))
            session = ResourceSession(pool, slots, home)
            session.install(refs, ['resource:first'])
            self.assertTrue(all(row['ok'] for row in session.verify(['resource:first'])))
            pool.catalog['resources'][0]['install']['files'][0]['target'] = 'etc/different'
            result = session.verify(['resource:first'])
            self.assertTrue(any(not row['ok'] and row['reason'] == 'manifest-mismatch' for row in result))
            pool.catalog['resources'][0]['install']['files'] = [
                row for row in pool.catalog['resources'][0]['install']['files'] if row['scope'] == 'shared-home']
            result = session.verify(['resource:first'])
            self.assertEqual(sum(not row['ok'] and row['reason'] == 'unexpected-installed-scope' for row in result), 2)


if __name__ == '__main__':
    unittest.main()
