# Windows 使用说明

1. 解压 `net-egress-switcher-windows.zip`。
2. 双击 `1-点我启动-网络出口切换.exe`。不要右键「以管理员身份运行」。
3. 看列表里的有线、Wi-Fi、USB 共享。点一张网卡，下面会出现将要修改的接口跃点。
4. 确认预览之后，点「设为默认出口」，再在 UAC 窗口里同意。
5. 想回到第一次修改前，点「恢复到更改前」。
6. 「自动」会重新打开 Windows 的自动跃点。

命令行在 `9-命令行-egress-switcher.exe`：

```text
egress-switcher list
egress-switcher set Wi-Fi
egress-switcher set Wi-Fi --yes
egress-switcher restore --yes
egress-switcher automatic --yes
```

不加 `--yes` 只会打印预览。

如果开着 Clash Meta、mihomo、sing-box 或游戏加速器的 TUN，普通程序的流量仍会进那张 TUN。这个程序不会关闭它。
