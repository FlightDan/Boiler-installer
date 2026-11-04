"""Generate source views from branded model TOON records; no signed bytes are changed."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

common = module('common_device_sources', ROOT / 'scripts/common-device-sources.py')
adaptations = module('adaptations', ROOT / 'scripts/adaptations.py')

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

def resource_view(model):
    resource = model['resource_model']
    runtime = model['runtime']
    if ('name' in resource or 'hardware_matching' in resource or resource.get('id') != runtime['id']):
        raise ValueError('Invalid duplicated resource identity or hardware metadata')
    return dict(resource, name=runtime['name']['en'], hardware_matching=runtime['hardware_matching'])

def generate(root=ROOT, check=False):
    root = Path(root)
    outputs, index, runtime, concise = {}, [], [], []
    seen, paths = {}, set()
    sources = []
    for path in sorted((root / 'brands').glob('*/*/*.toon')):
        if path.parent.name == 'common' and path.name == 'components.toon':
            continue
        if path.parent.name != 'common' and path.name == 'common-device-sources.toon':
            continue
        if path.parent.name == 'common' or path.name != path.parent.name + '.toon':
            raise ValueError('Unknown or misnamed model TOON: ' + path.relative_to(root).as_posix())
        sources.append(path)
    if not sources:
        raise ValueError('No model TOON sources found')
    for path in sources:
        name = path.relative_to(root).as_posix()
        parts = name.split('/')
        if any(not re.fullmatch(r'[a-z][a-z0-9-]*', p) for p in parts[1:3]):
            raise ValueError('Invalid brand/model directory: ' + name)
        if adaptations.local(root, name) != path or not path.is_file():
            raise ValueError('Invalid source path: ' + name)
        model = common.toon_format.loads(path.read_text(encoding='utf-8'), strict=True)
        canonical = 'device:' + parts[1] + '-' + parts[2]
        if model.get('canonical_id') != canonical:
            raise ValueError('Canonical ID differs from model path: ' + name)
        aliases = model.get('legacy_aliases')
        if not isinstance(aliases, dict) or set(aliases) != {'guide', 'package'}:
            raise ValueError('Invalid alias declaration: ' + name)
        for group, prefix in (('guide', 'guide:'), ('package', 'device:')):
            values = aliases[group]
            if not isinstance(values, list) or len(values) > 1:
                raise ValueError('Expected at most one legacy ' + group + ' ID: ' + name)
            for value in values:
                if not isinstance(value, str) or not re.fullmatch(prefix + r'[a-z0-9][a-z0-9-]*', value):
                    raise ValueError('Invalid alias: ' + name)
        published_aliases = {
            'device:onexplayer-oxp3': {'guide': ['guide:oxp3'], 'package': ['device:oxp3']},
            'device:onexplayer-x2-mini': {'guide': ['guide:x2-mini'], 'package': ['device:x2-mini']},
        }
        if aliases != published_aliases.get(canonical, {'guide': [], 'package': []}):
            raise ValueError('Unrecognized historical aliases: ' + name)
        legacy_guide = aliases['guide'][0] if aliases['guide'] else 'guide:' + parts[1] + '-' + parts[2]
        legacy_package = aliases['package'][0] if aliases['package'] else canonical
        if aliases['package'] and legacy_package == canonical:
            raise ValueError('Legacy package alias duplicates canonical ID: ' + name)
        identities = [canonical, legacy_guide]
        if legacy_package != canonical:
            identities.append(legacy_package)
        for value in identities:
            if value.casefold() in seen:
                raise ValueError('Conflicting canonical ID or legacy alias: ' + value)
            seen[value.casefold()] = name
        rt = model.get('runtime')
        if (not isinstance(rt, dict) or set(rt) != {'id', 'name', 'hardware_matching'}
                or rt['id'] != legacy_package or model.get('id') != legacy_guide):
            raise ValueError('Legacy runtime/guide identity differs: ' + name)
        hardware = rt['hardware_matching']
        if (not isinstance(rt['name'], dict) or set(rt['name']) != {'en', 'zh'}
                or not all(isinstance(v, str) and v.strip() for v in rt['name'].values())
                or not isinstance(hardware, dict) or set(hardware) != {'mode', 'pairs'}
                or hardware['mode'] != 'advisory-exact-dmi-v1'
                or not isinstance(hardware['pairs'], list) or not 1 <= len(hardware['pairs']) <= 32
                or any(not isinstance(pair, dict) or set(pair) != {'manufacturer', 'model'}
                       or not all(isinstance(v, str) and v.strip() for v in pair.values()) for pair in hardware['pairs'])
                or len({(pair['manufacturer'], pair['model']) for pair in hardware['pairs']}) != len(hardware['pairs'])):
            raise ValueError('Invalid advisory hardware metadata: ' + name)
        resource_model = model.get('resource_model')
        if not isinstance(resource_model, dict) or resource_model.get('id') != legacy_package:
            raise ValueError('Resource and runtime identity differs: ' + name)
        resource_view(model)
        for component in model['components']:
            for source in component['sources']:
                if not adaptations.local(root, source).is_file():
                    raise ValueError('Missing component source: ' + source)
        record = model.get('device_record')
        required = {'schema_version', 'repository', 'package_status', 'notes', 'compatibility_records', 'known_issues'}
        if not isinstance(record, dict) or not required <= set(record) or set(record) & {'id', 'name', 'hardware_matching'}:
            raise ValueError('Invalid unique device metadata: ' + name)
        destination = model.get('device_record_path')
        if not isinstance(destination, str) or not re.fullmatch(r'devices/[a-z0-9][a-z0-9-]*\.json', destination):
            raise ValueError('Invalid device projection path: ' + name)
        if not aliases['package'] and destination != 'devices/' + parts[1] + '-' + parts[2] + '.json':
            raise ValueError('New model projection path must include its brand: ' + name)
        if destination.casefold() in paths:
            raise ValueError('Conflicting device projection path: ' + destination)
        paths.add(destination.casefold())
        adaptations.local(root, destination)
        # Preserve the old protocol schema and content, including optional fields.
        device = dict(schema_version=record['schema_version'], id=legacy_package, name=rt['name'])
        device.update((k, v) for k, v in record.items() if k != 'schema_version')
        device['hardware_matching'] = rt['hardware_matching']
        outputs[destination] = json_bytes(device)
        manifest = str(Path(name).with_name('manifest.json')).replace('\\', '/')
        if model.get('guide') != '/'.join(parts[:3]) + '/model.md':
            raise ValueError('Guide path differs from model: ' + name)
        guide_keys = ('schema_version', 'kind', 'id', 'name', 'installable', 'guide', 'source_inventory', 'source_revision', 'components')
        guide = {k: model[k] for k in guide_keys}
        outputs[manifest] = json_bytes(guide)
        guide_path = adaptations.local(root, model['guide'])
        # Preserve prose while rebuilding the established marked sections.
        existing = guide_path.read_text(encoding='utf-8')
        begin, end = adaptations.BEGIN, adaptations.END
        sb, se = adaptations.SOURCE_BEGIN, adaptations.SOURCE_END
        if any(existing.count(x) != 1 for x in (begin, end, sb, se)):
            raise ValueError('Missing guide markers: ' + model['guide'])
        before, rest = existing.split(begin); _, after = rest.split(end)
        value = before + begin + '\n' + adaptations.table(guide) + '\n' + end + after
        revision = guide['source_revision']
        source = ('适配源码来源：`' + revision['repository'] + '`，固定提交 `' + revision['commit']
                  + '`。逐文件原路径、大小和 SHA-256 见 [源码库存](../../../source-lock.json)。')
        before, rest = value.split(sb); _, after = rest.split(se)
        outputs[model['guide']] = (before + sb + '\n' + source + '\n' + se + after).encode('utf-8')
        index.append(dict(id=guide['id'], name=guide['name'], source=name, manifest=manifest,
                          source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          components=len(guide['components']), complete=model['build']['complete'],
                          missing=model['build']['missing']))
        runtime.append(dict(rt, guide_id=guide['id'], manifest=manifest))
        concise.append(dict(id=canonical, source=name))
    outputs['models.toon'] = (common.toon_format.dumps(dict(schema_version=2, kind='BoilerModelSourceIndex', generated=True, models=concise)) + '\n').encode('utf-8')
    outputs['model-index.json'] = json_bytes(dict(schema_version=1, kind='BoilerModelSourceIndex', generated=True, models=index))
    outputs['adaptations.json'] = json_bytes(dict(schema_version=1, kind='AdaptationSourceCatalog', version='0.1.0-layout.1', installable=False, documentation='ADAPTATIONS.md', models=[r['manifest'] for r in index], runtime_models=runtime))
    for name, raw in outputs.items():
        path = adaptations.local(root, name)
        if check:
            if not path.is_file() or path.read_bytes() != raw:
                raise ValueError('Generated view differs: ' + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    return len(sources)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('write', 'check'))
    args = parser.parse_args()
    print('Models:', generate(check=args.action == 'check'))
