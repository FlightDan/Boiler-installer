"""Fingerprint verification bridge; PIN and template bytes are never handled here."""
import asyncio
import json
import os
from pathlib import Path
import re


class Plugin:
    async def _main(self):
        config = json.loads((Path(__file__).parent / 'profile.json').read_text())
        self.verify_path = config['verify_command']
        self.list_path = config['list_command']
        self.enroll_command = config['enroll_command']
        self.user = os.environ.get('DECKY_USER', 'deck')
        self.lock = asyncio.Lock()
        self.proc = None
        self.cancelled = False

    async def _stop(self, proc):
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except asyncio.TimeoutError:
                pass

    async def _run(self, executable, timeout):
        try:
            self.proc = await asyncio.create_subprocess_exec(
                executable, self.user, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
            if self.cancelled:
                await self._stop(self.proc)
                return None, '', 'cancelled'
            try:
                output, _ = await asyncio.wait_for(self.proc.communicate(), timeout)
            except asyncio.TimeoutError:
                await self._stop(self.proc)
                return None, '', 'timeout'
            return self.proc.returncode, output.decode('utf-8', errors='replace'), None
        except (OSError, ProcessLookupError):
            return None, '', 'error'
        finally:
            self.proc = None

    async def verify(self, timeout_s=30):
        if self.lock.locked():
            return {'result': 'busy'}
        async with self.lock:
            self.cancelled = False
            code, text, reason = await self._run(self.verify_path, max(1, min(int(timeout_s), 30)))
            # Exact result line and successful process completion are both required.
            results = re.findall(r'^Verify result: ([^\r\n]+)[ \t]*$', text, re.M)
            if self.cancelled:
                return {'result': 'unavailable', 'reason': 'cancelled'}
            if code == 0 and len(results) == 1 and results[0].strip() == 'verify-match (done)':
                return {'result': 'match'}
            if code == 1 and len(results) == 1 and results[0].strip() == 'verify-no-match (done)':
                reason = 'no-match'
            elif (reason is None and code == 1 and len(results) == 1
                  and results[0].strip() == 'verify-unknown-error (done)'
                  and not re.search(r'^(?:VerifyStart|VerifyStop) failed:', text, re.M)):
                reason = 'device-error'
            return {'result': 'unavailable', 'reason': reason or 'error'}

    async def cancel(self):
        self.cancelled = True
        proc = self.proc
        if proc is not None:
            await self._stop(proc)
        return {'cancelled': True}

    async def status(self):
        if self.lock.locked():
            return {'available': False}
        async with self.lock:
            self.cancelled = False
            code, text, _ = await self._run(self.list_path, 10)
            return {'available': code == 0, 'enrolled': bool(re.search(r'#\d+:\s*\S+', text)) if code == 0 else False,
                    'enroll_command': self.enroll_command}

    async def _unload(self):
        await self.cancel()
