"""简体中文图形界面。选择只更新预览，点按钮才修改系统。"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

from egress_switcher.models import Interface, InterfaceKind, SystemState
from egress_switcher.planner import Plan, automatic_blurb, plan_automatic, plan_restore, plan_set

AUTO = "__auto__"
KIND_BADGE = {
    InterfaceKind.WIRED: "primary",
    InterfaceKind.WIFI: "info",
    InterfaceKind.USB_TETHER: "success",
    InterfaceKind.VIRTUAL: "secondary",
    InterfaceKind.VPN_TUN: "warning",
    InterfaceKind.UNKNOWN: "secondary",
    InterfaceKind.LOOPBACK: "secondary",
}

try:
    import ttkbootstrap as ttk
    from ttkbootstrap.widgets.scrolled import ScrolledFrame

    USING_BOOTSTRAP = True
except ImportError:  # pragma: no cover - 开发机和发布包都会装主题
    from tkinter import ttk

    ScrolledFrame = None  # type: ignore[misc, assignment]
    USING_BOOTSTRAP = False


def launch(demo: bool = False, screenshot: str = "", select: str = "") -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("没有 tkinter。Debian/Ubuntu 请先安装 python3-tk。", file=sys.stderr)
        return 1
    _enable_dpi()
    app = App(demo=demo, screenshot=screenshot, select=select, enable_tray=not screenshot)
    app.root.mainloop()
    return 0


class App:
    def __init__(
        self,
        *,
        demo: bool = False,
        screenshot: str = "",
        select: str = "",
        enable_tray: bool = True,
        headless_notices: bool = False,
    ) -> None:
        self.demo = demo
        self.headless_notices = headless_notices
        self.last_notice: tuple[str, str] | None = None
        self.busy = False
        self.state: SystemState | None = None
        self.snapshot: dict | None = None
        self.font_family = "TkDefaultFont"
        self.root = self._create_root()
        self.font_family = _pick_font(self.root)
        self._configure_fonts()
        self.selected = tk.StringVar(value="")
        self.status = tk.StringVar(value="正在读取网卡…")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.tray = None
        if enable_tray:
            self._start_tray()
        self.reload(initial_select=select)
        if screenshot:
            self.root.after(400, lambda: self.capture(screenshot))

    def reload(self, initial_select: str = "") -> None:
        previous = initial_select or self.selected.get()
        try:
            self.state = self._load_state()
            self.snapshot = None if self.demo else self._load_snapshot()
        except Exception as exc:  # noqa: BLE001 - 界面要显示读取失败的原因
            self.state = None
            self.status.set(str(exc))
            self._set_preview(str(exc))
            return
        self._render_state()
        if previous:
            self.selected.set(previous)
        self.update_preview()

    def update_preview(self) -> None:
        plan = self.current_plan()
        self._set_preview(plan.preview_text())
        if self.selected.get() == AUTO:
            self.apply_button.configure(text="交给系统")
        else:
            self.apply_button.configure(text="设为默认出口")
        can_apply = plan.payload is not None and not plan.noop and not plan.error and not self.busy
        self.apply_button.configure(state="normal" if can_apply else "disabled")
        can_restore = self.snapshot is not None and not self.busy
        self.restore_button.configure(state="normal" if can_restore else "disabled")

    def current_plan(self) -> Plan:
        if self.state is None:
            return Plan(action="set", target=None, error="还没有读到网卡。")
        token = self.selected.get()
        if not token:
            return Plan(action="set", target=None, error="请先选择一张网卡或「自动」。选择本身不会修改系统。")
        if token == AUTO:
            return plan_automatic(self.state, self.snapshot)
        iface = self.state.by_name(token)
        if iface is None:
            return Plan(action="set", target=token, error="这张网卡已经不在列表里，请刷新。")
        return plan_set(self.state, iface)

    def apply_clicked(self) -> None:
        plan = self.current_plan()
        if plan.error:
            self.notify("还不能修改", plan.error)
            return
        if plan.noop or plan.payload is None:
            self.notify("无需修改", plan.preview_text())
            return
        if self.demo:
            self.notify("演示模式", "这是录制的样例，没有修改系统。")
            return
        self._run_plan(plan)

    def restore_clicked(self) -> None:
        if self.state is None:
            return
        plan = plan_restore(self.state, self.snapshot)
        if plan.error or plan.noop or plan.payload is None:
            self.notify("恢复", plan.preview_text())
            return
        if self.demo:
            self.notify("演示模式", "这是录制的样例，没有修改系统。")
            return
        self._run_plan(plan)

    def capture(self, path: str) -> None:
        self.root.update_idletasks()
        self.root.update()
        saved = _grab_window(self.root, path)
        if not saved:
            print(f"截图失败：{path}", file=sys.stderr)
            self.root.destroy()
            raise SystemExit(1)
        print(path)
        self.root.destroy()

    def notify(self, title: str, message: str) -> None:
        self.last_notice = (title, message)
        self.status.set(message.replace("\n", " ")[:180])
        if not self.headless_notices:
            messagebox.showinfo(title, message, parent=self.root)

    def _run_plan(self, plan: Plan) -> None:
        if self.state is None or plan.payload is None or self.busy:
            return
        from egress_switcher.snapshot import ensure_snapshot

        ensure_snapshot(self.state)
        self.snapshot = self._load_snapshot()
        self.busy = True
        self.status.set("正在等待系统授权…取消授权则不会修改。")
        self.update_preview()
        threading.Thread(target=self._apply_worker, args=(plan,), daemon=True).start()

    def _apply_worker(self, plan: Plan) -> None:
        from egress_switcher.elevate import request_apply

        result = request_apply(plan.payload or {})
        self.root.after(0, lambda: self._apply_done(result))

    def _apply_done(self, result: dict) -> None:
        self.busy = False
        self.notify("网络出口切换", str(result.get("message") or ""))
        self.reload()

    def _load_state(self) -> SystemState:
        if self.demo:
            from egress_switcher.demo import load_demo_state

            return load_demo_state()
        from egress_switcher.collect import collect_state

        return collect_state()

    def _load_snapshot(self) -> dict | None:
        from egress_switcher.snapshot import load_snapshot

        return load_snapshot()

    def _create_root(self):
        if USING_BOOTSTRAP:
            root = ttk.Window(title="网络出口切换", themename="litera")
        else:
            root = tk.Tk()
            root.title("网络出口切换")
            _fallback_theme(root)
        root.geometry("1040x860")
        root.minsize(920, 680)
        return root

    def _configure_fonts(self) -> None:
        family = self.font_family
        style = ttk.Style()
        page = "#eef3f8"
        card = "#ffffff"
        ink = "#1c2833"
        muted = "#5c6b7a"
        try:
            style.configure("TFrame", background=page)
            style.configure("TLabel", background=page, foreground=ink, font=(family, 11))
            style.configure("TButton", font=(family, 11))
            style.configure("TRadiobutton", background=card, font=(family, 11))
            style.configure("Title.TLabel", background=page, foreground=ink, font=(family, 22, "bold"))
            style.configure("Subtle.TLabel", background=page, foreground=muted, font=(family, 10))
            style.configure("Card.TFrame", background=card, relief="solid", borderwidth=1)
            style.configure("CardInner.TFrame", background=card, relief="flat", borderwidth=0)
            style.configure("Card.TLabel", background=card, foreground=ink, font=(family, 11))
            style.configure("CardName.TLabel", background=card, foreground=ink, font=(family, 12, "bold"))
            style.configure("CardMuted.TLabel", background=card, foreground=muted, font=(family, 10))
            style.configure("Head.TLabel", background=page, foreground=muted, font=(family, 10))
        except tk.TclError:
            pass
        try:
            self.root.configure(background=page)
        except tk.TclError:
            pass

    def _build(self) -> None:
        header = ttk.Frame(self.root, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="网络出口切换", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="选择哪张网卡负责上网。点选只刷新下面的预览，点按钮才会改系统。",
            style="Subtle.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        self.banner = ttk.Label(self.root, text="", padding=(16, 8), wraplength=920, justify="left")
        self.banner.pack(fill="x", padx=20, pady=(0, 8))

        status_row = ttk.Frame(self.root, padding=(20, 0, 20, 8))
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status, wraplength=760, justify="left").pack(side="left", fill="x", expand=True)
        self._button(status_row, "刷新", self.reload, primary=False).pack(side="right")

        bottom = ttk.Frame(self.root, padding=(20, 8, 20, 16))
        bottom.pack(side="bottom", fill="x")
        ttk.Label(bottom, text="即将修改", style="Name.TLabel").pack(anchor="w")
        self.preview = tk.Text(
            bottom,
            height=6,
            wrap="word",
            relief="flat",
            padx=12,
            pady=10,
            font=(self.font_family, 11),
            highlightthickness=1,
        )
        self.preview.pack(fill="x", pady=(6, 10))
        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x")
        self.apply_button = self._button(buttons, "设为默认出口", self.apply_clicked, primary=True)
        self.apply_button.pack(side="left")
        self.restore_button = self._button(buttons, "恢复到更改前", self.restore_clicked, primary=False)
        self.restore_button.pack(side="left", padx=(8, 0))
        ttk.Label(buttons, text="取消授权窗口 = 系统保持原样", style="Subtle.TLabel").pack(side="right")

        if USING_BOOTSTRAP and ScrolledFrame is not None:
            self.cards = ScrolledFrame(self.root, autohide=True, width=1000, height=460)
            self.cards.container.pack(fill="both", expand=True, padx=16, pady=(0, 4))
            self.card_parent = self.cards
        else:
            scrolled = _SimpleScroll(self.root)
            scrolled.pack(fill="both", expand=True, padx=16, pady=(0, 4))
            self.card_parent = scrolled.inner
        self._tint_preview()

    def _render_state(self) -> None:
        assert self.state is not None
        for child in self.card_parent.winfo_children():
            child.destroy()
        banner = self._banner_text()
        self.banner.configure(text=banner)
        if USING_BOOTSTRAP:
            self.banner.configure(bootstyle="warning" if self.state.tun_interfaces() or self.demo else "secondary")
        default = self.state.default_interface()
        physical = self.state.physical_preferred()
        if default:
            text = f"当前默认路由：{default.name}（{default.kind_label()}）"
        else:
            text = "当前没有 IPv4 默认路由"
        if physical and (default is None or physical.name != default.name):
            text += f"      物理网卡里优先：{physical.name}"
        if self.snapshot:
            text += "      已记住第一次修改前的状态"
        self.status.set(text)

        self._column_header().pack(fill="x", padx=8, pady=(0, 2))
        self._card_auto().pack(fill="x", pady=3, padx=4)
        for iface in _sorted_interfaces(self.state):
            self._card_iface(iface).pack(fill="x", pady=3, padx=4)

    def _column_header(self):
        row = ttk.Frame(self.card_parent, padding=(12, 0, 12, 0))
        ttk.Label(row, text="网卡", style="Head.TLabel").grid(row=0, column=1, sticky="w", padx=(28, 8))
        ttk.Label(row, text="IPv4", style="Head.TLabel").grid(row=0, column=3, sticky="w")
        ttk.Label(row, text="网关", style="Head.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Label(row, text="优先级", style="Head.TLabel").grid(row=0, column=5, sticky="w")
        ttk.Label(row, text="互联网", style="Head.TLabel").grid(row=0, column=6, sticky="w")
        _size_columns(row)
        return row

    def _card_auto(self):
        card = ttk.Frame(self.card_parent, padding=(10, 8), style="Card.TFrame")
        radio = self._radio(card, AUTO)
        radio.grid(row=0, column=0, rowspan=2, sticky="w")
        ttk.Label(card, text="自动", style="CardName.TLabel").grid(row=0, column=1, sticky="w", padx=(6, 8))
        self._badge(card, "系统默认", "secondary").grid(row=0, column=2, sticky="w")
        blurb = automatic_blurb(self.state.platform if self.state else sys.platform)
        ttk.Label(card, text=blurb, style="CardMuted.TLabel").grid(row=1, column=1, columnspan=6, sticky="w", padx=(6, 0), pady=(2, 0))
        _size_columns(card)
        return card

    def _card_iface(self, iface: Interface):
        card = ttk.Frame(self.card_parent, padding=(10, 8), style="Card.TFrame")
        radio = self._radio(card, iface.name)
        if not iface.selectable:
            radio.configure(state="disabled")
        radio.grid(row=0, column=0, rowspan=2, sticky="w")
        title = iface.name if not iface.device or iface.device == iface.name else f"{iface.name}  ·  {iface.device}"
        ttk.Label(card, text=title, style="CardName.TLabel").grid(row=0, column=1, sticky="w", padx=(6, 8))
        badges = ttk.Frame(card, style="CardInner.TFrame")
        badges.grid(row=0, column=2, sticky="w")
        self._badge(badges, iface.kind_label(), KIND_BADGE.get(iface.kind, "secondary")).pack(side="left", padx=(0, 4))
        if iface.is_default:
            self._badge(badges, "当前默认路由", "danger" if iface.kind == InterfaceKind.VPN_TUN else "success").pack(side="left", padx=(0, 4))
        elif iface.is_physical_preferred:
            self._badge(badges, "物理优先", "info").pack(side="left", padx=(0, 4))
        if not iface.selectable:
            self._badge(badges, "不可作为出口", "secondary").pack(side="left")
        self._card_label(card, ", ".join(iface.ipv4) or "—").grid(row=0, column=3, sticky="w")
        self._card_label(card, iface.gateway or "—").grid(row=0, column=4, sticky="w")
        self._card_label(card, iface.metric_text()).grid(row=0, column=5, sticky="w")
        internet_style = "success" if iface.has_internet else "danger" if iface.has_internet is False else "secondary"
        self._badge(card, iface.internet_text(), internet_style).grid(row=0, column=6, sticky="w")
        ttk.Label(
            card,
            text=f"IPv6  {iface.ipv6_text()}（仅展示，不修改）  ·  {iface.reason}",
            style="CardMuted.TLabel",
        ).grid(row=1, column=1, columnspan=6, sticky="w", padx=(6, 0), pady=(2, 0))
        _size_columns(card)
        return card

    def _card_label(self, parent, text: str):
        return ttk.Label(parent, text=text, style="Card.TLabel")

    def _banner_text(self) -> str:
        parts = []
        if self.demo:
            parts.append("演示数据：录制的 Windows 样例，不是这台电脑的网卡。联网结果也是样例，不会修改系统。")
        if self.state and self.state.tun_interfaces():
            names = "、".join(iface.name for iface in self.state.tun_interfaces())
            parts.append(f"检测到 TUN/代理「{names}」。它仍会接管普通程序的流量；切换物理出口不会关闭它，也不会改它的路由。")
        return "\n".join(parts)

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _tint_preview(self) -> None:
        background = "#ffffff"
        foreground = "#212529"
        border = "#dee2e6"
        if USING_BOOTSTRAP:
            try:
                colors = ttk.Style().colors
                background = colors.inputbg
                foreground = colors.fg
                border = colors.border
            except (AttributeError, tk.TclError):
                pass
        self.preview.configure(background=background, foreground=foreground, highlightbackground=border, highlightcolor=border)

    def _radio(self, parent, value: str):
        kwargs = {"variable": self.selected, "value": value, "command": self.update_preview}
        if USING_BOOTSTRAP:
            kwargs["bootstyle"] = "primary"
        return ttk.Radiobutton(parent, **kwargs)

    def _badge(self, parent, text: str, bootstyle: str):
        kwargs = {"text": text, "padding": (8, 2)}
        if USING_BOOTSTRAP:
            kwargs["bootstyle"] = f"{bootstyle}-inverse"
        return ttk.Label(parent, **kwargs)

    def _button(self, parent, text: str, command, *, primary: bool):
        kwargs = {"text": text, "command": command}
        if USING_BOOTSTRAP:
            kwargs["bootstyle"] = "primary" if primary else "secondary-outline"
        return ttk.Button(parent, **kwargs)

    def _start_tray(self) -> None:
        try:
            from egress_switcher.tray import start_tray
        except Exception:
            return
        try:
            self.tray = start_tray(self)
        except Exception:
            self.tray = None

    def _on_close(self) -> None:
        if self.tray is not None:
            self.root.withdraw()
            return
        self.root.destroy()

    def show_window(self) -> None:
        self.root.after(0, self._show_window)

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass

    def choose_from_tray(self, name: str) -> None:
        def go() -> None:
            self.selected.set(name)
            self.update_preview()
            self.show_window()
            self.status.set(f"已选中「{name}」。请确认下面的预览，点按钮后才会修改系统。")

        self.root.after(0, go)

    def quit_app(self) -> None:
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.root.after(0, self.root.destroy)


def _size_columns(frame) -> None:
    frame.columnconfigure(2, weight=1)
    frame.columnconfigure(3, minsize=150)
    frame.columnconfigure(4, minsize=130)
    frame.columnconfigure(5, minsize=110)
    frame.columnconfigure(6, minsize=80)


def _sorted_interfaces(state: SystemState) -> list[Interface]:
    rank = {
        InterfaceKind.WIRED: 0,
        InterfaceKind.WIFI: 1,
        InterfaceKind.USB_TETHER: 2,
        InterfaceKind.UNKNOWN: 3,
        InterfaceKind.VIRTUAL: 4,
        InterfaceKind.VPN_TUN: 5,
        InterfaceKind.LOOPBACK: 6,
    }
    visible = [iface for iface in state.interfaces if iface.kind != InterfaceKind.LOOPBACK]
    return sorted(
        visible,
        key=lambda iface: (
            0 if iface.selectable else 1,
            0 if iface.is_physical_preferred else 1,
            rank.get(iface.kind, 9),
            iface.metric if iface.metric is not None else 9999,
            iface.name,
        ),
    )


def _pick_font(root) -> str:
    families = set(tkfont.families(root))
    for name in (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "PingFang SC",
        "Hiragino Sans GB",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "Droid Sans Fallback",
    ):
        if name in families:
            return name
    return "TkDefaultFont"


def _enable_dpi() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _fallback_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        return
    background = "#f5f7fb"
    card = "#ffffff"
    root.configure(background=background)
    style.configure("TFrame", background=background)
    style.configure("Card.TFrame", background=card, relief="solid", borderwidth=1)
    style.configure("TLabel", background=background, foreground="#1f2933")
    style.configure("TButton", padding=8)
    style.configure("Title.TLabel", background=background, font=("TkDefaultFont", 20, "bold"))
    style.configure("Name.TLabel", background=card)
    style.configure("Subtle.TLabel", background=background, foreground="#52606d")


class _SimpleScroll(ttk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self._inner = self.inner

    def winfo_children(self):
        return self.inner.winfo_children()


def _grab_window(root, path: str) -> bool:
    import subprocess
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    window_id = str(root.winfo_id())
    attempts = [
        ["import", "-window", window_id, str(target)],
        ["import", "-window", "root", str(target)],
    ]
    for command in attempts:
        try:
            subprocess.run(command, check=True, timeout=20)
        except (OSError, subprocess.CalledProcessError):
            continue
        if target.is_file() and target.stat().st_size > 0:
            return True
    return False
