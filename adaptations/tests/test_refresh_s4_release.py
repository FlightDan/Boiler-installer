import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('refresh_s4', Path(__file__).resolve().parents[1] / 'scripts' / 'refresh-s4-release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class RefreshTests(unittest.TestCase):
    def archive(self, overrides=None, extra=None):
        values = {name: name.encode() for name in module.MEMBERS}
        values.update(overrides or {})
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            for name, data in values.items():
                z.writestr(module.PREFIX + name, data)
            if extra:
                z.writestr(module.PREFIX + extra, b'extra')
        data = stream.getvalue()
        return data, {'size': len(data), 'digest': 'sha256:' + module.digest(data)}

    def test_archive_requires_matching_metadata_and_every_git_member(self):
        data, asset = self.archive()
        def fake_git(binary, checkout, action, path):
            return path.split(':', 1)[1].encode()
        with patch.object(module, 'git', side_effect=fake_git):
            self.assertEqual(len(module.checked_archive(data, asset, 'git', '.', 'a' * 40)), 8)
            with self.assertRaises(ValueError):
                module.checked_archive(data, {**asset, 'size': asset['size'] + 1}, 'git', '.', 'a' * 40)
            wrong, meta = self.archive({'README.md': b'changed'})
            with self.assertRaises(ValueError):
                module.checked_archive(wrong, meta, 'git', '.', 'a' * 40)
            extra, meta = self.archive(extra='../escape')
            with self.assertRaises(ValueError):
                module.checked_archive(extra, meta, 'git', '.', 'a' * 40)

    def test_router_only_accepts_static_bytes(self):
        self.assertEqual(module.router(b"ROUTER_BYTES = b'example\\n'"), b'example\n')
        with self.assertRaises((ValueError, TypeError)):
            module.router(b"ROUTER_BYTES = make_router()")

    def test_resolve_requires_stable_release_asset_and_tag_commit(self):
        commit = 'a' * 40
        asset = {'name': 'Boiler-S2S4-Switcher-1.2.3.zip', 'size': 3, 'digest': 'sha256:' + 'b' * 64,
                 'browser_download_url': 'https://github.com/' + module.REPO + '/releases/download/v1.2.3/Boiler-S2S4-Switcher-1.2.3.zip'}
        release = {'tag_name': 'v1.2.3', 'draft': False, 'prerelease': False, 'assets': [asset]}
        def fake_api(path):
            return release if path == '/releases/latest' else {'object': {'type': 'commit', 'sha': commit}}
        with patch.object(module, 'api', side_effect=fake_api):
            self.assertEqual(module.resolve_release()[:2], ('v1.2.3', commit))
            release['prerelease'] = True
            with self.assertRaises(ValueError): module.resolve_release()
            release['prerelease'] = False
            asset['digest'] = None
            with self.assertRaises(ValueError): module.resolve_release()

    def test_rejects_downgrade_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'common' / 'sleep'
            base.mkdir(parents=True)
            (base / 'install.json').write_text(json.dumps({'version': '2.0.0'}))
            (base / 'source-lock.json').write_text('{}')
            def fake_git(binary, checkout, *args):
                return (('https://github.com/' + module.REPO + '.git') if args[0] == 'remote' else 'a' * 40).encode()
            with patch.object(module, 'resolve_release', return_value=('v1.0.0', 'a' * 40, {})), patch.object(module, 'git', side_effect=fake_git), patch.object(module, 'fetch') as fetch:
                with self.assertRaisesRegex(ValueError, 'downgrade'):
                    module.refresh(root, 'git', root)
                fetch.assert_not_called()

    def test_refresh_generates_payload_and_consistent_manifests(self):
        install = b"ROUTER_BYTES = b'router\\n'"
        package = b'{"name":"boiler-sleep","version":"1.2.3"}'
        archive, asset = self.archive({'install.py': install, 'package.json': package})
        asset.update(name='Boiler-S2S4-Switcher-1.2.3.zip', browser_download_url='asset')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'common' / 'sleep'
            (base / 'runtime' / 'dist').mkdir(parents=True)
            rows = [{'source': 'common/sleep/runtime/' + name, 'target': target, 'mode': 420}
                    for name, target in zip(module.RUNTIME, module.TARGETS)]
            (base / 'install.json').write_text(json.dumps({'version': '1.0.0', 'files': rows}))
            (base / 'source-lock.json').write_text(json.dumps({'source': {'repository': module.REPO, 'commit': 'b' * 40}}))
            def fake_git(binary, checkout, *args):
                if args[0] == 'remote': return ('https://github.com/' + module.REPO + '.git').encode()
                if args[0] == 'rev-parse': return b'a' * 40
                name = args[1].split(':', 1)[1]
                return {'install.py': install, 'package.json': package}.get(name, name.encode())
            with patch.object(module, 'resolve_release', return_value=('v1.2.3', 'a' * 40, asset)), patch.object(module, 'git', side_effect=fake_git), patch.object(module, 'fetch', return_value=archive):
                module.refresh(root, 'git', root)
                module.refresh(root, 'git', root)
                original_digest = asset['digest']
                asset['digest'] = 'sha256:' + 'b' * 64
                with self.assertRaisesRegex(ValueError, 'different commit or archive'):
                    module.refresh(root, 'git', root)
                asset['digest'] = original_digest
            manifest = json.loads((base / 'install.json').read_text())
            lock = json.loads((base / 'source-lock.json').read_text())
            self.assertEqual(manifest['version'], '1.2.3')
            self.assertEqual(lock['source']['commit'], 'a' * 40)
            self.assertEqual((base / 'runtime' / '90-boiler-sleep.conf').read_bytes(), b'router\n')
            for row, locked in zip(manifest['files'], lock['files']):
                content = (root / row['source']).read_bytes()
                self.assertEqual((row['size'], row['sha256']), (len(content), module.digest(content)))
                self.assertEqual((locked['size'], locked['sha256']), (row['size'], row['sha256']))

if __name__ == '__main__':
    unittest.main()
