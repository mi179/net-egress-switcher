"""菜单栏 / 托盘只负责把主窗口里的预览准备好，不直接改路由。"""

from __future__ import annotations


def start_tray(app):
    import pystray
    from PIL import Image, ImageDraw

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
    import threading

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
