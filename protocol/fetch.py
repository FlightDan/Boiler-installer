"""Bounded source acquisition. Only complete verified packages are published."""
import hashlib
import contextlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen

from .manifest import (ProtocolError, validate_package, verify_signed_index,
                       safe_extract_archive, confined, relative_path, _source)


@contextlib.contextmanager
def _cache_lock(cache):
    lock=cache/"cache.lock"
    if lock.is_symlink(): raise ProtocolError("Unsafe cache lock")
    with lock.open("a+b") as stream:
        if os.name=="nt":
            import msvcrt
            if stream.seek(0,2)==0: stream.write(b"0"); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
            try: yield
            finally: stream.seek(0); msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def download(url,path,*,maximum,expected=None):
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
        raise ProtocolError("Source must be credential-free HTTPS")
    request=Request(url,headers={"User-Agent":"Boiler-installer/0.1","Accept":"application/vnd.github+json"})
    digest=hashlib.sha256(); size=0
    with urlopen(request,timeout=60) as response,Path(path).open("xb") as stream:
        if urlparse(response.url).scheme!="https": raise ProtocolError("Source redirected away from HTTPS")
        while True:
            block=response.read(1024*1024)
            if not block: break
            size+=len(block)
            if size>maximum: raise ProtocolError("Download exceeds size limit")
            stream.write(block); digest.update(block)
        stream.flush(); os.fsync(stream.fileno())
    if expected and (digest.hexdigest()!=expected["sha256"] or size!=expected["size"]):
        raise ProtocolError("Downloaded source digest mismatch")
    return {"sha256":digest.hexdigest(),"size":size}


def fetch_index(url,signature_url,cache,*,public_key=None,openssl="openssl"):
    cache=Path(cache); cache.mkdir(parents=True,exist_ok=True)
    # A directory rename publishes an immutable generation. `current.json` is
    # the sole atomic pointer, so crashes never pair a new index with old sig.
    with tempfile.TemporaryDirectory(prefix=".index-",dir=cache) as temp:
        temp=Path(temp); download(url,temp/"index.json",maximum=8*1024**2)
        download(signature_url,temp/"index.sig",maximum=64)
        index=verify_signed_index(temp/"index.json",temp/"index.sig",public_key,openssl=openssl)
        digest=hashlib.sha256((temp/"index.json").read_bytes()).hexdigest()
        pointer=cache/"current.json"
        if pointer.exists():
            previous=json.loads(pointer.read_text(encoding="utf-8"))
            if index["sequence"]<previous["sequence"] or (index["sequence"]==previous["sequence"] and digest!=previous["sha256"]):
                raise ProtocolError("Signed index rollback or sequence reuse rejected")
        with _cache_lock(cache):
            if pointer.exists():
                previous=json.loads(pointer.read_text(encoding="utf-8"))
                if index["sequence"]<previous["sequence"] or (index["sequence"]==previous["sequence"] and digest!=previous["sha256"]):
                    raise ProtocolError("Signed index rollback or sequence reuse rejected")
            generation=cache/digest
            if not generation.exists():
                staged=temp/"generation"; staged.mkdir()
                for name in ("index.json","index.sig"):
                    shutil.copyfile(temp/name,staged/name)
                    with (staged/name).open("r+b") as stream: os.fsync(stream.fileno())
                os.replace(staged,generation)
            metadata={"sequence":index["sequence"],"sha256":digest,"generation":digest}
            staging=cache/(".current-"+os.urandom(8).hex()+".json")
            with staging.open("x",encoding="utf-8") as stream:
                json.dump(metadata,stream); stream.flush(); os.fsync(stream.fileno())
            os.replace(staging,pointer)
            if os.name!="nt":
                fd=os.open(cache,os.O_RDONLY | getattr(os,"O_DIRECTORY",0))
                try: os.fsync(fd)
                finally: os.close(fd)
        return {"index":index,"cache":str(cache.resolve()),"sha256":digest}


