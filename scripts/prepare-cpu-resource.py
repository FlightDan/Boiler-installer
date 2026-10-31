"""Build only the immutable public CPU baseline; never execute deployment code."""
import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.cpu_contract import IDENTITY, KIND, COMMIT, CONFIG, POLICY, KERNEL, TARGETS, HARDWARE
from protocol.cpu_live_contract import HARDWARE as LIVE_HARDWARE
from protocol.resource_pool import decoder, local, validate_pool

PREFIX = 'brands/onexplayer/oxp3/scripts/controls/'
SOURCE_NAMES = [PREFIX+'cpu/'+name for name in ('cpu_control.py', 'power_control.py', 'cpu-plugin-main.py', 'cpu-plugin-index.js', 'deploy-cpu.py')]
AUDIT_NAMES = [PREFIX+name for name in ('rapl/provider.py', 'profile.json', 'compatibility.py')]
LOCK_SHA = 'b57fe2a5c69d0d5e41ba5a9710c0310eedc4a8b2d8b5048bbfb3a429e9aea9fa'
LICENSE_SHA = '0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0'


def sha(raw): return hashlib.sha256(raw).hexdigest()
def jb(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def inputs(project, *, source_root=None):
    project = Path(project)
    if source_root is not None:
        root=Path(source_root);toon=decoder(project/'adaptations');raw=local(root,'locks/public-baseline.toon').read_bytes();lock=toon.loads(raw.decode(),strict=True)
        if lock['source'] != dict(kind='github',repository='FlightDan/OneXPlayer3-SteamOS',commit=COMMIT):raise ValueError('Only fixed public CPU baseline is allowed')
        rows={r['original_path']:r for r in lock['files']};sources={};proof=[]
        for name in SOURCE_NAMES+AUDIT_NAMES:
            row=rows[name];data=local(root,row['path']).read_bytes()
            if len(data)!=row['size'] or sha(data)!=row['sha256']:raise ValueError('Changed public CPU source: '+name)
            sources[name]=data;proof.append(dict(row,url='https://github.com/FlightDan/OneXPlayer3-SteamOS/blob/'+COMMIT+'/'+row['upstream_path']))
        license_raw=local(root,'LICENSE').read_bytes()
        if sha(license_raw)!=LICENSE_SHA:raise ValueError('Changed project AGPL license')
        return sources,proof,license_raw
    raw = local(project, 'adaptations/source-lock.json').read_bytes(); lock = json.loads(raw)
    if sha(raw) != LOCK_SHA or lock['source'] != dict(kind='github', repository='FlightDan/OneXPlayer3-SteamOS', commit=COMMIT):
        raise ValueError('Only the fixed public source lock is allowed')
    rows = {r['path']: r for r in lock['files']}; sources = {}; proof = []
    for name in SOURCE_NAMES+AUDIT_NAMES:
        data = local(project, 'adaptations/'+name).read_bytes(); row = rows[name]
        if len(data) != row['size'] or sha(data) != row['sha256']: raise ValueError('Changed immutable public CPU source: '+name)
        sources[name] = data; proof.append(dict(row, url='https://github.com/FlightDan/OneXPlayer3-SteamOS/blob/'+COMMIT+'/'+row['upstream_path']))
    license_raw = local(project, 'adaptations/LICENSE').read_bytes()
    if sha(license_raw) != LICENSE_SHA: raise ValueError('Changed project AGPL license')
    decoder(project/'adaptations')
    return sources, proof, license_raw


def deployment_data(raw):
    tree=ast.parse(raw)
    unit_nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='unit' for t in n.targets)]
    plugin_nodes=[n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='plugin' for t in n.targets)]
    if len(unit_nodes)!=1 or len(plugin_nodes)!=1:raise ValueError('Changed public CPU deployment shape')
    unit=ast.literal_eval(unit_nodes[0].value).encode();plugin=plugin_nodes[0].value
    metadata={}
    for key,value in zip(plugin.keys,plugin.values):
        name=ast.literal_eval(key)
        if name not in ('plugin.json','package.json'):continue
        # Exactly json.dumps(<literal dictionary>).encode(), never eval/exec.
        if (not isinstance(value,ast.Call) or not isinstance(value.func,ast.Attribute) or value.func.attr!='encode' or
            not isinstance(value.func.value,ast.Call) or not isinstance(value.func.value.func,ast.Attribute) or
            not isinstance(value.func.value.func.value,ast.Name) or value.func.value.func.value.id!='json' or value.func.value.func.attr!='dumps'):
            raise ValueError('Changed public CPU metadata generator')
        call=value.func.value
        if len(call.args)!=1 or call.keywords or value.args or value.keywords:raise ValueError('Changed public CPU encoding options')
        metadata[name]=json.dumps(ast.literal_eval(call.args[0])).encode()
    if set(metadata)!= {'plugin.json','package.json'}:raise ValueError('Missing public CPU metadata')
    configs=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Constant) or not isinstance(node.value,str) or "(base/'config.json').write_text" not in node.value:continue
        printed=ast.parse(node.value)
        for part in ast.walk(printed):
            if isinstance(part,ast.Call) and isinstance(part.func,ast.Attribute) and part.func.attr=='write_text':
                if len(part.args)!=1:continue
                call=part.args[0]
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and call.func.value.id=='json' and call.func.attr=='dumps':
                    configs.append(ast.literal_eval(call.args[0]))
    if len(configs)!=1 or configs[0].get('enabled') is not False:raise ValueError('Missing disabled public CPU default')
    return unit,metadata,json.dumps(configs[0]).encode()

