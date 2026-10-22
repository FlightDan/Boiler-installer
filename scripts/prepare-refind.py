"""Prepare the common pinned rEFInd payload, including its public source/notices."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('boiler_dependency_bootstrap', Path(__file__).with_name('bootstrap-dependencies.py'))
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--public-installer', type=Path)
parser.add_argument('--cache', type=Path)
parser.add_argument('--offline', action='store_true')
parser.add_argument('--check-only', action='store_true')
args = parser.parse_args()
value = bootstrap.prepare(args.root, 'refind', public_installer=args.public_installer, cache=args.cache,
                          offline=args.offline, check_only=args.check_only)
print(json.dumps({'component': 'core:refind', 'files': len(value['files']), 'created': value['created'], 'mode': value['mode']}))
