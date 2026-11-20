# S4 自动协调 SteamOS 交换文件

两款 ONEXPLAYER 机型的 TOON 申请 S2/S4 0.1.2，固定到
`FlightDan/SteamOS-S2-S4-switcher@10c328de008fe8c476c3e72ae1859e2f773d6e48`。原厂 SteamOS 的
`home-swapfile.swap` 仍按系统配置启动。Decky 用户可直接开启 S4；
切换器只在受控休眠事务内临时协调原厂交换文件，恢复后验证它重新启用。

未确认归属的交换设备以及已有休眠镜像仍会阻挡 S4。现有偏好、构件 ID、
Decky 注册名和安装目标延续 0.1.1。物理休眠和唤醒按机型另行验收。
