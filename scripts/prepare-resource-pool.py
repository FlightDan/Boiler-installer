"""Rebuild one shared resource pool from explicit project-local single sources.

No adjacent checkout, old model package/cache, archive or private input directory
is discovered. Export this script, its helpers and the reviewed input whitelist
to rebuild in a new directory with an ordinary Python interpreter.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts')]
from protocol.local import _safe_path
from protocol.resource_pool import decoder, local, validate_pool, json_document
from resources import split_components, assemble_source_pool, build_wifi_pool, merge_firmware_resource_pool, merge_cpu_resource_pool, xevma_proof, hdr_game, merge_hdr_resource_pool, model_source

LOCK = 'adaptations/resource-build-inputs.json'
CODE = ['scripts/prepare-resource-pool.py', 'scripts/rebuild-xevma.py', 'protocol/resource_pool.py', 'protocol/firmware_contract.py',
    *('scripts/resources/'+name for name in ('__init__.py', 'split_components.py', 'hardware_name_patches.py', 'charge_guard.py',
        'model_context.py', 'assemble_source_pool.py', 'build_wifi_pool.py', 'merge_firmware_resource_pool.py', 'xevma_proof.py'))]
CODE += ['scripts/prepare-cpu-resource.py','scripts/resources/merge_cpu_resource_pool.py','protocol/cpu_contract.py',
    'maintenance/cpu_resources.py','maintenance/engine.py','maintenance/resource_runtime.py']
CODE += ['scripts/resources/hdr_game.py','scripts/resources/merge_hdr_resource_pool.py','protocol/hdr_contract.py',
    'maintenance/hdr_resources.py','maintenance/resource_entrypoints.py','maintenance/resource_cli.py',
    'protocol/resource_selection.py','guest/resource_coordinator.py','guest/resource_results.py','scripts/resources/model_source.py']


def sha(raw): return hashlib.sha256(raw).hexdigest()
def json_bytes(value): return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()


def cpu_builder(project):
    path=local(project,'scripts/prepare-cpu-resource.py')
    spec=importlib.util.spec_from_file_location('_project_public_cpu_builder',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def check_inputs(project):
    project = _safe_path(project); raw = local(project, LOCK).read_bytes(); lock = json_document(raw)
    if lock.get('schema_version') != 1 or lock.get('kind') != 'BoilerProjectResourceBuildInputs': raise ValueError('Unsupported project resource input lock')
    if lock.get('public_baseline') != '72f864e0498c10366493c74608c5ffed62cc99ca': raise ValueError('Public research exclusion baseline changed')
    names = set()
    for row in lock['files']:
        name = row['path']
        if name in names: raise ValueError('Duplicate resource build input')
        names.add(name); path = _safe_path(local(project, name)); data = path.read_bytes()
        if len(data) != row['size'] or sha(data) != row['sha256']: raise ValueError('Locked project input differs: '+name)
    for required in (build_wifi_pool.SOURCE, lock['xevma_module'], assemble_source_pool.CURRENT_PROOF,
            xevma_proof.PROOF, xevma_proof.RECIPE, xevma_proof.LOG, xevma_proof.ROOT+'REBUILD.md'):
        if required not in names: raise ValueError('Required single-source input is not locked')
    xevma_proof.verified_rebuild(project)
    cpu=cpu_builder(project);cpu.inputs(project)
    if lock.get('cpu_resource') != cpu.IDENTITY: raise ValueError('Fixed public CPU resource identity changed')
    if not {'adaptations/'+name for name in cpu.SOURCE_NAMES+cpu.AUDIT_NAMES} <= names:
        raise ValueError('Every public CPU source/context member must be explicitly locked')
    hdr_game.inputs(project)
    if lock.get('hdr_resource') != hdr_game.IDENTITY or not set(hdr_game.SOURCE_NAMES.values()) <= names:
        raise ValueError('Fixed shared HDR identity and both public source/license inputs must be locked')
    decoder(project/'adaptations')
    return project, lock, raw


def finalize(pool, project, lock, lock_raw, *, model_source_repository=None):
    catalog = json_document(local(pool, 'catalog.json').read_bytes())
    model_source.apply(pool, project, catalog, source_root=model_source_repository)
    model_origin=None if model_source_repository is None else dict(kind='explicit-adaptations-repository',
        index='repository.toon',index_sha256=sha(local(model_source_repository,'repository.toon').read_bytes()))
    proof = dict(kind='ProjectSourceResourceBuild', official_release=False, project_local_single_inputs=model_source_repository is None,
        model_source=model_origin,fresh_kernel_module_build=False,
        source_lock=LOCK, source_lock_sha256=sha(lock_raw), public_baseline=lock['public_baseline'],
        predecessor_catalog_sha256=lock['predecessor_catalog_sha256'], old_package_cache_or_archive_read=False,
        code=[dict(path=name, size=len(raw), sha256=sha(raw)) for name in CODE for raw in [local(project, name).read_bytes()]],
        source_provenance_complete=not lock['publication_blockers'], independent_xevma_source_rebuild_verified=True,
        xevma_source_rebuild_proof='provenance/xevma-source-rebuild/xevma-rebuild-proof.json', release_ready=False,
        cpu_production_activation_allowed=False,
        hdr_scope='shared-game-files-only',hdr_runtime_accepted=False,desktop_hdr_installable=False,
        publication_blockers=lock['publication_blockers'], unresolved=lock['unresolved'])
    path = local(pool, 'provenance/project-resource-build.json'); path.write_bytes(json_bytes(proof))
    catalog['files'] = [dict(path=p.relative_to(pool).as_posix(), size=p.stat().st_size, sha256=sha(p.read_bytes()))
        for p in sorted(pool.rglob('*')) if p.is_file() and p.relative_to(pool).as_posix() not in ('catalog.toon', 'catalog.json')]
    (pool/'catalog.toon').write_bytes((decoder(project/'adaptations').dumps(catalog)+'\n').encode())
    (pool/'catalog.json').write_bytes(json_bytes(catalog))
    return validate_pool(pool, expected_catalog_sha256=sha((pool/'catalog.toon').read_bytes()))


def build(project, output, *, local_candidate=False, model_source_repository=None):
    project, lock, raw_lock = check_inputs(project); output = _safe_path(output)
    if lock['publication_blockers'] and not local_candidate:
        raise ValueError('Public resource build blocked by unverified source provenance; use --local-candidate only for isolated validation: '+ '; '.join(lock['publication_blockers']))
    if os.path.lexists(output): raise FileExistsError('Use a fresh resource build destination')
    for name in ('adaptations', 'scripts', 'protocol', 'maintenance', 'components', 'app', 'tests'):
        source = project/name
        if output == source or source in output.parents: raise ValueError('Resource output cannot be inside project source directories')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.project-resource-build-', dir=output.parent) as temporary:
        work = Path(temporary)
        component = work/'components'; split_components.build(project, component, xevma_module=local(project, lock['xevma_module']))
        checked = validate_pool(component)
        if {r['id'] for r in checked['resources']} != set(lock['static_resources']): raise ValueError('Independent resource source closure differs from lock')
        base = work/'base'; assemble_source_pool.build(project, component, base)
        wifi = work/'wifi'; build_wifi_pool.build(project, wifi)
        twelve = work/'twelve-resources'; merge_firmware_resource_pool.build(base, wifi, twelve,
            base_sha256=sha((base/'catalog.toon').read_bytes()), wifi_sha256=sha((wifi/'catalog.toon').read_bytes()))
        cpu=work/'cpu';cpu_builder(project).build(project,cpu)
        thirteen=work/'thirteen';merge_cpu_resource_pool.build(twelve,cpu,thirteen,
            base_sha256=sha((twelve/'catalog.toon').read_bytes()),cpu_sha256=sha((cpu/'catalog.toon').read_bytes()))
        hdr=work/'hdr';hdr_game.build(project,hdr)
        final=work/'combined';merge_hdr_resource_pool.build(thirteen,hdr,final,
            base_sha256=sha((thirteen/'catalog.toon').read_bytes()),hdr_sha256=sha((hdr/'catalog.toon').read_bytes()))
        catalog = finalize(final, project, lock, raw_lock, model_source_repository=model_source_repository)
        if ({r['id'] for r in catalog['resources']} != set(lock['static_resources']) | {lock['wifi_resource'],lock['cpu_resource'],lock['hdr_resource']}
): raise ValueError('Final resource identity set differs from lock')
        # Verify source identities again before publishing the new directory.
        check_inputs(project)
        if os.path.lexists(output): raise FileExistsError('Resource destination appeared during build')
        _safe_path(output); os.rename(final, output)
    return dict(kind='ProjectResourceBuildResult', official_release=False, resources=len(catalog['resources']), models=len(catalog['models']),
        inventory_members=len(catalog['files']), catalog_sha256=sha((output/'catalog.toon').read_bytes()),
        project_local_single_inputs=model_source_repository is None, model_source='explicit-adaptations-repository' if model_source_repository is not None else 'project-whitelist',fresh_kernel_module_build=False,
        old_package_cache_or_archive_read=False, source_provenance_complete=not lock['publication_blockers'], publishable=False, release_ready=False,
        cpu_production_activation_allowed=False, publication_blockers=lock['publication_blockers'], unresolved=lock['unresolved'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default=str(ROOT)); parser.add_argument('--output'); parser.add_argument('--check-inputs', action='store_true'); parser.add_argument('--local-candidate', action='store_true')
    parser.add_argument('--model-source-repository', type=Path, help='Explicit independent TOON model source repository; no adjacent checkout discovery')
    args = parser.parse_args()
    if args.check_inputs:
        _, lock, raw = check_inputs(args.project)
        print(json.dumps(dict(ok=True, files=len(lock['files']), lock_sha256=sha(raw), old_package_cache_or_archive_read=False,
            source_provenance_complete=not lock['publication_blockers'], publishable=False, release_ready=False,
            publication_blockers=lock['publication_blockers']), indent=2)); return
    if not args.output: parser.error('--output is required for a build')
    print(json.dumps(build(args.project, args.output, local_candidate=args.local_candidate,
        model_source_repository=args.model_source_repository), indent=2))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error))), file=sys.stderr); raise SystemExit(2)
