# ONEXPLAYER 3 and X2 Mini PL1 floor

The native SteamOS TDP property and the Decky power control use an 8–40 W PL1 range on ONE-NETBOOK systems reporting ONEXPLAYER 3, ONEXPLAYER X2Mini or ONEXPLAYER X2 Mini. Missing or other DMI identities keep the public provider’s 10 W minimum. The change sets the selectable range. A write still requires an explicit request and the existing RAPL validation and readback.

Both model TOONs reference the same brand policy. The package builder derives the installed RAPL provider and the CPU power helper from the public CPU source lock at `72f864e0498c10366493c74608c5ffed62cc99ca`. The localized Decky editor uses the same reported minimum when queuing a write. PL2 retains its 10 W minimum, 80 W maximum and live PL1 constraint.

Installation and retained maintenance consume the integration members of the new model package. Package and legacy component versions advance during derivation. The fixed EFI provider contract still covers the historical options file, which can display its original 10–40 W label. The current TOON, model capability and runtime report the new range.

The earlier [X2 Mini hardware record](../x2-mini-g3e-tdp-20261108.md) covers an Arc G3 Extreme device and the earlier derived files. The two-model change has synthetic controller/readback and UI-source coverage. Physical OXP3 and current X2 package tests, in-game slider checks, installed maintenance upgrades and rollback remain pending. No release or device deployment accompanies this source merge.
