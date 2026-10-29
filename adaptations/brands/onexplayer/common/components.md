# ONEXPLAYER 共用源码

`scripts/input_profile.py` 及两份模板按唯一 MCU 接口生成 InputPlumber 运行时配置。
现有识别要求 `1a86:fe00` 的接口 `02`；没有设备或存在歧义时不凭机型名称强行匹配。
完整组件另需已公开、与目标内核匹配的 hid-oxp 驱动及 capability map。

`scripts/gyro/` 保留 BMI260 源码、补丁、Makefile、服务和启动脚本。
它依赖精确内核、ACPI/I2C 和芯片识别、bmi270_core、固件及专用 InputPlumber。
归档不含 `.ko` 或专用 InputPlumber 二进制；新装必须重新校准，不能复制其他设备的零偏。

`gyro/gyro_binding.py` 是公开维护器使用的独立绑定/恢复助手，保存原驱动并在失败或
停止时恢复。现有维护准备流程生成调用该助手的启动/停止包装脚本。归档的旧
`load.sh` / `unload.sh` 则是直接 insmod/rmmod 的历史入口，不能用来替代维护器
的绑定恢复流程；其原始字节保留作版本来源参考。

这些脚本供 OXP3 和 X2 Mini 分别核实硬件后复用。品牌目录不解除任何组件的现有
硬件与版本检查，也不代表两个机型的全部底层接口相同。各机型建议和证据边界见
[OXP3](../oxp3/model.md) 与 [X2 Mini](../x2-mini/model.md)。
