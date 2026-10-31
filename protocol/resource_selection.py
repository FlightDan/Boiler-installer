"""Local-test resource selection boundary. This module never authorizes deployment."""
from __future__ import annotations

from pathlib import Path
import re

from .resource_pool import validate_pool, local, sha, require

ROOT = Path(__file__).resolve().parents[1]

def installer_dependencies(resource):
    """Use the same fixed core option identity as the Windows selection UI."""
    return {'core:decky' if value=='decky' else value
            for value in resource.get('external_component_dependencies',[])}


def _pool(root):
    pool=Path(root)/'cache/integration/resource-pool'
    # An explicitly supplied alternate project root is a development fixture,
    # never the packaged runtime's implicit search path.
    if not pool.exists() and Path(root).resolve()!=ROOT:pool=Path(root)/'cache/resource-pool'
    data = local(pool, 'catalog.toon').read_bytes()
    return pool, validate_pool(pool, expected_catalog_sha256=sha(data)), sha(data)


def _choices(pool, model):
    resources = {row['id']: row for row in pool['resources']}
    refs = model['resources']
    require(len({row['resource'] for row in refs}) == len(refs), 'Duplicate model resource')
    authorized = {row['resource'] for row in refs}
    choices = []
    for ref in refs:
        resource = resources[ref['resource']]
        parameters = ref.get('parameters')
        selectable = (parameters in (None, {}) and resource['install']['files']
                      and not resource.get('parameters')
                      and set(resource['dependencies']) <= authorized)
        activation_blocked = resource.get('activation',{}).get('kind')=='blocked-unverified-service-provider-v1'
        if activation_blocked: selectable=False
        if resource['install']['kind']=='intel-g3e-public-cpu-live-v1':
            from .cpu_live_contract import validate_contract
            validate_contract(resource)
            selectable=parameters in (None,{}) and bool(resource['install']['files']) and set(resource['dependencies'])<=authorized
        if resource.get('ec_profile_contract') is not None:
            from .ec_profile_contract import validate_contract
            validate_contract(resource)
            selectable=parameters in (None,{}) and bool(resource['install']['files']) and set(resource['dependencies'])<=authorized
        if resource['install']['kind']=='boiler-input-runtime-v1':
            from .input_live_contract import validate_contract
            validate_contract(resource)
            selectable=parameters in (None,{}) and bool(resource['install']['files']) and set(resource['dependencies'])<=authorized
        if resource['install']['kind']=='boiler-kde-keyboard-v1':
            from .keyboard_resource_contract import validate_contract
            validate_contract(resource)
            selectable=parameters in (None,{}) and set(resource['dependencies'])<=authorized
        if resource['install']['kind']=='boiler-boot-settings-v1':
            from maintenance.boot_resources import validate_contract
            validate_contract(resource)
            selectable=parameters in (None,{}) and set(resource['dependencies'])<=authorized
        parameter_data=None
        if resource['install']['kind']=='gamma22-hdr-game-v1':
            from .hdr_contract import render,PROFILES
            rendered=render(resource,parameters);key=rendered['hdr_selected_profile'];profile=PROFILES[key]
            selectable=bool(rendered['install']['files']) and set(resource['dependencies'])<=authorized
            parameter_data=dict(profile=key,full_frame=profile['full_frame'],peak=profile['peak'],panel_model_known=profile['model'] is not None)
        if resource['install']['kind']=='gamma22-hdr-desktop-v1':
            from .desktop_hdr_contract import render,PROFILES
            rendered=render(resource,parameters);key=rendered['hdr_selected_profile'];profile=PROFILES[key]
            selectable=bool(rendered['install']['files']) and set(resource['dependencies'])<=authorized
            parameter_data=dict(profile=key,full_frame=profile['full_frame'],peak=profile['peak'],panel_model_known=False)
        choices.append(dict(id=resource['id'], version=resource['version'],
            selectable=bool(selectable), default=ref.get('default',False), optional=ref.get('optional',True), dependencies=resource['dependencies'],
            external_component_dependencies=resource.get('external_component_dependencies',[]),
            scope=['root-slots'] if resource['install']['kind']=='boiler-boot-settings-v1' else ['shared-home'] if resource['install']['kind']=='boiler-kde-keyboard-v1' else sorted({row['scope'] for row in resource['install']['files']}),
            reason='Local-test complete file deployment; fixed first-boot runtime checks actual interfaces' if selectable
                else 'Activation lifecycle is blocked' if activation_blocked else 'Parameters, dependencies or installation lifecycle are not implemented',
            limitations=resource.get('limitations', [])))
        if parameter_data is not None:
            choices[-1]['parameter_data']=parameter_data
            choices[-1]['reason']='Fixed shared HDR; software mapping only. First boot binds the actual panel and checks the required display interfaces.'
        if resource['install']['kind']=='boiler-kde-keyboard-v1':
            choices[-1]['reason']='Fixed shared keyboard transaction; desktop entry is checked before installation and session behavior requires acceptance.'
        if resource['install']['kind']=='boiler-flathub-mirror-v1':
            choices[-1]['reason']='Configures the selected Flathub remote with its existing signing key; network access is verified separately.'
    # Propagate unavailable dependencies; cycles are rejected at selection time.
    changed = True
    while changed:
        changed = False
        available = {row['id'] for row in choices if row['selectable']}
        for row in choices:
            if row['selectable'] and not set(row['dependencies']) <= available:
                row['selectable'] = False
                row['reason'] = 'A required resource is unavailable'
                changed = True
    for row in choices:
        if not row['selectable']:
            row['default'] = False
    return choices


def resource_catalog(root=ROOT):
    """Validated display data, with no executable or production-trust capability."""
    _, pool, digest = _pool(root)
    models = []
    for model in pool['models']:
        models.append(dict(id=model['id'], guide_id='guide:' + model['id'].split(':', 1)[1],
            name=model['name'], hardware_matching=model['hardware_matching'],
            components=_choices(pool, model), pending=model.get('pending', [])))
    return dict(schema_version=1, kind='LocalResourceSelectionCatalog', catalog_sha256=digest,
        trust='untrusted-local-candidate', deployment_available=True, models=models)


def select_resources(model_id, components, catalog_sha256, *, trust_local_test=False, root=ROOT):
    require(trust_local_test is True, 'Explicit local-test session consent is required')
    require(isinstance(catalog_sha256, str) and re.fullmatch('[0-9a-f]{64}', catalog_sha256), 'Invalid catalog digest')
    catalog = resource_catalog(root)
    require(catalog_sha256 == catalog['catalog_sha256'], 'Resource catalog changed; reload and review choices')
    require(isinstance(components, list) and 0 < len(components) <= 128
            and all(isinstance(value, str) for value in components)
            and len(set(components)) == len(components), 'Invalid explicit resource choices')
    model = next((row for row in catalog['models'] if row['id'] == model_id), None)
    require(model is not None, 'Unknown resource model')
    choices = {row['id']: row for row in model['components']}
    visited, visiting, ordered = set(), set(), []
    def visit(identity):
        require(identity in choices and choices[identity]['selectable'], 'Resource is not selectable for this model')
        require(identity not in visiting, 'Cyclic resource dependency')
        if identity in visited: return
        visiting.add(identity)
        for dependency in choices[identity]['dependencies']: visit(dependency)
        visiting.remove(identity); visited.add(identity); ordered.append(identity)
    for identity in components: visit(identity)
    return dict(schema_version=1, model_id=model_id, catalog_sha256=catalog_sha256,
                components=ordered, trust='local-test')
