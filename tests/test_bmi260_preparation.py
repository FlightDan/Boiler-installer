import tempfile
import unittest
import zipfile
import json
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from scripts.resources import bmi260


class BMI260PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / 'stage'
        (self.stage / 'payload-inputs/bmi260/gyro').mkdir(parents=True)
        source = self.stage / bmi260.COMMON / 'sources'
        source.mkdir(parents=True)
        (source / 'gyro_binding.py').write_bytes(b'journaled binding')
        self.members = {'000': b'module', '001': b'inputplumber', '004': b'launch',
                        '005': b'TimeoutStartSec=20\nTimeoutStopSec=10\n',
                        '006': b'drop-in', '007': b'profile', '008': b'steering'}
        for name in ('000', '001'):
            (self.stage / 'payload-inputs/bmi260/gyro' / (name + '.bin')).write_bytes(self.members[name])
        self.archive = self.root / 'original.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, data in self.members.items():
                z.writestr('cache/integration/gyro/' + name + '.bin', data)
        for i in range(74):
            (self.stage / ('extra-%02d' % i)).write_bytes(b'x')
        rows = []
        for p in sorted(self.stage.rglob('*')):
            if p.is_file():
                data = p.read_bytes()
                rows.append(dict(path=p.relative_to(self.stage).as_posix(), size=len(data), sha256=bmi260.digest(data)))
        self.proof_data = dict(source_commit=bmi260.COMMIT, official_release=False, deployable=False,
                               project_input_lock='adaptations/resource-build-inputs.json',
                               project_input_lock_sha256='1' * 64, files=rows)
        semantic = dict(self.proof_data)
        del semantic['project_input_lock_sha256']
        semantic_hash = bmi260.digest(json.dumps(semantic, sort_keys=True, separators=(',', ':')).encode())
        self.semantic = mock.patch.object(bmi260, 'SEMANTIC_SHA', semantic_hash)
        self.semantic.start()
        self.addCleanup(self.semantic.stop)
        self.save_proof()
        self.archive_hash = mock.patch.object(bmi260, 'ARCHIVE_SHA', bmi260.file_digest(self.archive))
        self.archive_hash.start()
        self.addCleanup(self.archive_hash.stop)
        self.archive_size = mock.patch.object(bmi260, 'ARCHIVE_SIZE', self.archive.stat().st_size)
        self.archive_size.start()
        self.addCleanup(self.archive_size.stop)

    def save_proof(self):
        (self.stage / 'preparation.json').write_text(json.dumps(self.proof_data), encoding='utf-8')

    def test_runtime_and_model_profiles(self):
        files, profiles, provenance = bmi260.prepare(self.stage, self.archive)
        prefix = 'var/lib/oxp3-gyro-v1/'
        self.assertEqual(files[prefix + 'load.sh'], (b'#!/bin/sh\nexec /usr/bin/python3 -I -B /var/lib/oxp3-gyro-v1/binding.py start\n', 0o755))
        self.assertEqual(files[prefix + 'unload.sh'][0], b'#!/bin/sh\nexec /usr/bin/python3 -I -B /var/lib/oxp3-gyro-v1/binding.py stop\n')
        self.assertEqual(files['etc/systemd/system/oxp3-gyro.service'][0], b'TimeoutStartSec=40\nTimeoutStopSec=20\n')
        self.assertEqual(profiles['oxp3'], profiles['x2-mini'])
        self.assertEqual(profiles['x2-mini'][bmi260.PROFILE_TARGETS['007.bin']][0], b'profile')
        output = self.root / 'out'
        bmi260.write_output(output, files, profiles, provenance)
        self.assertIn('no independent measurement', (output / 'preparation.json').read_text())
        self.assertEqual(json.loads((output / 'preparation.json').read_text())['project_input_lock'],
                         'adaptations/resource-build-inputs.json')
        with self.assertRaises(FileExistsError):
            bmi260.write_output(output, files, profiles, provenance)

    def test_changed_binary_is_rejected(self):
        (self.stage / 'payload-inputs/bmi260/gyro/000.bin').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Canonical input differs'):
            bmi260.prepare(self.stage, self.archive)

    def test_archive_digest_is_required(self):
        with mock.patch.object(bmi260, 'ARCHIVE_SHA', '0' * 64):
            with self.assertRaisesRegex(ValueError, 'locked size and SHA-256'):
                bmi260.prepare(self.stage, self.archive)

    def test_proof_only_allows_lock_hash_to_differ(self):
        first = bmi260.prepare(self.stage, self.archive)
        self.proof_data['project_input_lock_sha256'] = '2' * 64
        self.save_proof()
        second = bmi260.prepare(self.stage, self.archive)
        self.assertEqual(first[:2], second[:2])
        self.assertNotEqual(first[2]['source_proof_sha256'], second[2]['source_proof_sha256'])
        self.assertEqual(second[2]['project_input_lock_sha256'], '2' * 64)
        self.proof_data['files'][0]['sha256'] = '0' * 64
        self.save_proof()
        with self.assertRaisesRegex(ValueError, 'semantic proof differs'):
            bmi260.prepare(self.stage, self.archive)

    def test_stage_bytes_are_not_reopened_after_validation(self):
        original_open = bmi260.public_input
        @contextmanager
        def replace_then_open(path, expected):
            (self.stage / bmi260.COMMON / 'sources/gyro_binding.py').write_bytes(b'replaced')
            with original_open(path, expected) as stream:
                yield stream
        with mock.patch.object(bmi260, 'public_input', side_effect=replace_then_open) as held:
            files, _, _ = bmi260.prepare(self.stage, self.archive)
        held.assert_called_once_with(self.archive, dict(size=self.archive.stat().st_size, sha256=bmi260.file_digest(self.archive)))
        self.assertEqual(files['var/lib/oxp3-gyro-v1/binding.py'][0], b'journaled binding')


if __name__ == '__main__':
    unittest.main()
