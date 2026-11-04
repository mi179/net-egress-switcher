# macOS 使用说明

安装包是临时签名，没有 Apple 公证。第一次打开时，在 Finder 里按住 Control 点「网络出口切换」，再选「打开」。

1. 打开应用。不要用 `sudo` 启动。这一版没有菜单栏图标，避免后台线程启动 AppKit 导致一打开就崩溃。
2. 列表是「系统设置 → 网络」里的服务顺序。点一张物理网卡，预览会给出完整的新旧顺序。
3. 点「设为默认出口」，输入管理员密码。
4. 「恢复到更改前」把顺序放回本程序第一次切换之前。

也可以用源码里的命令：

```bash
egress-switcher list
egress-switcher set "Wi-Fi"
egress-switcher set "Wi-Fi" --yes
egress-switcher restore --yes
```

iPhone USB 会标成 USB 共享。名字像「USB 10/100/1000 LAN」的是有线网卡。

TUN（utun）开着时，`route -n get default` 仍可能指向它。切换服务顺序不会关闭代理。
