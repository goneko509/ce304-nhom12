# -*- coding: utf-8 -*-
"""
Module: core/ui_widgets.py
Chuc nang: Cac widget/helper UI dung chung cho toan bo Panel & Step, tranh
           viet lai nhieu lan (truoc day _bind_combobox_instant() ton tai
           3 ban gan giong nhau o tab0_analysis.py, tab2_stats.py,
           tab3_gps_imu.py).

ScienceInfoPanel: khoi thong tin "Chuc nang / Co so khoa hoc / Tham khao"
dat o dau moi Tab, doc truc tiep tu module_config (tab_cfg) duoc
main_app.py truyen san cho moi module khi khoi tao - khong can thay doi
co che nap module dong hien co.
"""

import webbrowser
import tkinter as tk
from tkinter import ttk


class Tooltip:
    """Popup nho hien chu thich gan con tro chuot, dung chung cho moi loai
    widget (Label/Entry/Combobox/Treeview...). Goi show()/hide() thu cong
    hoac dung ham attach_tooltip() ben duoi de tu dong gan theo <Motion>."""

    def __init__(self, widget):
        self.widget = widget
        self._tip = None
        self._label = None

    def show(self, x_root, y_root, text):
        if self._tip is None:
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            try:
                self._tip.wm_attributes("-topmost", True)
            except tk.TclError:
                pass
            self._label = tk.Label(
                self._tip, text=text, justify=tk.LEFT, background="#ffffe0",
                foreground="#1e3d59", relief=tk.SOLID, borderwidth=1,
                font=("Segoe UI", 9), padx=6, pady=4
            )
            self._label.pack()
        else:
            self._label.config(text=text)
        self._tip.wm_geometry(f"+{x_root + 16}+{y_root + 12}")
        self._tip.deiconify()

    def hide(self):
        if self._tip is not None:
            self._tip.withdraw()


def attach_tooltip(widget, text_or_func):
    """Gan tooltip hover cho 1 widget. text_or_func la chuoi tinh, hoac ham
    callable(event) -> str|None de tooltip doi noi dung theo vi tri con tro
    (vd: hover tung dong cua 1 Treeview thong qua tree.identify_row)."""
    tooltip = Tooltip(widget)

    def on_motion(event):
        text = text_or_func(event) if callable(text_or_func) else text_or_func
        if not text:
            tooltip.hide()
            return
        tooltip.show(event.x_root, event.y_root, text)

    def on_leave(event):
        tooltip.hide()

    widget.bind("<Motion>", on_motion, add="+")
    widget.bind("<Leave>", on_leave, add="+")
    return tooltip


def lock_treeview_columns(tree):
    """Chan thao tac keo gian/thu hep cot bang tay tren thanh phan cach
    header (bo sung cho stretch=False da chan tu-dong gian khi resize)."""

    def _block_resize(event):
        if tree.identify_region(event.x, event.y) == "separator":
            return "break"

    tree.bind("<Button-1>", _block_resize)
    tree.bind("<B1-Motion>", _block_resize)


def bind_instant_combobox(combo_widget, callback_func):
    """Kich hoat callback ngay khi chon bang chuot / phim Len-Xuong / lan
    chuot, khong bung xo dropdown mac dinh cua Tkinter."""

    def on_select(event=None):
        combo_widget.after(50, callback_func)

    def _step(delta):
        values = combo_widget.cget("values")
        if not values:
            return "break"
        current_val = combo_widget.get()
        if current_val in values:
            idx = values.index(current_val)
            new_idx = min(max(0, idx + delta), len(values) - 1)
            combo_widget.current(new_idx)
        else:
            combo_widget.current(0)
        on_select()
        return "break"

    combo_widget.bind("<<ComboboxSelected>>", on_select)
    combo_widget.bind("<Up>", lambda e: _step(-1))
    combo_widget.bind("<Down>", lambda e: _step(1))
    combo_widget.bind("<MouseWheel>", lambda e: _step(-1 if e.delta > 0 else 1))


