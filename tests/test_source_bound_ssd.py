import importlib.util
import tempfile
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('source_info',ROOT/'guest/source-info.py')
import sys
sys.path.insert(0,str(ROOT/'guest'))
source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)

class SourceSsdLedgerTests(unittest.TestCase):
    def test_absent_ledger_is_accepted_and_any_known_ledger_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); source.reject_bound_ssd_home(home)
            (home/'.boiler').mkdir();source.reject_bound_ssd_home(home)
            (home/'.boiler/ssd-boot-v1').mkdir()
            with self.assertRaisesRegex(ValueError,'destination-bound SSD'): source.reject_bound_ssd_home(home)
    def test_nondirectory_owner_path_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); (home/'.boiler').write_text('unexpected')
            with self.assertRaisesRegex(ValueError,'unsafe destination-bound'): source.reject_bound_ssd_home(home)

class SourcePartitionTests(unittest.TestCase):
    def prepared(self, prefix=()):
        from layout import PARTS
        roles = [(name, kind) for name, _, kind in PARTS]
        roles.append(('home', '933AC7E1-2EB4-4F13-B844-0E14E2AEF915'))
        parts = [dict(part) for part in prefix]
        parts += [dict(name=name, type=kind.lower(), node='/dev/vdc' + str(index))
                  for index, (name, kind) in enumerate(roles, len(parts)+1)]
        return dict(label='gpt', sectorsize=512, partitions=parts)

    def test_prepared_source_accepts_windows_prefix_and_uses_only_steamos_tail(self):
        prefix = [dict(name='Windows EFI', type='C12A7328-F81F-11D2-BA4B-00A0C93EC93B', node='/dev/vdc1'),
                  dict(name='Windows data', type='EBD0A0A2-B9E5-4433-87C0-68B6B72699C7', node='/dev/vdc2')]
        table = self.prepared(prefix)
        layout, nodes = source.source_partitions(table)
        self.assertEqual(layout, 'steamos-ab')
        self.assertEqual(nodes['esp'], '/dev/vdc3')
        self.assertEqual(nodes['home'], '/dev/vdc10')
        self.assertNotIn('/dev/vdc1', nodes.values())
        self.assertNotIn('/dev/vdc2', nodes.values())
        self.assertEqual(source.source_partitions(self.prepared())[0], 'steamos-ab')

    def test_duplicate_role_prefix_and_wrong_role_or_type_are_rejected(self):
        for name in ('esp', 'rootfs-A', 'home'):
            with self.subTest(duplicate=name), self.assertRaises(ValueError):
                source.source_partitions(self.prepared([dict(name=name, node='/dev/vdc1', type='other')]))
        for index, value in ((0, 'wrong-type'), (3, None), (7, 'EBD0A0A2-B9E5-4433-87C0-68B6B72699C7')):
            with self.subTest(index=index), self.assertRaises(ValueError):
                table = self.prepared()
                table['partitions'][index]['type'] = value
                source.source_partitions(table)
        table = self.prepared()
        table['partitions'][2]['name'] = 'rootfs-A'
        with self.assertRaises(ValueError): source.source_partitions(table)

    def test_gpt_sector_and_recovery_compatibility(self):
        for property_name, value in (('label', 'dos'), ('sectorsize', 4096)):
            with self.subTest(property_name=property_name), self.assertRaises(ValueError):
                table = self.prepared()
                table[property_name] = value
                source.source_partitions(table)
        names = ('esp', 'efi-A', 'rootfs-A', 'var-A', 'home')
        parts = [dict(name=name, node='/dev/vdc' + str(index)) for index, name in enumerate(names, 1)]
        layout, nodes = source.source_partitions(dict(label='gpt', sectorsize=512, partitions=parts))
        self.assertEqual(layout, 'steamos-recovery')
        self.assertEqual(nodes['rootfs-A'], '/dev/vdc3')
        self.assertEqual(nodes['home'], '/dev/vdc5')
        with self.assertRaises(ValueError):
            source.source_partitions(dict(label='gpt', sectorsize=512,
                                          partitions=[dict(name='Windows data', node='/dev/vdc1'), *parts]))


if __name__ == '__main__': unittest.main()
