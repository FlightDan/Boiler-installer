# KDE Plasma lock-screen fingerprint unlock

Plasma 6 ships a non-interactive PAM authenticator for fingerprints. The
lock screen (`kscreenlocker_greet`) starts up to three `PamAuthenticator`
instances with these fixed service names:

| Authenticator | PAM service | Purpose |
| --- | --- | --- |
| interactive | `kde` | Password / PIN entry |
| non-interactive | `kde-fingerprint` | Fingerprint scan |
| non-interactive | `kde-smartcard` | Smartcard |

The `kscreenlocker` package itself installs all three service files under
`/usr/lib/pam.d/`, which is the vendor default location. PAM resolves
`/etc/pam.d/` first, so a site can override any of them without touching the
package-owned copy.

## What is already provided by the distribution

`/usr/lib/pam.d/kde-fingerprint` contains:

```
#%PAM-1.0

auth       required                    pam_shells.so
auth       requisite                   pam_nologin.so
auth       requisite                   pam_faillock.so      preauth
-auth      required                    pam_fprintd.so
auth       optional                    pam_permit.so
auth       required                    pam_env.so

account    include                     system-local-login

password   required                    pam_deny.so

session    include                     system-local-login
```

This stack is correct and must not be rewritten to add the module. The only
thing missing on SteamOS is the `pam_fprintd.so` module itself: the Arch
`fprintd` package ships it at `usr/lib/security/pam_fprintd.so`, but SteamOS
does not preinstall that package.

## What this integration provides

`install-kde-pam.py` installs exactly one file, `pam_fprintd.so`, into
`/usr/lib/security/`. It does not modify any PAM service file, because the
vendor `kde-fingerprint` stack already calls the module.

The module is a thin D-Bus client. It links only `libpam`, `libsystemd`,
`libgcc_s` and `libc`; it does **not** link `libfprint`. It therefore talks to
whichever service owns the bus name `net.reactivated.Fprint`, which is the
Boiler `boiler-fprintd.service` on a managed device.

## Behaviour verified against kscreenlocker 6.7.3

These results were reproduced on an `ONEXPLAYER X2Mini` running a
`7.2.4-valve1` kernel with the Boiler `libfprint-2.so.2` carrying both the
BF63112 and FTE3600 backends.

| Condition | `pam_authenticate("kde-fingerprint")` | Lock screen behaviour |
| --- | --- | --- |
| `pam_fprintd.so` absent | `PAM_MODULE_UNKNOWN` (28) | fingerprint authenticator reported unavailable; password still works |
| module present, no usable authentication info | `PAM_AUTHINFO_UNAVAIL` (9) | authenticator reported unavailable; password still works |
| module present, finger enrolled | `PAM_SUCCESS` | screen unlocks |

The failure path was also checked for a bypass. A deliberately broken stack
using `-auth required pam_deny.so` followed by `auth optional pam_permit.so`
returned `PAM_PERM_DENIED` (6), not success. The leading `-` prefix records
the failure as ignored but still contributes a negative result, so the
`-auth ... pam_fprintd.so` line in the vendor stack cannot authenticate a user
on its own.

## Requirements on the target

1. A working `fprintd` D-Bus service owning `net.reactivated.Fprint`. On a
   Boiler-managed device that is `boiler-fprintd.service`; on a standard
   SteamOS install it is `fprintd.service`.
2. A fingerprint enrolled for the account through KDE System Settings → Users,
   which is the `kcm_users` module and uses the same `kde-fingerprint`
   authenticator indirectly through the D-Bus API.
3. A seat that polkit treats as active. Authentication attempted from an SSH
   session is rejected by policy (`net.reactivated.fprint.device.verify` is
   `allow_active=yes` only); this is expected and is not a defect.

## PIN fallback

Plasma keeps the interactive `kde` authenticator alive next to the fingerprint
one. A failed or unavailable scan never removes the password/PIN field, and
`pam_fprintd` is `-auth` (optional in effect) inside its own stack, so the
fingerprint path cannot lock a user out of the password path.

## Removal

`install-kde-pam.py uninstall` removes the module only when it still matches
the recorded digest. A module that was replaced or modified by something else
is left in place and reported.

## Scope

This is the KDE desktop path. The Decky plugin in this repository is a
separate path that drives `fprintd-verify` directly for the Steam game-mode
lock screen. Both consume the same `fprintd` service and the same enrolled
templates.
