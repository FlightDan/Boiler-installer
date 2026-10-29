import importlib.util
import asyncio
spec=importlib.util.spec_from_file_location('battery','/var/lib/oxp3-battery/battery_control.py')
battery=importlib.util.module_from_spec(spec)
spec.loader.exec_module(battery)
sleep_spec=importlib.util.spec_from_file_location('battery_sleep','/var/lib/oxp3-battery/sleep_control.py')
sleep=importlib.util.module_from_spec(sleep_spec)
sleep_spec.loader.exec_module(sleep)

class Plugin:
    async def get_status(self): return battery.status()
    async def set_limit(self, limit): return battery.set_value('charge_control_end_threshold',limit)
    async def set_mode(self, mode): return battery.set_value('charge_behaviour',mode)
    async def get_sleep_status(self): return await asyncio.to_thread(sleep.status)
    async def set_sleep_policy(self, mode, delay_minutes):
        return await asyncio.to_thread(sleep.save, mode, delay_minutes)
    async def _main(self): pass
    async def _unload(self): pass
