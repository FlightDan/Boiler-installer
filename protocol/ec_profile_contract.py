"""Fixed EC source/profile/component contract for the compiled G1 Intel profile."""
IDENTITY='resource:onexplayer-ec-fan-driver'
PROFILE='oxp3-g1i-v1'
KERNEL='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9'
MODULE_SHA='a819b435f1214252d1d0b15b039baa00d31642495075e864b3153299ecd6e372'
SOURCE_SHA='ec6f58f18e2bb2a1a013d5ca647d01a91a42ada2ccc3437f2ab59a34e67fa86f'
MARKER={'kind':'onexplayer-ec-profile-v1','profile':PROFILE,'module_sha256':MODULE_SHA,'source_sha256':SOURCE_SHA}
TARGETS={
 'var/lib/oxp3-fan/oxpec.ko':MODULE_SHA,
 'usr/lib/modules/'+KERNEL+'/updates/oxpec.ko':MODULE_SHA,
 'var/lib/oxp3-fan/load-fan-module.py':'df717562fa87bd74fb1d2dd7d30efc2336bf89b94cee582ab4960f4cd93bf49c',
 'etc/systemd/system/oxp3-fan-module.service':'3b047eac9f1a6d13ccfcaef58001e7d93635106405d86bc024f023fa79ba3625',
}

def validate_contract(resource):
    def need(ok,message):
        if not ok:raise ValueError('Invalid fixed EC profile contract: '+message)
    need(isinstance(resource,dict) and resource.get('id')==IDENTITY and resource.get('ec_profile_contract')==MARKER,'identity/profile')
    install=resource.get('install',{})
    need(install.get('kind')=='files-v1' and resource.get('verify')=={'kind':'sha256-v1'} and resource.get('restore')=={'kind':'transaction-backup-v1'},'lifecycle')
    need(not resource.get('hooks') and not resource.get('dynamic_handler'),'extensions')
    need(resource.get('hardware_requires')=={'kernel':[KERNEL],'acpi_ec_interface':[True]} and resource['hardware_requires']['acpi_ec_interface'][0] is True,'hardware requirements')
    need(resource.get('compatibility')=={'architectures':['x86_64'],'os_ids':['steamos'],'os_versions':['3.10'],'kernels':[KERNEL]},'compatibility')
    need(resource.get('dependencies')==[] and resource.get('external_component_dependencies')==[],'dependencies')
    need(resource.get('activation')=={'kind':'fixed-ec-profile-provider-v1','phase':'first-boot','autoload':True} and resource['activation']['autoload'] is True,'activation')
    rows=install.get('files'); files=resource.get('payload',{}).get('files')
    need(isinstance(rows,list) and len(rows)==4 and isinstance(files,list) and len(files)==3,'inventory')
    inventory={r['path']:r for r in files};need(len(inventory)==3,'duplicate payload')
    need({r['target'] for r in rows}==set(TARGETS),'target set')
    for row in rows:
        need(row.get('source') in inventory and inventory[row['source']].get('sha256')==TARGETS[row['target']] and
             type(row.get('mode')) is int and row['mode']==0o644 and row.get('scope')=='root-slots','target digest/mode')
    return resource

