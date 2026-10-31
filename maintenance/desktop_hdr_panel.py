"""Fixed profile-neutral desktop HDR panel identity; no DMI inference."""
import copy
import contextlib
import hashlib
import json
import re
import tempfile
from pathlib import Path
from .hdr_live_panel import decode_edid

IDENTITY = 'resource:gamma22-hdr-desktop'
KIND = 'BoilerDesktopHdrPanelBindingV1'
PROFILES = {'oxp3': (472, 1100, 'oxp3-exact-edid-v2'),
            'x2-mini': (475, 700, 'x2-edid-native-v2')}

def probe_internal_edp(drm_root=Path('/sys/class/drm')):
    candidates = []
    for path in sorted(Path(drm_root).iterdir()):
        match = re.fullmatch(r'card[0-9]+-(eDP-[0-9]+)', path.name)
        if not match or (path / 'status').read_text().strip() != 'connected':
            continue
        with (path / 'edid').open('rb') as stream:
            raw = stream.read(4097)
        panel = decode_edid(raw)
        candidates.append(dict(sysfs_connector=path.name, connector=match.group(1),
                               edid=raw.hex(), edid_sha256=hashlib.sha256(raw).hexdigest(), panel=panel))
    if len(candidates) != 1:
        raise ValueError('Exactly one connected internal eDP panel is required')
    return candidates[0]

def make_binding(profile, observation, *, generation, account, bundle_sha256):
    if type(profile) is not str or profile not in PROFILES:
        raise ValueError('Unsupported desktop HDR profile')
    full_frame, peak, policy = PROFILES[profile]
    value = dict(kind=KIND, schema_version=1, resource=IDENTITY, profile=profile,
                 full_frame=full_frame, peak=peak, policy=policy, timing='edid-native',
                 generation=generation, account=copy.deepcopy(account), bundle_sha256=bundle_sha256,
                 **copy.deepcopy(observation))
    return validate_binding(value)

def validate_binding(value):
    fields = {'kind','schema_version','resource','profile','full_frame','peak','policy','timing',
              'generation','account','bundle_sha256','sysfs_connector','connector','edid','edid_sha256','panel'}
    if type(value) is not dict or set(value) != fields or value['kind'] != KIND or type(value['schema_version']) is not int or value['schema_version'] != 1 or value['resource'] != IDENTITY:
        raise ValueError('Invalid desktop HDR binding envelope')
    profile = value['profile']
    if type(profile) is not str or profile not in PROFILES or (value['full_frame'], value['peak'], value['policy']) != PROFILES[profile] or type(value['full_frame']) is not int or type(value['peak']) is not int or value['timing'] != 'edid-native':
        raise ValueError('Invalid desktop HDR profile policy')
    if type(value['connector']) is not str or not re.fullmatch(r'eDP-[0-9]+', value['connector']) or type(value['sysfs_connector']) is not str or not re.fullmatch(r'card[0-9]+-' + re.escape(value['connector']), value['sysfs_connector']):
        raise ValueError('Invalid desktop HDR connector')
    if type(value['edid']) is not str or not re.fullmatch(r'[0-9a-f]{256,8192}', value['edid']) or len(value['edid']) % 2:
        raise ValueError('Invalid complete desktop HDR EDID')
    raw = bytes.fromhex(value['edid'])
    panel = value['panel']
    if (type(panel) is not dict or set(panel) != {'vendor','product','model'} or type(panel['vendor']) is not str
            or type(panel['product']) is not int or (panel['model'] is not None and type(panel['model']) is not str)
            or decode_edid(raw) != panel or value['edid_sha256'] != hashlib.sha256(raw).hexdigest()):
        raise ValueError('Desktop HDR EDID identity differs')
    if profile == 'oxp3' and raw[8:12] != bytes.fromhex('4c830143'):
        raise ValueError('OXP3 exact panel policy does not match')
    account = value['account']
    if type(account) is not dict or set(account) != {'name','uid','gid','home'} or account['name'] != 'deck' or account['home'] != '/home/deck' or any(type(account[k]) is not int or not 0 < account[k] < 2**32-1 for k in ('uid','gid')):
        raise ValueError('Unsupported target account layout')
    if type(value['generation']) is not str or not re.fullmatch(r'[0-9a-f]{32}', value['generation']) or type(value['bundle_sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', value['bundle_sha256']):
        raise ValueError('Invalid desktop HDR generation or bundle')
    return copy.deepcopy(value)

def render_runtime_binding(binding):
    return (json.dumps(validate_binding(binding), sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode('ascii')

@contextlib.contextmanager
def bind_live_intent(pool, references, selected, binding):
    """Reconstruct the projection from durable state while retaining payload authority."""
    from protocol.manifest import confined, verify_file
    binding = validate_binding(binding)
    resource = pool.resource(IDENTITY)
    refs = copy.deepcopy(references)
    if IDENTITY not in selected or len([r for r in refs if r['resource'] == IDENTITY]) != 1 or resource.get('hdr_selected_profile') != binding['profile'] or resource.get('desktop_bundle_sha256') != binding['bundle_sha256']:
        raise ValueError('Desktop binding requires its selected fixed profile')
    source = 'payload/desktop/panel-binding.json'
    target = dict(source=source, target='opt/oxp3-desktop-gamma22/6.7.3-1.6/share/boiler-desktop-hdr/panel-binding.json', scope='root-slots', mode=420)
    if resource['install']['files'].count(target) != 1:
        raise ValueError('Desktop binding target differs from fixed contract')
    inventory = resource['payload']['files']
    if len({row['path'] for row in inventory}) != len(inventory) or sum(row['path'] == source for row in inventory) != 1:
        raise ValueError('Invalid desktop payload inventory')
    root = pool.payload_root(IDENTITY)
    with tempfile.TemporaryDirectory(prefix='boiler-desktop-hdr-') as folder:
        temporary = Path(folder)
        new_inventory = []
        for row in inventory:
            path = confined(root, row['path'])
            verify_file(path, row)
            data = render_runtime_binding(binding) if row['path'] == source else path.read_bytes()
            dst = temporary / row['path']
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            new_inventory.append(dict(path=row['path'], size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        resource['payload']['files'] = new_inventory
        resource['desktop_hdr_panel_binding'] = binding
        catalog = copy.deepcopy(pool.catalog)
        rows = catalog['resources']
        if isinstance(rows, dict): rows[IDENTITY] = resource
        else: catalog['resources'] = [resource if r.get('id', r.get('record', {}).get('id')) == IDENTITY else r for r in rows]
        yield pool.derived(catalog, payload_roots={IDENTITY: temporary}), refs
