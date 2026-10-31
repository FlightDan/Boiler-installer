import builtins
import importlib._bootstrap_external
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ADAPTATIONS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('review_collection', ADAPTATIONS/'scripts/adaptations.py')
collection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collection)
catalog = collection.toon_catalog


class ToonReviewRegressions(unittest.TestCase):
    def test_prepare_preserves_inherited_package_source_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adaptations = root/'adaptations'
            shutil.copytree(ADAPTATIONS, adaptations)
            source = adaptations/'brands/onexplayer/x2-mini/x2-mini.toon'
            before = catalog.load(source, adaptations)
            self.assertEqual(before['package']['kind'], 'BoilerInheritedPackageDefinition')
            plan = json.loads((adaptations/'source-plan.json').read_bytes())
            blobs = {row['upstream_path']: (adaptations/row['path']).read_bytes() for row in plan['files']}
            def git(args, **kwargs):
                argv = args[3:]
                if argv[:3] == ['remote', 'get-url', 'origin']:
                    return ('https://github.com/'+plan['source']['repository']+'\n').encode()
                if argv[0] == 'rev-parse': return (plan['source']['commit']+'\n').encode()
                if argv[0] == 'ls-tree': return ('100644 blob '+'0'*40+'\t'+argv[-1]+'\n').encode()
                if argv[0] == 'show': return blobs[argv[1].split(':', 1)[1]]
                raise AssertionError(args)
            with patch.object(collection.subprocess, 'check_output', side_effect=git):
                result = collection.prepare(root/'git-fixture', adaptations)
            self.assertEqual(result['status'], 'source-collection-verified')
            after = catalog.load(source, adaptations)
            self.assertEqual(after['package'], before['package'])
            # Refreshes must stay idempotent while inherited data remains authoritative.
            with patch.object(collection.subprocess, 'check_output', side_effect=git):
                collection.prepare(root/'git-fixture', adaptations)
            self.assertEqual(catalog.load(source, adaptations)['package'], before['package'])

    def test_valid_timestamp_bytecode_cannot_replace_pinned_parser_source(self):
        marker = '_boiler_review_unverified_pyc_executed'
        if hasattr(builtins, marker): delattr(builtins, marker)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                shutil.copytree(ADAPTATIONS/'vendor', root/'vendor')
                shutil.copyfile(ADAPTATIONS/'toon-parser-lock.json', root/'toon-parser-lock.json')
                source = root/'vendor/toon_format/__init__.py'
                stat = source.stat()
                malicious = ('import builtins\nbuiltins.'+marker+'=True\n'
                             '__version__="1.2.0"\n__toon_spec__="4.4"\n')
                cache = Path(importlib.util.cache_from_source(str(source)))
                cache.parent.mkdir(exist_ok=True)
                cache.write_bytes(importlib._bootstrap_external._code_to_timestamp_pyc(
                    compile(malicious, str(source), 'exec'), int(stat.st_mtime), stat.st_size))
                parser = catalog.decoder(root)
                self.assertFalse(getattr(builtins, marker, False))
                self.assertEqual(parser.loads('x: 1', strict=True), {'x': 1})
        finally:
            if hasattr(builtins, marker): delattr(builtins, marker)


if __name__ == '__main__': unittest.main()
