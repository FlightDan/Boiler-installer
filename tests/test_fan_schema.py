import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('fan_schema', Path(__file__).resolve().parents[1] / 'maintenance' / 'fan_schema.py')
fan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fan)

class FanSchemaTests(unittest.TestCase):
    def test_valid(self):
        for config in ({'mode': 'auto'}, {'mode': 'balanced'}, {'mode': 'performance'},
                       {'mode': 'custom', 'points': [[30, 35], [85, 100]]}):
            self.assertIs(fan.validate(config), config)
    def test_invalid(self):
        for config in (None, {}, {'mode': 'other'}, {'mode': 'custom'},
                       {'mode': 'custom', 'points': [[30, 35], [84, 100]]},
                       {'mode': 'custom', 'points': [[30, 35], [30, 100]]},
                       {'mode': 'custom', 'points': [[30, True], [85, 100]]}):
            with self.subTest(config=config), self.assertRaises(ValueError): fan.validate(config)

if __name__ == '__main__': unittest.main()
