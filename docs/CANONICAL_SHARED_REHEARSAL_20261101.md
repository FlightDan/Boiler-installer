# Canonical shared-target rehearsal layout

A canonical eight-partition SteamOS image has no protected Windows partitions. An internal shared-disk rehearsal must nevertheless exercise protected-volume preservation. Previously it attempted to lock an empty selection and stopped before system partition deployment.

The owned virtual shared-target path now checks for an aligned 128 MiB leading gap after the first MiB before creating a VHD. When available, it derives two target-only fixture GUIDs from the retained VHD identity, copies the existing compact FAT and NTFS fixture into that gap, and retains all eight source role GUIDs, offsets, sizes and attributes. The source image and its journal binding are unchanged. Recovery reconstructs the same target layout from the retained VHD identity and rechecks its completed prefix. Existing protected-source and external-disk paths retain their existing layouts. Physical installation is unaffected.

Acceptance boundaries: source-level planning checks and the Windows build are separate from the compact owned-VHD interruption, resume and tamper test. Native target execution, packaged installer and maintenance integration, offline artifact verification, actual VM boot, upgrade/recovery and physical hardware acceptance require their own recorded gates. No physical disk, firmware or user volume is authorized by this record.

| Gate | Status |
| --- | --- |
| Windows Release build | Complete: zero warnings and zero errors |
| Source-level checks | Blocked: the existing boiler-checks suite requires 125 GiB free; the selected workspace reported 64 GiB before reaching the new checks |
| Compact owned-VHD deployment, resume and tamper | Pending controlled Windows execution |
| Maintenance upgrade/recovery and installer package | Pending matching release candidate |
| Artifact inventory and offline verification | Pending matching release candidate |
| Actual VM boot and hardware | Pending separate acceptance |
| Guest, model and physical-write implementation | Not applicable: this change is limited to owned virtual rehearsals |
