# xevma loader kmod invocation correction

The system kmod executable dispatches according to its invoked program name.
The previous loader passed `/usr/bin/kmod` as argv[0] and `modprobe` or
`insmod` as argv[1], which kmod rejects. The generated loader now invokes the
same fixed executable with the respective command as argv[0].

The correction applies to newly built OXP3, X2 and shared-resource candidates.
The original reviewed loader, module bytes, source snapshots and historical
artifacts are unchanged. OXP3 retains its exact DMI gate; the reviewed X2
model-advisory behavior and the resource's real Intel xe interface checks remain.
OS, kernel, root, module digest and existing-module checks remain unchanged.

Controlled source tests verify arguments, ordering, failure propagation and
guard preservation without loading a module. Physical wake behavior remains
unverified: the observed kmod failure is confirmed, but its causal connection
to a suspend or resume hang has not been established. Rebuilt installer and
retained-maintenance copies, actual target activation, recovery and hardware
sleep testing are separate pending gates.
