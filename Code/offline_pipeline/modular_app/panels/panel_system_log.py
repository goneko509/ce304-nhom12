# -*- coding: utf-8 -*-
"""
Module: panels/panel_system_log.py
Chuc nang: Panel Nhat Ky - ghi nhan tinh toan, thong bao xu ly he thong.
"""

import tkinter as tk
from tkinter import ttk

from core.app_state import AppState
from core.ui_widgets import ScienceInfoPanel


class PanelSystemLog(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self._build_ui()
        self.state.register_log_listener(self.append_log)
        self.state.register_data_listener(self._on_data_changed)

    def _build_ui(self):
        main_box = ttk.Frame(self, padding=8)
        main_box.pack(fill=tk.BOTH, expand=True)

        ScienceInfoPanel(main_box, self.module_config).pack(fill=tk.X, pady=(0, 5))

        hdr_frame = ttk.LabelFrame(main_box, text="Thông Tin Tổng Quan Mẫu Dữ Liệu", padding=6)
        hdr_frame.pack(fill=tk.X, pady=(0, 5))

        self.lbl_info = ttk.Label(hdr_frame, text="Trạng thái: Chưa nạp dữ liệu. Hãy bấm '⚡ Nạp Dữ Liệu' ở Top bar.", font=("Segoe UI", 9, "bold"))
        self.lbl_info.pack(anchor=tk.W)

        log_frame = ttk.LabelFrame(main_box, text="Nhật Ký Tính Toán & Xử Lý Hệ Thống", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)

        btn_bar = ttk.Frame(log_frame)
        btn_bar.pack(fill=tk.X, pady=(0, 4))

        btn_clear = ttk.Button(btn_bar, text="🧹 Xóa Nhật Ký", command=self.clear_log)
        btn_clear.pack(side=tk.RIGHT)

        self.txt_log = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff66")
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        mod_name = self.module_config.get("ten", "Panel Nhật Ký")
        self.append_log(f"Khởi tạo thành công [{mod_name}]. Đã sẵn sàng ghi nhận nhật ký.")

    def append_log(self, message):
        self.txt_log.insert(tk.END, f"> {message}\n")
        self.txt_log.see(tk.END)

    def clear_log(self):
        self.txt_log.delete("1.0", tk.END)

    def _on_data_changed(self):
        if self.state.df_gps is not None and self.state.df_imu is not None:
            msg = f"Đã nạp Dữ liệu: GPS ({len(self.state.df_gps)} dòng) | IMU ({len(self.state.df_imu)} dòng)"
            self.lbl_info.config(text=msg, foreground="green")
        else:
            self.lbl_info.config(text="Trạng thái: Chưa có dữ liệu.", foreground="red")
