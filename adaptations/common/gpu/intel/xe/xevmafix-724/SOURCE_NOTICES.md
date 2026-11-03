# xe VMA source notices

`sources/xevmafix.c` declares `MODULE_LICENSE("GPL")`; the locked module
record states that the exact upstream license variant is unspecified. No
additional GPL grant is asserted here. The upstream repair-source lock records
MIT for its surrounding source set and `licenses/UPSTREAM-MIT.txt` preserves
that notice. The Boiler loader and the selected advisory transforms are local
AGPL-3.0-or-later modifications; they are independent of the kernel module.
