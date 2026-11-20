import asyncio
import importlib.util
from pathlib import Path

# Status remains available before activation. The systemd hook always uses
# the separate root-owned installed copy, never this Decky directory.
installed = Path('/var/lib/boiler-sleep/sleep_control.py')
source = installed if installed.is_file() else Path(__file__).with_name('sleep_control.py')
spec = importlib.util.spec_from_file_location('boiler_sleep', source)
sleep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sleep)

class Plugin:
    async def get_sleep_status(self):
        return await asyncio.to_thread(sleep.status)
    async def set_enabled(self, enabled):
        return await asyncio.to_thread(sleep.set_enabled, enabled)
    async def set_tsc_deadline_disabled(self, disabled):
        return await asyncio.to_thread(sleep.set_tsc_deadline_disabled, disabled)
    async def set_sleep_policy(self, mode, delay_minutes):
        return await asyncio.to_thread(sleep.save, mode, delay_minutes)
    async def _main(self): pass
    async def _unload(self): pass
