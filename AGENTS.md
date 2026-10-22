# 给后续代理的说明

主人是 KiQi（GitHub：mi179）。他是学生，经常在有线、Wi-Fi 和手机 USB / USB-C 共享之间切换。这个仓库是「网络出口切换」。

他本人合并 PR。代理只开草稿 PR，不要合并。

## 偏好

- 用最稳、最底层、操作系统自带的办法。
  - Windows：IPv4 接口跃点。先 `Set-NetIPInterface`，失败再 `netsh interface ipv4 set interface`。
  - macOS：`networksetup -ordernetworkservices`。必须带上全部网络服务，不能把 VPN 服务从列表里丢掉。
  - Ubuntu：优先 NetworkManager 的 `ipv4.route-metric`，没有 NM 再 `ip route replace`。`ip route` 重启后可能丢失，预览里要写明。
- 不要后台常驻守护进程，除非缺了它就做不到。托盘只是菜单，不是守护进程。没有 systemd 服务。
- 没有界面里的明确确认，禁止改系统设置。先把将要改的跃点或服务顺序展示出来。每次修改都要能一键恢复，原始状态只在第一次修改前记一次。
- 界面和文档用简体中文。
- 校园网只有 IPv4。逻辑只改 IPv4，IPv6 只展示。
- 要和 mihomo / Clash Meta、sing-box、游戏加速器的 TUN，以及 VMware / Hyper-V 虚拟网卡共存。识别并标注，不要把它们选成出口，也不要改它们的跃点。说清楚：TUN 开着时普通程序的流量仍会进 TUN；切换物理网卡通常只影响代理自己的上游，以及没走 TUN 的程序。
- 不要为了提权把整个 GUI 用 root / 管理员跑起来。只在用户点击应用时按平台提权：Windows UAC、macOS 管理员密码、Ubuntu `pkexec`。
- 发布方式和 campusnet-guard 一样：GitHub Actions 打 Windows exe、macOS app、Ubuntu deb。推送 `v*` 标签才发布。

## 不要做的事

- 不要合并 PR。
- 不要静默改路由、DNS、代理，也不要关掉 TUN 或结束代理进程。
- 不要为了“修好上网”去卸载网卡、改注册表启动项或加开机服务。
- 提权进程只接受本程序生成的计划（改跃点、排服务顺序、改 NM metric 或替换默认路由）。不要让它执行任意命令。
