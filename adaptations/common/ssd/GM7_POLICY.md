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
The preserved MIT notice is `adaptations/common/scripts/licenses/upstream-fixes-MIT.txt`.

This policy and its source integration are a development candidate. Synthetic
target binding, conditional UI defaults, transaction and runtime inventory
checks have passed; they do not constitute a release or hardware acceptance.
The fixed OXP3 model/SSD bridge is wired as a development candidate; delivery
remains blocked on its combined A/B verification, Linux VFAT lifecycle,
maintenance recovery, final artifact validation, and device testing. The actual Windows native model representation has not
been validated.
