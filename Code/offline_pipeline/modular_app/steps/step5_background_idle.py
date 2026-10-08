# -*- coding: utf-8 -*-
"""
Module: steps/step5_background_idle.py
Chuc nang: Step 5 - Trich xuat tin hieu Background (Xe dung yen/Dung den do)
           dua tren tieu chuan Dong luc hoc SOTA (Sensor Fusion). Khu nhieu
           Bias/Drift bang bo loc Bien do dao dong (Rolling Peak-to-Peak).
           Dong bo Timeseries 2 file bang pd.merge_asof chuyen dung.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from core.app_state import AppState
from core.ui_widgets import ScienceInfoPanel, add_chart_zoom_button


class Step5BackgroundIdle(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self.merged_df = None
        self.background_segments = []
        self.active_segment = None

        self.plot_vars = ["speed", "acc_y", "acc_x", "gyr_z", "pitch", "roll"]
        self.check_vars = {}

        self._build_ui()

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=4, pady=(4, 0))

        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        top_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=1)

        cfg_frame = ttk.LabelFrame(top_frame, text="Cấu Hình Động Lực Học SOTA (Loại Bỏ Nhiễu Drift Bằng Rolling Window)", padding=6)
        cfg_frame.pack(fill=tk.X, padx=4, pady=2)

        ttk.Label(cfg_frame, text="Vận tốc max (km/h):").grid(row=0, column=0, padx=3, pady=2, sticky="e")
        self.ent_max_speed = ttk.Entry(cfg_frame, width=8)
        self.ent_max_speed.insert(0, "2.5")
        self.ent_max_speed.grid(row=0, column=1, padx=3, pady=2, sticky="w")

        ttk.Label(cfg_frame, text="Cửa sổ lọc (giây):").grid(row=0, column=2, padx=3, pady=2, sticky="e")
        self.ent_window = ttk.Entry(cfg_frame, width=8)
        self.ent_window.insert(0, "1.0")
        self.ent_window.grid(row=0, column=3, padx=3, pady=2, sticky="w")

        ttk.Label(cfg_frame, text="Biên độ ΔAcc max (m/s²):").grid(row=0, column=4, padx=3, pady=2, sticky="e")
        self.ent_noise_acc = ttk.Entry(cfg_frame, width=8)
        self.ent_noise_acc.insert(0, "0.3")
        self.ent_noise_acc.grid(row=0, column=5, padx=3, pady=2, sticky="w")

        ttk.Label(cfg_frame, text="Biên độ ΔGyr max (rad/s):").grid(row=0, column=6, padx=3, pady=2, sticky="e")
        self.ent_noise_gyr = ttk.Entry(cfg_frame, width=8)
        self.ent_noise_gyr.insert(0, "0.1")
        self.ent_noise_gyr.grid(row=0, column=7, padx=3, pady=2, sticky="w")

        ttk.Label(cfg_frame, text="Dừng tối thiểu (giây):").grid(row=1, column=0, padx=3, pady=2, sticky="e")
        self.ent_min_dur = ttk.Entry(cfg_frame, width=8)
        self.ent_min_dur.insert(0, "3.0")
        self.ent_min_dur.grid(row=1, column=1, padx=3, pady=2, sticky="w")

        btn_run = ttk.Button(cfg_frame, text="🔍 Phân Tích Background", style="Accent.TButton", command=self._run_analysis)
        btn_run.grid(row=1, column=2, columnspan=2, padx=10, pady=4, sticky="we")

        btn_export_all = ttk.Button(cfg_frame, text="💾 Xuất Background (Gộp Đồng Bộ)", command=self._export_all_background)
        btn_export_all.grid(row=1, column=4, columnspan=2, padx=2, pady=4, sticky="we")

        list_frame = ttk.LabelFrame(top_frame, text="Danh Sách Các Lần Dừng Xe (Idle/Red Light)", padding=4)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)

        cols = ("ID", "Duration", "Start", "End", "Avg_Lat", "Avg_Lon")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=5)
        self.tree.heading("ID", text="#ID")
        self.tree.heading("Duration", text="Thời gian đỗ (s)")
        self.tree.heading("Start", text="T0 (s)")
        self.tree.heading("End", text="T1 (s)")
        self.tree.heading("Avg_Lat", text="Lat (Vị trí dừng)")
        self.tree.heading("Avg_Lon", text="Lon (Vị trí dừng)")

        self.tree.column("ID", width=50, anchor=tk.CENTER)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        bottom_frame = ttk.LabelFrame(paned, text="Biểu Đồ Động Lực Học Tại Điểm Dừng", padding=4)
        paned.add(bottom_frame, weight=2)

        var_frame = ttk.Frame(bottom_frame)
        var_frame.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(var_frame, text="Hiển thị biến:").pack(side=tk.LEFT)

        for var_name in self.plot_vars:
            var = tk.BooleanVar(value=True)
            self.check_vars[var_name] = var
            ttk.Checkbutton(var_frame, text=var_name, variable=var, command=self._plot_segment).pack(side=tk.LEFT, padx=5)

        add_chart_zoom_button(
            var_frame, lambda: self.fig, title="Biểu Đồ Động Lực Học - Step 5"
        ).pack(side=tk.RIGHT, padx=4)

        self.fig = Figure(figsize=(8, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(self.fig, master=bottom_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, bottom_frame)
        self.toolbar.update()

    def _sync_data(self):
        """Ky thuat SOTA xu ly chuoi thoi gian: dung pd.merge_asof.
        Ghep GPS (Low Freq) vao IMU (High Freq) ma khong lam ro ri du lieu
        tuong lai (no future leakage)."""
        if self.state.df_gps is None or self.state.df_imu is None:
            messagebox.showwarning("Thiếu dữ liệu", "Phải nạp đủ cả file GPS và IMU!")
            return False

        df_g = self.state.df_gps.copy()
        if "t_now" in df_g.columns:
            df_g.rename(columns={"t_now": "time"}, inplace=True)

        df_i = self.state.df_imu.copy()

        df_g = df_g.sort_values("time").reset_index(drop=True)
        df_i = df_i.sort_values("time").reset_index(drop=True)

        self.merged_df = pd.merge_asof(
            df_i,
            df_g,
            on="time",
            direction="nearest",
            tolerance=1.0
        )

        speed_col = "speed_kmh" if "speed_kmh" in self.merged_df.columns else "speed"

        subset_to_drop = []
        if speed_col in self.merged_df.columns:
            subset_to_drop.append(speed_col)
        if "acc_y" in self.merged_df.columns:
            subset_to_drop.append("acc_y")

        if subset_to_drop:
            self.merged_df.dropna(subset=subset_to_drop, inplace=True)

        return True

    def _run_analysis(self):
        if not self._sync_data():
            return

        try:
            v_max = float(self.ent_max_speed.get())
            window_s = float(self.ent_window.get())
            delta_acc = float(self.ent_noise_acc.get())
            delta_gyr = float(self.ent_noise_gyr.get())
            min_dur = float(self.ent_min_dur.get())
        except ValueError:
            messagebox.showerror("Lỗi", "Vui lòng nhập số hợp lệ vào các cấu hình!")
            return

        df = self.merged_df.copy()
        speed_col = "speed_kmh" if "speed_kmh" in df.columns else "speed"

        dt = df['time'].diff().mean()
        if dt == 0 or pd.isna(dt):
            dt = 0.01
        window_samples = max(2, int(window_s / dt))

        acc_ptp = df["acc_y"].rolling(window=window_samples, min_periods=1, center=True).max() - \
            df["acc_y"].rolling(window=window_samples, min_periods=1, center=True).min()

        gyr_ptp = df["gyr_z"].rolling(window=window_samples, min_periods=1, center=True).max() - \
            df["gyr_z"].rolling(window=window_samples, min_periods=1, center=True).min()

        is_stopped = (
            (df[speed_col] < v_max) &
            (acc_ptp < delta_acc) &
            (gyr_ptp < delta_gyr)
        )

        stop_events = is_stopped.astype(int)
        event_groups = (stop_events.diff() != 0).cumsum()

        self.background_segments = []
        seg_id = 1

        for group_id, group_df in df[is_stopped == 1].groupby(event_groups):
            t_start = group_df["time"].iloc[0]
            t_end = group_df["time"].iloc[-1]
            dur = t_end - t_start

            if dur >= min_dur:
                self.background_segments.append({
                    "id": seg_id,
                    "t_start": t_start,
                    "t_end": t_end,
                    "duration": dur,
                    "avg_lat": group_df["lat"].mean(),
                    "avg_lon": group_df["lon"].mean(),
                    "lat_start": group_df["lat"].iloc[0],
                    "lon_start": group_df["lon"].iloc[0],
                    "lat_end": group_df["lat"].iloc[-1],
                    "lon_end": group_df["lon"].iloc[-1],
                    "distance": 0.0,  # Diem dung nen xem nhu quang duong = 0
                    "avg_speed": group_df[speed_col].mean(),
                    "data": group_df
                })
                seg_id += 1

        self._update_treeview()

        if not self.background_segments:
            messagebox.showinfo("Kết quả", "Không tìm thấy đoạn xe dừng nào thỏa mãn cấu hình lọc!")

    def _update_treeview(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for seg in self.background_segments:
            self.tree.insert("", tk.END, values=(
                f"#{seg['id']}",
                f"{seg['duration']:.2f}",
                f"{seg['t_start']:.3f}",
                f"{seg['t_end']:.3f}",
                f"{seg['avg_lat']:.6f}",
                f"{seg['avg_lon']:.6f}"
            ))

        if self.background_segments:
            first_item = self.tree.get_children()[0]
            self.tree.selection_set(first_item)

    def _on_tree_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return

        item = self.tree.item(selected[0])
        seg_id = int(item["values"][0].replace("#", ""))
        self.active_segment = next(s for s in self.background_segments if s["id"] == seg_id)

        self.state.selected_segment = self.active_segment
        self._plot_segment()
        self.state.notify_data_changed()

    def _plot_segment(self):
        if not self.active_segment:
            return

        df_sub = self.active_segment["data"]
        self.ax.clear()

        colors = ["#e74c3c", "#2ecc71", "#3498db", "#f1c40f", "#9b59b6", "#1abc9c"]
        color_idx = 0

        speed_col = "speed_kmh" if "speed_kmh" in df_sub.columns else "speed"

        for var_name, is_checked in self.check_vars.items():
            if is_checked.get():
                col_name = speed_col if var_name == "speed" else var_name
                if col_name in df_sub.columns:
                    vals = df_sub[col_name].values
                    v_min, v_max = np.nanmin(vals), np.nanmax(vals)

                    if v_max - v_min > 1e-6:
                        norm_vals = (vals - v_min) / (v_max - v_min)
                    else:
                        norm_vals = np.zeros_like(vals) + 0.5

                    c = colors[color_idx % len(colors)]
                    label_str = f"{var_name} (Thực tế: {v_min:.2f} ~ {v_max:.2f})"

                    self.ax.plot(df_sub["time"], norm_vals, label=label_str, color=c, linewidth=1.5, alpha=0.85)
                    color_idx += 1

        self.ax.set_title(f"Phân tích Background (Xe đứng yên) Đoạn #{self.active_segment['id']}")
        self.ax.set_xlabel("Thời gian hệ thống (s)")
        self.ax.set_ylabel("Biên độ chuẩn hóa [0, 1]")
        self.ax.grid(True, linestyle=":", alpha=0.6)

        if color_idx > 0:
            self.ax.legend(loc="upper right", framealpha=0.9, fontsize=8)

        self.fig.tight_layout()
        self.canvas.draw()

    def _export_all_background(self):
        """Gop tat ca cac doan Background dat chuan vao 1 dataframe duy
        nhat, co san su dong bo giua IMU va GPS, luu ra .csv"""
        if not self.background_segments:
            messagebox.showwarning("Trống", "Chưa có dữ liệu Background nào được lọc!")
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile="background_sync_data.csv"
        )

        if save_path:
            all_bkg_df = pd.concat([seg["data"] for seg in self.background_segments], ignore_index=True)
            all_bkg_df.to_csv(save_path, index=False)
            messagebox.showinfo("Thành công", f"Đã xuất đồng bộ IMU & GPS dữ liệu nền (Background) ra file:\n{save_path}")
