# ONEXPLAYER X2 Mini / Mini PRO 机型安装清单

日期：2026-10-29。本文由机型 manifest 的组件数据生成安装表；正文记录适用与恢复边界。

此目录是适配源码归档和安装指南，尚不是可执行的 Boiler DevicePackage。所有 `default` 为 false 表示此指南不向安装器注入默认选择；表内“推荐”供选型参考，不表示自动执行。脚本保持原公开提交的字节和检查，未改写硬件门槛。

## 安装清单

<!-- BEGIN GENERATED INSTALL LIST -->
| 组件 ID | 适配项目 | 建议 | 验证与适用边界 |
| --- | --- | --- | --- |
| base-system | SteamOS、按芯片匹配的固件与维护运行时 | 必装 | 由 Boiler 通用流程提供；核对实际系统和内核版本。 |
| input | 背键 / MCU 输入适配 | 推荐 | 唯一 MCU 接口满足后推荐；需匹配公开驱动、capability map；新 Boiler 包未验收。 |
| multifunction | 多功能键 | 推荐 | 逐实体键确认映射，不能仅凭品牌套用模板；依赖：input |
| gyro | BMI260 陀螺仪 | 条件适用 | 核对 ACPI/I2C、0x27 芯片 ID、精确内核和配套 InputPlumber；新装重新校准；依赖：input |
| desktop-hdr | KDE 桌面 HDR 475/700 安全试用 | 推荐 | 显式试用；固定 KWin/ABI、SDC4301 内屏、无启用外屏；10 秒未确认恢复；需配套完整原生运行时。 |
| hdr | 游戏模式 HDR | 条件适用 | 现有 OXP3 游戏配置为 472/1100，不能作为 X2 475/700 实现；需单独核实面板和方案。 |
| gm7-resume | GM7 休眠恢复 | 条件适用 | 只有实际 SSD 命中适用条件时安装；验证休眠与恢复。 |
| split-lock | Split-lock 报告洪泛修复 | 条件适用 | 确认存在报告洪泛且启动文件受支持时使用；重启生效，按原事务恢复。 |
| wifi | 按芯片选择无线固件 | 条件适用 | 按实际无线芯片选择 Boiler 通用固件；不固定为所有机器均需 BE201。 |
| overlay | 功耗 / PVRAM 叠加层 | 条件适用 | 仅公开常规遥测参考；核对 X2 实际接口、单位与读数。 |
| manager | SteamOS Manager 修复 | 条件适用 | 核对实际 Manager 和硬件接口；不继承 OXP3 验收结论。 |
| native-tdp | 原生 TDP | 条件适用 | 单独确认 X2 处理器、RAPL/Manager 接口与范围；不套用 OXP3 的 10–40W；依赖：manager |
| pl4-175w | PL4 175W（用户指定需求） | 待补实现 | 已有用户测试反馈；未找到独立公开脚本，原值备份、应用与恢复入口待补，不能以 PL1/PL2 代替。 |
| decky | Decky Loader | 可选 | Boiler 通用部署入口；所选插件的依赖。 |
| slot-switch | KDE 槽位切换 / 系统回退 | 可选 | A/B 布局及系统工具满足时可选；真实切换与重启分别验证。 X2 新装与恢复需分别验证。 |
| partition-mount | KDE 图形分区持久挂载 | 可选 | 共享 home/NTFS 布局核对后可选；本次归档基线不代表工作区后续 NTFS 修复。 X2 新装与恢复需分别验证。 |
| gamescope-env | 游戏模式启动环境隔离 · 修复桌面切回黑屏 | 可选 | 仅用于已确认的游戏模式环境问题；保留原环境恢复。 X2 新装与恢复需分别验证。 |
| keyboard | KDE 屏幕键盘配置 | 可选 | 按 KDE 会话与目标软件检查；验证实际输入及恢复。 X2 新装与恢复需分别验证。 |
| flathub-ustc | SteamOS 桌面模式 应用商店 · 中科大 Flathub 源（可选） | 可选 | 用户主动选择；保留原 remote，当前归档不补写尚未接入的换源流程。 X2 新装与恢复需分别验证。 |
| refind | rEFInd · 双系统图形启动菜单 | 可选 | 独立启动管理器入口；脚本不归机型适配所有，核对 EFI 与双系统布局。 X2 新装与恢复需分别验证。 |
| windows-reboot | 重启进入 Windows · Decky 按钮与桌面快捷方式 | 可选 | 核对 Windows EFI 启动项、授权入口及恢复。 X2 新装与恢复需分别验证；依赖：decky |
| opti2xmx | Opti2XMX · 每游戏 XMX / Opti 注入与版本管理 | 可选 | 使用独立公开插件与运行库；需要受支持 Intel GPU，逐游戏验证。 X2 新装与恢复需分别验证；依赖：decky |
| waydroid | Waydroid r2 · Android 宿主适配 | 可选 | 可选；另需公开 r2 资源，核对内核与 Android 容器条件。 X2 新装与恢复需分别验证。 |
| lsfg-vk | LSFG-VK | 可选 | 默认不选；需要 Decky、用户购买 Lossless Scaling 并按插件流程安装；依赖：decky |
| xe-dsb | OXP3 DSB 显示修复 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
| fan-driver | OXP3 风扇/充电驱动 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
| oxp3-fan | OXP3 风扇插件 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
| charging | OXP3 充电插件 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
| oxp3-light | OXP3 灯光插件 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
| oxp3-cpu | OXP3 CPU 专用配置 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
| legacy-runtime | OXP3 音量/电量旧运行时 | 排除 | 不直接用于 X2 Mini；同硬件证据、独立组件及恢复验证完成后另行接入。 |
<!-- END GENERATED INSTALL LIST -->

