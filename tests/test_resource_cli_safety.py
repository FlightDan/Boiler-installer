"""Restore reviews must not misstate mutable CPU/input configuration."""
import unittest
from unittest.mock import patch
from maintenance import resource_cli as cli
from protocol.cpu_contract import IDENTITY as CPU_ID

class MutableRestorePlanSafety(unittest.TestCase):
    def test_mutable_handlers_refuse_before_session_or_write(self):
        for identity in (CPU_ID, 'resource:onexplayer-input', 'resource:bosch-bmi260-gyro'):
            with self.subTest(identity=identity), patch.object(cli, '_selection', return_value=([], [identity], object())), patch.object(cli, '_session') as session:
                with self.assertRaisesRegex(cli.ResourceMaintenanceError, 'mutable-configuration plan renderer'):
                    cli._restore_plan(object(), object(), 'device:fixture', [identity], 'a'*32, 'A')
                session.assert_not_called()
