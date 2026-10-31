"""Strict per-resource source envelope and fixed legacy renderer adaptation."""
import copy,re
from protocol.resource_pool import require
FIELDS={'schema_version','kind','id','version','dependencies','external_component_dependencies','hardware_requires','compatibility','install_kind','verify','restore','renderer','source_lock','recipe'}
RECIPES={
 'files-v1':{'payload','license_files','install_details'},
 'additional-files-v1':{'payload','license_files','runtime_state','install_details'},
 'fixed-ec-profile-v1':{'payload','license_files','runtime_state','install_details','profile_build'},
 'firmware-v1':{'resource','files','origin','original_metadata'},
 'gamma22-shared-v1':{'upstream','license'},
 'fixed-desktop-hdr-v1':{'native_manifest','runtime_sources','license_sources'},
 'fixed-public-cpu-v1':{'public_commit','deployment','templates'},
 'fixed-public-cpu-live-v1':{'public_commit','deployment','templates'},
 'fixed-boot-settings-v1':{'resource','license_sources'},
 'fixed-public-rapl-v1':{'name','payload','license_files','provenance','install_details'},
 'fixed-input-r5-v1':{'archive','archive_size','archive_sha256','variant','native_build_sha256','native_artifact_type','resources'},
 'fixed-kde-keyboard-v1':{'license_files'},
 'fixed-flathub-mirror-v1':{'definition','license_files','mirror_url'},
 'fixed-opti2xmx-v1':{'plugin','frontend','input_recipe','license_files','patches'},
 'fixed-telemetry-overlay-v1':{'collector','system_unit','user_unit','resume_hook','schema','license_files'},
}
KINDS={'fixed-opti2xmx-v1':'BoilerOptiSource','fixed-desktop-hdr-v1':'BoilerResourceSource','files-v1': 'BoilerResourceSource', 'additional-files-v1': 'BoilerAdditionalResourceSource', 'firmware-v1': 'BoilerFirmwareSource', 'gamma22-shared-v1': 'BoilerHdrSource', 'fixed-public-cpu-v1': 'BoilerPublicCpuSource', 'fixed-boot-settings-v1': 'BoilerBootSource', 'fixed-public-rapl-v1': 'BoilerResource', 'fixed-input-r5-v1': 'BoilerInputSource', 'fixed-kde-keyboard-v1': 'BoilerKeyboardSource', 'fixed-flathub-mirror-v1': 'BoilerFlathubSource', 'fixed-ec-profile-v1': 'BoilerAdditionalResourceSource', 'fixed-public-cpu-live-v1': 'BoilerPublicCpuSource', 'fixed-opti2xmx-v1':{'plugin','frontend','input_recipe','license_files','patches'},
 'fixed-telemetry-overlay-v1': 'BoilerTelemetryOverlaySource'}
META=('id','version','dependencies','external_component_dependencies','hardware_requires','compatibility','verify','restore')
def validate(e):
 require(isinstance(e,dict) and set(e)==FIELDS and e['kind']=='BoilerResourceSource' and type(e['schema_version']) is int and e['schema_version']==1,'Invalid resource source envelope')
 require(isinstance(e['id'],str) and re.fullmatch('resource:[a-z][a-z0-9-]*',e['id']) and isinstance(e['version'],str) and e['version'],'Invalid envelope identity')
 require(e['renderer'] in RECIPES and isinstance(e['recipe'],dict) and set(e['recipe'])==RECIPES[e['renderer']],'Invalid fixed renderer recipe')
 if 'resource' in e['recipe']:
  require(isinstance(e['recipe']['resource'],dict) and not ({'schema_version','kind','id','version','dependencies','external_component_dependencies','hardware_requires','compatibility','install','verify','restore'} & set(e['recipe']['resource'])),'Recipe shadows resource metadata')
  nested={'firmware-v1':{'status','brand_scope','model_name_policy','payload','license_files','provenance','acceptance','limitations','install_details'},'fixed-boot-settings-v1':{'name','parameters','payload','fixed_targets','license_files','provenance','install_details'}}
  require(set(e['recipe']['resource'])==nested[e['renderer']],'Unknown nested renderer recipe field')
 for details in (e['recipe'].get('install_details'),e['recipe'].get('resource',{}).get('install_details')):
  if details is not None:
   allowed={'files'} if e['renderer'] in ('firmware-v1','fixed-boot-settings-v1','fixed-public-rapl-v1') else set()
   require(isinstance(details,dict) and set(details)==allowed,'Invalid install recipe fields')
 require(isinstance(e['source_lock'],str) and e['source_lock']=='locks/resources/'+e['id'].split(':',1)[1]+'.toon','Invalid per-resource source lock')
 require(all(isinstance(e[k],list) for k in ('dependencies','external_component_dependencies')) and all(isinstance(e[k],dict) for k in ('hardware_requires','compatibility','verify','restore')) and isinstance(e['install_kind'],str),'Invalid resource metadata')
 return e
def legacy(e):
 validate(e);renderer=e['renderer'];recipe=copy.deepcopy(e['recipe']);details=recipe.pop('install_details',{})
 common=dict(schema_version=1,kind=KINDS[renderer],id=e['id'],version=e['version'],dependencies=e['dependencies'],external_component_dependencies=e['external_component_dependencies'],hardware_requires=e['hardware_requires'],compatibility=e['compatibility'],install=dict(kind=e['install_kind'],**details),verify=e['verify'],restore=e['restore'])
 if renderer=='files-v1':
  common.pop('compatibility');return dict(common,**recipe)
 if renderer in ('additional-files-v1','fixed-ec-profile-v1'):return dict(common,source_lock='source-lock.toon',**recipe)
 if renderer in ('firmware-v1','fixed-boot-settings-v1'):
  nested=recipe.pop('resource');details=nested.pop('install_details',{})
  nested=dict(nested,**{k:copy.deepcopy(e[k]) for k in META},schema_version=1,kind='BoilerResource',install=dict(kind=e['install_kind'],**details))
  result=dict(schema_version=1,kind=KINDS[renderer],resource=nested,**recipe)
  if renderer=='fixed-boot-settings-v1':result['renderer']=renderer
  return result
 if renderer=='fixed-public-rapl-v1':return dict(common,**recipe)
 if renderer in ('gamma22-shared-v1','fixed-public-cpu-v1','fixed-public-cpu-live-v1'):
  return dict(schema_version=1,kind=KINDS[renderer],id=e['id'],version=e['version'],renderer=renderer,**recipe)
 return dict(schema_version=1,kind=KINDS[renderer],renderer=renderer,**recipe)
