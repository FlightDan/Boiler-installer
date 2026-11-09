# Plasma lock-screen fingerprint unlock

This record covers the KDE desktop unlock path, which is separate from the
Decky game-mode plugin. It records what was implemented, what was measured on
hardware, and what remains unverified.

## Why a separate path was needed

Plasma's lock screen does not use the Decky plugin. `kscreenlocker_greet`
(`kscreenlocker` 6.7.3) constructs up to three `PamAuthenticator` instances
against fixed PAM service names compiled into the binary:

| Authenticator | Service | Source |
| --- | --- | --- |
| interactive | `kde` | `KSCREENLOCKER_PAM_PASSWORD_SERVICE`, defaults to `kde` |
| non-interactive | `kde-fingerprint` | `KSCREENLOCKER_PAM_FINGERPRINT_SERVICE` |
| non-interactive | `kde-smartcard` | `KSCREENLOCKER_PAM_SMARTCARD_SERVICE` |

The names were read from the packaged binary (`strings -el`) and confirmed
against `greeter/CMakeLists.txt` in the upstream 6.7.3 source.

The `kscreenlocker` package installs all three service files under
`/usr/lib/pam.d/`, not `/etc/pam.d/`. `/usr/lib/pam.d/kde-fingerprint` already
contains:

```
auth       required    pam_shells.so
auth       requisite   pam_nologin.so
auth       requisite   pam_faillock.so  preauth
-auth      required    pam_fprintd.so
auth       optional    pam_permit.so
auth       required    pam_env.so
```

So the service definition is vendor-provided and does not need to be authored.
What SteamOS lacks is `/usr/lib/security/pam_fprintd.so`: the Arch `fprintd`
package ships it, but SteamOS does not preinstall that package.

## What was added

`common/fingerprint/kde/install-kde-pam.py` installs exactly one file,
`/usr/lib/security/pam_fprintd.so`, extracted from an explicitly supplied
package archive. It records the installed digest in
`/var/lib/boiler-fingerprint/kde-pam.json`, refuses to overwrite a module that
changed since installation, and refuses to remove one that no longer matches.
It never writes a PAM service file.

`common/fingerprint/kde/README.md` documents the authenticator contract and
the measured behaviour.

Both files are referenced by the `fingerprint-unlock` component of
`devices/onexplayer/oxp3.toon` and `devices/onexplayer/x2-mini.toon`, are in
`model-source-files.json`, and are mirrored in the standalone
`FlightDan/FingerprintUnlock-SteamOS` repository under `kde/`, where
`scripts/install.py kde-pam` exposes the same action.

## Measured on hardware

Target: `ONEXPLAYER X2Mini` (`ONE-NETBOOK`), kernel
`7.2.4-valve1-1-neptune-72-g5ab4af5e2eb9`, Boiler `boiler-fprintd.service`
owning `net.reactivated.Fprint` with a merged BF63112 + FTE3600
`libfprint-2.so.2`.

A direct `pam_authenticate("kde-fingerprint")` harness (ctypes binding to
`libpam.so.0`, conversation callback returning `PAM_CONV_ERR`) produced:

| Condition | Result |
| --- | --- |
| `pam_fprintd.so` absent | `PAM_MODULE_UNKNOWN` (28) |
| module present, no usable auth info (SSH session, polkit inactive) | `PAM_AUTHINFO_UNAVAIL` (9) |
| control: `auth required pam_deny.so` | `PAM_AUTH_ERR` (7) |
| control: `-auth required pam_deny.so` + `auth optional pam_permit.so` | `PAM_PERM_DENIED` (6) |

The last two rows exist because the vendor stack's `-auth` prefix could in
principle suppress the module's result. It does not: a `-auth required`
failure still contributes a negative outcome, so the fingerprint line alone
cannot authenticate a user.

`PAM_MODULE_UNKNOWN` and `PAM_AUTHINFO_UNAVAIL` are both turned into
`m_unavailable` by `PamWorker::authenticate`, which removes the fingerprint
authenticator from the lock screen. The password field remains. There is no
state in which a missing or non-matching fingerprint unlocks the screen.

## Hardware acceptance

The Plasma lock-screen path was accepted on the same `ONEXPLAYER X2Mini` after
the module was installed through `kde/install-kde-pam.py`: the user locked the
session, saw the `(or scan your fingerprint on the reader)` prompt appear, and
unlocked with the enrolled FTE3600 finger. The password path was left in place
throughout.

This acceptance covers the KDE desktop path only. It does not transfer to the
Decky game-mode plugin, and it does not cover enrollment robustness over time.

## Not verified

- PIN/password fallback behaviour after a deliberately failed scan. The
  password field was present and usable, but a forced-failure cycle was not
  measured.
- Suspend/resume with the sensor bound.
- Survival of a SteamOS system update. `/usr/lib/security` and `/etc/pam.d`
  are on the writable overlay and are not preserved by an OS update, same as
  the existing Boiler fingerprint runtime under `/var`.
- Installation of the FTE3600 runtime itself from an installer or maintenance
  payload. The library, kernel module, udev rule, service unit and PAM module
  were deployed by hand for this acceptance.

## Acceptance limit

KDE lock-screen unlock with FTE3600 is accepted on this model. Source tests and
synthetic installer tests still do not establish it for a different sensor,
kernel, or Plasma release, and the boot-time installation path remains an
open gate.
