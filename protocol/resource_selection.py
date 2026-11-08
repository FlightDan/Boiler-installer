"""Local-test resource selection boundary. This module never authorizes deployment."""
from __future__ import annotations

from pathlib import Path
import re

from .resource_pool import validate_pool, local, sha, require

ROOT = Path(__file__).resolve().parents[1]


def _pool(root):
    pool = Path(root) / 'cache/resource-pool'
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
        parameter_data=None
        if resource['install']['kind']=='gamma22-hdr-game-v1':
            from .hdr_contract import render
            rendered=render(resource,parameters);key=rendered['hdr_selected_profile'];profile=resource['profiles'][key]
            selectable=bool(rendered['install']['files']) and set(resource['dependencies'])<=authorized
            parameter_data=dict(profile=key,full_frame=profile['full_frame'],peak=profile['peak'],panel_model_known=profile['model'] is not None)
        choices.append(dict(id=resource['id'], version=resource['version'],
            selectable=bool(selectable), default=False, dependencies=resource['dependencies'],
            scope=sorted({row['scope'] for row in resource['install']['files']}),
            reason='Local-test file resource; guest installation is not connected' if selectable
                else 'Parameters, dependencies or installation lifecycle are not implemented',
            limitations=resource.get('limitations', [])))
        if parameter_data is not None:
            choices[-1]['parameter_data']=parameter_data
            choices[-1]['reason']='Fixed shared game HDR; software mapping only. Apply requires trusted panel/software facts; desktop native HDR remains blocked.'
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
        trust='untrusted-local-candidate', deployment_available=False, models=models)


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
