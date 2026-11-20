# ONEXPLAYER 指纹键盘、S2/S4 和微码事务

OXP3 和 X2 Mini 的两份机型 TOON 申请共用指纹插件 0.1.11。桥接来源为
`FlightDan/FingerprintUnlock-SteamOS@8be889082ebf4e8df6ad706857d2f59bd701d070`。指纹事务部署
独立的 KDE 授权代理、配置和许可证，并保留各机型的指纹驱动、PAM 与
fprintd 库加载方式。代理仅在游戏模式处理授权，密码继续由系统 Polkit 处理。
包内附有对应 KDE 6.7.3 源码、补丁和构建说明。普通升级和恢复沿用已有
指纹构件 ID。旧包仍可校验和恢复。

两款机型共用 S2/S4 0.1.1，固定到
`FlightDan/SteamOS-S2-S4-switcher@f95e486beedcbe05b4b4797803953da95404b580`。
Decky 注册名及后端 RPC 统一为 `Boiler S2-S4 switcher`，面板显示 `S2/S4`。
构件 ID、目录、待机路由和休眠偏好路径保持原样。Decky 按名称记录的
隐藏、禁用和排序偏好可能需要重新设置。机型默认选择插件，S4 仍由用户自行开启。

微码 TOON 请求 `onexplayer-intel-microcode-v1`，最低引擎 0.1.7。两个机型复用同一份
Intel Panther Lake 载荷及许可证，按 CPU 签名和平台匹配，默认留空。安装和
维护使用早期 initramfs 事务，恢复原启动字节及权限。既有实机证据覆盖 OXP3 B 槽
由 0x11a 早期更新到 0x11d。X2 Mini 及物理 A 槽的微码验收仍待执行。

OP3 SteamOS 3.10 授权对照测试中，系统代理没有弹出键盘。相同录入流程使用已锁定
代理时，授权窗口自动从 229 像素扩展到 544 像素。取消后已录入指纹仍为 0。
指纹采集、实际解锁和 S4 唤醒需要独立实机验证。
