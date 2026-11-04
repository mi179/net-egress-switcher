<p align="center"><a href="#zh">中文</a> · <a href="#en">English</a></p>

<a id="zh"></a>

# 网络出口切换

在 Windows、Mac 和 Ubuntu 上，选一张网卡当作上网出口。可以在有线、Wi-Fi 和手机 USB 共享之间切换。

**点选网卡、看预览，都不会改你的电脑。** 只有你点了按钮，并且在系统弹出的授权窗口里同意之后，才会修改。取消授权，系统保持原样。

![主窗口（演示数据，不是本机网卡）](docs/screenshots/main-window-demo.png)

## 下载

到 [Releases 页面](https://github.com/mi179/net-egress-switcher/releases) 下载。当前是 **v0.1.1 预发布**，还没有在真机上验收。请先看下面的安装和用法，试用时出了问题请 [开一个 Issue](https://github.com/mi179/net-egress-switcher/issues)。

| 系统 | 下载哪个文件 |
| --- | --- |
| Windows 10/11 | `net-egress-switcher-windows.zip` |
| Mac（Apple 芯片，M1 及更新） | `net-egress-switcher-macos-apple-silicon.dmg`，或同名的 `.zip` |
| Mac（Intel） | `net-egress-switcher-macos-intel.dmg`，或同名的 `.zip` |
| Ubuntu | `net-egress-switcher_0.1.1_all.deb` |

## 安装

### Windows

1. 解压 zip。
2. 双击 `1-点我启动-网络出口切换.exe` 打开窗口。不要右键「以管理员身份运行」整个程序。
3. 命令行用同一个文件夹里的 `9-命令行-egress-switcher.exe`。

### macOS

1. 打开 dmg，把「网络出口切换」拖进「应用程序」。用 zip 的话，解压后得到 `网络出口切换.app`。
2. 这个应用没有苹果公证。第一次打开时，系统可能提示已损坏或无法验证开发者。请在 Finder 里按住 Control 点这个应用，选「打开」，再在对话框里点「打开」。
3. 不要用 sudo 启动窗口。Mac 上没有菜单栏图标：托盘会在后台线程启动 AppKit，一打开就崩溃，所以这一版不放。

### Ubuntu

在 deb 所在的文件夹里执行：

```bash
sudo apt install ./net-egress-switcher_0.1.1_all.deb
```

装好后可以在应用菜单里找「网络出口切换」，也可以在终端运行 `egress-switcher`。`net-egress-switcher` 是同一个命令。

## 怎么用窗口

1. 打开程序。列表里是这台电脑的网卡：有线、Wi-Fi、USB 共享，以及认出来的虚拟网卡和代理 TUN。IPv6 只是一行说明，不会被改。
2. 点一张物理网卡。下面的「即将修改」会写出准备改什么。这时路由还没变。
3. 看完预览，点「设为默认出口」。
4. 在系统弹窗里同意：Windows 是 UAC，Mac 是管理员密码，Ubuntu 是 `pkexec`。同意之后才会改。点取消则什么都不改。
5. 想回到第一次修改之前，点「恢复到更改前」，再在授权窗口里同意。
6. 点「自动」，是把出口交回系统自己的优先级（Windows 重新打开自动跃点，Ubuntu 把 metric 设回自动，Mac 恢复第一次切换前的服务顺序）。已经是自动时，预览会说明不用改。

窗口右上角的「使用说明」是同一段简短提示。

## 命令行

不加 `--yes` 只打印预览，不会改系统。

```bash
egress-switcher list
egress-switcher set "Wi-Fi"
egress-switcher set "Wi-Fi" --yes
egress-switcher restore --yes
egress-switcher automatic --yes
egress-switcher --demo
```

`--demo` 打开一份录好的 Windows 样例，不读取也不修改这台电脑。名字换成你机器上的网卡名、Mac 的网络服务名，或 Ubuntu 里 NetworkManager 的连接名。

## 开着代理 TUN 时

如果窗口或 `list` 最上面出现：

> 现在流量被某块虚拟网卡接管，切换出口不会生效。

意思是 Clash / mihomo、sing-box、Shadowrocket 或其他代理用 TUN 接走了上网流量。物理网卡上的「默认路由」可能还在，但普通程序的流量并不走它。本程序**不会**删除那些路由，也**不会**关掉代理。

请到代理客户端里，把出站绑到你想用的物理网卡，然后再回来切换：

- sing-box：`route.default_interface` 或 `auto_detect_interface`
- mihomo：`interface-name`

改完之后再切换。预览里会写明：这次切换只改变代理自己出门走哪张网卡（如果代理开了自动检测网卡），不改变应用直接的流量。

## 常见问题

**点了网卡，网是不是已经换了？** 没有。点选只刷新预览。

**授权窗口点了取消？** 系统保持原样。

**能不能改 IPv6？** 不能。这个程序只动 IPv4。校园网一般也只用 IPv4。

**VMware、Hyper-V 能选成出口吗？** 不能。它们会被标成虚拟网卡。

**真机测过了吗？** 还没有。仓库里的测试用的是录下来的命令输出。请按 [真机验收清单](docs/HARDWARE_CHECKLIST.md) 在你自己的电脑上试，结果不对就 [开 Issue](https://github.com/mi179/net-egress-switcher/issues)。

---

<a id="en"></a>

# Net Egress Switcher

Pick which network adapter your computer uses to reach the internet: Ethernet, Wi-Fi, or a phone’s USB tether. Windows, Mac, and Ubuntu.

**Choosing an adapter and reading the preview does not change the computer.** Nothing is changed until you click a button and approve the system prompt. If you cancel that prompt, the system stays as it was.

![Main window (demo data, not this computer)](docs/screenshots/main-window-demo.png)

## Download

Get the files from the [Releases page](https://github.com/mi179/net-egress-switcher/releases). **v0.1.1 is a prerelease** and has not been tested on real hardware. If something does not match this guide, [open an issue](https://github.com/mi179/net-egress-switcher/issues).

| System | File |
| --- | --- |
| Windows 10/11 | `net-egress-switcher-windows.zip` |
| Mac, Apple silicon (M1 or newer) | `net-egress-switcher-macos-apple-silicon.dmg` or the `.zip` with the same name |
| Mac, Intel | `net-egress-switcher-macos-intel.dmg` or the `.zip` with the same name |
| Ubuntu | `net-egress-switcher_0.1.1_all.deb` |

## Install

### Windows

1. Unzip the archive.
2. Double-click `1-点我启动-网络出口切换.exe` to open the window. Do not use “Run as administrator” on the whole app.
3. The command-line program in the same folder is `9-命令行-egress-switcher.exe`.

### macOS

1. Open the dmg and drag 网络出口切换 into Applications. The zip unpacks to `网络出口切换.app`.
2. The app is not notarized. The first time you open it, macOS may say it is damaged or from an unidentified developer. Control-click the app in Finder, choose Open, then click Open in the dialog.
3. Do not start the window with sudo. There is no menu-bar icon on Mac: a tray would start AppKit on a background thread and crash on launch, so this version does not add one.

### Ubuntu

From the folder that contains the deb:

```bash
sudo apt install ./net-egress-switcher_0.1.1_all.deb
```

Then launch 网络出口切换 from the app menu, or run `egress-switcher` in a terminal. `net-egress-switcher` is the same command.

## Using the window

1. Open the app. The list shows this computer’s adapters: Ethernet, Wi-Fi, USB tethering, plus virtual adapters and proxy TUNs it recognizes. IPv6 is shown only; it is not changed.
2. Click a physical adapter. The preview at the bottom says what would change. Routes are still unchanged.
3. When the preview looks right, click 设为默认出口 (Set as default egress).
4. Approve the system prompt: UAC on Windows, an administrator password on Mac, `pkexec` on Ubuntu. The change happens only after you approve. Cancel, and nothing is modified.
5. 恢复到更改前 (Restore) returns to the state from before the first change. Approve the prompt again.
6. 自动 (Automatic) hands the choice back to the system: automatic metrics on Windows, automatic route metric on Ubuntu, and the saved service order on Mac. If that is already the case, the preview says there is nothing to change.

The 使用说明 (How to use) button at the top right shows the same short reminder.

## Command line

Without `--yes`, the command only prints a preview.

```bash
egress-switcher list
egress-switcher set "Wi-Fi"
egress-switcher set "Wi-Fi" --yes
egress-switcher restore --yes
egress-switcher automatic --yes
egress-switcher --demo
```

`--demo` opens a recorded Windows sample. It does not read or change this computer. Replace `Wi-Fi` with your adapter name, the macOS service name, or the NetworkManager connection name on Ubuntu.

## When a proxy TUN is on

If the top of the window or of `list` says that a virtual adapter has taken over the traffic, and that switching egress will have no effect, a proxy such as Clash / mihomo, sing-box, or Shadowrocket is capturing traffic with a TUN. A physical default route may still be listed, but ordinary apps are not using it. This app does **not** delete those routes and does **not** stop the proxy.

Bind the proxy’s own outbound interface to the physical adapter you want, then switch again:

- sing-box: `route.default_interface` or `auto_detect_interface`
- mihomo: `interface-name`

The preview will say that switching only changes which adapter the proxy itself uses to go out (if the proxy auto-detects the adapter). It does not change traffic sent directly by other apps.

## FAQ

**Did the network change when I clicked an adapter?** No. A click only refreshes the preview.

**I cancelled the authorization prompt.** The system is unchanged.

**Can it change IPv6?** No. Only IPv4 is modified.

**Can I pick VMware or Hyper-V?** No. Those are labeled as virtual adapters.

**Has this been tested on a real computer?** Not yet. The automated tests use recorded command output. Follow the [hardware checklist](docs/HARDWARE_CHECKLIST.md) on your own machine, and [open an issue](https://github.com/mi179/net-egress-switcher/issues) if it does not match this guide.
