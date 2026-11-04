# Branded model identity migration — 2026-11-04

Baseline: `aea9ee329aee01896332f7156483436580acdb2a` in `FlightDan/Boiler-adaptations`.

The two authoritative model TOON records retain their historical guide and package IDs as explicit aliases. Their branded canonical IDs are listed in `models.toon` schema 2. The existing device JSON records are generated legacy protocol views; the two existing records retain their baseline bytes. Runtime names and hardware matching also supply the resource-model projection. Signed index, package and maintenance bytes are not rewritten.

Source-only validation: model-identity generation and check, legacy semantic regression, conflicting aliases, identical model slugs across brands, stale generated views, missing source and duplicate hardware metadata are checked. Common-device source checks and source inventory checks pass. The installer adaptation import record is historical evidence only; current authoritative source and the current model-source-files inventory are validated through their existing independent checks. The historical record is unchanged. Complete source test suite: 85 tests passed with the explicitly selected clean installer protocol checkout.

Installation, maintenance, upgrade, recovery, signed releases, published artifact download, and hardware acceptance: not performed by this source-only migration. Installer consumption of schema 2 and deployment integration require a separately reviewed migration and verification. No new signing identity or release was produced.
