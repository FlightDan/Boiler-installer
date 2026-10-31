"""Read-only fixed public G3E cpufreq probe, no resource-supplied paths."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from maintenance.adoption_blobs import _file
from protocol.cpu_contract import CONFIG, KERNEL

class CpuProbeError(RuntimeError): pass
EPPS = ('performance','balance_performance','balance_power','power')


def _text(path):
    try:
        value=path.read_text(encoding='ascii').strip()
    except (OSError,UnicodeError) as exc:
        raise CpuProbeError('Cannot read CPU interface: '+str(path)) from exc
    if not value or len(value)>512: raise CpuProbeError('Invalid CPU interface: '+str(path))
    return value


def _positive(path):
    value=_text(path)
    if not re.fullmatch('[0-9]+',value) or int(value)<=0: raise CpuProbeError('Invalid CPU frequency: '+str(path))
    return int(value)


def _configuration(root):
    try:
        with _file(Path(root)/CONFIG,private=True) as (stream,info):
            if stat.S_IMODE(info.st_mode)!=0o600 or info.st_gid!=0 or info.st_size>16384:
                raise CpuProbeError('Unsafe CPU private configuration')
            def pairs(items):
                result={}
                for k,v in items:
                    if k in result: raise CpuProbeError('Duplicate CPU configuration key')
                    result[k]=v
                return result
            raw=stream.read(16385)
            value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs)
    except (OSError,UnicodeError,ValueError) as exc:
        raise CpuProbeError('Missing or invalid CPU private configuration') from exc
    if not isinstance(value,dict) or type(value.get('enabled')) is not bool:
        raise CpuProbeError('Invalid CPU enabled value')
    return value, hashlib.sha256(raw).hexdigest()


def _scan(root, config):
    if not isinstance(config,dict) or type(config.get('enabled')) is not bool:
        raise CpuProbeError('Invalid CPU configuration')
    root=Path(root);base=root/'sys/devices/system/cpu/cpufreq'
    paths=list(base.glob('policy*'))
    if any(not re.fullmatch('policy[0-9]+',p.name) for p in paths): raise CpuProbeError('Invalid CPU policy name')
    groups={};seen=set();rows=[]
    for path in paths:
        resolved=path.resolve(strict=True)
        if resolved in seen: raise CpuProbeError('Duplicate CPU policy')
        seen.add(resolved)
        if _text(path/'scaling_driver')!='intel_pstate': raise CpuProbeError('intel_pstate required')
        maximum=_positive(path/'cpuinfo_max_freq'); minimum=_positive(path/'scaling_min_freq')
        current=_positive(path/'scaling_max_freq')
        governor=_text(path/'scaling_governor'); choices=_text(path/'energy_performance_available_preferences').split()
        epp=_text(path/'energy_performance_preference')
        if minimum>maximum or not minimum<=current<=maximum or epp not in choices:
            raise CpuProbeError('CPU policy values are inconsistent')
        for name in ('scaling_max_freq','energy_performance_preference'):
            info=(path/name).stat()
            if info.st_uid!=0 or not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o200:
                raise CpuProbeError('CPU policy write interface is unavailable')
        row=dict(policy=path.name,maximum=maximum,minimum=minimum,current=current,
                 governor=governor,choices=choices,epp=epp)
        groups.setdefault(maximum,[]).append(row); rows.append(row)
    maxima=sorted(groups,reverse=True)
    if len(maxima)!=3 or [len(groups[m]) for m in maxima] != [2,8,4]:
        raise CpuProbeError('Unexpected G3E CPU policy topology')
    desired={}
    if config['enabled']:
        epp=config.get('epp')
        if epp not in EPPS: raise CpuProbeError('Invalid CPU EPP')
        for key,maximum in zip(('p','e','lp'),maxima):
            cap=config.get(key+'_mhz')
            if type(cap) is not int or cap%100: raise CpuProbeError('Invalid CPU limit')
            for row in groups[maximum]:
                if not row['minimum'] <= cap*1000 <= row['maximum'] or epp not in row['choices'] or (row['governor']=='performance' and epp!='performance'):
                    raise CpuProbeError('CPU setting unavailable on selected policy')
                prefix='/sys/devices/system/cpu/cpufreq/'+row['policy']+'/'
                desired[prefix+'scaling_max_freq']=str(cap*1000)
                desired[prefix+'energy_performance_preference']=epp
    return {'policies':sorted(rows,key=lambda r:int(r['policy'][6:])), 'desired':desired,
            'enabled':config['enabled']}


def _validate_host():
    if os.name!='posix' or os.geteuid()!=0 or os.uname().release!=KERNEL:
        raise CpuProbeError('CPU probe requires root on the exact supported Linux kernel')
    release={}
    for line in Path('/etc/os-release').read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep: release[key]=value.strip().strip('"')
    if (release.get('ID'),release.get('VERSION_ID'),release.get('BUILD_ID')) != ('steamos','3.10','20260924.1000'):
        raise CpuProbeError('Unsupported SteamOS identity')


def probe():
    _validate_host()
    config,digest=_configuration(Path('/'))
    return dict(_scan(Path('/'),config),config_sha256=digest,config=config)


def probe_observe():
    _validate_host()
    return _scan(Path('/'),{'enabled':False})



