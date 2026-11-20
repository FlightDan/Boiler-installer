# Fingerprint userspace loader isolation

On the X2 Mini test device, a manual debugging configuration added the
fingerprint runtime directory to the system loader cache. The stock polkit
daemon and KDE authentication helper selected the runtime's libpolkit and
exited with undefined symbols. With the cache bypassed, the packaged daemon
found its direct dependencies but its libfprint could not find libgusb and
five OpenCV libraries. The daemon's DT_RUNPATH applies to direct dependencies.

Both D-Bus activation paths now pass the private library directory to the
system loader for the packaged fprintd process. The package continues to deploy
its existing service files through the model component transaction. The
fingerprint authorization policy and data directory are unchanged.

Clean-device acceptance requires installation through the model package,
reboot, KDE enrollment and verification with the system polkit running.
FTE3600 kernel-glue persistence is a separate open requirement. The manually
created global loader configuration on the debug unit is outside the model
component transaction and requires separate device remediation.
