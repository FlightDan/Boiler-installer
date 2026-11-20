# 机型导航与构建元数据目录

两台机型的维护入口继续由 `models.toon` 索引。英文和中文首页分别链接机型说明、权威 TOON 和机型源码目录。`docs/ADAPTATIONS.md` 维护目录说明，根目录同名文件保留旧链接入口。

| 原路径 | 当前路径 | 读取位置 |
| --- | --- | --- |
| `xevma-kmod-source-lock.json` | `metadata/locks/xevma-kmod-source-lock.json` | OXP3 与 X2 Mini 的 `build.materials`、资源构建输入清单 |
| `retained-build-inputs.json` | `metadata/build-inputs/retained-build-inputs.json` | 保留构建输入的来源记录 |

两个文件按原字节迁移。机型申请的共用设备资源仍按原资源 ID 解析，GM7 策略继续匹配目标 SSD。此次整理的验收范围是文档链接、TOON 路径、生成视图和来源库存。安装、维护、恢复与实机行为沿用各版本已有验收记录。
