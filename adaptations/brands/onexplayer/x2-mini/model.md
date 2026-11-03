# ONEXPLAYER X2 Mini / Mini PRO 机型安装清单

日期：2026-10-29。本文由机型 manifest 的组件数据生成安装表；正文记录适用与恢复边界。

此目录是适配源码归档和安装指南，尚不是可执行的 Boiler DevicePackage。所有 `default` 为 false 表示此指南不向安装器注入默认选择；表内“推荐”供选型参考，不表示自动执行。脚本保持原公开提交的字节和检查，未改写硬件门槛。

## 安装清单

<!-- BEGIN GENERATED INSTALL LIST -->
| 组件 ID | 适配项目 | 建议 | 验证与适用边界 |
| --- | --- | --- | --- |
| fingerprint-unlock | 指纹解锁（实验功能） | 可选 | Default enabled for the confirmed BF63112 module; preserve user disable and Steam PIN fallback. Source: FlightDan/FingerprintUnlock-SteamOS@fde8fa62bbad0318a9a8be88cc77bb4c3b4f919e; public lock: common/fingerprint/public-bridge-lock.json. X2 reset electrical behavior is not verified; no reset GPIO is configured.；依赖：decky |
| slot-switch | KDE 槽位切换 / 系统回退 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| partition-mount | KDE 图形分区持久挂载 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| refind | rEFInd · 双系统图形启动菜单 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| opti2xmx | Opti2XMX · 每游戏 XMX / Opti 注入与版本管理 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：decky |
| gamescope-env | 游戏模式启动环境隔离 · 修复桌面切回黑屏 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| flathub-ustc | SteamOS 桌面模式 应用商店 · 中科大 Flathub 源（可选） | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| hdr | 游戏模式 HDR | 条件适用 | 复用 OXP3 HDR 机制及运行时，X2 专属目标映射 475/700；单独验证游戏模式与桌面模式。 |
| desktop-hdr | KDE 桌面 HDR 475/700 安全试用 | 推荐 | 复用 OXP3 HDR 机制及运行时，X2 专属目标映射 475/700；单独验证游戏模式与桌面模式。 |
| keyboard | KDE 屏幕键盘配置 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| input | 背键驱动 · M1/L4、M2/R4、Home/L5 | 推荐 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| multifunction | 多功能键映射为 R5 | 推荐 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：input |
| gyro | BMI260 陀螺仪（新装需校准） | 推荐 | Source reference only; verify BMI260 identity, kernel and InputPlumber compatibility before installation or calibration.；依赖：input |
| xe-dsb | OXP3 显示兼容修复 · 暂停 DSB | 条件适用 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| gm7-resume | GM7 休眠恢复 · 自动匹配目标 SSD | 条件适用 | Apply only after matching the target SSD identity; verify installation and recovery. |
| split-lock | Split-lock 报告洪泛修复 · 需要重启 | 条件适用 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| wifi | BE201 无线 c106 与蓝牙固件 | 条件适用 | Source reference only; verify the actual wireless device and firmware compatibility. |
| manager | SteamOS Manager 状态与错误修复 | 推荐 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| native-tdp | 原生 TDP 10–40W 控制 | 条件适用 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：manager |
| overlay | CPU/GPU/APU 功耗与 PVRAM 叠加层 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| fan-driver | OXP3 风扇与充电接口驱动 | 条件适用 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| oxp3-fan | OXP3 风扇 · 自动与曲线 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：fan-driver、decky |
| oxp3-cpu | OXP3 CPU · EPP 与分组限频 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：decky |
| charging | OXP3 Battery · 充电上限与模式 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：fan-driver、decky |
| oxp3-light | OXP3 Light · 三分区灯光控制 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：decky |
| legacy-runtime | 音量键与电量显示修复 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| windows-reboot | 重启进入 Windows · Decky 按钮与桌面快捷方式 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示；依赖：decky |
| decky | Decky Loader · 插件运行环境 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| waydroid | Waydroid r2 · Android 宿主适配 | 可选 | 完整复用 OXP3 公共适配；系统与实际接口依赖独立检查，机型不匹配只提示。 |
| base-system | SteamOS、按芯片匹配的固件与维护运行时 | 必装 | 由 Boiler 通用流程提供；核对实际系统和内核版本。 |
| pl4-175w | PL4 175W（用户指定需求） | 待补实现 | 已有用户测试反馈；未找到独立公开脚本，原值备份、应用与恢复入口待补，不能以 PL1/PL2 代替。 |
| lsfg-vk | LSFG-VK | 可选 | 默认不选；需要 Decky、用户购买 Lossless Scaling 并按插件流程安装；依赖：decky |
| speaker-route | 扬声器声道修复 | 可选 | Source reference only; verify the actual audio device and routing compatibility. |
| xevmafix | xevma 显示模块修复 | 可选 | 复用 OXP3 公开载荷；核对实际系统与依赖，硬件验收单独记录。 |
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
