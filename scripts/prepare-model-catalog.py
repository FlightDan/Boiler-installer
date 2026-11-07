"""Freeze display-only model guides from an exact public repository commit."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.model_catalog import REPOSITORY, SOURCE_SUBTREE, MAX_CATALOG_BYTES, CATALOG_SOURCE, document, catalog_records, guide_model, source_member
from protocol.manifest import confined, relative_path


def _adaptations_root(output, adaptations):
    if adaptations is not None:
        return Path(adaptations)
    if output.name == 'model-catalog' and output.parent.name == 'components':
        return output.parents[1] / CATALOG_SOURCE
    return output / CATALOG_SOURCE


def prepare(checkout, commit, output, *, source_subtree=SOURCE_SUBTREE, adaptations=None):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Exact public catalog commit required')
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh catalog snapshot directory')
    if source_subtree != SOURCE_SUBTREE: raise ValueError('Canonical catalog must use repository root')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(checkout), *args], stderr=subprocess.PIPE)
    if git('remote', 'get-url', 'origin').decode().strip() not in (
            'https://github.com/'+REPOSITORY, 'https://github.com/'+REPOSITORY+'.git',
            'git@github.com:'+REPOSITORY+'.git'):
        raise ValueError('Catalog must come from the canonical public Boiler-adaptations repository')
    source = {'repository': REPOSITORY, 'commit': commit, 'subtree': source_subtree}
    catalog_data = git('show', commit+':'+source_member(source, 'adaptations.json'))
    catalog = document(catalog_data)
    records=catalog_records(catalog)
    contents = {'adaptations.json': catalog_data}
    for row in records:
        path=row['manifest']
        data = git('show', commit+':'+source_member(source, path))
        guide_model(document(data),path,source,row)
        contents[path] = data
    if sum(len(data) for data in contents.values())>MAX_CATALOG_BYTES:
        raise ValueError('Model catalog snapshot exceeds size limit')
    inventory = [dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest())
                 for name, data in contents.items()]
    adaptations_root = _adaptations_root(output, adaptations)
    adaptations_root.mkdir(parents=True, exist_ok=True)
    for name, data in contents.items():
        path = confined(adaptations_root, relative_path(name))
        if path.exists():
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError('Adaptation snapshot differs from the pinned commit: ' + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    output.mkdir(parents=True, exist_ok=True)
    lock = dict(schema_version=1, kind='ModelCatalogLock', source=source, files=inventory)
    (output/'lock.json').write_text(json.dumps(lock, indent=2)+'\n', encoding='utf-8', newline='\n')
    return lock


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-checkout', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--source-subtree', default=SOURCE_SUBTREE,
                        help='Explicit catalog subtree (default: repository root)')
    parser.add_argument('--adaptations', type=Path, default=None,
                        help='Adaptation snapshot directory to verify or populate')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source_checkout, args.source_commit, args.output,
                     source_subtree=args.source_subtree, adaptations=args.adaptations)
    print(json.dumps({'source': result['source'], 'files': len(result['files'])}))
