"""Fixed public G3E CPU staging contract; this is not live activation authority."""
IDENTITY = 'resource:intel-g3e-public-cpu-controls'
KIND = 'intel-g3e-public-cpu-staging-v1'
COMMIT = '72f864e0498c10366493c74608c5ffed62cc99ca'
CONFIG = 'var/lib/oxp3-cpu/config.json'
POLICY = {'kind': 'preserve-public-cpu-config-v1', 'target': CONFIG}
KERNEL = '7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
TARGETS = {
    'var/lib/oxp3-cpu/cpu_control.py': '7dabf1846c473fbd3a17f4453e835fd5116d74abe6d0e3aee528bc74dcaa4872',
    'var/lib/oxp3-cpu/power_control.py': 'd5c5efd2f847d443bbff04dd30a3af847576d867eebcfb9b25c94d8e2b31654f',
    'var/lib/boiler/decky/runtime/plugins/OXP3-CPU/main.py': '36e6ceacda689a4856486c5fe2fd541094b4f603b8282b79886ad7519f30736b',
    'var/lib/boiler/decky/runtime/plugins/OXP3-CPU/dist/index.js': 'a1cd67a64fc69b4f279b17ffe8fc5948b37fb7f4a233a10745fa01698492fa44',
    'etc/systemd/system/oxp3-cpu.service': '6ad5885293456a25d30a42c630c4b4f629f8cc02fd111c50bb73e761a29ac2b4',
    'var/lib/boiler/decky/runtime/plugins/OXP3-CPU/plugin.json': 'fc1ea20d9025d7334c2e261e37988d25ab3904059f3722cb181b2e01fcbbc745',
    'var/lib/boiler/decky/runtime/plugins/OXP3-CPU/package.json': '215a62ac7749d053baf5369639b2eeb981d467de96a770fb1a56ecfe22f38d1f',
    CONFIG: '072977979a035f61c8f5ded59d2e382f94c947629c8cadcee3b30b2cbd969dda',
}
HARDWARE = dict(intel_pstate=[True], cpu_core_groups=[[2, 8, 4]], steamos_build=['20260924.1000'],
    cpu_service_state=['inactive'], cpu_service_enablement=['disabled'], cpu_staging_target=['offline-mounted-root'])


def validate_contract(resource):
    def need(condition, message):
        if not condition: raise ValueError('Invalid fixed public CPU contract: '+message)
    need(resource.get('id') == IDENTITY, 'identity')
    need(resource.get('install', {}).get('kind') == KIND and resource['install'].get('config_policy') == POLICY, 'handler')
    need(resource.get('verify') == {'kind': 'cpu-staged-files-v1'} and resource.get('restore') == {'kind': 'cpu-staged-restore-v1'}, 'lifecycle')
    need(resource.get('activation') == {'kind': 'blocked-unverified-service-provider-v1'}, 'activation must remain blocked')
    need(resource.get('hardware_requires') == HARDWARE, 'positive offline topology/service facts')
    need(resource['hardware_requires']['intel_pstate'][0] is True, 'boolean pstate fact')
    need(resource.get('compatibility') == dict(architectures=['x86_64'], os_ids=['steamos'], os_versions=['3.10'], kernels=[KERNEL]), 'compatibility')
    rows = resource['install']['files']; inventory = {r['path']: r for r in resource['payload']['files']}
    need(len(rows) == 8 and {r['target'] for r in rows} == set(TARGETS), 'exact eight target set')
    for row in rows:
        need(row['source'] in inventory and inventory[row['source']]['sha256'] == TARGETS[row['target']], 'immutable public payload')
        need(type(row['mode']) is int and row['mode'] == (0o600 if row['target'] == CONFIG else 0o644) and row['scope'] == 'root-slots', 'mode/scope')
    need(resource.get('dependencies') in ([],['resource:common-intel-rapl']) and not resource.get('hooks') and not resource.get('dynamic_handler'), 'no executable resource extensions')
    return resource


def mutable_target(component):
    value = component.get('cpu_mutable_config')
    if value is None: return None
    if (component.get('id') != IDENTITY or value != POLICY or component.get('hooks')
            or len([r for r in component.get('files', []) if r['target'] == CONFIG and r.get('mode') == 0o600]) != 1):
        raise ValueError('Mutable CPU config policy is restricted to its fixed private target')
    return CONFIG