def build(project, output, *, source_root=None, source_spec=None, _live=False):
    if type(_live) is not bool or (_live and source_root is None): raise ValueError('Live CPU build requires the fixed independent source')
    project, output = Path(project).absolute(), Path(output).absolute()
    sources, proof, license_raw = inputs(project,source_root=source_root)
    if os.path.lexists(output): raise FileExistsError('Use a fresh CPU resource output')
    for name in ('adaptations', 'protocol', 'maintenance', 'scripts', 'tests'):
        if output == project/name or (project/name) in output.parents: raise ValueError('Output must not cover source inputs')
    unit, metadata, config = deployment_data(sources[PREFIX+'cpu/deploy-cpu.py'])
    if source_root is not None:
        if not isinstance(source_spec,dict) or set(source_spec)!={'schema_version','kind','id','version','public_commit','renderer','deployment','templates'} or type(source_spec['schema_version']) is not int or source_spec['schema_version']!=1 or source_spec['kind']!='BoilerPublicCpuSource' or source_spec['id']!=IDENTITY or source_spec['public_commit']!=COMMIT or source_spec['renderer']!=('fixed-public-cpu-live-v1' if _live else 'fixed-public-cpu-v1') or not isinstance(source_spec['version'],str) or not source_spec['version'] or len(source_spec['templates'])!=4:
            raise ValueError('Invalid public CPU source declaration')
        if source_spec['deployment']!='baseline/'+PREFIX+'cpu/deploy-cpu.py':raise ValueError('Unexpected public CPU deployment source')
        toon=decoder(project/'adaptations');templates=[local(source_root,name).read_bytes() for name in source_spec['templates']]
        authored=[templates[0]]+[json.dumps(toon.loads(value.decode(),strict=True)).encode() for value in templates[1:]]
        if authored != [unit,metadata['plugin.json'],metadata['package.json'],config]:raise ValueError('CPU templates differ from fixed public literals')
        unit,metadata,config=authored[0],dict(zip(('plugin.json','package.json'),authored[1:3])),authored[3]
    data = [sources[PREFIX+'cpu/'+name] for name in ('cpu_control.py', 'power_control.py', 'cpu-plugin-main.py', 'cpu-plugin-index.js')]
    data += [unit, metadata['plugin.json'], metadata['package.json'], config]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cpu-resource-', dir=output.parent) as temporary:
        work = Path(temporary); records = {}; payload = []; files = []
        def write(name, raw):
            path = local(work, name); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw); records[name] = raw
        for index, ((target, expected), raw) in enumerate(zip(TARGETS.items(), data)):
            if sha(raw) != expected: raise ValueError('Generated public CPU bytes differ from fixed contract')
            name = 'devices/CPU/Intel/G3E/files/'+str(index).zfill(3)+'-'+Path(target).name
            write(name, raw); payload.append(dict(path=name, size=len(raw), sha256=sha(raw)))
            files.append(dict(source=name, target=target, mode=0o600 if target == CONFIG else 0o644, scope='root-slots'))
        write('licenses/project-AGPL-3.0', license_raw)
        blockers = ['CPU service activation/stop and live CPU-state recovery have not been accepted for the final installer and maintenance entrances.',
            'The public source lock contains RAPL provider.py but lacks the profile-referenced Manager patches, RAPL service/D-Bus deployment units and their installation/recovery proof.',
            'Real topology, cpufreq/EPP, RAPL/D-Bus/Manager, Decky reload and hardware acceptance remain required.']
        if _live:blockers=[]
        write('provenance/public-cpu-source-lock.json', jb(dict(kind='FixedPublicCpuResourceInputs', source_commit=COMMIT, source_lock_sha256=sha(local(source_root,'locks/public-baseline.toon').read_bytes()) if source_root is not None else LOCK_SHA,
            files=proof, deployment_code_executed=False, private_research=False, activation_blockers=blockers,
            generator_sha256=sha(Path(__file__).read_bytes()), host_code=[dict(path=name,size=len(raw),sha256=sha(raw)) for name in ('protocol/cpu_contract.py','protocol/manifest.py','protocol/resource_pool.py','maintenance/engine.py','maintenance/cpu_resources.py','maintenance/resource_runtime.py') for raw in [local(project,name).read_bytes()]])))
        resource = dict(schema_version=1, kind='BoilerResource', id=IDENTITY, version=source_spec['version'] if source_root is not None else '0.2.0+baseline.72f864.stage1',
            name='Intel G3E public CPU controls' if _live else 'Intel G3E public CPU controls (inactive staging)', label=dict(en='Intel G3E public CPU controls', zh='Intel G3E 公开 CPU 控制') if _live else dict(en='Intel G3E public CPU controls (inactive staging)', zh='Intel G3E 公开 CPU 控制（停用暂存）'),
            payload=dict(files=payload), dependencies=['resource:common-intel-rapl'] if _live else [], external_component_dependencies=['decky'] if _live else ['decky', 'native-tdp'],
            hardware_requires=copy.deepcopy(LIVE_HARDWARE if _live else HARDWARE), compatibility=dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10'], kernels=[KERNEL]),
            install=dict(kind='intel-g3e-public-cpu-live-v1' if _live else KIND, files=files, config_policy=dict(POLICY)), verify=dict(kind='cpu-live-state-v1' if _live else 'cpu-staged-files-v1'), restore=dict(kind='cpu-live-transaction-v1' if _live else 'cpu-staged-restore-v1'),
            activation=dict(kind='fixed-public-cpu-provider-v1',phase='first-boot',autoload=True) if _live else dict(kind='blocked-unverified-service-provider-v1'), license_files=['licenses/project-AGPL-3.0'],
            provenance=dict(public_commit=COMMIT, source_lock='provenance/public-cpu-source-lock.json', deployment_transforms='AST literals only; no exec/eval/deploy script execution'),
            acceptance=dict(public_baseline_only=True,default_policy_enabled=False,activation_implemented=False,software_lifecycle_verified=False,hardware_verified=False) if _live else dict(public_baseline_only=True, default_policy_enabled=False, service_activation_verified=False, hardware_verified=False, production_execution_allowed=False),
            limitations=blockers)
        toon = decoder(project/'adaptations'); name = 'devices/CPU/Intel/G3E/resource.toon'; write(name, (toon.dumps(resource)+'\n').encode())
        model = dict(schema_version=1, kind='BoilerResourceModel', id='device:oxp3', name='ONEXPLAYER 3 (public G3E staging only)',
            hardware_matching=dict(mode='advisory-exact-dmi-v1', pairs=[dict(manufacturer='ONE-NETBOOK', model='ONEXPLAYER 3')]),
            resources=[dict(resource=IDENTITY, enabled=False, default=False, parameters={})], parameters={},
            pending=[dict(component='oxp3-cpu', status='blocked-production-activation', reason=reason) for reason in blockers])
        model_name='brands/onexplayer/models/oxp3.toon'; write(model_name, (toon.dumps(model)+'\n').encode())
        catalog = dict(schema_version=1, kind='BoilerResourceCatalog', trust='unsigned-development', official_release=False,
            resources=[dict(id=IDENTITY, version=resource['version'], path=name, sha256=sha(records[name]))], models=[dict(id=model['id'], path=model_name, sha256=sha(records[model_name]))],
            files=[dict(path=n, size=len(raw), sha256=sha(raw)) for n, raw in sorted(records.items())])
        write('catalog.toon', (toon.dumps(catalog)+'\n').encode()); write('catalog.json', jb(catalog)); validate_pool(work, _cpu_fragment=True) if _live else validate_pool(work)
        inputs(project,source_root=source_root)
        if os.path.lexists(output): raise FileExistsError('CPU resource output appeared during build')
        os.rename(work, output)
    return dict(resources=1, files=8, catalog_sha256=sha((output/'catalog.toon').read_bytes()), public_commit=COMMIT,
        private_research=False, deployment_code_executed=False, activation_blocked=True, production_execution_allowed=False)


def build_live(project, output, *, source_root, source_spec):
    return build(project,output,source_root=source_root,source_spec=source_spec,_live=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--project', default=str(ROOT)); parser.add_argument('--output'); parser.add_argument('--check-inputs', action='store_true'); args = parser.parse_args()
    if args.check_inputs:
        _, rows, _ = inputs(args.project); print(json.dumps(dict(verified=True, public_commit=COMMIT, files=len(rows), private_research=False)))
    else:
        if not args.output: parser.error('--output is required')
        print(json.dumps(build(args.project, args.output), indent=2))

