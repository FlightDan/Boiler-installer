"""Compare an explicitly pinned old model package with a local resource pool.

This is an audit, not a source of executable trust. It never copies old package
files into the pool and does not infer that different bytes have the same effect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.manifest import validate_package


def audit(package_root, manifest_sha256, pool_root, model_id, validate_pool):
    package = validate_package(Path(package_root), expected_manifest_sha256=manifest_sha256)
    pool = validate_pool(Path(pool_root))
    models = [model for model in pool['models'] if model['id'] == model_id]
    if len(models) != 1 or package['id'] != model_id:
        raise ValueError('Baseline and resource model identity differ')
    by_id = {resource['id']: resource for resource in pool['resources']}
    selected = [ref['resource'] for ref in models[0]['resources']]
    if len(selected) != len(set(selected)) or any(identity not in by_id for identity in selected):
        raise ValueError('Invalid model resource references')
    blobs = {}
    for identity in selected:
        for entry in by_id[identity]['payload']['files']:
            blobs.setdefault((entry['sha256'], entry['size']), []).append((identity, entry['path']))
    rows = []
    for item in package['files']:
        key = (item['sha256'], item['size'])
        matches = blobs.get(key, [])
        rows.append(dict(path=item['path'], size=item['size'], sha256=item['sha256'],
                         identical_resource_members=[dict(resource=identity, path=path) for identity, path in matches]))
    capabilities = {entry['id'].split(':', 1)[-1] for entry in package.get('legacy_capabilities', [])}
    capabilities.update(entry['id'].split(':', 1)[-1] for entry in package.get('components', [])
                        if not entry['id'].endswith(':legacy-adaptation'))
    pending = {entry['component'] for entry in models[0].get('pending', [])}
    reported = {cap for identity in selected for cap in by_id[identity].get('capabilities', [])}
    return dict(kind='ResourceCoverageAudit', model=model_id, baseline_manifest_sha256=manifest_sha256,
        baseline_files=len(rows), baseline_bytes=sum(item['size'] for item in rows),
        baseline_identical_files=sum(bool(row['identical_resource_members']) for row in rows),
        baseline_unmatched_files=sum(not row['identical_resource_members'] for row in rows),
        selected_resources=selected, baseline_capabilities=sorted(capabilities),
        capabilities_reported=sorted(reported), capabilities_uncovered=sorted(capabilities-reported),
        capabilities_not_explicitly_pending=sorted(capabilities-reported-pending),
        # Old bridge and test bytes are intentionally replaced, not copied.
        # Equality alone cannot prove semantic lifecycle parity. A future
        # separately reviewed migration record must resolve every unmatched row.
        complete=False,
        file_reconciliation='required',
        files=rows,
        note='Identical bytes are evidence only; lifecycle and transformed files require separate review.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--pool', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from protocol.resource_pool import validate_pool
    result = audit(args.package, args.manifest_sha256, args.pool, args.model, validate_pool)
    if args.output.exists():
        raise FileExistsError('Audit output already exists')
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
