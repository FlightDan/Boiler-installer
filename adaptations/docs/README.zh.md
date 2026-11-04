[Boiler Installer 项目](https://github.com/FlightDan/Boiler-installer) · [English](../README.md)

# Boiler Adaptations

这里收集 Boiler 的设备和机型适配。你可以查看已有机型的资料，复用共用硬件组件，也欢迎提交自己的适配。

| 文档 | 已适配机型 |
| --- | --- |
| [TOON 是怎么工作的](TOON.md)<br>[构建机型包和安装器](BUILD.md)<br>[设备索引与签名](REGISTRY.md)<br>[品牌与机型适配资料](ADAPTATIONS.md) | [ONEXPLAYER 3](../brands/onexplayer/oxp3/model.md)<br>[ONEXPLAYER X2 Mini / Mini PRO](../brands/onexplayer/x2-mini/model.md) |

<details open>
<summary>如何提交新适配</summary>

1. Fork 这个仓库。
2. 在对应品牌和机型目录添加适配；共用硬件资源放在 `common/`，通过机型 TOON 引用。
3. 写清楚机型、系统和内核版本，以及测试过的功能；附上能说明情况的截图或日志。
4. 提交 Pull Request。作者会使用 Agent 协助检查代码，再审阅和合并。

设备包发布时，作者会统一签署设备索引。详细流程和构建入口见上面的文档。

</details>

<details>
<summary>许可证</summary>

本仓库使用 [AGPL-3.0](../LICENSE)。部分源码和资源使用其他许可证，详见各资源目录和 [第三方声明](../THIRD_PARTY_NOTICES.md)。

</details>

<details>
<summary>签名与信任链</summary>

签名私钥由作者本人保存在本地并控制使用。安装器用受信任的公钥验证已签名的设备索引，再按索引核对设备包。[已发布的签名目录](https://github.com/FlightDan/Boiler-adaptations/releases/tag/catalog-v0.1.0-preview.1)和仓库根目录的开发索引分别维护，详见[设备索引与签名](REGISTRY.md)。

</details>
