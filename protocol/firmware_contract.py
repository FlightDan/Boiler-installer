"""Fixed data contract for BE201 firmware; no pool-supplied executable handlers."""
import hashlib
import json

KIND = 'intel-be201-firmware-v1'
IDENTITY = 'resource:intel-be201-firmware'
VERIFY = {'kind': 'firmware-preserved-sha256-v1'}
RESTORE = {'kind': 'transaction-backup-v1'}
TARGETS = {
    **{'usr/lib/firmware/iwlwifi-sc-a0-fm-c0-c'+str(n)+'.ucode': n != 106 for n in (101, 102, 103, 106)},
    **{'usr/lib/firmware/intel/ibt-00a0-0291-'+name: True for name in ('iml.sfi', 'pci.ddc', 'pci.sfi', 'usb.ddc', 'usb.sfi')},
}
HARDWARE = {'wifi_driver': ['iwlwifi'], 'wifi_firmware_prefix': ['iwlwifi-sc-a0-fm-c0'],
            'bluetooth_firmware_prefix': ['ibt-00a0-0291']}


def validate_contract(resource):
    def require(ok, message):
        if not ok: raise ValueError(message)
    require(resource.get('id') == IDENTITY and resource.get('install', {}).get('kind') == KIND,
            'Unsupported fixed firmware identity/handler')
    require(resource.get('verify') == VERIFY and resource.get('restore') == RESTORE, 'Invalid firmware lifecycle')
    require(not resource.get('hooks') and not resource.get('dynamic_handler') and not resource.get('parameters'),
            'Firmware handler does not accept executable hooks or parameters')
    require(resource.get('hardware_requires') == HARDWARE, 'Fixed firmware hardware requirements changed')
    compatibility = resource.get('compatibility', {})
    require(compatibility.get('architectures') == ['x86_64'] and compatibility.get('os_ids') == ['steamos']
            and compatibility.get('os_versions') == ['3.10'], 'Fixed firmware OS compatibility is required')
    kernels = compatibility.get('kernels')
    require(isinstance(kernels, list) and kernels and all(isinstance(k, str) and k for k in kernels)
            and len(set(kernels)) == len(kernels), 'Explicit firmware kernel compatibility is required')
    rows = resource['install'].get('files')
    require(isinstance(rows, list) and len(rows) == len(TARGETS), 'Fixed firmware requires all nine targets')
    require({r.get('target') for r in rows} == set(TARGETS), 'Fixed firmware target set changed')
    for row in rows:
        require(set(row) == {'source', 'target', 'mode', 'scope', 'missing_only'}, 'Unknown firmware policy field')
        require(type(row['missing_only']) is bool and row['missing_only'] == TARGETS[row['target']],
                'Fixed firmware missing-only policy changed')
        require(type(row['mode']) is int and row['mode'] == 420 and row['scope'] == 'root-slots',
                'Fixed firmware requires ordinary root-slot firmware files')


def policy_digest(resource):
    validate_contract(resource)
    return hashlib.sha256(json.dumps(resource, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
