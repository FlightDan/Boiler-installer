"""Rebuild game HDR from explicitly pinned project-local public source bytes."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from protocol.hdr_contract import KIND, IDENTITY, PROFILES, LEGACY_PROFILES, FACTS, TARGET, VERSION, profile_bytes
PINS={'upstream.lua':(5836,'a37042dbcbcb91515244bf42ec3f5efe96e04b9e417b6945dd476b31e48ae846'),
      'HDR-MIT.txt':(1070,'1959942e9e263938024cbd4d8c63bba342647877777dd48056c467672dcb915a')}
SOURCE_NAMES={'upstream.lua':'adaptations/brands/onexplayer/oxp3/scripts/hdr/upstream.lua',
              'HDR-MIT.txt':'adaptations/brands/onexplayer/oxp3/scripts/hdr/UPSTREAM_LICENSE'}
def sha(raw): return hashlib.sha256(raw).hexdigest()
def document(path, value): path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')

def inputs(project):
    from protocol.resource_pool import local
    result={}
    for name,(size,digest) in PINS.items():
        raw=local(project,SOURCE_NAMES[name]).read_bytes()
        if len(raw)!=size or sha(raw)!=digest: raise ValueError('Pinned input mismatch: '+name)
        result[name]=raw
    return result

def build(project,output):
    output=Path(output)
    if output.exists(): raise FileExistsError('Fresh output required')
    checked=inputs(project)
    from scripts.resources.model_context import models
    x2 = next(model['parameters']['hdr'] for model, _ in models(project) if model['id'] == 'device:x2-mini')
    mapping = (x2['sdr_white_nits'], x2['peak_nits'])
    if mapping == (472, 1100):
        version, profiles = VERSION, PROFILES
    elif mapping == (475, 700):
        version, profiles = '1.0.0-hdr.local1', LEGACY_PROFILES
    else:
        raise ValueError('Unsupported X2 mapping in the pinned TOON')
    source=checked['upstream.lua'].decode()
    # Shared implementation, profile state supplied by a separately inventoried
    # model parameter file. Startup works in either Lua-script loading order.
    source=source.replace('max_content_light_level = 1100','max_content_light_level = profile.peak')
    source=source.replace('max_frame_average_luminance = 472','max_frame_average_luminance = profile.full_frame')
    source=source.replace('gamescope.config.known_displays.oxp3_sdc_oled','gamescope.config.known_displays.boiler_gamma22')
    source=source.replace('pretty_name = "ONEXPLAYER 3 SDC AMS881KB01-0 OLED"','pretty_name = "Boiler Gamma 2.2 ("..profile.id..")"')
    source=source.replace('dynamic_refresh_rates = oxp3_oled_refresh_rates', 'dynamic_refresh_rates = profile.timing == "oxp3-public" and oxp3_oled_refresh_rates or nil')
    source=source.replace('colorimetry = oxp3_oled_colorimetry','colorimetry = profile.timing == "oxp3-public" and oxp3_oled_colorimetry or nil')
    source=source.replace('local vfps = {','if profile.timing ~= "oxp3-public" then return base_mode end\n        local vfps = {')
    source=source.replace('display.vendor == "SDC" and display.model ~= nil and string.match(display.model, "^%s*(.-)%s*$") == "AMS881KB01-0"',
        'profile.model ~= nil and profile.vendor ~= nil and display.vendor == profile.vendor and display.model ~= nil and string.match(display.model, "^%s*(.-)%s*$") == profile.model')
    # Preserve upstream historical comments but don't emit its hardcoded claimed
    # mapping as the runtime log for a different parameter profile.
    source=source.replace('"oxp3 gamma22 daily1100: maxCLL=1100 maxFALL=472; composite_force="', '"boiler gamma22: profile="..profile.id.." peak="..profile.peak.." full="..profile.full_frame.."; composite_force="')
    source=source.replace('debug("oxp3: registered OXP3 SDC OLED with gamma22 HDR + 30-144Hz dynamic refresh (Deck-OLED style)")',
        'debug("boiler: registered fixed gamma22 profile "..profile.id.."; panel matching remains mandatory")')
    common=('-- SPDX-License-Identifier: MIT\n-- Derived from pinned public OXP3 source; see provenance.json.\n'
            'boiler_hdr_register = function(profile)\n'+source+'\nend\n'
            'if boiler_hdr_profile ~= nil then boiler_hdr_register(boiler_hdr_profile) end\n').encode()
    files={'payload/game/common.lua':common,'licenses/HDR-MIT.txt':checked['HDR-MIT.txt']}
    for key in profiles:files['payload/game/'+key+'.lua']=profile_bytes(key, profiles)
    inventory=[dict(path=n,size=len(raw),sha256=sha(raw)) for n,raw in sorted(files.items())]
    resource=dict(schema_version=1,kind='BoilerResource',id=IDENTITY,version=version,
        install=dict(kind=KIND,files=[dict(source='payload/game/common.lua',target=TARGET+'98-boiler-gamma22.lua',scope='shared-home',mode=420)]),
        verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'},profiles=profiles,
        payload={'files':[r for r in inventory if r['path'].startswith('payload/')]},license_files=['licenses/HDR-MIT.txt'],
        compatibility=dict(architectures=['x86_64'],os_ids=['steamos'],os_versions=['3.10'],
            kernels=['7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9']),hardware_requires=FACTS,dependencies=[])
    output.mkdir(parents=True)
    for name,raw in files.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    (output/'resources').mkdir();(output/'provenance').mkdir()
    document(output/'resources/gamma22-hdr-game.json',resource)
    document(output/'provenance/hdr-game-source-build.json',dict(kind='PinnedHdrSourceBuild',official_release=False,
        source=dict(repository='https://github.com/FlightDan/OneXPlayer3-SteamOS',commit='72f864e0498c10366493c74608c5ffed62cc99ca',upstream_path='windows-installer/components/hdr/upstream.lua',project_local_path='adaptations/brands/onexplayer/oxp3/scripts/hdr/upstream.lua',license='MIT',license_upstream_path='windows-installer/components/hdr/UPSTREAM_LICENSE'),
        inputs=[dict(path=n,size=s,sha256=h) for n,(s,h) in PINS.items()],files=inventory,
        hardware_accepted=False,x2_panel_model_known=False,production_authenticated=False))
    from protocol.resource_pool import decoder,validate_pool
    toon=decoder()
    def encoded(path,value):path.write_text(toon.dumps(value)+'\n',encoding='utf-8')
    encoded(output/'resources/gamma22-hdr-game.toon',resource)
    models=[]
    for key in profiles:
        model=dict(schema_version=1,kind='BoilerResourceModel',id='device:'+key,name=key,resources=[dict(resource=IDENTITY,default=False,enabled=False,parameters={'profile':key})],complete=False,
            pending=[dict(component='desktop-hdr',reason='Native rebuild/ABI and runtime evidence required')])
        name='models/hdr-'+key+'.toon';path=output/name;path.parent.mkdir(exist_ok=True);encoded(path,model)
        models.append(dict(id=model['id'],path=name,sha256=sha(path.read_bytes())))
    inventory=[dict(path=p.relative_to(output).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(output.rglob('*')) if p.is_file()]
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,
        resources=[dict(id=IDENTITY,version=resource['version'],path='resources/gamma22-hdr-game.toon',sha256=sha((output/'resources/gamma22-hdr-game.toon').read_bytes()))],models=models,files=inventory)
    encoded(output/'catalog.toon',catalog);document(output/'catalog.json',catalog);validate_pool(output)
    return resource
if __name__=='__main__': build(ROOT,sys.argv[1])
