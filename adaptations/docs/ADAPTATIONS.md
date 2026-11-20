# Boiler 品牌与机型适配

[新机型与单部件贡献指南（最低实机要求、开发步骤和 PR 模板）](https://github.com/FlightDan/Boiler-installer/blob/main/docs/CONTRIBUTING_ADAPTATIONS.md)

这里按品牌、机型整理已有公开适配源码，并记录应该选择哪些组件。

| 要做的事 | 从哪里开始 |
| --- | --- |
| 修改某台机型申请的部件、依赖或默认选项 | [OXP3 TOON](../devices/onexplayer/oxp3.toon) 或 [X2 Mini TOON](../devices/onexplayer/x2-mini.toon) |
| 查看机型组件说明及适用条件 | [OXP3 安装清单](../brands/onexplayer/oxp3/model.md) 或 [X2 Mini 安装清单](../brands/onexplayer/x2-mini/model.md) |
| 修改机型专属实现 | [OXP3](../brands/onexplayer/oxp3/) 或 [X2 Mini](../brands/onexplayer/x2-mini/) |
| 修改 ONEXPLAYER 品牌共用实现 | [brands/onexplayer/common](../brands/onexplayer/common/) |
| 修改跨品牌复用的设备资源 | [common](../common/) 下对应的硬件目录 |

### 两台机型实际引用的共用设备资源

两台机型的 `resource_model.common_references` 包含两组条目。`resources` 列出前四项共用源码资源，`boot_policies` 列出 GM7 启动策略。两组条目的 `source` 都相对于适配仓库根目录。打开某个 `source.toon` 后，其中的 `files[].path` 相对于该文件所在目录。其他组件及机型专属路径见各自 TOON 的 `components` 和 `build`。

| 设备 | TOON 入口 |
| --- | --- |
| Intel Xe VMA | [GPU 修复资源](../common/gpu/intel/xe/xevmafix-724/source.toon) |
| Intel BE201 | [无线固件资源](../common/network/intel/be201/source.toon) |
| Realtek ALC245 | [扬声器路由资源](../common/audio/realtek/alc245/speaker-route/source.toon) |
| Bosch BMI260 | [陀螺仪资源](../common/sensors/bosch/bmi260/gyro/source.toon) |
| Acer GM7 | [SSD 启动策略](../common/ssd/acer/gm7/components.toon) |

例如，修改 GM7 修复时，从上表的启动策略进入；调整哪台机型提供该修复时，修改对应机型 TOON 的 `boot_policies`。实际应用时仍需匹配目标 SSD。

### 构建与生成文件

- 两份 TOON 在 `build.materials` 引用同一份 [Xe VMA 转换源码锁](../metadata/locks/xevma-kmod-source-lock.json)。[保留输入记录](../metadata/build-inputs/retained-build-inputs.json)列出可重建所需的外部材料。
- [models.toon](../models.toon) 提供机型 TOON 索引。[adaptations.json](../adaptations.json) 和机型目录中的 `manifest.json` 供工具读取。修改组件时先改对应机型 TOON，再按 [TOON 指南](TOON.md) 更新生成视图。
- [源码导入计划](../source-plan.json) 和[历史来源库存](../source-lock.json)用于核对固定公开提交中的原始文件。生产设备发现与签名流程见[设备索引与签名](REGISTRY.md)。

## 检查与更新

使用 Python 3.11 或更新版本，检查库存、Python 语法、组件引用、依赖及生成表：

```sh
python scripts/adaptations.py check
python -m unittest discover -s tests -p 'test_adaptations.py' -v
```

更新源码时先审查 `source-plan.json` 的固定公开提交和逐文件白名单，再指定该公开
仓库的独立 checkout；准备器读取 Git 对象，不复制其未提交文件，也不执行适配脚本：

```sh
python scripts/adaptations.py prepare --source-checkout /explicit/public/OneXPlayer3-SteamOS
```

该命令先从公开 Git 对象重建库存并与不可变的 `source-lock.json` 完整比对；一致后才写入源码，同步机型来源字段并刷新两份 MD 的安装表及来源段落。BMI260 的历史路径通过迁移记录映射到共用资源，准备器恢复共用路径而不重建旧路径。库存不能手填哈希代替提取；检查器拒绝遗漏库存的脚本。

驱动、固件、插件归档、KWin 和其他原生运行时继续由既有准备和发布流程提供。
后续若将这些源码接成可执行机型包，再同步安装器、维护器、最终载荷和实机验收。
本次整理状态见 [整理记录](RELEASE_0.1.0-layout.1.md)。
