#!/bin/bash
# Explicit uninstall: return to normal charging at 100%, retain the fan module.
set -eu
if [ -d /var/lib/oxp3-installer/decky/runtime/plugins/OXP3-battery ]; then
    echo 'Restore charging through the maintenance tool for this managed installation.' >&2
    exit 1
fi
python3 - <<'PY'
import sys
sys.path.insert(0,'/var/lib/oxp3-battery')
import battery_control as b
b.set_value('charge_behaviour','auto')
b.set_value('charge_control_end_threshold',100)
print(b.status())
PY
systemctl disable --now oxp3-battery.service
python3 - <<'PY'
from pathlib import Path
import time
p=Path('/home/deck/homebrew/plugins/OXP3-battery')
if p.exists(): p.rename(Path('/var/lib/oxp3-research/battery-connect-v1')/('removed-plugin-'+str(time.time_ns())))
for p in ('/etc/systemd/system/oxp3-battery.service','/etc/dbus-1/system.d/org.onexplayer.SteamOSManager.Battery.conf','/etc/steamos-manager/remotes.d/oxp3-battery.toml','/etc/systemd/system/systemd-suspend.service.d/90-oxp3-battery.conf'):
    Path(p).unlink(missing_ok=True)
PY
systemctl daemon-reload
busctl call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig
runuser -u deck -- env XDG_RUNTIME_DIR=/run/user/1000 systemctl --user restart steamos-manager
systemctl restart plugin_loader
