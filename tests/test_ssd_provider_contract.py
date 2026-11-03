import copy
import unittest

from protocol.manifest import ProtocolError, validate_manifest


class SsdProviderContractTests(unittest.TestCase):
    def component(self):
        return dict(schema_version=1, kind='ComponentManifest', id='device:gm7', version='1.0.0',
                    minimum_engine_version='0.1.3', optional=True, files=[], hooks={},
                    ssd_boot_policy=dict(id='ssd:gm7-boot-repair', version=1))

    def package(self):
        return dict(schema_version=1, kind='DevicePackage', id='device:test', version='1.0.0',
                    minimum_engine_version='0.1.3', source=dict(kind='local', path='fixture'),
                    components=[self.component()], files=[])

    def test_valid_provider_and_legacy_components(self):
        validate_manifest(self.package(), 'DevicePackage')
        old = self.component()
        del old['ssd_boot_policy']; del old['minimum_engine_version']
        validate_manifest(old, 'ComponentManifest')

    def test_unknown_policy_and_untrusted_execution_rejected(self):
        for update in (dict(ssd_boot_policy=dict(id='ssd:other', version=1)),
                       dict(ssd_boot_policy=dict(id='ssd:gm7-boot-repair', version=2)),
                       dict(ssd_boot_policy=dict(id='ssd:gm7-boot-repair', version=True)),
                       dict(hooks=dict(apply='scripts/run.sh')),
                       dict(files=[dict(source='x', target='x', size=0, sha256='0'*64)]),
                       dict(compatibility=dict(os_versions=['3.99'])),
                       dict(optional=False),
                       dict(requires=['device:other']),
                       dict(conflicts=['device:other']),
                       dict(dynamic_handler='anything')):
            with self.subTest(update=update), self.assertRaises(ProtocolError):
                validate_manifest(dict(self.component(), **update), 'ComponentManifest')

    def test_both_package_and_component_require_new_engine(self):
        for where in ('package', 'component'):
            for version in (None, '0.1.2'):
                package = self.package()
                target = package if where == 'package' else package['components'][0]
                if version is None: target.pop('minimum_engine_version')
                else: target['minimum_engine_version'] = version
                with self.subTest(where=where, version=version), self.assertRaises(ProtocolError):
                    validate_manifest(package, 'DevicePackage')

    def test_duplicate_providers_are_not_ambiguous(self):
        package = self.package()
        second = self.component(); second['id'] = 'device:other-gm7'
        package['components'].append(second)
        with self.assertRaises(ProtocolError):
            validate_manifest(package, 'DevicePackage')

    def test_provider_cannot_be_an_implicit_dependency(self):
        package = self.package()
        dependent = dict(schema_version=1, kind='ComponentManifest', id='device:other', version='1.0.0', requires=['device:gm7'])
        package['components'].append(dependent)
        with self.assertRaises(ProtocolError): validate_manifest(package, 'DevicePackage')


if __name__ == '__main__':
    unittest.main()
