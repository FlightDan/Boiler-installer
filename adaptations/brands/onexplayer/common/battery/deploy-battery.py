import base64
import json
from pathlib import Path
here=Path(__file__).parent
BUS='org.onexplayer.SteamOSManager.Battery'
unit=f'''[Unit]
Description=OXP3 native battery charge limit
Requires=dbus.socket
After=dbus.socket oxp3-fan-module.service
Wants=oxp3-fan-module.service
[Service]
Type=dbus
BusName={BUS}
ExecStart=/usr/bin/python3 -I -B /var/lib/oxp3-battery/battery_provider.py
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelModules=yes
ReadWritePaths=/run/lock /sys/class/power_supply/BAT0/charge_control_end_threshold
[Install]
WantedBy=multi-user.target
'''
policy=f'''<busconfig>
<policy context="default"><deny own="{BUS}"/><deny send_destination="{BUS}"/></policy>
<policy user="root"><allow own="{BUS}"/><allow send_destination="{BUS}"/></policy>
<policy user="deck"><allow send_destination="{BUS}"/></policy>
</busconfig>
'''
files={f'/var/lib/oxp3-battery/{n}':(here/n).read_bytes() for n in ('battery_control.py','battery_provider.py','sleep_control.py')}
files['/etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf']=(here/'sleep-router.conf').read_bytes()
files['/etc/systemd/system/oxp3-battery.service']=unit.encode()
files[f'/etc/dbus-1/system.d/{BUS}.conf']=policy.encode()
files['/etc/steamos-manager/remotes.d/oxp3-battery.toml']=f'[BatteryChargeLimit1]\nbus_name = "{BUS}"\nobject_path = "/org/onexplayer/SteamOSManager/Battery"\n'.encode()
plugin={'main.py':(here/'battery-plugin-main.py').read_bytes(),'dist/index.js':(here/'battery-plugin-index.js').read_bytes(),
 'plugin.json':json.dumps({'name':'OXP3 Battery','author':'Local OXP3 adaptation','flags':['root'],'api_version':1}).encode(),
 'package.json':json.dumps({'name':'oxp3-battery','version':'0.2.2','type':'module'}).encode()}
print("set -eu\npython3 - <<'PY'\nfrom pathlib import Path\nimport base64,json")
print("state=Path('/var/lib/oxp3-research/battery-connect-v1');stage=Path('/home/deck/homebrew/.oxp3-battery-stage');plugin=Path('/home/deck/homebrew/plugins/OXP3-battery');assert not state.exists() and not stage.exists() and not plugin.exists();state.mkdir(mode=0o700)")
print("bat=Path('/sys/class/power_supply/BAT0');(state/'before.json').write_text(json.dumps({n:(bat/n).read_text().strip() for n in ('charge_control_end_threshold','charge_behaviour')}))")
for dest,data in files.items():
 print(f"p=Path({dest!r});assert not p.exists();p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode('{base64.b64encode(data).decode()}'));p.chmod(0o644)")
for dest,data in plugin.items():
 print(f"p=stage/{dest!r};p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode('{base64.b64encode(data).decode()}'));p.chmod(0o644)")
print("stage.rename(plugin)\nPY")
print('python3 - <<\'PY\'\nimport sys,json\nsys.path.insert(0,"/var/lib/oxp3-battery")\nimport battery_control as b\nprint(json.dumps(b.status()))\nfor name,value in [("charge_control_end_threshold",True),("charge_control_end_threshold",9),("charge_control_end_threshold",101),("charge_behaviour","bad")]:\n try: b.set_value(name,value)\n except ValueError: pass\n else: raise AssertionError("invalid value accepted")\nprint("4 invalid requests rejected without writes")\nPY')
print('systemctl daemon-reload\nbusctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig\nsystemctl enable --now oxp3-battery.service')
print('runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus busctl --user call com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1 com.steampowered.SteamOSManager1.Manager2 ReloadConfig')
print('runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart steamos-manager')
print('systemctl restart plugin_loader\nsystemctl is-active oxp3-battery plugin_loader\ncat /sys/class/power_supply/BAT0/charge_control_end_threshold /sys/class/power_supply/BAT0/charge_behaviour')
