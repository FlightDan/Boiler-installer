"""Candidate fixed public CPU live metadata; not activation authority."""
from protocol.cpu_contract import IDENTITY, COMMIT, CONFIG, POLICY, KERNEL, TARGETS

KIND = 'intel-g3e-public-cpu-live-v1'
HARDWARE = {'intel_pstate': [True], 'cpu_core_groups': [[2, 8, 4]],
            'steamos_build': ['20260924.1000']}


def validate_contract(resource):
    def need(condition, message):
        if not condition:
            raise ValueError('Invalid fixed public CPU live contract: ' + message)
    need(isinstance(resource, dict), 'resource')
    install = resource.get('install')
    need(resource.get('id') == IDENTITY, 'identity')
    need(isinstance(install, dict) and install.get('kind') == KIND and
         install.get('config_policy') == POLICY, 'handler')
    need(resource.get('verify') == {'kind': 'cpu-live-state-v1'} and
         resource.get('restore') == {'kind': 'cpu-live-transaction-v1'}, 'lifecycle')
    need(resource.get('activation') == {'kind': 'fixed-public-cpu-provider-v1',
         'phase': 'first-boot', 'autoload': True} and
         resource['activation']['autoload'] is True, 'activation')
    need(resource.get('hardware_requires') == HARDWARE and
         resource['hardware_requires']['intel_pstate'][0] is True and
         all(type(x) is int for x in resource['hardware_requires']['cpu_core_groups'][0]), 'live hardware facts')
    need(resource.get('compatibility') == dict(architectures=['x86_64'], os_ids=['steamos'],
         os_versions=['3.10'], kernels=[KERNEL]), 'compatibility')
    need(resource.get('dependencies') == ['resource:common-intel-rapl'] and
         resource.get('external_component_dependencies') == ['decky'], 'dependencies')
    need(not resource.get('hooks') and not resource.get('dynamic_handler'), 'executable extensions')
    rows = install.get('files')
    payload = resource.get('payload')
    need(isinstance(rows, list) and len(rows) == 8 and isinstance(payload, dict), 'files')
    files = payload.get('files')
    need(isinstance(files, list) and len(files) == 8 and all(isinstance(r, dict) and isinstance(r.get('path'), str)
         for r in files), 'payload inventory')
    inventory = {r['path']: r for r in files}
    need(len(inventory) == len(files), 'duplicate payload path')
    need(all(isinstance(r, dict) and isinstance(r.get('target'), str) for r in rows), 'file rows')
    need(len({r['target'] for r in rows}) == 8 and
         {r['target'] for r in rows} == set(TARGETS), 'exact eight targets')
    for row in rows:
        source = row.get('source')
        need(isinstance(source, str) and source in inventory and
             inventory[source].get('sha256') == TARGETS[row['target']], 'public payload digest')
        need(type(row.get('mode')) is int and row['mode'] ==
             (0o600 if row['target'] == CONFIG else 0o644) and
             row.get('scope') == 'root-slots', 'mode/scope')
    return resource



