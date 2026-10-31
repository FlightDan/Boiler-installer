"""Closed Flathub remote source and lifecycle contract."""
IDENTITY='resource:common-flathub-mirror'
KIND='boiler-flathub-mirror-v1'
TARGET='usr/local/lib/boiler/resources/flathub.flatpakrepo'
SHA256='3371dd250e61d9e1633630073fefda153cd4426f72f4afa0c3373ae2e8fea03a'
MIRROR='https://mirrors.ustc.edu.cn/flathub'

def validate_contract(resource):
    payload=resource.get('payload',{}).get('files',[]);install=resource.get('install',{})
    files=install.get('files',[])
    if (resource.get('id')!=IDENTITY or install.get('kind')!=KIND or set(install)!={'kind','files'}
            or len(payload)!=1 or len(files)!=1 or payload[0].get('sha256')!=SHA256
            or payload[0].get('size')!=4040 or files[0]!={'source':payload[0].get('path'),'target':TARGET,'mode':420,'scope':'root-slots'}
            or resource.get('verify')!={'kind':'sha256-v1'} or resource.get('restore')!={'kind':'transaction-backup-v1'}
            or resource.get('dependencies')!=[] or resource.get('external_component_dependencies')!=[]
            or resource.get('hardware_requires')!={}):
        raise ValueError('Invalid fixed Flathub mirror resource contract')
    return resource
