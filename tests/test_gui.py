import unittest
import tkinter
from unittest import mock


def _display_available() -> bool:
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        return False
    root.destroy()
    return True


@unittest.skipUnless(_display_available(), "需要图形环境才能打开主窗口")
class GuiTests(unittest.TestCase):
    def test_demo_window_shows_fixture_and_does_not_apply(self):
        from egress_switcher.gui import AUTO, App

        app = App(demo=True, enable_tray=False, headless_notices=True)
        try:
            app.root.update()
            self.assertTrue(app.root.resizable()[0])
            self.assertTrue(app.root.resizable()[1])
            self.assertIs(app.preview.winfo_toplevel(), app.root)
            self.assertIn(app.scroller.inner, _ancestors(app.preview))
            self.assertIn(app.scroller.inner, _ancestors(app.apply_button))
            self.assertIn("演示数据", app.banner.cget("text"))
            self.assertIn("Meta", app.banner.cget("text"))
            self.assertIn("切换出口不会生效", app.banner.cget("text"))
            self.assertIn("实际出口：Meta", app.status.get())
            texts = _widget_texts(app.root)
            self.assertTrue(any("2408:4000:1234::50" in item for item in texts))
            self.assertTrue(any("仅展示" in item for item in texts))
            self.assertTrue(any("USB 共享" in item for item in texts))
            app.selected.set("Wi-Fi")
            app.update_preview()
            preview = app.preview.get("1.0", "end")
            self.assertIn("→ 1（手动）", preview)
            self.assertIn("不修改：", preview)
            self.assertIn("Meta", preview)
            self.assertIn("切换只改变代理自己出门走哪张网卡", preview)
            with mock.patch("egress_switcher.elevate.request_apply") as apply:
                app.apply_clicked()
                apply.assert_not_called()
            self.assertEqual(app.last_notice[0], "演示模式")
            app.selected.set(AUTO)
            app.update_preview()
            self.assertIn("自动跃点", app.preview.get("1.0", "end"))
            self.apply_button_disabled_for_noop(app)
        finally:
            app.root.destroy()

    def apply_button_disabled_for_noop(self, app):
        self.assertEqual(str(app.apply_button.cget("state")), "disabled")


def _ancestors(widget):
    found = []
    current = widget
    while current is not None:
        found.append(current)
        current = getattr(current, "master", None)
    return found


def _widget_texts(widget) -> list[str]:
    found = []
    try:
        found.append(str(widget.cget("text")))
    except tkinter.TclError:
        pass
    for child in widget.winfo_children():
        found.extend(_widget_texts(child))
    return found
