# -*- coding: utf-8 -*-
"""
Module: panels/panel_status_bar.py
Chuc nang: Panel Bottom - Thanh trang thai (Status Bar) duoi cung ung dung.
"""

import tkinter as tk
from tkinter import ttk
from core.app_state import AppState


class PanelStatusBar(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self._build_ui()
        self.state.register_data_listener(self._update_status)

    def _build_ui(self):
        container = ttk.Frame(self, padding=(5, 2))
        container.pack(fill=tk.X, expand=True)

        self.lbl_status = ttk.Label(container, text="Trạng Thái: Sẵn sàng | Chưa nạp dữ liệu", font=("Segoe UI", 8), foreground="#555555")
        self.lbl_status.pack(side=tk.LEFT)

        lbl_copy = ttk.Label(container, text="Hệ Thống Phân Tích Dữ Liệu Xe Telemetry v3.0 Modular", font=("Segoe UI", 8, "italic"), foreground="#888888")
        lbl_copy.pack(side=tk.RIGHT)

    def _update_status(self):
        if self.state.df_gps is not None and self.state.df_imu is not None:
            text = f"Trạng Thái: Đã nạp thành công | GPS ({len(self.state.df_gps)} mẫu) | IMU ({len(self.state.df_imu)} mẫu)"
            self.lbl_status.config(text=text, foreground="green")
        else:
            self.lbl_status.config(text="Trạng Thái: Sẵn sàng | Chưa nạp dữ liệu", foreground="#555555")
