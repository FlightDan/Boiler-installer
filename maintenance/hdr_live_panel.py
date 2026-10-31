"""Fixed X2 mapping bound to a real internal eDP EDID, never model-name facts.

The caller owns pool authentication, target mounts/lease and resource transactions.
This module cannot grant any of those authorities. Optical brightness is unmeasured.
"""
import contextlib
import copy
import hashlib
import json
import re
import tempfile
from pathlib import Path

IDENTITY = 'resource:gamma22-hdr-game'
KIND = 'BoilerInternalEdpHdrBindingV1'
COMMON_SHA256 = 'a2a2ba7728de035e5f067c622ab0a409617e2e0cf8b7ea95b57ce106315924ed'
TARGET = 'home/deck/.config/gamescope/scripts/'


def decode_edid(raw):
    if (type(raw) is not bytes or not 128 <= len(raw) <= 4096 or len(raw) % 128
            or raw[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00'
            or (raw[126] + 1) * 128 != len(raw)
            or any(sum(raw[i:i+128]) % 256 for i in range(0, len(raw), 128))):
        raise ValueError('Invalid complete internal panel EDID')
    code = int.from_bytes(raw[8:10], 'big')
    chars = [(code >> shift) & 31 for shift in (10, 5, 0)]
    if code & 0x8000 or any(not 1 <= c <= 26 for c in chars):
        raise ValueError('Invalid EDID PNP manufacturer')
    model = None
    for start in range(54, 126, 18):
        block = raw[start:start+18]
        if block[:5] == b'\x00\x00\x00\xfc\x00':
            name = block[5:18].split(b'\n', 1)[0].rstrip(b' \x00')
            if not name or any(c < 32 or c > 126 for c in name) or model is not None:
                raise ValueError('Invalid or ambiguous EDID display name')
            model = name.decode('ascii')
    return dict(vendor=''.join(chr(64+c) for c in chars),
                product=int.from_bytes(raw[10:12], 'little'), model=model)


def probe_internal_edp(drm_root=Path('/sys/class/drm')):
    """Only a unique connected kernel eDP connector supplies the panel identity."""
    candidates = []
    for path in sorted(Path(drm_root).iterdir()):
        if not re.fullmatch(r'card[0-9]+-eDP-[0-9]+', path.name):
            continue
        if (path/'status').read_text().strip() != 'connected':
            continue
        with (path/'edid').open('rb') as stream:
            raw = stream.read(4097)
        panel = decode_edid(raw)
        candidates.append(dict(kind=KIND, profile='x2-mini', connector=path.name,
            edid=raw.hex(), edid_sha256=hashlib.sha256(raw).hexdigest(),
            panel=panel, full_frame=475, peak=700, timing='edid-native'))
    if len(candidates) != 1:
        raise ValueError('Exactly one connected internal eDP panel is required')
    return validate_binding(candidates[0])


def validate_binding(binding):
    if (type(binding) is not dict or set(binding) != {'kind','profile','connector','edid',
            'edid_sha256','panel','full_frame','peak','timing'}
            or binding['kind'] != KIND or binding['profile'] != 'x2-mini'
            or binding['full_frame'] != 475 or type(binding['full_frame']) is not int
            or binding['peak'] != 700 or type(binding['peak']) is not int
            or binding['timing'] != 'edid-native'
            or type(binding['connector']) is not str
            or not re.fullmatch(r'card[0-9]+-eDP-[0-9]+', binding['connector'])
            or type(binding['edid']) is not str or len(binding['edid']) > 8192
            or not re.fullmatch('[0-9a-f]+', binding['edid'])):
        raise ValueError('Unsupported fixed internal panel HDR binding')
    raw = bytes.fromhex(binding['edid'])
    panel = binding['panel']
    if (type(panel) is not dict or set(panel) != {'vendor','product','model'}
            or type(panel['vendor']) is not str or not re.fullmatch('[A-Z]{3}',panel['vendor'])
            or type(panel['product']) is not int or not 0 <= panel['product'] <= 65535
            or (panel['model'] is not None and (type(panel['model']) is not str
                or not 1 <= len(panel['model']) <= 13 or any(not 32 <= ord(c) <= 126 for c in panel['model'])))
            or panel != decode_edid(raw)
            or binding['edid_sha256'] != hashlib.sha256(raw).hexdigest()):
        raise ValueError('Panel binding differs from recorded EDID bytes')
    return copy.deepcopy(binding)


def render_profile(binding):
    binding = validate_binding(binding)
    fields = dict(id='x2-mini', full_frame=475, peak=700,
                  **binding['panel'], timing='edid-native')
    lua = lambda v: 'nil' if v is None else json.dumps(v, ensure_ascii=True)
    return ('-- Fixed mapping bound to the recorded internal eDP EDID; not optical measurements.\n'
        'boiler_hdr_profile = {' + ','.join(k+'='+lua(v) for k,v in fields.items()) +
        '}\nif boiler_hdr_register ~= nil then boiler_hdr_register(boiler_hdr_profile) end\n').encode()


def render_common(raw):
    # Keep the reviewed public OXP3 implementation and its timing branch exact.
    if len(raw) != 6340 or hashlib.sha256(raw).hexdigest() != COMMON_SHA256:
        raise ValueError('Reviewed shared HDR implementation differs')
    old = ('if profile.model ~= nil and profile.vendor ~= nil and display.vendor == profile.vendor '
           'and display.model ~= nil and string.match(display.model, "^%s*(.-)%s*$") == profile.model then')
    new = ('if profile.vendor ~= nil and display.vendor == profile.vendor and '
           '((profile.timing == "edid-native" and profile.product ~= nil and display.product == profile.product '
           'and (profile.model == nil or (display.model ~= nil and string.match(display.model, "^%s*(.-)%s*$") == profile.model))) '
           'or (profile.timing == "oxp3-public" and profile.model ~= nil and display.model ~= nil '
           'and string.match(display.model, "^%s*(.-)%s*$") == profile.model)) then')
    text = raw.decode('utf-8')
    if text.count(old) != 1:
        raise ValueError('Fixed HDR matcher anchor differs')
    return text.replace(old, new).encode()


@contextlib.contextmanager
def bind_live_intent(pool, references, selected, binding):
    """Reconstruct only from durable binding and authenticated source, not sysfs.

    Must remain open for the entire ResourceSession apply/verify/restore lifetime.
    ResourcePool.derived routes only this identity to our fixed generated payload.
    """
    binding = validate_binding(binding)
    refs = copy.deepcopy(references)
    matching = [r for r in refs if r['resource'] == IDENTITY]
    resource = pool.resource(IDENTITY)
    if (IDENTITY not in selected or len(matching) != 1
            or resource.get('hdr_selected_profile') != 'x2-mini'):
        raise ValueError('Durable panel binding requires the selected X2 HDR profile')
    if resource['install']['files'] != [
            dict(source='payload/game/common.lua', target=TARGET+'98-boiler-gamma22.lua', scope='shared-home', mode=420),
            dict(source='payload/game/x2-mini.lua', target=TARGET+'00-boiler-hdr-profile.lua', scope='shared-home', mode=420)]:
        raise ValueError('HDR generated target contract differs')
    from protocol.manifest import confined, verify_file
    inventory = {r['path']:r for r in resource['payload']['files']}
    root = pool.payload_root(IDENTITY)
    source = confined(root, 'payload/game/common.lua')
    verify_file(source, inventory['payload/game/common.lua'])
    files = {'payload/game/common.lua':render_common(source.read_bytes()),
             'payload/game/x2-mini.lua':render_profile(binding)}
    with tempfile.TemporaryDirectory(prefix='boiler-hdr-panel-') as temporary:
        temporary = Path(temporary)
        for name, data in files.items():
            path = temporary/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        resource['payload']['files'] = [dict(path=n, size=len(b), sha256=hashlib.sha256(b).hexdigest()) for n,b in files.items()]
        resource['hardware_requires'].update(gamescope_display_vendor=[binding['panel']['vendor']],
            gamescope_display_model=[binding['panel']['model']], hdr_panel_policy=['edid-native'])
        # Missing display-name descriptors are legitimate; vendor+product remain mandatory.
        if binding['panel']['model'] is None:
            resource['hardware_requires'].pop('gamescope_display_model', None)
        resource['hardware_requires']['gamescope_display_product'] = [binding['panel']['product']]
        resource['hdr_panel_binding'] = binding
        catalog = copy.deepcopy(pool.catalog)
        rows = catalog['resources']
        if isinstance(rows, dict): rows[IDENTITY] = resource
        else: catalog['resources'] = [resource if r.get('id',r.get('record',{}).get('id')) == IDENTITY else r for r in rows]
        yield pool.derived(catalog, payload_roots={IDENTITY:temporary}), refs


def bind_firstboot(pool, references, selected, *, slots, shared_home, group, record_binding):
    """Install the fixed generated HDR generation under the caller's live mounts.

    Caller journals this return value into BOTH slot bootstrap intents while
    holding the same target/write scope; a later call must reuse that binding.
    """
    if (IDENTITY not in selected or set(slots) != {'A', 'B'} or not callable(record_binding)
            or type(group) is not str or not re.fullmatch('[0-9a-f]{32}',group)):
        raise ValueError('First-boot panel binding requires both owned slots and selected HDR')
    binding = probe_internal_edp()
    from maintenance.resource_runtime import ResourceSession
    from maintenance.hdr_resources import check_legacy
    check_legacy(shared_home, selected)
    live_slots = copy.deepcopy(slots)
    for slot in live_slots.values():
        slot['hardware'].update(gamescope_display_vendor=binding['panel']['vendor'],
            gamescope_display_product=binding['panel']['product'],
            gamescope_display_model=binding['panel']['model'], hdr_panel_policy='edid-native')
    with bind_live_intent(pool, references, selected, binding) as (effective, refs):
        # Preserve reconstruction after source validation but before the first
        # resource mutation. The fixed production caller must durably journal
        # this into its two-slot workflow; an in-memory return is insufficient.
        record_binding(copy.deepcopy(binding))
        receipt = ResourceSession(effective, live_slots, shared_home).install(refs, [IDENTITY], group=group)
    return dict(binding=binding, receipt=receipt)
