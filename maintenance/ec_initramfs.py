"""Read the fixed target initramfs and reject an early stock oxpec module."""
import gzip,hashlib,io,lzma,resource,subprocess,tempfile
from pathlib import Path
from maintenance.adoption_blobs import _file

IMAGE='boot/initramfs-linux-neptune-72.img'
MAX_IMAGE=128*1024*1024
MAX_UNPACKED=512*1024*1024
class InitramfsError(RuntimeError):pass

def _cpio(raw,start,names):
    pos=start
    while True:
        if raw[pos:pos+6] not in (b'070701',b'070702') or len(raw)-pos<110:
            raise InitramfsError('Invalid initramfs cpio header')
        try: fields=[int(raw[pos+6+i*8:pos+14+i*8],16) for i in range(13)]
        except ValueError as exc:raise InitramfsError('Invalid initramfs cpio field') from exc
        size,namesize=fields[6],fields[11]
        if not 1<=namesize<=4096:raise InitramfsError('Invalid initramfs cpio name')
        name_start=pos+110;name_end=name_start+namesize
        if name_end>len(raw) or raw[name_end-1]!=0:raise InitramfsError('Truncated initramfs name')
        if b'\0' in raw[name_start:name_end-1]:raise InitramfsError('Invalid initramfs name')
        name=raw[name_start:name_end-1].decode('utf-8','surrogateescape')
        data_start=(name_end+3)&~3;data_end=data_start+size
        if data_end>len(raw):raise InitramfsError('Truncated initramfs entry')
        pos=(data_end+3)&~3
        if name=='TRAILER!!!':return pos
        names.append(name)

def _decompress(raw):
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        command=['/usr/bin/zstd','-q','-d','-c']
        def limit():
            _,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
            cap=MAX_UNPACKED+1 if hard==resource.RLIM_INFINITY else min(MAX_UNPACKED+1,hard)
            resource.setrlimit(resource.RLIMIT_FSIZE,(cap,hard))
        with tempfile.TemporaryFile() as output:
            process=subprocess.run(command,input=raw,stdout=output,stderr=subprocess.PIPE,timeout=120,check=False,preexec_fn=limit)
            if process.returncode:raise InitramfsError('Cannot decompress target initramfs within bound')
            output.seek(0);result=output.read(MAX_UNPACKED+1)
    elif raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:result=stream.read(MAX_UNPACKED+1)
    elif raw.startswith(b'\xfd7zXZ\x00'):
        with lzma.LZMAFile(io.BytesIO(raw)) as stream:result=stream.read(MAX_UNPACKED+1)
    else:raise InitramfsError('Unsupported initramfs compression')
    if len(result)>MAX_UNPACKED:raise InitramfsError('Initramfs expands beyond the fixed bound')
    return result

def _entries(raw):
    names=[];pos=0
    while pos<len(raw):
        while pos<len(raw) and raw[pos]==0:pos+=1
        if pos==len(raw):break
        if raw[pos:pos+6] in (b'070701',b'070702'):
            pos=_cpio(raw,pos,names);continue
        decoded=_decompress(raw[pos:])
        names.extend(_entries(decoded));break
    return names

def inspect(root):
    with _file(Path(root)/IMAGE) as (stream,info):
        if info.st_uid!=0 or info.st_gid!=0 or info.st_size<=0 or info.st_size>MAX_IMAGE:
            raise InitramfsError('Unsafe target initramfs')
        raw=stream.read(MAX_IMAGE+1)
    if len(raw)!=info.st_size:raise InitramfsError('Target initramfs changed during reading')
    names=_entries(raw)
    if not names:raise InitramfsError('Empty target initramfs')
    matches=[name for name in names if name.rsplit('/',1)[-1]=='oxpec.ko' or name.rsplit('/',1)[-1].startswith('oxpec.ko.')]
    if matches:raise InitramfsError('Target initramfs contains a pre-root oxpec module')
    return {'path':IMAGE,'sha256':hashlib.sha256(raw).hexdigest(),'entries':len(names),'stock_oxpec':False}
