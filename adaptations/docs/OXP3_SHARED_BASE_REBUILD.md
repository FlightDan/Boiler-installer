# Historical OXP3 .3 shared base rebuild

This fixed historical builder reproduces the complete unsigned
`0.1.0-localtest.20261030.3` package from a complete explicitly supplied `.2`
package and locked public binary inputs. It never signs, publishes, deploys,
queries hardware, reads a sibling checkout, or discovers a cache/private helper.
It is not a build of every nested third-party binary from source.

The `.2` input manifest is
`510342dec0bfe6ce39389f5e13cae0d504755aa4072d90b73641b4dd2d5aead9`.
Every file is verified before copying and the copied package is verified again.
The public bridge source is independently pinned at
`4bf4fac5f58ac6ba4ea4e6789c675e5c2b22c5226b849ddcbc8209c99b3c4e32`.
Only its authenticated source bytes are compiled; plugin and guest code is data.

## Explicit inputs

`records/oxp3-shared-base/opti031-inputs.json` is a frozen public input record,
SHA-256 `f8cf3f4007e1ff6f436b0b914bf10762fe3737c0ec013d2bacc36bcd222abfa6`.
It contains public upstream URLs, exact sizes, hashes, 36 plugin-member hashes,
the unchanged published runtime identity and all three original OptiScaler
archive identities. It contains no local directories, signing identities or
device records. URL availability is separate from byte verification.

| Input | Size | SHA-256 |
| --- | ---: | --- |
| Opti2XMX 0.3.1 ZIP | 1,483,205 | `d2d6b3ccdfd73c8676450dcce5312330db861276e1ab2062e56efb0ac5043fea` |
| Optiscaler_0.9.4-final.20260718._MM.7z | 55,016,448 | `575cb4df866116093df75af607e37fd70e10f5163e0f23fd5c804142e80ef0ad` |
| OptiScaler_0.7.9.7z | 32,581,791 | `70103bf3746140d368179773edeafb02ec9718efa130276b8da833c3156fbfe3` |
| OptiScaler_v10.0.0-pre1_20261025.7z | 57,146,678 | `6bb2e9cd6ac0204c76c974df1c202afb1fc7a765844a9ccf608103557aa595fe` |

Run from the integrated public source root, using new explicit output paths:

```sh
python3 -B adaptations/scripts/build-shared-base.py --project-root . \
  --base /explicit/verified-oxp3-dot2 \
  --plugin-zip /explicit/opti2xmx-0.3.1.zip \
  --optiscaler-archive /explicit/Optiscaler_0.9.4-final.20260718._MM.7z \
  --optiscaler-archive /explicit/OptiScaler_0.7.9.7z \
  --optiscaler-archive /explicit/OptiScaler_v10.0.0-pre1_20261025.7z \
  --output /explicit/new-dot3-build
python3 -B adaptations/tests/test_shared_base_builder.py --project-root . \
  --plugin-zip /explicit/opti2xmx-0.3.1.zip --built /explicit/new-dot3-build
```

Success requires the actual `.3` manifest hash
`c80efd9cce07d35c5f8828d17b5fab8c15fbc177a10476754cfac04db3f27485`
and historical 420,343,373-byte ZIP hash
`46c1913e5ce935df0a766687f7ffd825ee556dc4f25a54721dbe0eb4ff944871`.
The exact ZIP is reimported by the existing protocol. No verification report is
written unless all these checks succeed. Failed output remains for diagnosis.

The manifest's `source.path: Boiler-oxp3` is historical provenance retained as a
fixed data label. It is not a filesystem input; do not create an old project
directory or junction to satisfy it.

All non-Opti inventory records, Battery/microphone resources, original legacy
licenses and the published runtime must remain unchanged. The five CPU files
are checked against the immutable public
`72f864e0498c10366493c74608c5ffed62cc99ca` baseline. The fixed full `.2` anchor
and plugin-member pins exclude current private research and user game state.
Original AGPL notices, upstream MIT/kernel notices and the plugin's bundled
AGPL/wimlib/other third-party licenses remain separate and are retained verbatim.

## Earlier stages and current boundary

The existing public tools generate the `.2` stage from an explicitly supplied
complete `c442eef2039779be8e3f432a65bb5cc46c432f8cd7b51734ea76168ab07136e2`
package and current source-locked Battery/microphone bytes:

```sh
python3 -B adaptations/scripts/prepare-current-inputs.py --output /explicit/new-static-inputs
python3 -B adaptations/brands/onexplayer/oxp3/bridge/scripts/build-shared-adaptations.py \
  --base /explicit/verified-c442-package --inputs /explicit/new-static-inputs/inputs.json \
  --output /explicit/new-dot2-build
```

The static input lock is
`089a7c31bd1e96a4b7396f40adbd142ce14af7abd5c1858ef9aba86a955ea7a0`;
the OXP3 output is the `.2` anchor above. This also builds the historical X2
`.2` package; it does not substitute for the later four-component X2 candidate.

`c442...` originates from the already-public 555,684,389-byte installer archive
locked by `bridge/upstream-lock.json` (SHA-256
`d8bee8bad14f4cd136c814ebcaa3299fa59202b6cdc6d26ec9c52af0e055f4d4`),
plus the public Opti 0.3.0 ZIP and the same three OptiScaler archives. The
existing `bridge/scripts/fetch-input.py` retrieves that exact installer, and
`bridge/scripts/build.py` accepts `--public-installer`, `--selection`,
`--opti2xmx-plugin`, repeated `--optiscaler-archive`, `--version
0.1.0-localtest.20261029.6` and `--output`.

An exact new-root `c442...` source rebuild still needs an explicitly reviewed
historical provenance-label option in that earlier generator: its current local
mode derives `source.path` from the bridge directory name, which is now
`bridge`. The historical manifest used `Boiler-oxp3`. This `.3` builder does not
silently rewrite that earlier generated manifest or relax its anchor. Until that
earlier generator is integrated, a fork can rebuild `.3` from the explicit
authenticated `.2` input; it cannot claim a complete zero-intermediate rebuild.
