"""Prototype native backend; only trusted host code may construct bindings.

It is deliberately not registered with firstboot or package metadata.
"""
import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import io

from .flathub_definition import render
from .adoption_blobs import _directory,_file


class Backend:
    def __init__(self, bindings, definition):
        if set(bindings) != {'system', 'user'}: raise ValueError('Invalid fixed installation bindings')
        self.bindings = bindings
        self.definition = render(definition)
        parser=configparser.ConfigParser(interpolation=None,strict=True);parser.optionxform=str
        parser.read_file(io.StringIO(self.definition.decode('utf-8')))
        section=parser['Flatpak Repo']
        self.created_config={'gpg-verify':'true','gpg-verify-summary':'true',
            **{'xa.'+key.lower():section[key] for key in ('Title','Comment','Description','Icon','Homepage')}}

    def created_matches(self, current):
        return (current.get('exists') is True and current.get('url')=='https://mirrors.ustc.edu.cn/flathub'
                and current.get('flathub_key') is True and current.get('configuration')==self.created_config)

    def _run(self, args):
        return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=60,
                              env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C','HOME':'/root'}).stdout.strip()

    def observe(self, scope):
        binding = self.bindings[scope]
        repo=Path(binding['repo']);existing=repo
        while not os.path.lexists(existing):
            if existing==existing.parent:raise ValueError('Flatpak installation has no existing ancestor')
            existing=existing.parent
        options=binding.get('user_owner',{})
        with _directory(existing,**options):pass
        config=repo/'config'
        if not os.path.lexists(config):return {'exists':False}
        # Reading repo/config does not initialize an installation or apply
        # site defaults before the transaction's durable before-state.
        parser = configparser.ConfigParser(interpolation=None, strict=True)
        parser.optionxform = str
        with _file(config,**options) as (stream,info):
            if info.st_size>1024*1024:raise ValueError('Flatpak configuration exceeds limit')
            parser.read_string(stream.read().decode('utf-8'))
        section = 'remote "flathub"'
        if section not in parser:return {'exists':False}
        if not parser[section].get('url'):
            raise ValueError('Missing materialized Flatpak remote configuration')
        remote = dict(parser[section]); url = remote.pop('url')
        serialized = json.dumps(remote, sort_keys=True, separators=(',',':')).encode()
        keys = self._run(['/usr/bin/ostree', '--repo='+binding['repo'], 'remote', 'gpg-list-keys', 'flathub'])
        return dict(exists=True, url=url, key_digest=hashlib.sha256(keys.encode()).hexdigest(),
                    configuration_digest=hashlib.sha256(serialized).hexdigest(),configuration=remote,
                    flathub_key='6E5C05D979C76DAF93C081354184DD4D907A7CAE' in keys.upper().replace(' ',''))

    def set_url(self, scope, url):
        if not isinstance(url, str) or '\x00' in url or len(url) > 8192: raise ValueError('Invalid recorded URL')
        self._run(self.bindings[scope]['flatpak']+['remote-modify','--url='+url,'flathub'])

    def restore_url_flag(self, scope, before):
        binding=self.bindings[scope];config=before['configuration']
        prior=config.get('url-is-set')
        if prior not in (None,'true','false'):raise ValueError('Unexpected original Flatpak URL flag')
        command=binding['ostree']+['--repo='+binding['repo'],'config']
        if prior is None:self._run(command+['unset','--group=remote "flathub"','url-is-set'])
        elif prior=='false':self._run(command+['set','--group=remote "flathub"','url-is-set','false'])

    def create(self, scope, url):
        if url != 'https://mirrors.ustc.edu.cn/flathub': raise ValueError('Unexpected mirror URL')
        # The remote definition contains a public signing key. The 0644 file
        # is needed because the user installation executes as deck.
        fd, path = tempfile.mkstemp(prefix='boiler-flathub-', suffix='.flatpakrepo', dir='/tmp')
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o644); stream.write(self.definition); stream.flush(); os.fsync(stream.fileno())
            self._run(self.bindings[scope]['flatpak']+['remote-add','--from','flathub',path])
        finally:
            os.unlink(path)

    def delete_without_force(self, scope):
        self._run(self.bindings[scope]['flatpak']+['remote-delete','flathub'])
