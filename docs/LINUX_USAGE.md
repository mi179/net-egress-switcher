# Ubuntu 使用说明

安装：

```bash
sudo apt install ./net-egress-switcher_*_all.deb
```

然后从应用菜单打开「网络出口切换」，或运行 `egress-switcher-gui`。不要用 `sudo` 打开窗口。点「设为默认出口」时，系统会用 `pkexec` 单独要一次管理员密码。

源码运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"
egress-switcher
```

Ubuntu 桌面一般由 NetworkManager 管理网卡。程序改的是连接上的 `ipv4.route-metric`，数字更小的优先，然后重新应用该连接，网卡可能闪断一下。自动值是 `-1`。

没有 NetworkManager 的网卡会退回 `ip route replace`。这种改法重启后可能消失，预览里会写明，恢复要在重启前做。

命令：

```bash
egress-switcher list
egress-switcher set wlp2s0
egress-switcher set wlp2s0 --yes
egress-switcher restore --yes
egress-switcher automatic --yes
```

依赖：`python3`、`python3-tk`、`python3-pil`、`iproute2`、`pkexec`。建议安装 `network-manager`。
