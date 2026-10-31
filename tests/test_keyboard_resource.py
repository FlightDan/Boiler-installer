import unittest
from unittest.mock import patch

from maintenance import keyboard_resource as bridge
from maintenance.resource_runtime import ResourceError
from protocol.resource_selection import _choices


class Pool:
    def __init__(self, resource): self.value = resource
    def resource(self, identity):
        if identity != bridge.IDENTITY: raise AssertionError(identity)
        return self.value


def resource():
    return dict(id=bridge.IDENTITY, install=dict(bridge.INSTALL),
                verify=dict(bridge.VERIFY), restore=dict(bridge.RESTORE),
                payload={'files': []}, dependencies=[],
                external_component_dependencies=[], hardware_requires={})


class KeyboardResourceTests(unittest.TestCase):
    def setUp(self):
        self.pool = Pool(resource())
        self.intent = {'slot': 'A', 'selection': {'components': [bridge.IDENTITY]}}

    def test_authorization_and_fixed_contract(self):
        self.assertEqual(bridge.authorize(self.pool, self.intent), 'A')
        for key, value in [('install', {'kind': 'files-v1', 'files': []}),
                           ('payload', {'files': [{'path': 'kwinrc'}]}),
                           ('hardware_requires', {'fake': [True]}),
                           ('id', 'resource:other')]:
            altered = resource(); altered[key] = value
            with self.assertRaises(ResourceError): bridge.authorize(Pool(altered), self.intent)
        for components in ([], [bridge.IDENTITY, bridge.IDENTITY]):
            with self.assertRaises(ResourceError):
                bridge.authorize(self.pool, {'slot': 'A', 'selection': {'components': components}})

    def test_empty_payload_fixed_handler_is_selectable_with_model_default(self):
        item=resource();item.update(version='1',install=dict(bridge.INSTALL))
        model={'resources':[{'resource':bridge.IDENTITY,'default':True,'optional':True}]}
        row=_choices({'resources':[item]},model)[0]
        self.assertTrue(row['selectable']);self.assertTrue(row['default']);self.assertEqual(row['scope'],['shared-home'])

    @patch.object(bridge, 'Keyboard')
    def test_bridge_uses_canonical_runtime(self, keyboard):
        service = keyboard.return_value
        service.plan.return_value = {'target_binding': {'label': 'A'}, 'review_sha256': 'digest'}
        reviewed = bridge.plan(self.pool, self.intent, 'apply', root='/fixture')
        service.plan.assert_called_once_with('apply', 'A')
        bridge.execute(self.pool, self.intent, reviewed, reviewed_sha256='digest', root='/fixture')
        service.execute.assert_called_once_with(reviewed, reviewed_sha256='digest')
        bridge.verify_readonly(self.pool, self.intent, root='/fixture')
        service.verify_readonly.assert_called_once_with('A')
        reviewed['target_binding']['label'] = 'B'
        with self.assertRaises(ResourceError):
            bridge.execute(self.pool, self.intent, reviewed, reviewed_sha256='digest')

    @patch.object(bridge, 'Keyboard')
    def test_fixed_lifecycle_requires_matching_reviewed_action(self, keyboard):
        for action, call in [('apply', bridge.activate), ('restore', bridge.restore),
                             ('recover', bridge.retry)]:
            reviewed = {'action': action, 'target_binding': {'label': 'A'}}
            call(self.pool, self.intent, reviewed, reviewed_sha256='digest')
            reviewed['action'] = 'verify'
            with self.assertRaises(ResourceError):
                call(self.pool, self.intent, reviewed, reviewed_sha256='digest')
        self.assertEqual(keyboard.return_value.execute.call_count, 3)


if __name__ == '__main__': unittest.main()
