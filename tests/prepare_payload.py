"""Regenerate real current core files and explicitly retain one verified pool."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'components')]

def build(pool_root, output):
    from protocol.resource_pool import validate_pool,inventory
    pool_root,output=Path(pool_root),Path(output)
    validate_pool(pool_root)
    if output.exists(): raise FileExistsError('Fresh payload output required')
    spec=importlib.util.spec_from_file_location('fresh_real_core_builder',ROOT/'scripts/prepare-integration.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    manifest=builder.prepare(output/'integration')
    row=manifest['components']['core:maintenance']
    from maintenance.core_retention import CORE_MARKER
    row['component']['core_retention']=dict(CORE_MARKER)
    for entry in inventory(pool_root):
        raw=(pool_root/entry['path']).read_bytes()
        if len(raw)!=entry['size'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('Pool changed during preparation')
        name='resource-pool/'+entry['path'];path=output/'integration'/name
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        record=dict(source=name,target='usr/local/lib/boiler/cache/'+name,mode=0o644,
                    size=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        row['files'].append(record)
        # prepare-integration's component.files shares this list by design.
    manifest['fresh_resource_candidate']=dict(production_authenticated=False,old_package_read=False)
    (output/'integration/manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    return manifest
