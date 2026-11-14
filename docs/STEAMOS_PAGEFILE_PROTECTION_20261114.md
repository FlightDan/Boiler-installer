# SteamOS EFI selection with an active Windows pagefile

The Windows installer identified the SteamOS layout but disabled all three boot partitions. Its read-only protection check attempted to open the active pagefile from Win32_PageFileUsage and got Windows error 32 (sharing violation). The installer treated that result as an unverified host volume for every candidate.

The check can now identify a locked active pagefile by inspecting the file's attributes and resolving its immediate parent by native handle. The fallback requires a regular file and applies only to an active pagefile reporting a sharing violation. The exact partition extent still determines whether the paging volume is protected. The same policy runs during inventory and before each deletion.

Read-only verification on the affected GM7 confirmed that the earlier package protected partitions 7, 8 and 9 with the generic volume-check error. With the revised code, all three passed inventory and boot-content checks. The Windows EFI and Windows volumes remained protected, and the active pagefile still resolved to the Windows volume.

The test does not execute physical deletion. Guest installation, maintenance payloads and recovery formats do not change. The 0.2.5 Windows x64 slim release contains this fix. It uses the previously signed generic Boiler Maintenance 0.2.0+maintenance.22 and the same pinned model catalog as 0.2.4. Physical deletion, end-to-end installation, and target upgrade and recovery remain separate acceptance gates.
