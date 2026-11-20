import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
X2 = HERE.parents[2] / 'x2-mini' / 'current' / 'power' / 'battery_control.py'

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PowerBackendTests(unittest.TestCase):
    def test_x2_telemetry_and_denied_writes(self):
        module = load(X2, 'x2_battery_test')
        with tempfile.TemporaryDirectory() as directory:
            module.ROOT = Path(directory)
            battery = module.ROOT / 'BAT0'; battery.mkdir()
            for name, value in {'type': 'Battery', 'capacity': '82', 'status': 'Charging', 'power_now': '12300000'}.items():
                (battery / name).write_text(value)
            ac = module.ROOT / 'AC'; ac.mkdir(); (ac / 'online').write_text('1')
            result = module.status()
            self.assertEqual((result['capacity'], result['status'], result['power_now'], result['ac']), ('82', 'Charging', '12300000', True))
            self.assertFalse(result['charge_control_available'])
            self.assertIsNone(result['energy_now'])
            with self.assertRaisesRegex(RuntimeError, 'Charge control is not enabled') as error:
                module.set_value('charge_behaviour', 'auto')
            self.assertNotIn('sleep', str(error.exception))

    def test_plugin_exposes_only_power_operations(self):
        class Battery:
            def status(self): return {'capacity': '82'}
            def set_value(self, name, value): return (name, value)
        class Loader:
            def exec_module(self, module):
                module.status = Battery().status
                module.set_value = Battery().set_value
        class Spec: loader = Loader()
        import types
        with patch('importlib.util.spec_from_file_location', return_value=Spec()), patch('importlib.util.module_from_spec', side_effect=lambda spec: types.ModuleType('battery')):
            plugin = load_plugin_without_loader_patch_conflict()
        async def verify():
            self.assertEqual(await plugin.get_status(), {'capacity': '82'})
            self.assertEqual(await plugin.set_limit(75), ('charge_control_end_threshold', 75))
            self.assertEqual(await plugin.set_mode('auto'), ('charge_behaviour', 'auto'))
            self.assertFalse(hasattr(plugin, 'get_sleep_status'))
            self.assertFalse(hasattr(plugin, 'set_sleep_policy'))
        asyncio.run(verify())

def load_plugin_without_loader_patch_conflict():
    namespace = {}
    exec(compile((HERE / 'battery-plugin-main.py').read_text(), 'battery-plugin-main.py', 'exec'), namespace)
    return namespace['Plugin']()

if __name__ == '__main__': unittest.main()
