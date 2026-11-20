[Boiler Installer](https://github.com/FlightDan/Boiler-installer) · [中文](docs/README.zh.md)

# Boiler Adaptations

This repository collects device and model adaptations for Boiler. Browse the existing models, reuse shared hardware components, or contribute an adaptation of your own.

| Model | Adaptation guide | Edit components | Model source |
| --- | --- | --- | --- |
| ONEXPLAYER 3 | [Guide](brands/onexplayer/oxp3/model.md) | [oxp3.toon](devices/onexplayer/oxp3.toon) | [Source directory](brands/onexplayer/oxp3/) |
| ONEXPLAYER X2 Mini / Mini PRO | [Guide](brands/onexplayer/x2-mini/model.md) | [x2-mini.toon](devices/onexplayer/x2-mini.toon) | [Source directory](brands/onexplayer/x2-mini/) |

[TOON guide](docs/TOON.en.md) · [Build guide](docs/BUILD.en.md) · [Device index and signatures](docs/REGISTRY.en.md) · [Repository layout (中文)](docs/ADAPTATIONS.md)

<details open>
<summary>Contribute an adaptation</summary>

[New handheld or individual component: contribution guide and PR evidence](https://github.com/FlightDan/Boiler-installer/blob/main/docs/CONTRIBUTING_ADAPTATIONS.md)

1. Fork this repository.
2. Add the adaptation under the right brand and model. Put reusable hardware resources in `common/` and reference them from the model's TOON.
3. Include the model, operating system and kernel versions, what you tested, and screenshots or logs that show the result.
4. Open a pull request. The author uses an agent to help check the code, then reviews and merges it.

When publishing packages, the author signs the device index. The documents above explain the workflow and build tools.

</details>

<details>
<summary>License</summary>

This repository uses [AGPL-3.0](LICENSE). Some sources and resources use other licenses; see their directories and the [third-party notices](THIRD_PARTY_NOTICES.md).

</details>

<details>
<summary>Signatures and trust</summary>

The author keeps and controls the signing private key locally. The installer verifies a signed device index with a trusted public key, then uses that index to check packages. The [published signed catalog](https://github.com/FlightDan/Boiler-adaptations/releases/tag/catalog-v0.1.0-preview.1) and the generated development index are maintained separately; see [Device index and signatures](docs/REGISTRY.en.md).

</details>
