import importlib.util
spec=importlib.util.spec_from_file_location('battery','/var/lib/oxp3-battery/battery_control.py')
battery=importlib.util.module_from_spec(spec)
spec.loader.exec_module(battery)

class Plugin:
    async def get_status(self): return battery.status()
    async def set_limit(self, limit): return battery.set_value('charge_control_end_threshold',limit)
    async def set_mode(self, mode): return battery.set_value('charge_behaviour',mode)
    async def _main(self): pass
    async def _unload(self): pass
