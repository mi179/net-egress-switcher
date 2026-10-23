"""菜单栏 / 托盘只负责把主窗口里的预览准备好，不直接改路由。

macOS 上不启动托盘。pystray 的 AppKit 后端会在 icon.run() 里调用
NSApplication.run()。这个调用必须在主线程，而主线程已经交给 Tk。
放到 threading.Thread 里会在打开时直接崩溃：
NSUpdateCycleInitialize() is called off the main thread.
"""

from __future__ import annotations

import sys
import threading


def tray_supported() -> bool:
    return sys.platform != "darwin"


def assert_main_thread(action: str = "NSApplication.run") -> None:
    """AppKit / NSApplication.run 只能在主线程。后台线程调用会 SIGTRAP。"""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            f"{action} 不能离开主线程。macOS 上在后台线程调用 NSApplication.run() 会直接崩溃。"
        )


def invoke_appkit_run(run) -> None:
    """给必须走 AppKit 事件循环的代码用。不在主线程就拒绝，不会去调用 run。"""
    assert_main_thread("NSApplication.run")
    run()


def start_tray(app):
    if not tray_supported():
        return None
    import pystray

    image = _icon_image()

    def build_menu():
        items = [
            pystray.MenuItem("打开主窗口", lambda _icon, _item: app.show_window(), default=True),
            pystray.MenuItem("刷新", lambda _icon, _item: app.root.after(0, app.reload)),
        ]
        state = app.state
        if state is not None:
            items.append(pystray.Menu.SEPARATOR)
            items.append(pystray.MenuItem("准备切换到「自动」", lambda _icon, _item: app.choose_from_tray("__auto__")))
            for iface in state.interfaces:
                if not iface.selectable:
                    continue
                name = iface.name
                items.append(
                    pystray.MenuItem(
                        f"准备切换到「{name}」",
                        lambda _icon, _item, name=name: app.choose_from_tray(name),
                    )
                )
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("退出", lambda _icon, _item: app.quit_app()))
        return tuple(items)

    icon = pystray.Icon("net-egress-switcher", image, "网络出口切换", menu=pystray.Menu(build_menu))
    # Windows / Linux 的托盘循环不碰 AppKit，可以留在后台线程。主线程继续跑 Tk。
    thread = threading.Thread(target=icon.run, name="egress-switcher-tray", daemon=True)
    thread.start()
    return icon


def _icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(31, 111, 235, 255))
    draw.line((16, 26, 40, 26), fill="white", width=4)
    draw.polygon([(36, 18), (48, 26), (36, 34)], fill="white")
    draw.line((48, 40, 24, 40), fill="white", width=4)
    draw.polygon([(28, 32), (16, 40), (28, 48)], fill="white")
    return image
