from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError

from maintenance import project
from maintenance.engine import MaintenanceError


class ProjectDiscoveryTests(unittest.TestCase):
    @staticmethod
    def _opti_model():
        return {'components': [{'id': 'opti2xmx'}]}

    @staticmethod
    def _write_installed_opti(root, version, *, name='Opti2XMX'):
        plugin = Path(root) / project.OPTI_PLUGIN_DIR
        plugin.mkdir(parents=True)
        (plugin/'plugin.json').write_text(json.dumps({'name': name}), encoding='utf-8')
        (plugin/'package.json').write_text(json.dumps({'version': version}), encoding='utf-8')

    def test_unique_exact_matching_with_packaged_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            dmi = Path(temporary)
            (dmi/'sys_vendor').write_text('ONE-NETBOOK\n')
            for name in ('ONEXPLAYER X2 Mini', 'ONEXPLAYER X2Mini'):
                (dmi/'product_name').write_text(name+'\n')
                match, model = project.hardware_match(dmi=dmi)
                self.assertEqual(match['model_id'], 'device:x2-mini')
                self.assertIsNone(project._fallback(match, True))
            match, _ = project.hardware_match(dmi=dmi, catalog={'models': [model, model]})
            self.assertEqual(match['status'], 'ambiguous')
            (dmi/'product_name').write_text('ONEXPLAYER X2Mini PRO\n')
            match, _ = project.hardware_match(dmi=dmi)
            self.assertEqual(match['status'], 'no-model')

    def test_legacy_lookup_only_for_no_model_and_fixed_exact_pair(self):
        match = dict(status='no-model', manufacturer='ONE-NETBOOK', product='ONEXPLAYER 3')
        result = project._fallback(match, True)
        self.assertEqual(result['repository'], 'FlightDan/OneXPlayer3-SteamOS')
        self.assertEqual(result['status'], 'offline')
        for status in ('matched', 'unavailable', 'ambiguous'):
            self.assertIsNone(project._fallback(dict(match, status=status), False))

    def test_github_rate_limit_falls_back_to_maintenance_tags(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom">
        <entry><link href="https://github.com/FlightDan/Boiler-installer/releases/tag/v0.2.1"/></entry>
        <entry><link href="https://github.com/FlightDan/Boiler-installer/releases/tag/maintenance-v0.2.0-maintenance.6"/></entry>
        </feed>'''
        limited = HTTPError('', 403, 'Rate limited', {}, None)
        with mock.patch.object(project, 'remote_bytes', side_effect=(limited, feed)):
            self.assertEqual(project._generic_endpoint(), 'https://github.com/FlightDan/Boiler-installer'
                             '/releases/download/maintenance-v0.2.0-maintenance.6')

    def test_generic_channel_refuses_hooks_and_other_release_repositories(self):
        source = dict(kind='github', repository=project.GENERIC_REPO, commit='a'*40)
        component = dict(id='core:maintenance', version='1.0.0',
                         files=[{'target': 'usr/local/bin/boiler-maintenance'}])
        package = dict(source=source, purpose='maintenance', version='1.0.0', components=[component])
        project._validate_channel('maintenance', {}, package)
        component['hooks'] = {'apply': 'apply.sh'}
        with self.assertRaisesRegex(MaintenanceError, 'static runtime'):
            project._validate_channel('maintenance', {}, package)
        with self.assertRaisesRegex(MaintenanceError, 'official Boiler Release'):
            project._transport({'source': {'kind': 'https', 'url': 'https://example.org/release.zip'}},
                               'maintenance')

    def test_opti_upstream_compares_to_installed_package_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._write_installed_opti(temporary, '0.3.1')
            with mock.patch.object(project, '_releases', return_value=[{'tag_name': 'v0.3.2'}]):
                result = project._upstream_plugin(temporary, self._opti_model())
        self.assertEqual(result['installed_version'], '0.3.1')
        self.assertEqual(result['version'], '0.3.2')
        self.assertTrue(result['newer'])

    def test_opti_upstream_does_not_compare_an_uninstalled_plugin_to_the_source_lock(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(project, '_releases') as releases:
            result = project._upstream_plugin(temporary, self._opti_model())
        releases.assert_not_called()
        self.assertEqual(result['status'], 'not-installed')
        self.assertIsNone(result['installed_version'])
        self.assertFalse(result['newer'])

    def test_opti_upstream_uses_an_older_installed_record_not_the_newer_lock_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._write_installed_opti(temporary, '0.3.0')
            with mock.patch.object(project, '_releases', return_value=[{'tag_name': 'v0.3.1'}]):
                result = project._upstream_plugin(temporary, self._opti_model())
        self.assertEqual(result['installed_version'], '0.3.0')
        self.assertEqual(result['version'], '0.3.1')
        self.assertTrue(result['newer'])

    def test_opti_upstream_rejects_a_source_record_for_another_plugin(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._write_installed_opti(temporary, '0.3.0', name='Other Plugin')
            with mock.patch.object(project, '_releases') as releases:
                result = project._upstream_plugin(temporary, self._opti_model())
        releases.assert_not_called()
        self.assertIn('does not identify Opti2XMX', result['message'])


if __name__ == '__main__':
    unittest.main()
