# TOON：机型如何引用适配

[首页](README.zh.md) · [English](TOON.en.md) · [构建指南](BUILD.md)

TOON 是结构化文本。在这里，一台机型的 TOON 就是一张清单：它描述组件、依赖、源码位置、硬件参数和构建输入。读取它的工具负责检查这些内容并生成目录或调用构建器。

## 从哪两个文件开始

- [ONEXPLAYER 3：oxp3.toon](../devices/onexplayer/oxp3.toon)
- [X2 Mini：x2-mini.toon](../devices/onexplayer/x2-mini.toon)

这两份文件是各机型的维护入口。`models.toon` 和 `common-device-sources.toon` 是由机型资料生成的目录或视图。安装器源码快照中的机型 `manifest.json` 由 TOON 生成；适配仓库的既有检查工具也会读取 `manifest.json` 并据此核对机型说明。修改时先改机型 TOON，再用相应工具同步和检查这些视图。

生成目录里的 `complete` 描述该机型的构建资料状态；安装、维护、恢复和实机测试的结果按各自记录查看。

## 一份 TOON 里有什么

| 内容 | 用途 |
| --- | --- |
| `id`、`name` | 机型的标识和名称 |
| `components` | 组件、依赖、源码路径和说明 |
| `build` | 选择构建适配器，并列出该构建器需要的输入及来源锁 |
| `resource_model.resources` | 机型申请的资源 ID 和机型参数 |
| `resource_model.common_references` | 对共用设备资源和启动策略的引用 |

例如，OXP3 的 `resource_model.common_references` 中有下面一行；X2 Mini 也引用同一路径：

```text
"resource:bmi260-gyro",common/sensors/bosch/bmi260/gyro/source.toon
```

[BMI260 的 source.toon](../common/sensors/bosch/bmi260/gyro/source.toon)列出资源文件、大小、SHA-256、来源、许可证、硬件匹配和恢复信息。机型 TOON 中的路径相对于适配仓库根目录；`source.toon` 内文件路径相对于该资源目录。

两台机型还通过 `boot_policies` 引用 [GM7 的 components.toon](../common/ssd/acer/gm7/components.toon)。它是启动策略，和 `resources` 里的源码资源分开记录；实际使用按 SSD 身份匹配。

TOON 中的 `resources[4]{id,source}` 表示四行，每行按顺序填写资源 ID 与来源路径。增删行时，方括号里的数量也要对应修改。解析器会检查字段、数量和引用。

## 修改或复用

1. 从机型 TOON 找到要用的组件和资源，沿 `sources` 或 `source` 读取实际代码与许可证。
2. 共用硬件实现放在 `common/`，由机型引用；品牌共用和机型专属文件放在相应品牌或机型目录。
3. 记录实际硬件、系统和测试情况，维护源码清单及来源锁，然后运行仓库提供的检查。生成文件通过工具更新，不手工填充哈希。
4. 用 Boiler Installer 的快照与构建工具处理需要的输入；[构建指南](BUILD.md)列出了当前命令。

在安装器根目录运行 `python -B scripts/toon_catalog.py generate --catalog-root <adaptation-checkout>`，统一更新机型目录与共用引用视图。用同一入口的 `check` 核对。

TOON 写的是需要什么；运行、维护和恢复逻辑由构建器与对应组件提供。把 TOON 用作两台机型共同的购物清单时，同一路径只应收录一份，机型专属文件分别保留。

`models.toon` 的 schema 2 只列出带品牌的 `canonical_id` 和相对仓库根目录的 `source`。机型 TOON 的 `legacy_aliases` 明确保存旧指南及包标识；`device_record` 保存设备记录的其余元数据，名称和硬件匹配从 `runtime` 生成。`devices/*.json` 是旧协议视图，不会改写历史签名。在安装器根目录运行 `python -B scripts/toon_catalog.py generate --catalog-root <adaptation-checkout>` 更新视图，使用 `check --catalog-root <adaptation-checkout>` 验证。`resource_model` 的名称和硬件匹配由 `runtime` 投影，避免重复维护。

现有两台机型的共用引用视图由其 TOON 生成，并核验公共资源来源锁。`common-device-source-index.json` 是两机型兼容视图。