def cached_index(cache,*,public_key=None,openssl="openssl"):
    cache=Path(cache); pointer=json.loads((cache/"current.json").read_text(encoding="utf-8"))
    generation=confined(cache,pointer["generation"])
    index=verify_signed_index(generation/"index.json",generation/"index.sig",public_key,openssl=openssl)
    digest=hashlib.sha256((generation/"index.json").read_bytes()).hexdigest()
    if digest!=pointer["sha256"] or index["sequence"]!=pointer["sequence"]: raise ProtocolError("Index cache pointer mismatch")
    return index


def _package_from_archive(archive,destination,source,*,expected=None,developer=False,identity=None,version=None,manifest_size=None):
    destination=Path(destination)
    if destination.exists(): raise ProtocolError("Package destination already exists")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-",dir=destination.parent) as temp:
        temp=Path(temp); tree=temp/"tree"; safe_extract_archive(archive,tree)
        children=list(tree.iterdir())
        # GitHub archives have exactly one repository wrapper directory.
        root=children[0] if source["kind"]=="github" and len(children)==1 and children[0].is_dir() else tree
        if source.get("package_path"): root=confined(root,source["package_path"])
        manifest=validate_package(root,allow_unsigned=developer,expected_manifest_sha256=expected)
        if ((identity is not None and manifest["id"]!=identity) or (version is not None and manifest["version"]!=version)):
            raise ProtocolError("Signed entry and package identity differ")
        if manifest_size is not None and (root/"manifest.json").stat().st_size!=manifest_size:
            raise ProtocolError("Signed manifest size differs")
        published=temp/"verified"; shutil.copytree(root,published)
        # Recheck copied bytes before exposing the result to the installer.
        validate_package(published,allow_unsigned=developer,expected_manifest_sha256=expected)
        os.replace(published,destination)
    return manifest


def fetch_package(cache,identity,version,destination,*,public_key=None,openssl="openssl"):
    index=cached_index(cache,public_key=public_key,openssl=openssl)
    entry=next((p for p in index["packages"] if p["id"]==identity and p["version"]==version),None)
    if entry is None: raise ProtocolError("Package is not in signed snapshot")
    if entry["sha256"] in index.get("revocations",[]): raise ProtocolError("Package has been revoked")
    source=entry["source"]; _source(source)
    if source["kind"]=="local": raise ProtocolError("Normal registry cannot fetch local developer source")
    url=source["url"] if source["kind"]=="https" else "https://codeload.github.com/"+source["repository"]+"/zip/"+source["commit"]
    with tempfile.TemporaryDirectory(prefix="boiler-download-") as temp:
        archive=Path(temp)/"package.zip"
        download(url,archive,maximum=8*1024**3,expected=source if source["kind"]=="https" else None)
        manifest=_package_from_archive(archive,destination,source,expected=entry["sha256"],identity=identity,version=version,manifest_size=entry["size"])
    return {"package":manifest,"directory":str(Path(destination).resolve()),"manifest_sha256":entry["sha256"],"index_sequence":index["sequence"]}


def fetch_github(url,destination,*,ref="HEAD",package_path=None,developer=False):
    if not developer: raise ProtocolError("GitHub import requires explicit developer mode")
    parsed=urlparse(url); parts=parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.scheme!="https" or parsed.hostname!="github.com" or len(parts)!=2 or parsed.query or parsed.fragment:
        raise ProtocolError("Use a GitHub repository URL")
    repository="/".join(parts)
    if package_path: relative_path(package_path)
    with tempfile.TemporaryDirectory(prefix="boiler-github-") as temp:
        temp=Path(temp); metadata=temp/"commit.json"
        download("https://api.github.com/repos/"+repository+"/commits/"+quote(ref,safe=""),metadata,maximum=4*1024**2)
        commit=json.loads(metadata.read_text(encoding="utf-8"))["sha"]
        source={"kind":"github","repository":repository,"commit":commit}; _source(source)
        if package_path: source["package_path"]=package_path
        archive=temp/"package.zip"; download("https://codeload.github.com/"+repository+"/zip/"+commit,archive,maximum=8*1024**3)
        manifest=_package_from_archive(archive,destination,source,developer=True)
    return {"package":manifest,"directory":str(Path(destination).resolve()),"source":source,
            "manifest_sha256":hashlib.sha256((Path(destination)/"manifest.json").read_bytes()).hexdigest(),"trust":"developer-session-only"}
