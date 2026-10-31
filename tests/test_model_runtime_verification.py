"""Directory runtime verification keeps its own limit, separate from v1 adoption."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'boiler_runtime_verification', ROOT/'adaptations/brands/onexplayer/oxp3/bridge/adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


def snapshot(data, mode=0o644):
    return dict(exists=True, data=base64.b64encode(data).decode(), mode=mode, uid=0, gid=0)


class RuntimeVerificationTests(unittest.TestCase):
    def test_large_runtime_snapshot_does_not_expand_inline_adoption_limit(self):
        saved = snapshot(b'x'*(128*1024*1024+1))
        with self.assertRaisesRegex(RuntimeError, 'snapshot exceeds'):
            ADAPTER._normal_snapshot(saved)
        normalized = ADAPTER._normal_snapshot(saved, maximum=256*1024*1024)
        self.assertEqual(normalized['sha256'], hashlib.sha256(base64.b64decode(saved['data'])).hexdigest())

    def test_runtime_limit_still_rejects_members_above_256_mib(self):
        saved = snapshot(b'x'*(256*1024*1024+1))
        with self.assertRaisesRegex(RuntimeError, 'snapshot exceeds'):
            ADAPTER._normal_snapshot(saved, maximum=256*1024*1024)

    @unittest.skipUnless(os.name == 'posix', 'Native Linux ownership and activation-link verification')
    def test_verify_directory_member_above_inline_limit_and_detect_changed_bytes(self):
        if os.geteuid() != 0:
            self.skipTest('Protected verification fixture requires Linux root')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            options = dict(repairs=['opti2xmx'], plugins=[])
            target = 'opt/oxp3-opti2xmx/20261005/igc/local/lib/libopencl-clang2.so.15'
            saved = snapshot(b'x'*(128*1024*1024+1))
            reports = {name: dict(status='files_installed', files=[]) for name in
                       ('opti2xmx', 'system-update-hold', 'serial-console-policy', 'maintenance-tool')}
            reports['opti2xmx']['files'] = [target]
            snapshots = {'etc/passwd': snapshot(b'deck:x:1000:1000::/home/deck:/bin/bash\n'), target: saved}
            base = 'home/.oxp3-installer/integration/'

            def document(name, value):
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                snapshots[name] = snapshot(json.dumps(value).encode(), 0o600)

            document(base+'A/result.json', dict(schema=1, slot='A', options=options, components=reports))
            for component, name in [('runtime', 'etc/runtime.py'), ('release-note', 'etc/release.txt')]:
                content = snapshot(b'small managed file')
                snapshots[name] = content
                document(base+'A/'+component+'/state.json', dict(
                    phase='installed', allowed=[name], original={name: dict(exists=False)},
                    applied={name: content['data']}))
            document('opt/oxp3-opti2xmx/.installer-state/state.json', {})
            link = root/ADAPTER.LINK
            link.parent.mkdir(parents=True)
            link.symlink_to(ADAPTER.LINK_TARGET)
            for path in [root, *[p for p in root.rglob('*') if p.is_dir()]]:
                path.chmod(0o755)
            decoder = types.SimpleNamespace(_directory_originals=lambda *_: {
                target: dict(original=dict(exists=False), applied=saved)})
            spec = types.SimpleNamespace(loader=mock.Mock())
            # The decoder's tree trust checks have their own integration gate;
            # inject its bounded output to exercise both adapter normalizations.
            with mock.patch('importlib.util.spec_from_file_location', return_value=spec), \
                    mock.patch('importlib.util.module_from_spec', return_value=decoder):
                proof = ADAPTER.verify_staged(root, 'A', options, reader=lambda name, **_: snapshots[name])
                entry = next(item for item in proof['files'] if item['path'] == target)
                self.assertEqual(entry['size'], 128*1024*1024+1)
                snapshots[target] = snapshot(b'changed runtime bytes')
                with self.assertRaisesRegex(RuntimeError, 'differs from its applied journal'):
                    ADAPTER.verify_staged(root, 'A', options, reader=lambda name, **_: snapshots[name])


if __name__ == '__main__':
    unittest.main()
