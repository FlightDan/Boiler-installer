"""Build only a reviewed desktop HDR resource from explicit locked inputs."""
import hashlib,io,json,re,tarfile
from pathlib import Path,PurePosixPath
from protocol.resource_pool import decoder,local,sha,validate_pool,json_document,relative
from protocol import desktop_hdr_contract as contract

def _relative(value):
    if not isinstance(value,str) or not value or '\\' in value:raise ValueError('Invalid archive member path')
    relative(value)
    path=PurePosixPath(value)
    if path.is_absolute() or any(x in ('.','..') for x in path.parts) or str(path)!=value:raise ValueError('Unsafe archive member path')
    return value

def _load_native(manifest,archive):
    if (type(manifest) is not dict or set(manifest)!={'schema_version','kind','archive','files','aliases','extras','ignored_links'}
            or type(manifest['schema_version']) is not int or manifest['schema_version']!=1 or manifest['kind']!='BoilerDesktopHdrNativeBuildV1'):
        raise ValueError('Invalid native build manifest')
    pin=manifest['archive']
    if type(pin) is not dict or set(pin)!={'size','sha256'} or type(pin['size']) is not int or pin['size']<1 or not re.fullmatch('[0-9a-f]{64}',str(pin['sha256'])):
        raise ValueError('Invalid native archive pin')
    archive=Path(archive).absolute()
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (archive,*archive.parents)) or not archive.is_file() or archive.stat().st_size!=pin['size']:
        raise ValueError('Explicit native archive is absent or differs in size')
    if type(manifest['files']) is not list or not manifest['files'] or type(manifest['aliases']) is not list or type(manifest['extras']) is not list or type(manifest['ignored_links']) is not list:
        raise ValueError('Invalid native member inventory')
    members={};aliases={};extras={};ignored_links={};output_paths=set()
    for row in manifest['files']:
        if type(row) is not dict or set(row)!={'member','path','size','sha256'} or type(row['size']) is not int or row['size']<0 or not re.fullmatch('[0-9a-f]{64}',str(row['sha256'])):
            raise ValueError('Invalid native file inventory entry')
        member=_relative(row['member']);path=_relative(row['path'])
        if member!=contract.PREFIX+path:raise ValueError('Native member is not from the fixed logical prefix')
        if member in members or path in output_paths:raise ValueError('Duplicate native inventory member or path')
        members[member]=row;output_paths.add(path)
    for row in manifest['extras']:
        if type(row) is not dict or set(row)!={'member','size','sha256'} or type(row['size']) is not int or row['size']<0 or not re.fullmatch('[0-9a-f]{64}',str(row['sha256'])):
            raise ValueError('Invalid non-payload native member')
        member=_relative(row['member'])
        if not member.startswith(contract.PREFIX):raise ValueError('Non-payload member is outside the fixed logical prefix')
        if member in members or member in extras:raise ValueError('Duplicate native archive inventory')
        extras[member]=row
    for row in manifest['aliases']:
        if type(row) is not dict or set(row)!={'member','path','target_member','linkname'}:raise ValueError('Invalid native alias')
        member=_relative(row['member']);path=_relative(row['path']);target=_relative(row['target_member'])
        if member!=contract.PREFIX+path:raise ValueError('Native alias is not from the fixed logical prefix')
        if member in members or member in aliases or member in extras or path in output_paths or type(row['linkname']) is not str:
            raise ValueError('Unsupported native alias')
        # A link is accepted only when its resolved destination is the exact
        # listed in-prefix regular member; no extraction follows a link.
        resolved=PurePosixPath(member).parent.joinpath(row['linkname'])
        stack=[]
        for part in resolved.parts:
            if part=='..':
                if not stack:raise ValueError('Native alias escapes archive')
                stack.pop()
            elif part not in ('','.'):stack.append(part)
        if row['linkname'].startswith('/') or '/'.join(stack)!=target:raise ValueError('Native alias target differs')
        aliases[member]=row;output_paths.add(path)
    def ultimate(name,seen=None):
        seen=set() if seen is None else seen
        if name in seen:raise ValueError('Native alias cycle')
        if name in members:return name
        if name not in aliases:raise ValueError('Native alias does not end at a reviewed regular member')
        seen.add(name)
        return ultimate(aliases[name]['target_member'],seen)
    for name in aliases:ultimate(name)
    for row in manifest['ignored_links']:
        if type(row) is not dict or set(row)!={'member','target_member','linkname'}:raise ValueError('Invalid ignored native link')
        member=_relative(row['member']);target=_relative(row['target_member']);linkname=row['linkname']
        if not member.startswith(contract.PREFIX):raise ValueError('Ignored native link is outside the fixed logical prefix')
        if member in members or member in aliases or member in extras or member in ignored_links or target not in members and target not in extras and target not in aliases or type(linkname) is not str or linkname.startswith('/'):
            raise ValueError('Unsupported ignored native link')
        stack=[]
        for part in PurePosixPath(member).parent.joinpath(linkname).parts:
            if part=='..':
                if not stack:raise ValueError('Ignored native link escapes archive')
                stack.pop()
            elif part not in ('','.'):stack.append(part)
        if '/'.join(stack)!=target:raise ValueError('Ignored native link target differs')
        ignored_links[member]=row
    data={};seen=set()
    with archive.open('rb') as archive_stream:
        if hashlib.file_digest(archive_stream,'sha256').hexdigest()!=pin['sha256']:raise ValueError('Native archive digest differs')
        archive_stream.seek(0)
        with tarfile.open(fileobj=archive_stream,mode='r:*') as stream:
          for item in stream:
            if item.name.startswith('/') or '\\' in item.name:raise ValueError('Unsafe native archive path')
            name=item.name.removeprefix('./').rstrip('/')
            if not name or (name=='.' and item.isdir()):continue
            _relative(name)
            if name in seen:raise ValueError('Duplicate archive member')
            seen.add(name)
            if item.isdir():continue
            if item.isfile():
                if name not in members and name not in extras:raise ValueError('Unreviewed native archive file')
                row=members.get(name,extras.get(name))
                if item.size!=row['size']:raise ValueError('Native member size differs')
                reader=stream.extractfile(item)
                if name in members:
                    raw=reader.read(row['size']+1)
                    if len(raw)!=row['size'] or sha(raw)!=row['sha256']:raise ValueError('Native member digest differs')
                    data[name]=raw
                elif hashlib.file_digest(reader,'sha256').hexdigest()!=row['sha256']:
                    raise ValueError('Non-payload native member digest differs')
            elif item.issym():
                row=aliases.get(name,ignored_links.get(name))
                if row is None or item.linkname!=row['linkname']:raise ValueError('Unreviewed native archive link')
            else:raise ValueError('Special native archive member refused')
    if set(data)!=set(members) or not (set(aliases)|set(extras)|set(ignored_links))<=seen:raise ValueError('Native archive lacks reviewed members')
    return members,{name:dict(row,resolved_member=ultimate(name)) for name,row in aliases.items()},data

