# Canonical common-device model candidates

`scripts/finalize-canonical-model.py` builds a new unsigned model candidate
from an explicitly supplied, independently verified four-component package and
the canonical preparation for `FlightDan/Boiler-devices` commit
`fb102eb212c08a714ab7bb8e5de0a09b6df067f6`. It requires the pinned original
public installer archive for BMI260 preparation. Inputs and output must be
explicit; use a fresh project-owned RAID preparation directory.

The OXP3 input is the complete `.14`-derived package rebuilt with the reviewed
full OXP3 EFI bridge using `scripts/build-oxp3-full-efi.py`. First use the
normal `adaptations/scripts/build-upstream-repairs.py` pipeline with its pinned
`.4` base and explicitly locked Decky and xe inputs to generate that complete
four-component `.14` package; the normal TOON pipeline constructs and verifies
its preceding `.3` and `.4` intermediates. The X2 input is the
complete `.x2-fixed-efi.3` candidate built by
`adaptations/scripts/build-x2-complete.py`. Each has the four components
`oxp3:legacy-adaptation`, `oxp3:microphone`, `oxp3:speaker-route`, and
`oxp3:xevmafix`. A legacy-only EFI lifecycle fixture is not an input to this
builder. Supply the exact input manifest SHA-256 via `--base-sha256`; this
digest binds the explicit local input and does not establish release trust.

```text
python -B scripts/finalize-canonical-model.py --base <complete-model-package> --base-sha256 <exact-manifest-sha256> --stage <canonical-stage> --public-installer <pinned-public-installer.zip> --version <new-model-version> --output <fresh-raid-output>
```

The builder validates the stage inventory and model TOON references, retains
their sources and licenses, inserts a fileless GM7 provider, replaces the xe
and speaker inputs, and updates the legacy BMI260 transaction. It advances the
two modified Engine component versions to `2026.10.30` so maintenance update
planning can distinguish changed bytes from the older `2026.10.29` versions.
Existing model profile bytes and retained maintenance recovery semantics are
preserved. The original GM7 legacy capability remains unavailable; the new
provider still requires the actual target SSD identity and transaction review.

The nine BE201 firmware files and licenses are retained as source material.
Their model-package installation remains blocked pending trusted target-device
matching and connection to the fixed firmware handler. X2 speaker selection is
unavailable until its board route is verified. X2 gyro orientation is inherited
and has not been independently measured. Source preparation and archive import
do not establish Linux lifecycle, physical hardware, release signing, or public
download acceptance.
