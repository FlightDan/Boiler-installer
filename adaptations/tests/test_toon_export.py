import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('toon_source_export_tests',ROOT/'scripts/export-source.py')
exporter=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(exporter)


class ToonSourceExportTests(unittest.TestCase):
    def test_existing_export_preflight_verifies_authoritative_toon_and_parser(self):
        files=set(exporter.reviewed_files(ROOT))
        exporter.verify_adaptation_snapshots(ROOT,files)
        self.assertIn('adaptations/models.toon',files)
        self.assertIn('adaptations/brands/onexplayer/x2-mini/x2-mini.toon',files)
        self.assertIn('adaptations/brands/onexplayer/common/components.toon',files)

    def test_export_refuses_omitted_toon_or_parser_even_when_json_is_present(self):
        files=set(exporter.reviewed_files(ROOT))
        for name in ('adaptations/models.toon','adaptations/vendor/toon_format/_decoder.py',
                     'adaptations/scripts/build-shared-base.py',
                     'adaptations/scripts/build-x2-complete.py',
                     'adaptations/scripts/build-complete-bridge.py',
                     'adaptations/scripts/build-x2-hdr-overlay.py',
                     'adaptations/records/oxp3-shared-base/opti031-inputs.json',
                     'adaptations/brands/onexplayer/x2-mini/x2-mini.toon'):
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'omitted from whitelist'):
                exporter.verify_model_catalog(ROOT,files-{name})

    def test_actual_source_export_preserves_toon_and_legacy_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination=Path(temporary)/'export'
            report=exporter.export(destination)
            names={row['path'] for row in report['files']}
            self.assertIn('adaptations/models.toon',names)
            for name in ('adaptations/models.toon','adaptations/brands/onexplayer/x2-mini/manifest.json',
                         'adaptations/brands/onexplayer/x2-mini/x2-mini.toon'):
                self.assertEqual((destination/name).read_bytes(),(ROOT/name).read_bytes())
            self.assertFalse(any('.local' in Path(name).parts or name.startswith('adaptations/inputs/') for name in names))


if __name__=='__main__': unittest.main()
