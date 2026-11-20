import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'components'), str(ROOT)]
from maintenance import onexplayer_microcode as model


class OnexplayerMicrocodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = (ROOT / 'adaptations/common/cpu/intel/microcode/inputs/06-cc-02').read_bytes()

    def test_pinned_extended_signature_and_platform_are_required(self):
        records = model.patch_matches(self.blob)
        model.check_hardware([dict(cpu=0, signature=0xc06c2, flags=0x80, revision=0x11a)], records)
        for cpu in (dict(cpu=0, signature=0xc06c2, flags=0x02, revision=0x11a),
                    dict(cpu=0, signature=0xc06c4, flags=0x80, revision=0x11a),
                    dict(cpu=0, signature=0xc06c2, flags=0x80, revision=0x11d)):
            with self.assertRaises(model.MaintenanceError):
                model.check_hardware([cpu], records)

    def test_fixed_declaration_and_marker(self):
        component = dict(id=model.IDENTITY, microcode_boot_policy={'kind': 'onexplayer-intel-microcode-v1'},
                         optional=True, default=False, files=[dict(target=target, size=size, sha256=digest)
                             for target, (size, digest) in model.INPUTS.items()])
        model.check_component(component)
        component['microcode_boot_policy']['kind'] = 'unreviewed'
        with self.assertRaises(model.MaintenanceError):
            model.check_component(component)

    def test_changed_patch_checksum_rejected(self):
        changed = bytearray(self.blob)
        changed[100] ^= 1
        with self.assertRaises(ValueError):
            model.patch_matches(bytes(changed))

    def test_running_cpu_identity_requires_all_platform_flags(self):
        info = ('processor : 0\nvendor_id : GenuineIntel\ncpu family : 6\nmodel : 204\n'
                'stepping : 2\nmicrocode : 0x11a\n\nprocessor : 1\nvendor_id : GenuineIntel\n'
                'cpu family : 6\nmodel : 204\nstepping : 2\nmicrocode : 0x11a\n')
        def read(path, *args, **kwargs):
            if path == Path('/test/cpuinfo'):
                return info
            if str(path).endswith('processor_flags'):
                return '0x80\n'
            raise FileNotFoundError(path)
        with patch.object(Path, 'read_text', read):
            cpus = model.hardware_snapshot(Path('/test/cpuinfo'), Path('/test/sys'))
        self.assertEqual([0xc06c2, 0xc06c2], [cpu['signature'] for cpu in cpus])
        model.check_hardware(cpus, model.patch_matches(self.blob))
        cpus[1]['flags'] = 0x02
        with self.assertRaises(model.MaintenanceError):
            model.check_hardware(cpus, model.patch_matches(self.blob))

    def test_verify_binds_boot_preimage_and_pinned_record(self):
        from components import microcode_boot
        artifact = dict(original_sha256='a' * 64, original_size=10)
        proof = dict(identity='microcode:intel', artifacts={'boot/initramfs-test.img': artifact})
        files = [dict(target='boot/initramfs-test.img'),
                 dict(target=model.LICENSE_TARGET, size=model.INPUTS[model.LICENSE_TARGET][0],
                      sha256=model.INPUTS[model.LICENSE_TARGET][1])]
        journal = dict(component=dict(id=model.IDENTITY, files=files, early_boot_proof=proof),
                       operations=[dict(target='boot/initramfs-test.img', before=dict(exists=True, sha256='a'*64, size=10)),
                                   dict(target=model.LICENSE_TARGET, before=dict(exists=False))])
        engine = Mock()
        engine.verify_readonly.return_value = {'ok': True}
        engine._state.return_value = {'components': {model.IDENTITY: {'transaction': 'test'}}}
        with patch.object(model, '_read', return_value=journal), patch.object(Path, 'read_bytes', return_value=b'boot'), \
             patch.object(microcode_boot, 'verify'), patch.object(microcode_boot, '_native_member', return_value=self.blob):
            self.assertTrue(model.verify('/', engine)['ok'])
            journal['operations'][0]['before']['sha256'] = 'b' * 64
            with self.assertRaises(model.MaintenanceError):
                model.verify('/', engine)


if __name__ == '__main__':
    unittest.main()
