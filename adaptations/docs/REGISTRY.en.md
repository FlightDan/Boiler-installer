# Device index and signatures

[Home](../README.md) · [中文](REGISTRY.md) · [Build guide](BUILD.en.md)

The device index tells the installer which model packages are available, where to obtain them, and how to check them. Each package manifest then lists its files and hashes.

## Two indexes

- [device-index.json](../device-index.json) in the repository root is an unsigned development baseline. Its current sequence is 0 and its package list is empty.
- [catalog-v0.1.0-preview.1](https://github.com/FlightDan/Boiler-adaptations/releases/tag/catalog-v0.1.0-preview.1) provides a signed `index.json` and `index.sig`. This historical catalog has sequence 1 and lists the OXP3 experimental package; its bound source commit differs from the current model TOON snapshot.

The maintainer holds the signing private key. The installer verifies the index with a trusted public key, then checks the packages bound by the index. Contributors do not need the private key to open a PR.

## Prepare an index

[scripts/build-index.py](../scripts/build-index.py) accepts an existing device-package directory and checks its manifest and file inventory. Run this command from the Boiler-adaptations repository root to create a local development index; replace both paths with your own and use a new output file:

```powershell
$Package = "PATH_TO_DEVICE_PACKAGE"
$Output = "PATH_TO_NEW_DEVELOPMENT_INDEX.json"
python -B scripts/build-index.py --package "$Package" --output "$Output"
```

For a release candidate, also provide the actual ZIP, a fixed release asset URL, and an increasing `--sequence`. The script checks the ZIP, manifest, and hashes, and requires the package to name an exact public source commit. The single-package release-candidate path accepts the registered experimental OXP3 package. The two-model candidate mode takes two `--model PACKAGE ZIP URL` inputs, one each for OXP3 and X2 Mini, and checks their fingerprint components. It also requires the independently signature-verified previous index and its SHA-256, an increasing sequence, an exact adaptation repository commit and version, and an installer checkout and commit. Run `python -B scripts/build-index.py --help` for the full arguments.

For subsequent releases, independently verify the previous signed index before passing it with `--previous-index`; the script does not verify that file's signature for the operator. The sequence must increase and prior revocations must be retained. `verified_at` records when the local artifact inventory was checked.

The script creates an unsigned candidate. It does not sign, upload, or rewrite the repository's index. When publishing, verify and publish the actual package first, then sign and verify the final index using the maintainer's signing process.