## 适用与依赖

当前可整理的 X2 专属实现是 `scripts/desktop-hdr/` 的四个公开脚本；`hdr_trial.py` 与 `x2mini_device.py` 保持同级。品牌共用输入/陀螺仪源码见 [ONEXPLAYER 共用脚本](../common/scripts/)，跨品牌辅助源码见 [共用脚本](../../../common/scripts/)。

桌面试用核对规范化厂商与完整机型名、KDE Wayland、SDC4301 内屏、外接屏状态和配套软件。固定运行时前缀为 `/opt/oxp3-desktop-gamma22/6.7.3-1.6`，还需匹配 KWin/libkwin、`compatible-packages.txt`、`oxp3-hdr-pattern`、用户 runtime `disable-hdr.py` 和系统 KDE 工具。只有四个源码文件不能完成运行时安装。

输入配置核对唯一 `1a86:fe00` MCU 接口 `02`；完整组件还需要已公开的匹配驱动与 capability map。陀螺仪需核对 ACPI `10EC5280:00`、I2C `0x68`、芯片 ID `0x27`、精确内核、`bmi270_core`/固件及 InputPlumber `0.78.0` 的配套运行时。重装重新校准。

## 验证与恢复

2026-10-28 用户确认：此前公网 SSH 测试的 X2 Mini 已测试，机组反馈一切正常。该反馈保留为用户反馈，不扩写为本目录每个组件的独立日志、亮度仪器测量或新 Boiler 设备包验收。

桌面 HDR 使用显式试用、后台 10 秒未确认恢复、启动前恢复和卸载互锁；确认前必须有真实 HDR10 首帧。475/700 为目标映射，四块图案不替代亮度测量。游戏 HDR 的 X2 参数尚未单独实现。

PL4 175W 是用户明确需求且有测试反馈，本次受审公开源码未找到独立实现。后续必须补齐原值备份、接口适用条件、应用、重启持久化和恢复，再制作组件；当前不得当作可安装项。

其余候选按硬件与故障逐项验证。OXP3 专属风扇、充电、灯光、CPU 配置、DSB 和旧运行时保持排除。新 Boiler X2 设备包、新装、维护升级、重启与必要恢复未在本次整理中验收。

## 源码来源

<!-- BEGIN GENERATED SOURCE -->
适配源码来源：`FlightDan/OneXPlayer3-SteamOS`，固定提交 `72f864e0498c10366493c74608c5ffed62cc99ca`。逐文件原路径、大小和 SHA-256 见 [源码库存](../../../source-lock.json)。
<!-- END GENERATED SOURCE -->

保留上游及第三方许可证。暂缓公开的动态功耗研究、设备记录、私钥、二进制驱动和完整运行时均未纳入。
