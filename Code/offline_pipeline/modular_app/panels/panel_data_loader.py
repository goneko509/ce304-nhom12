# -*- coding: utf-8 -*-
"""
Module: panels/panel_data_loader.py
Chuc nang: Panel Top - Chon thu muc va tu dong nap file du lieu tho (GPS & IMU).
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd

from core.app_state import AppState
from core.sensor_schema import GPS_COLUMNS, IMU_COLUMNS
from core.ui_widgets import ScienceInfoPanel, bind_instant_combobox


class PanelDataLoader(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self._build_ui()
        if self.state.current_dir:
            self._auto_scan_directory(self.state.current_dir)

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=5, pady=(5, 0))

        container = ttk.LabelFrame(self, text="Nạp Dữ Liệu Thô (RAW GPS & IMU)", padding=8)
        container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        row0 = ttk.Frame(container)
        row0.pack(fill=tk.X, pady=2)

        ttk.Label(row0, text="📁 Thư mục làm việc: ", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_dir = ttk.Label(row0, text=self.state.current_dir or "Chưa chọn", foreground="#1e3d59", font=("Consolas", 9, "bold"))
        self.lbl_dir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        btn_browse = ttk.Button(row0, text="Duyệt Thư Mục...", command=self._on_browse)
        btn_browse.pack(side=tk.RIGHT, padx=2)

        btn_scan = ttk.Button(row0, text="🔄 Quét Lại Thư Mục", command=lambda: self._auto_scan_directory(self.state.current_dir))
        btn_scan.pack(side=tk.RIGHT, padx=2)

        row1 = ttk.Frame(container)
        row1.pack(fill=tk.X, pady=4)

        ttk.Label(row1, text="File GPS:").pack(side=tk.LEFT, padx=(0, 2))
        self.cbo_gps = ttk.Combobox(row1, state="readonly", width=32)
        self.cbo_gps.pack(side=tk.LEFT, padx=(0, 15))
        bind_instant_combobox(self.cbo_gps, self._load_data)

        ttk.Label(row1, text="File IMU:").pack(side=tk.LEFT, padx=(0, 2))
        self.cbo_imu = ttk.Combobox(row1, state="readonly", width=32)
        self.cbo_imu.pack(side=tk.LEFT, padx=(0, 15))
        bind_instant_combobox(self.cbo_imu, self._load_data)

        self.lbl_auto_status = ttk.Label(row1, text="", foreground="#1e3d59", font=("Segoe UI", 9, "italic"))
        self.lbl_auto_status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))

    def _on_browse(self):
        selected = filedialog.askdirectory(initialdir=self.state.current_dir or os.getcwd())
        if selected:
            self.state.current_dir = selected
            self.lbl_dir.config(text=selected)
            self._auto_scan_directory(selected)

    def _auto_scan_directory(self, folder):
        if not folder or not os.path.exists(folder):
            self.state.log("PanelDataLoader: Thư mục không tồn tại hoặc chưa chọn.")
            return

        self.state.log(f"Quét tất cả tệp .txt trong thư mục: {folder}")

        all_txts = [f for f in os.listdir(folder) if f.lower().endswith(('.txt', '.csv'))]
        if not all_txts:
            self.state.log("Không thấy tệp .txt / .csv nào trong thư mục!")
            self.cbo_gps["values"] = []
            self.cbo_imu["values"] = []
            self.cbo_gps.set("")
            self.cbo_imu.set("")
            return

        self.cbo_gps["values"] = all_txts
        self.cbo_imu["values"] = all_txts

        gps_default = next((f for f in all_txts if "gps" in f.lower()), all_txts[0])
        imu_default = next((f for f in all_txts if "acc" in f.lower() or "imu" in f.lower()), all_txts[-1])

        self.cbo_gps.set(gps_default)
        self.cbo_imu.set(imu_default)

        self.state.gps_path = os.path.join(folder, gps_default)
        self.state.imu_path = os.path.join(folder, imu_default)

        self.state.log(f"Phát hiện tệp: GPS -> {gps_default} | IMU -> {imu_default}")

        # Tu dong nap ngay khi thu muc chua DUNG 2 file du lieu tho (GPS +
        # IMU), khong can bam nut thu cong. Thu muc co nhieu hon 2 file
        # (vd lan ca ket qua xuat cu) se khong tu dong nap de tranh chon
        # nham, nguoi dung van co the chon lai bang Combobox (tu dong nap
        # lai ngay khi doi lua chon).
        if len(all_txts) == 2 and gps_default != imu_default:
            self._load_data()
        else:
            self.lbl_auto_status.config(
                text="⚠️ Thư mục có nhiều hơn 2 tệp - vui lòng chọn đúng File GPS/IMU ở Combobox để tự động nạp."
            )

    def _load_data(self):
        gps_filename = self.cbo_gps.get()
        imu_filename = self.cbo_imu.get()

        if not gps_filename or not imu_filename:
            messagebox.showerror("Lỗi", "Vui lòng chọn đủ tệp GPS và IMU!")
            return

        gps_full_path = os.path.join(self.state.current_dir, gps_filename)
        imu_full_path = os.path.join(self.state.current_dir, imu_filename)

        if not os.path.exists(gps_full_path) or not os.path.exists(imu_full_path):
            messagebox.showerror("Lỗi", "File chọn không tồn tại trên đĩa!")
            return

        self.state.gps_path = gps_full_path
        self.state.imu_path = imu_full_path

        try:
            self.state.log(f"Đang đọc và parse dữ liệu thô GPS từ {gps_filename}...")
            df_gps = pd.read_csv(self.state.gps_path, sep=r"\s+", header=None, names=GPS_COLUMNS, engine="python")
            for c in ["t_now", "lat", "lon", "hdop", "alt", "fix", "cog", "speed_kmh", "speed_knots", "satellites"]:
                if c in df_gps.columns:
                    df_gps[c] = pd.to_numeric(df_gps[c], errors="coerce")
            self.state.df_gps = df_gps

            self.state.log(f"Đang đọc và parse dữ liệu thô IMU từ {imu_filename}...")
            df_imu = pd.read_csv(self.state.imu_path, sep=r"\s+", header=None, names=IMU_COLUMNS, engine="python")
            for c in IMU_COLUMNS:
                if c in df_imu.columns:
                    df_imu[c] = pd.to_numeric(df_imu[c], errors="coerce")
            self.state.df_imu = df_imu

            self.state.log(f"✅ Đã nạp thành công: {len(df_gps)} dòng GPS, {len(df_imu)} dòng IMU.")
            self.state.notify_data_changed()
            self.lbl_auto_status.config(
                text=f"✅ Đã tự động nạp: GPS {len(df_gps)} mẫu | IMU {len(df_imu)} mẫu",
                foreground="#17b978"
            )
        except Exception as e:
            self.state.log(f"❌ Lỗi nạp dữ liệu: {str(e)}")
            self.lbl_auto_status.config(text=f"❌ Lỗi nạp dữ liệu: {str(e)}", foreground="red")
            messagebox.showerror("Lỗi Nạp Dữ Liệu", str(e))
