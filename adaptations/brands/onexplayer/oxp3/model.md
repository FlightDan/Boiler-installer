# ONEXPLAYER 3 机型安装清单

本页由机型 TOON 生成。源码指南不授权自动安装；构建完整度、公开发布与实机验收分别核验。

## 安装清单

<!-- BEGIN GENERATED INSTALL LIST -->
| 组件 ID | 适配项目 | 建议 | 验证与适用边界 |
| --- | --- | --- | --- |
| slot-switch | KDE 槽位切换 / 系统回退 | 可选 | A/B 布局及系统工具满足时可选；真实切换与重启分别验证。 |
| partition-mount | KDE 图形分区持久挂载 | 可选 | 共享 home/NTFS 布局核对后可选；本次归档基线不代表工作区后续 NTFS 修复。 |
| refind | rEFInd · 双系统图形启动菜单 | 可选 | 独立启动管理器入口；脚本不归机型适配所有，核对 EFI 与双系统布局。 |
| opti2xmx | Opti2XMX · 每游戏 XMX / Opti 注入与版本管理 | 可选 | 使用独立公开插件与运行库；需要受支持 Intel GPU，逐游戏验证；依赖：decky |
| gamescope-env | 游戏模式启动环境隔离 · 修复桌面切回黑屏 | 可选 | 仅用于已确认的游戏模式环境问题；保留原环境恢复。 |
| flathub-ustc | SteamOS 桌面模式 应用商店 · 中科大 Flathub 源（可选） | 可选 | 用户主动选择；保留原 remote，当前归档不补写尚未接入的换源流程。 |
| hdr | 游戏模式 HDR · Gamma 2.2 | 可选 | OXP3 专用 472/1100 配置，精确面板及 Gamescope 3.16.30-1；不能套用 X2 参数。 |
| desktop-hdr | KDE 桌面 HDR（OXP3 472/1100） | 可选 | OXP3 传统桌面映射需固定 KWin/ABI 和完整原生运行时；X2mini 的安全试用入口按机型检查跳过，不作为 OXP3 功能。 |
| keyboard | KDE 屏幕键盘配置 | 可选 | 按 KDE 会话与目标软件检查；验证实际输入及恢复。 |
| input | 背键驱动 · M1/L4、M2/R4、Home/L5 | 推荐 | 核对唯一 1a86:fe00 MCU 接口 02；另需原公开 hid-oxp 模块和 capability map。 |
| multifunction | 多功能键映射为 R5 | 推荐 | 沿用现有按键模板；逐键验证实体键位；依赖：input |
| gyro | BMI260 陀螺仪（新装需校准） | 推荐 | 需精确内核、BMI260 芯片识别、bmi270_core/固件及配套 InputPlumber；新装重新校准；依赖：input |
| xe-dsb | OXP3 显示兼容修复 · 暂停 DSB | 条件适用 | 仅 OXP3 对应显示问题；保留现有精确系统/内核限制。 |
| gm7-resume | GM7 休眠恢复 · 自动匹配目标 SSD | 条件适用 | 只有实际 SSD 命中适用条件时安装；验证休眠与恢复。 |
| split-lock | Split-lock 报告洪泛修复 · 需要重启 | 条件适用 | 确认存在报告洪泛且启动文件受支持时使用；重启生效，按原事务恢复。 |
| wifi | BE201 无线 c106 与蓝牙固件 | 条件适用 | 按实际无线芯片选择 Boiler 通用固件；不固定为所有机器均需 BE201。 |
| manager | SteamOS Manager 状态与错误修复 | 推荐 | 核对实际 Manager 接口和软件版本；不取消已有硬件检查。 |
| native-tdp | 原生 TDP 10–40W 控制 | 条件适用 | 仅保留已公开常规 RAPL 实现；10–40W 不作为其他机型范围；依赖：manager |
| overlay | CPU/GPU/APU 功耗与 PVRAM 叠加层 | 可选 | 按实际 CPU/GPU 接口验证读数，缺失指标保持缺失。 |
| fan-driver | OXP3 风扇与充电接口驱动 | 条件适用 | OXP3 专用寄存器/驱动布局；另需匹配公开驱动载荷。 |
| oxp3-fan | OXP3 风扇 · 自动与曲线 | 可选 | OXP3 专用风扇界面与曲线；另需原公开插件载荷；依赖：fan-driver、decky |
| oxp3-cpu | OXP3 CPU · EPP 与分组限频 | 可选 | 仅保留公开常规控制；未纳入工作区新动态功耗研究；依赖：decky |
| charging | OXP3 Battery · 充电上限与模式 | 可选 | OXP3 专用电池/充电接口；另需原公开插件载荷；依赖：fan-driver、decky |
| oxp3-light | OXP3 Light · 三分区灯光控制 | 可选 | 核对现有 HID 报文格式；保留上游许可证和恢复行为；依赖：decky |
| legacy-runtime | 音量键与电量显示修复 | 可选 | 保留原音量/电量运行时依赖，不自动迁移到其他机型。 |
| windows-reboot | 重启进入 Windows · Decky 按钮与桌面快捷方式 | 可选 | 核对 Windows EFI 启动项、授权入口及恢复；依赖：decky |
| decky | Decky Loader · 插件运行环境 | 可选 | 使用 Boiler 通用部署入口；依赖插件选中时一起选择。 |
| waydroid | Waydroid r2 · Android 宿主适配 | 可选 | 可选；另需公开 r2 资源，核对内核与 Android 容器条件。 |
<!-- END GENERATED INSTALL LIST -->

## 源码来源

<!-- BEGIN GENERATED SOURCE -->
适配源码来源：`FlightDan/OneXPlayer3-SteamOS`，固定提交 `72f864e0498c10366493c74608c5ffed62cc99ca`。逐文件原路径、大小和 SHA-256 见 [源码库存](../../../source-lock.json)。
<!-- END GENERATED SOURCE -->