def build(project,output,*,source_root,source_spec,native_archive,locked_paths):
    """The caller supplies a verified source lock and an explicit CAS archive."""
    from .source_envelope import validate
    validate(source_spec)
    if source_spec.get('renderer')!='fixed-desktop-hdr-v1' or source_spec.get('id')!=contract.IDENTITY or source_spec.get('install_kind')!=contract.KIND:
        raise ValueError('Invalid desktop source recipe')
    for constant in (contract.REVIEWED_SOURCE_MANIFEST_SHA256,contract.REVIEWED_BUNDLE_SHA256,contract.REVIEWED_RUNTIME_INPUTS,contract.REVIEWED_LICENSE_INPUTS):
        if not constant:raise ValueError('Reviewed desktop native/source inventory is not frozen')
    if (source_spec['dependencies']!=[] or source_spec['verify']!={'kind':'sha256-v1'} or source_spec['restore']!={'kind':'transaction-backup-v1'}
            or not isinstance(contract.REVIEWED_METADATA,dict) or any(source_spec.get(k)!=v for k,v in contract.REVIEWED_METADATA.items())):
        raise ValueError('Desktop source metadata differs from reviewed contract')
    recipe=source_spec['recipe'];manifest_name=recipe['native_manifest']
    if manifest_name not in locked_paths or recipe['runtime_sources']!=contract.REVIEWED_RUNTIME_INPUTS or recipe['license_sources']!=contract.REVIEWED_LICENSE_INPUTS:
        raise ValueError('Desktop source inputs differ from reviewed lock')
    root=Path(source_root);manifest_raw=local(root,manifest_name).read_bytes()
    if sha(manifest_raw)!=contract.REVIEWED_SOURCE_MANIFEST_SHA256:raise ValueError('Reviewed native manifest differs')
    manifest=decoder().loads(manifest_raw.decode('utf-8'),strict=True)
    members,aliases,native=_load_native(manifest,native_archive)
    files={};native_inventory=[]
    for member,row in members.items():
        raw=native[member];name='payload/desktop/native/'+row['sha256'];files[name]=raw
        native_inventory.append(dict(path=row['path'],size=row['size'],sha256=row['sha256']))
    for row in aliases.values():
        source=members[row['resolved_member']]
        native_inventory.append(dict(path=row['path'],size=source['size'],sha256=source['sha256']))
    native_inventory.sort(key=lambda row:row['path'])
    inventory_raw=(json.dumps(dict(schema_version=1,files=native_inventory),sort_keys=True,separators=(',',':'))+'\n').encode()
    if sha(inventory_raw)!=contract.REVIEWED_BUNDLE_SHA256:raise ValueError('Native bundle identity differs')
    files['payload/desktop/native-inventory.json']=inventory_raw
    for row in [*recipe['runtime_sources'],*recipe['license_sources']]:
        if type(row) is not dict or set(row)!={'source','output','size','sha256'} or row['source'] not in locked_paths:
            raise ValueError('Desktop source is outside its per-resource lock')
        raw=local(root,row['source']).read_bytes()
        if len(raw)!=row['size'] or sha(raw)!=row['sha256'] or row['output'] in files:raise ValueError('Desktop reviewed source bytes differ')
        if row['output']=='payload/desktop/panel-binding.json':
            if decoder().loads(raw.decode('utf-8'),strict=True)!={'kind':'BoilerDesktopHdrPanelBindingV1','schema_version':1,'state':'unbound'}:raise ValueError('Invalid unbound template')
            raw=contract.UNBOUND_BINDING_BYTES
        files[_relative(row['output'])]=raw
    for profile in contract.PROFILES:files['payload/desktop/profile-'+profile+'.json']=contract.profile_bytes(profile)
    if files.get('payload/desktop/panel-binding.json')!=contract.UNBOUND_BINDING_BYTES:
        raise ValueError('Desktop unbound projection differs from canonical placeholder')
    payload=[dict(path=name,size=len(raw),sha256=sha(raw)) for name,raw in sorted(files.items()) if name.startswith('payload/')]
    if payload!=contract.REVIEWED_PAYLOAD:raise ValueError('Desktop output inventory differs from reviewed SDK')
    metadata=contract.REVIEWED_METADATA
    resource=dict(schema_version=1,kind='BoilerResource',id=contract.IDENTITY,version=source_spec['version'],dependencies=[],
        **metadata,profiles=contract.PROFILES,desktop_bundle_sha256=contract.REVIEWED_BUNDLE_SHA256,
        install=dict(kind=contract.KIND,files=contract.REVIEWED_INSTALL_FILES),verify={'kind':'sha256-v1'},restore={'kind':'transaction-backup-v1'},
        payload={'files':payload},license_files=sorted(name for name in files if name.startswith('licenses/')))
    contract.validate_contract(resource)
    output=Path(output);output.mkdir(parents=True,exist_ok=False);toon=decoder()
    for name,raw in files.items():
        path=local(output,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    proof=dict(kind='BoilerDesktopHdrSourceBuildV1',source_manifest_sha256=sha(manifest_raw),native_archive=manifest['archive'],
        native_bundle_sha256=contract.REVIEWED_BUNDLE_SHA256,inputs=[*recipe['runtime_sources'],*recipe['license_sources']],
        physical_hardware_accepted=False,official_release=False)
    proof_path=local(output,'provenance/desktop-hdr-build.json');proof_path.parent.mkdir(parents=True,exist_ok=True)
    proof_path.write_text(json.dumps(proof,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    resource_path='resources/gamma22-hdr-desktop.toon';path=local(output,resource_path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(toon.dumps(resource)+'\n',encoding='utf-8')
    refs=[]
    for profile in contract.PROFILES:
        model=dict(schema_version=1,kind='BoilerResourceModel',id='device:desktop-'+profile,name='desktop '+profile,
            resources=[dict(resource=contract.IDENTITY,default=False,enabled=False,parameters={'profile':profile})],complete=False,pending=[])
        name='models/desktop-'+profile+'.toon';path=local(output,name);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(toon.dumps(model)+'\n',encoding='utf-8');refs.append(dict(id=model['id'],path=name,sha256=sha(path.read_bytes())))
    entries=[dict(path=p.relative_to(output).as_posix(),size=p.stat().st_size,sha256=sha(p.read_bytes())) for p in sorted(output.rglob('*')) if p.is_file()]
    catalog=dict(schema_version=1,kind='BoilerResourceCatalog',trust='unsigned-development',official_release=False,
        resources=[dict(id=contract.IDENTITY,version=resource['version'],path=resource_path,sha256=sha(local(output,resource_path).read_bytes()))],models=refs,files=entries)
    local(output,'catalog.toon').write_text(toon.dumps(catalog)+'\n',encoding='utf-8')
    local(output,'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    validate_pool(output)
    return resource
