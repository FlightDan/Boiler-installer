import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(os.environ.get('BOILER_OVERLAY_TELEMETRY_SOURCE',
    str(Path(__file__).resolve().parents[2] / 'source/common/telemetry/overlay/files/telemetry.py')))
if not SOURCE.is_file():raise unittest.SkipTest('Set BOILER_OVERLAY_TELEMETRY_SOURCE to the locked adaptations source input')
spec = importlib.util.spec_from_file_location('telemetry', SOURCE)
telemetry = importlib.util.module_from_spec(spec)
with patch.object(os,'getuid',getattr(os,'getuid',lambda:1000),create=True):
    spec.loader.exec_module(telemetry)


class TelemetryTests(unittest.TestCase):
    def test_rebaseline_and_counter_wrap(self):
        old = (10.0, 20.0, {'core': (9_000_000, 10_000_000)})
        current = {'core': (1_000_000, 10_000_000)}
        self.assertEqual(telemetry.rapl_watts(11, 21, current, old), {'core': 2.0})
        self.assertEqual(telemetry.rapl_watts(14, 24, current, old), {})
        self.assertEqual(telemetry.rapl_watts(11, 25, current, old), {})
        self.assertEqual(telemetry.rapl_watts(11, 21, {'core': (1, 20)}, old), {})
        self.assertEqual(telemetry.rapl_watts(11, 21, {}, old), {})

    def test_ambiguous_domain_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base'
            children = [Path(temp) / 'child0', Path(temp) / 'child1']
            for path in (base, *children):
                path.mkdir()
                (path / 'name').write_text('core' if path != base else 'package-0')
                (path / 'energy_uj').write_text('5')
                (path / 'max_energy_range_uj').write_text('10')
            class Parent:
                def glob(self, pattern): return children
            class Root:
                name = 'base'
                parent = Parent()
                def __truediv__(self, name): return base / name
            previous = telemetry.BASE
            try:
                telemetry.BASE = Root()
                with self.assertRaisesRegex(ValueError, 'ambiguous'): telemetry.counters()
            finally:
                telemetry.BASE = previous

    def test_pmt_counter_rebaselines_on_source_reset_and_suspend(self):
        previous=(16384,'pmt-a',10.0,20.0)
        self.assertEqual(telemetry.pmt_gpu_watts((32768,'pmt-a',11.0,21.0),previous),1.0)
        for current in ((32768,'pmt-b',11.,21.),(1,'pmt-a',11.,21.),(32768,'pmt-a',11.,25.),(32768,'pmt-a',14.,24.),(999999999,'pmt-a',11.,21.)):
            self.assertIsNone(telemetry.pmt_gpu_watts(current,previous))
    def test_rapl_rejects_implausible_and_invalid(self):
        old=(10.,20.,{'core':(0,1000000000)})
        self.assertEqual(telemetry.rapl_watts(11.,21.,{'core':(500000000,1000000000)},old),{})
        self.assertEqual(telemetry.rapl_watts(11.,21.,{'core':(1000000000,1000000000)},old),{})
    def test_missing_sample_displays_na(self):
        self.assertEqual(telemetry.display_text({'watts': {}, 'limits': []}, 'domains'), 'N/A / N/A')
        self.assertEqual(telemetry.display_text({'watts': {}, 'limits': []}, 'limits'), 'N/A / N/A')


if __name__ == '__main__': unittest.main()
