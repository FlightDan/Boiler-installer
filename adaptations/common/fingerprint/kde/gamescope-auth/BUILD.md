# Game Mode fingerprint authorization keyboard

The retained ELF uses KDE polkit-kde-agent-1 v6.7.3. The unpacked source in
`polkit-kde-agent-1-v6.7.3-source.tar.gz` is the exact KDE commit
`851bc2547ac6a9f0bdf072d642b17119906496ad`. Build with the accompanying
`gamescope-auth.patch` and the 2026-07-16 Arch Linux Archive toolchain.
Dependencies include CMake, Ninja, a C++20 compiler, Extra CMake Modules,
Qt 6.11.1, KDE Frameworks 6.28.0, PolkitQt 0.201.1 and gettext.

```sh
tar -xzf polkit-kde-agent-1-v6.7.3-source.tar.gz
cd polkit-kde-agent-1-6.7.3
git apply --check ../gamescope-auth.patch
git apply ../gamescope-auth.patch
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_TESTING=OFF
cmake --build build --target polkit-kde-authentication-agent-1
```

The patched window embeds Qt Virtual Keyboard for fingerprint enrollment
authorization in Gamescope. The model package installs the agent alongside
the managed fprintd runtime. The bridge runs it as the active Steam user.
The agent handles the password through normal Polkit. The tested OP3 image
has Qt Virtual Keyboard 6.11.1 and SteamOS 3.10 build 20260924.1000.
If the reviewed agent cannot start, enrollment can use the system agent.

The patch and the relevant KDE agent code are licensed under
[GPL-2.0-or-later](GPL-2.0-or-later.txt). The source archive includes the original notices.
