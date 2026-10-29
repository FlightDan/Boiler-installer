# Boiler 品牌与机型适配

这里按品牌、机型整理已有公开适配源码，并记录应该选择哪些组件。

- [ONEXPLAYER 3 安装清单](brands/onexplayer/oxp3/model.md)
- [ONEXPLAYER X2 Mini / Mini PRO 安装清单](brands/onexplayer/x2-mini/model.md)
- [跨品牌共用源码说明](common/components.md)
- [ONEXPLAYER 共用源码说明](brands/onexplayer/common/components.md)

```text
common/scripts/                       跨品牌辅助源码
brands/onexplayer/common/scripts/     同品牌输入、陀螺仪源码
brands/onexplayer/oxp3/                OXP3 清单和专属源码
brands/onexplayer/x2-mini/             X2 Mini 清单和专属源码
adaptations.json                      源码目录索引
source-plan.json                      明确的公开源码导入白名单
source-lock.json                      生成的来源、大小、SHA-256 库存
```

每个机型包含 `model.md`、`manifest.json` 和 `scripts/`。机型 manifest 的种类是
`ModelInstallGuide`，用于结构化组件说明；不是安装协议的 `DevicePackage`。
`adaptations.json` 不参与生产设备发现，不替代 `device-index.json` 的签名信任链。
源码归档还没有接入新的安装、升级或恢复执行路径。

安装表使用 manifest 生成，避免表中的依赖、建议和状态各维护一份。源代码保持固定
公开提交的原始字节，组件原路径见库存；归档后的相对位置不是原部署目录。不要从
这里直接运行旧组合安装器，实际部署继续使用对应已发布版本及完整载荷。
跨品牌共用表示源码可复用，每个机型仍需确认系统、内核、接口和恢复条件。

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

该命令重新提取源码、生成 `source-lock.json`，同步机型来源字段并刷新两份 MD 的安装表及来源段落。库存不能手填
哈希代替提取。删除或移动白名单项时，应同时明确处理旧源码文件；检查器拒绝遗漏库存的脚本。

驱动、固件、插件归档、KWin 和其他原生运行时继续由既有准备和发布流程提供。
后续若将这些源码接成可执行机型包，再同步安装器、维护器、最终载荷和实机验收。
本次整理状态见 [整理记录](docs/RELEASE_0.1.0-layout.1.md)。
