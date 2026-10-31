"""Fixed pre-release Waydroid backend; the system pacman config is unchanged."""
import hashlib,os,re,subprocess,tempfile
from pathlib import Path
from maintenance.adoption_blobs import _file
from .waydroid_alpm_transaction import ORDER,VERSIONS

ROOT=Path('/usr/local/lib/boiler/resources/waydroid/packages')
FILENAMES={
 'lxc':'lxc-1_7.0.0-1-x86_64.pkg.tar.zst',
 'dnsmasq':'dnsmasq-2.93-1-x86_64.pkg.tar.zst',
 'libglibutil':'libglibutil-1.0.80-1-x86_64.pkg.tar.zst',
 'libgbinder':'libgbinder-1.1.52-1-x86_64.pkg.tar.zst',
 'python-gbinder':'python-gbinder-1.3.1-1-x86_64.pkg.tar.zst',
 'waydroid':'waydroid-1.6.3-1-any.pkg.tar.zst',
 'steamos-waydroid-binder':'steamos-waydroid-binder-1-2-x86_64.pkg.tar.zst',
}
PINS={
 'lxc':(852702,'9ae2c4353e8dde664790e0515ee873865a6773a6a10565917b80bed6a473495e'),
 'dnsmasq':(510774,'443d70d2606549270b1ef52183926714e515f81413db33ff96006e23866ad790'),
 'libglibutil':(47818,'98023538d51d0b24987943231f5740bb23d462a50ef0dd3ba661d9f13f0f33a1'),
 'libgbinder':(83959,'59eeaf358937101010b3534b5e47494381c984ad043a5c25952e6eec4abfff17'),
 'python-gbinder':(180157,'a9415277f341328d50e1e0eb841fb805ffa03e83845f0ab254e37897ac5c0d5a'),
 'waydroid':(245505,'c2833f8b0369955e82a2621513fe8d2ca1516fd94de4da172c5036fbe4fbfcd5'),
 'steamos-waydroid-binder':(796472,'f5bbf910f3087c0a54874ce448eab558f938b52badb04e05d4ca727cd6a3783f')}
OFFICIAL={'lxc','dnsmasq'}
SIGNATURES={'lxc':'90e5ae9860319b6764487e8452e396812bb36870e7a842c8adb6a0c7dac4c50d','dnsmasq':'a44acb59ae744b567568c3c34d2f7a73c7896a4e95ee54091220deddbe79d4e8'}

class LivePacman:
 def __init__(self,locked_inputs,*,trust_local_test=False,root=Path('/')):
  if os.name!='posix' or os.geteuid()!=0:raise RuntimeError('Waydroid package activation requires Linux root')
  if trust_local_test is not True:raise RuntimeError('Waydroid self-built packages require an authorized local test session')
  if set(locked_inputs)!=set(ORDER):raise RuntimeError('Waydroid package input set differs')
  if any(locked_inputs[n]!={'size':PINS[n][0],'sha256':PINS[n][1]} for n in ORDER):raise RuntimeError('Waydroid fixed build receipt differs')
  self.locked=locked_inputs;self.root=Path(root).absolute()
 def _run(self,args):
  return subprocess.run(['/usr/bin/pacman','--root',str(self.root),'--dbpath',str(self.root/'usr/lib/holo/pacmandb'),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900,env=dict(os.environ,LC_ALL='C',LANG='C'))
 def require_exact_target(self):
  rows={}
  for line in (self.root/'etc/os-release').read_text().splitlines():
   if '=' in line:
    key,value=line.split('=',1);rows[key]=value.strip('"')
  if rows.get('ID')!='steamos' or rows.get('VERSION_ID')!='3.10' or rows.get('BUILD_ID')!='20260924.1000' or os.uname().release!='7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9':raise RuntimeError('Waydroid requires its exact SteamOS/kernel ABI')
 def installed(self,name):
  if name not in FILENAMES:raise RuntimeError('Unknown Waydroid package')
  result=self._run(['-Q','--',name])
  if result.returncode:
   if result.returncode==1 and ("package '"+name+"' was not found") in result.stderr:return None
   raise RuntimeError('Unable to read package database: '+result.stderr[:500])
  fields=result.stdout.strip().split()
  if len(fields)!=2 or fields[0]!=name:raise RuntimeError('Unexpected package database output')
  return fields[1]
 def verify_inputs(self):
  for name in ORDER:
   pin=self.locked[name]
   if set(pin)!={'size','sha256'} or type(pin['size']) is not int or pin['size']<=0 or not isinstance(pin['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',pin['sha256']):raise RuntimeError('Invalid Waydroid locked input')
   path=self.root/ROOT.relative_to('/')/FILENAMES[name];digest=hashlib.sha256();size=0
   with _file(path) as (stream,info):
    for chunk in iter(lambda:stream.read(1024*1024),b''):size+=len(chunk);digest.update(chunk)
   if size!=pin['size'] or digest.hexdigest()!=pin['sha256']:raise RuntimeError('Waydroid package bytes differ: '+name)
   if name in OFFICIAL:
    with _file(Path(str(path)+'.sig')) as (stream,info):
     if hashlib.sha256(stream.read()).hexdigest()!=SIGNATURES[name]:raise RuntimeError('Waydroid official signature differs: '+name)
 def _install_group(self,names,*,local_test):
  if not names:return
  # This private config applies only to this one invocation. No system trust or
  # keyring changes; unsigned exception is limited to the fixed five build outputs.
  mode='Never' if local_test else 'Required'
  text='[options]\nArchitecture = x86_64\nDBPath = /usr/lib/holo/pacmandb\nSigLevel = Required\nLocalFileSigLevel = '+mode+'\n'
  fd,path=tempfile.mkstemp(prefix='boiler-waydroid-pacman-',suffix='.conf',dir='/run')
  try:
   with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(text);stream.flush();os.fsync(stream.fileno())
   result=self._run(['--config',path,'-U','--noconfirm','--',*[str(self.root/ROOT.relative_to('/')/FILENAMES[n]) for n in names]])
   if result.returncode:raise RuntimeError('Waydroid package install failed: '+result.stderr[:800])
  finally:os.unlink(path)
 def install_fixed(self,names):
  if not names or len(names)!=len(set(names)) or any(n not in FILENAMES for n in names):raise RuntimeError('Invalid Waydroid install selection')
  self.verify_inputs()
  self._install_group([n for n in names if n in OFFICIAL],local_test=False)
  self._install_group([n for n in names if n not in OFFICIAL],local_test=True)
 def remove_fixed(self,names):
  if not names or len(names)!=len(set(names)) or any(n not in FILENAMES for n in names):raise RuntimeError('Invalid Waydroid removal selection')
  result=self._run(['-R','--noconfirm','--',*names])
  if result.returncode:raise RuntimeError('Waydroid package removal blocked: '+result.stderr[:800])

