# Windows Custom Components hardware detection — 2026-11-03

The Windows installer's Custom Components view reads the current computer's present PnP devices through the existing native SetupAPI reader. This is a host UI feature: its results are not target-disk identity, installation authorization, or a Linux compatibility fact. No detector is added to the target filesystem.

The scoped results are Intel xeVMA, Intel BE201 firmware, Realtek ALC245 speaker routing, and BMI260 gyro. Exact display-class Intel B080/B082 PCI identities and HDAUDIO 10EC:0245 codec identities are reported as matched hardware. Other Intel PCI display devices are candidates until the Linux xe driver and compatibility gates are checked. Intel A840 remains a candidate because that function is shared between wireless products. ACPI 10EC5280, including the long VEN/DEV form, remains a candidate because the actual BMI260 chip ID has not been read. Friendly names and vendor-only identities do not establish these matches.

Results display the current-computer source, raw hardware ID, match state, applicability limits, and available related choices. Scanning and refreshing do not change component selections. Existing target-bound GM7 selection keeps its separate model-provider priority, common fallback, matched default, and explicit opt-out behavior.

The public implementation keeps the detector inside the general Custom Components section, available before model-package or SSD selection. Model selection only affects the availability of related choices. Loading, empty inventory, retry after an enumeration error, and manual selections are handled without changing disk plans, helper requests, or resource trust.

Acceptance boundaries:

- Source matching and Windows control behavior are checked separately from Linux runtime compatibility and hardware acceptance.
- The native SetupAPI probe may record aggregate enumeration counts; raw machine inventories are not exported or retained by this feature.
- Existing OXP3/X2 Mini runtime-fix acceptance is user-reported for both machines. It does not establish new source-migration, Windows visual, or package-publication acceptance.
- Empty WPF bitmap captures are a blocked visual check. Earlier Custom Components screenshots predate this detector and do not establish its visual acceptance.
- No new installer release or model package is established by a successful source build or UI check. Immutable package, inventory, signing, upgrade/recovery, download, and release gates remain separate.

Verified in the final source candidate:

- Locked Windows Release builds passed with zero warnings and errors for the working tree and isolated public-source tree.
- The actual working-tree control suite passed matching, repeated matching, read failure, empty inventory, selection/plan preservation, and live Chinese/English localization assertions.
- The actual public-tree `boiler-device-checks` passed, independently repeated by the primary agent, including native read-only SetupAPI enumeration, general Custom Components availability without a selected model or matching SSD, selection preservation, and Chinese/English controls.
- Public-tree `ssd-ui-checks` passed with the separate matched-default/provider/fallback behavior retained.
- The primary agent inspected rendered desktop Chinese and narrow English working-tree captures. A newly found stale localized heading/intro was fixed and checked through the actual controls. These captures predate the final related-component availability labels, so final visual acceptance remains pending. They do not establish physical-device or released-package acceptance.
- The explicit source whitelist includes the new matcher and this record. Publication and new package delivery remain outstanding.
