# Model maintenance write-action timeout

An isolated UI13 lifecycle rehearsal reached a model apply and then failed when
the model hook exceeded its 900-second subprocess timeout. A read-only
inspection of the preserved failed clone confirmed `TimeoutExpired`; it did
not resume or repair the failed transaction. The diagnostic record SHA-256 is
`4c2dbef9fd628c508d5deed04f86958039e32fcd43f4e158fafdab0cb81d5f99`.
The failed lifecycle outcome SHA-256 is
`fd644c282cc2e2c8650f88ecd2927648194ef4f10ff7f85c1e5517968f0b3a1b`;
its original source image was unchanged.

Model apply and restore hooks now have a 3600-second bound. Inspect, plan and
verify retain their 900-second bound. A timeout still preserves the failed
action receipt and pending recovery barrier; this change does not automatically
resume a transaction or guarantee that every write action finishes in time.

Acceptance remains separate: focused source and recovery tests cover the
timeout selection and preserved failure state. A fresh packaged Linux/VM model
apply and restore under the new bound, artifact verification, and physical-device
acceptance remain pending. The previous failed attempt remains historical
evidence and is not a completed lifecycle.
