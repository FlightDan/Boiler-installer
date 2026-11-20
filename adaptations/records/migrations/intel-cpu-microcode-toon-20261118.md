# Intel CPU microcode source declaration for OXP3 and X2 Mini

Both model TOONs request the same [Panther Lake resource](../../common/cpu/intel/microcode/source.toon).
Intel's [microcode-20261110 release](https://github.com/intel/Intel-Linux-Processor-Microcode-Data-Files/releases/tag/microcode-20261110)
provides byte-identical patches at `intel-ucode/06-cc-02` and
`intel-ucode/06-cc-03`. The resource retains one unmodified binary and the
[upstream license](../../common/cpu/intel/microcode/licenses/Intel-microcode.txt). The source TOON and canonical file inventory bind those inputs.

The declared scope is the Intel Arc G3 Extreme configurations. CPU signature
and platform flags determine applicability. The TOON records the source for
the brand package. The generic Boiler microcode installer already supports
an early initramfs archive. Wiring the model selection into that transaction,
A/B installation and maintenance restore checks, rebuilding delivery packages,
and verifying the loaded microcode revision on hardware are pending.
