# S4 插件 0.1.6 与上游版本解析

OXP3 和 X2 Mini 的 TOON 都申请同一份 `boiler:s2s4` 载荷。默认选择来自各机型的 TOON，Decky 中的 S4 功能仍由用户手动开启。

构建准备时使用 `scripts/refresh-s4-release.py --git <Git可执行文件> --upstream-checkout <官方插件检出>`。检出当前 GitHub Latest 标签对应的上游提交后，刷新器从官方稳定 Release 确认标签，校验附件大小和 SHA-256，并将 ZIP 的每个成员与 Git 提交的对应字节核对。然后生成共用运行时、安装清单和来源锁。更新后运行 `scripts/refresh-model-source-inventory.py --write`，提交生成结果，再用该权威适配提交准备机型快照和包。安装和维护按快照校验后使用固定载荷。

[0.1.6 Release](https://github.com/FlightDan/SteamOS-S2-S4-switcher/releases/tag/v0.1.6) 固定上游提交 `edfa430cbed63f95719e6f99ef5b67559f737f3c`。公开 ZIP 为 36,564 字节，SHA-256 为 `378521d3ba63ea7beac6462d49e73c9698d02577786c0c4b9d1f8f38cd0afc77`，已从 GitHub 匿名下载复验。

本次独立插件发布与已发布签名机型索引分开。后续机型包以各自版本和签名验收。
