# ONEXPLAYER single-copy installer

Local candidate 0.2.0-preview.20261105.6 embeds the ONEXPLAYER brand ZIP built from
FlightDan/Boiler-adaptations at `8d5053462aac703866866ca18b8f8635c5d0c007`.
The model TOON guides select the files included in the brand ZIP. Both guides use
the same bundled BE201 resource.

The installer lists both models from bundled metadata. Selecting a model extracts
its verified installation inventory into the normal package cache. Installation
retains the selected package through the same transaction used by imported model
packages, so maintenance and recovery can use it offline. The installer ZIP
contains a single brand ZIP and generic maintenance files.

To test on hardware, choose OXP3 or X2 Mini under **Bundled with installer**.
Available TOON components are selected unless explicitly opted out. Check Wi-Fi
and Bluetooth in SteamOS and exercise maintenance and recovery on the test target.
Physical-device acceptance remains pending. Keep the preparation directory on the
RAID volume on the test machine. This build is for local testing.
