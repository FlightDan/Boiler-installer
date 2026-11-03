# SSD boot repair candidate

The fixed repair recognizes only a reviewed physical NVMe destination whose
model normalizes, by trimming and collapsing ASCII spaces and ignoring case,
to `Predator SSD GM7 1TB`. It adds only `nvme.noacpi=1` to supported Valve
kernel boot entries. The model of a handheld, a PCI identifier, firmware
version, or an untrusted option value is not sufficient to select a repair.

After destination review, a matching repair is selected by default in the
custom build. A supported selected model package provider takes priority over
the common fallback. The user can uncheck it; that preference persists while
the reviewed destination remains unchanged. A changed destination clears the
previous choice and is matched again. Other or unknown SSDs do not receive the
repair. A package provider declares the fixed policy ID/version and requires
engine 0.1.3 on both component and package; it cannot supply boot hooks or files.

The renderer derives from `windows-installer/components/boot_repairs.py` in
the public FlightDan/OneXPlayer3-SteamOS revision
`72f864e0498c10366493c74608c5ffed62cc99ca`, SHA-256
`3084c2cfceddd6e4545597a702290caeae063eaed1bdf345fdb291b3aa78ea8d`.
The preserved MIT notice is `common/scripts/licenses/upstream-fixes-MIT.txt`.

This record describes source ownership and the existing fileless boot-policy provider.
The user has reported successful prior runtime verification on both OXP3 and
X2 Mini. That report is distinct from acceptance of newly generated common-source
packages. Source validation, Windows UI checks, Linux synthetic lifecycle checks,
and physical-device acceptance remain separate evidence. New package inventories,
maintenance upgrade/recovery, artifact downloads, and publication must be verified
for the exact delivered build. This source-only record does not declare a release.
