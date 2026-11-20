from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'components'), str(ROOT / 'guest')]
from integration import validate_sleep_provider
from maintenance.engine import MaintenanceError
from maintenance.shared_models import (OLD_SLEEP_ROUTER, SLEEP_COMPONENT, SLEEP_FILES,
                                       check_static_component, guard_sleep_restore)


class SleepComponentTests(unittest.TestCase):
    def test_deployment_requires_selected_model_decky(self):
        sleep = {'id': SLEEP_COMPONENT}
        model = {'id': 'oxp3:legacy-adaptation'}
        validate_sleep_provider([sleep, model], {'device_legacy_options': {'plugins': ['oxp3:decky']}})
        for selection, options in (([sleep, model], {}), ([sleep], {'device_legacy_options': {'plugins': ['oxp3:decky']}})):
            with self.assertRaisesRegex(ValueError, 'Decky provider'):
                validate_sleep_provider(selection, options)

    def test_static_deployment_refuses_old_router(self):
        engine = Mock()
        engine._target.side_effect = Path
        payload = {'id': SLEEP_COMPONENT, 'files': [{'target': path} for path in SLEEP_FILES]}
        with patch('maintenance.shared_models.observe', side_effect=lambda _, path: {'exists': path == Path(OLD_SLEEP_ROUTER)}):
            with self.assertRaisesRegex(MaintenanceError, 'old Battery sleep integration'):
                check_static_component(engine, payload)

    def test_restore_refuses_to_orphan_the_reserved_swap(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Mock(home_root=Path(temp))
            guard_sleep_restore(engine, SLEEP_COMPONENT)
            root = Path(temp) / '.boiler-s2s4'
            root.mkdir()
            swap = root / 'hibernation.swapfile'
            swap.touch()
            with self.assertRaisesRegex(MaintenanceError, 'Turn off S4'):
                guard_sleep_restore(engine, SLEEP_COMPONENT)
            swap.unlink()
            (root / 'swap-state.json').touch()
            with self.assertRaisesRegex(MaintenanceError, 'Turn off S4'):
                guard_sleep_restore(engine, SLEEP_COMPONENT)
            (root / 'swap-state.json').unlink()
            (root / 'deadline-efi.json').touch()
            with self.assertRaisesRegex(MaintenanceError, 'Turn off S4'):
                guard_sleep_restore(engine, SLEEP_COMPONENT)