class ScienceInfoPanel(ttk.Frame):
    """Khoi thu gon/mo rong hien thi mo_ta, co_so_khoa_hoc, tham_khao
    cua 1 Tab/Module, lay du lieu tu tab_cfg (dict app_config.json)."""

    def __init__(self, parent, tab_cfg=None, expanded=False):
        super().__init__(parent)
        self.tab_cfg = tab_cfg or {}
        self._expanded = tk.BooleanVar(value=expanded)

        self.header = ttk.Button(self, command=self._toggle, style="InfoToggle.TButton")
        self.header.pack(fill=tk.X)

        self.body = ttk.LabelFrame(self, text="📖 Cơ Sở Khoa Học & Tham Khảo", padding=8)

        self._build_body()
        self._refresh_header()
        if self._expanded.get():
            self.body.pack(fill=tk.X, pady=(2, 6))

    def _refresh_header(self):
        arrow = "▾" if self._expanded.get() else "▸"
        mo_ta = self.tab_cfg.get("mo_ta", "")
        text = f"{arrow} 📖 Chức năng & Cơ sở khoa học"
        if mo_ta:
            text += f"  —  {mo_ta}"
        self.header.config(text=text)

    def _toggle(self):
        self._expanded.set(not self._expanded.get())
        if self._expanded.get():
            self.body.pack(fill=tk.X, pady=(2, 6))
        else:
            self.body.pack_forget()
        self._refresh_header()

    def _build_body(self):
        mo_ta = self.tab_cfg.get("mo_ta", "")
        co_so = self.tab_cfg.get("co_so_khoa_hoc", [])
        tham_khao = self.tab_cfg.get("tham_khao", [])

        if mo_ta:
            ttk.Label(self.body, text=f"Chức năng: {mo_ta}", font=("Segoe UI", 9, "bold"),
                      wraplength=900, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 4))

        if co_so:
            ttk.Label(self.body, text="Cơ sở khoa học:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
            for item in co_so:
                ttk.Label(self.body, text=f"   • {item}", font=("Segoe UI", 9),
                          wraplength=880, justify=tk.LEFT).pack(anchor=tk.W)

        if tham_khao:
            ttk.Label(self.body, text="Tham khảo:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(4, 0))
            for ref in tham_khao:
                label = ref.get("label", ref.get("url", ""))
                url = ref.get("url", "")
                lnk = ttk.Label(self.body, text=f"   🔗 {label}", font=("Segoe UI", 9, "underline"),
                                 foreground="#1e6fd9", cursor="hand2")
                lnk.pack(anchor=tk.W)
                lnk.bind("<Button-1>", lambda e, u=url: webbrowser.open(u) if u else None)


def add_chart_zoom_button(parent, get_figure, title="Biểu Đồ Phóng Lớn"):
    """Tao 1 nut "Phong Lon" dung CHUNG cho moi bieu do matplotlib trong
    toan bo app: khi bam se mo 1 Toplevel kich thuoc lon, ve lai CHINH
    Figure hien hanh (get_figure() - thuong la `lambda: self.fig`) len 1
    FigureCanvasTkAgg + NavigationToolbar2Tk moi de xem/zoom chi tiet,
    khong anh huong canvas nho dang nhung trong Tab (Figure co the duoc
    ve dong thoi tren nhieu canvas doc lap).

    Tra ve ttk.Button - noi goi tu quyet dinh .pack()/.grid() vi tri phu
    hop trong thanh cong cu cua Tab (vd canh nut "Lam Moi"/"Chay Loc").
    """
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

    def _on_zoom():
        fig = get_figure()
        if fig is None:
            return

        win = tk.Toplevel(parent)
        win.title(title)
        win.geometry("1200x780")

        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        toolbar = NavigationToolbar2Tk(canvas, win)
        toolbar.update()

    return ttk.Button(parent, text="🔍 Phóng Lớn", command=_on_zoom)
