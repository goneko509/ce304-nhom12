# -*- coding: utf-8 -*-
"""
Module: steps/step2_statistics_viz.py
Chuc nang: Step 2 - Thong ke va truc quan hoa du lieu tho (GPS/IMU) & tep
           .txt / .csv khac. Ap dung Instant-trigger cho Combobox.
"""

import os
import tkinter as tk
from tkinter import ttk
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from core.app_state import AppState
from core.ui_widgets import bind_instant_combobox, ScienceInfoPanel, add_chart_zoom_button


class Step2StatisticsViz(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}
        self._custom_dfs_cache = {}

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)
        self._update_source_list()

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=5, pady=(5, 0))

        ctrl_frame = ttk.LabelFrame(self, text="Cấu Hình Thống Kê & Trực Quan Hóa", padding=8)
        ctrl_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(ctrl_frame, text="Chọn Tệp:").grid(row=0, column=0, padx=4, pady=2, sticky=tk.W)
        self.cbo_source = ttk.Combobox(ctrl_frame, values=["GPS File (RAW_GPS)", "IMU File (RAW_ACCELEROMETERS)"], state="readonly", width=28)
        self.cbo_source.current(0)
        self.cbo_source.grid(row=0, column=1, padx=4, pady=2)
        bind_instant_combobox(self.cbo_source, self._on_source_changed)

        ttk.Label(ctrl_frame, text="Chọn Cột:").grid(row=0, column=2, padx=4, pady=2, sticky=tk.W)
        self.cbo_column = ttk.Combobox(ctrl_frame, values=[], state="readonly", width=20)
        self.cbo_column.grid(row=0, column=3, padx=4, pady=2)
        bind_instant_combobox(self.cbo_column, self._update_plot)

        ttk.Label(ctrl_frame, text="Kiểu Đồ Thị:").grid(row=0, column=4, padx=4, pady=2, sticky=tk.W)
        self.cbo_chart_type = ttk.Combobox(
            ctrl_frame,
            values=["Chuỗi Thời Gian (Time Series)", "Cột Thống Kê (Min/Max/Mean)", "Phân Phối (Histogram)"],
            state="readonly", width=25
        )
        self.cbo_chart_type.current(0)
        self.cbo_chart_type.grid(row=0, column=5, padx=4, pady=2)
        bind_instant_combobox(self.cbo_chart_type, self._update_plot)

        self.var_has_header = tk.BooleanVar(value=True)
        chk_header = ttk.Checkbutton(
            ctrl_frame, text="Tệp có dòng tiêu đề (Header)",
            variable=self.var_has_header, command=self._reload_current_source
        )
        chk_header.grid(row=0, column=6, padx=(12, 4), pady=2, sticky=tk.W)

        btn_reload = ttk.Button(ctrl_frame, text="🔄 Tải Lại Dữ Liệu", command=self._reload_current_source)
        btn_reload.grid(row=0, column=7, padx=4, pady=2)

        content_frame = ttk.Frame(self)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        stats_frame = ttk.LabelFrame(content_frame, text="Chỉ Số Thống Kê", padding=6, width=280)
        stats_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4), pady=2)

        self.tree_stats = ttk.Treeview(stats_frame, columns=("Metric", "Value"), show="headings", height=14)
        self.tree_stats.heading("Metric", text="Đại Lượng")
        self.tree_stats.heading("Value", text="Giá Trị")
        self.tree_stats.column("Metric", width=140, anchor=tk.W)
        self.tree_stats.column("Value", width=100, anchor=tk.E)
        self.tree_stats.pack(fill=tk.BOTH, expand=True)

        plot_frame = ttk.LabelFrame(content_frame, text="Biểu Đồ Trực Quan", padding=4)
        plot_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(4, 0), pady=2)

        plot_toolbar_row = ttk.Frame(plot_frame)
        plot_toolbar_row.pack(fill=tk.X)
        add_chart_zoom_button(plot_toolbar_row, lambda: self.fig, title="Biểu Đồ Trực Quan - Step 2").pack(side=tk.RIGHT)

        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.toolbar = NavigationToolbar2Tk(self.canvas, plot_frame)
        self.toolbar.update()

    def _update_source_list(self):
        """Cap nhat danh sach cac tep .txt va .csv tu thu muc lam viec"""
        sources = []
        gps_file = os.path.basename(self.state.gps_path) if self.state.gps_path else "RAW_GPS"
        imu_file = os.path.basename(self.state.imu_path) if self.state.imu_path else "RAW_ACCELEROMETERS"

        if self.state.df_gps is not None or self.state.gps_path:
            sources.append(f"GPS File ({gps_file})")
        else:
            sources.append("GPS File (RAW_GPS)")

        if self.state.df_imu is not None or self.state.imu_path:
            sources.append(f"IMU File ({imu_file})")
        else:
            sources.append("IMU File (RAW_ACCELEROMETERS)")

        folder = self.state.current_dir
        if folder and os.path.exists(folder) and os.path.isdir(folder):
            try:
                all_files = sorted(os.listdir(folder))
                for f in all_files:
                    if f.lower().endswith(('.txt', '.csv')):
                        if self.state.gps_path and os.path.exists(self.state.gps_path):
                            try:
                                if os.path.samefile(os.path.join(folder, f), self.state.gps_path):
                                    continue
                            except Exception:
                                pass
                        if self.state.imu_path and os.path.exists(self.state.imu_path):
                            try:
                                if os.path.samefile(os.path.join(folder, f), self.state.imu_path):
                                    continue
                            except Exception:
                                pass

                        if f not in sources:
                            sources.append(f)
            except Exception:
                pass

        curr = self.cbo_source.get()
        self.cbo_source["values"] = sources
        if curr in sources:
            self.cbo_source.set(curr)
        elif sources:
            self.cbo_source.current(0)

    def _get_df_for_source(self, source_name):
        if not source_name:
            return None

        if "GPS" in source_name or (self.state.gps_path and os.path.basename(self.state.gps_path) in source_name):
            return self.state.df_gps
        if "IMU" in source_name or (self.state.imu_path and os.path.basename(self.state.imu_path) in source_name):
            return self.state.df_imu

        folder = self.state.current_dir
        if not folder or not os.path.exists(folder):
            return None

        filepath = os.path.join(folder, source_name)
        if not os.path.exists(filepath):
            return None

        if filepath in self._custom_dfs_cache:
            return self._custom_dfs_cache[filepath]

        # Neu tep co dong tieu de (vd .csv xuat tu Excel/pandas), dong dau
        # duoc dung lam ten cot (header=0, bo khoi phan du lieu). Nguoc lai
        # header=None de giu nguyen dong dau nhu 1 mau du lieu binh thuong.
        header_opt = 0 if self.var_has_header.get() else None

        try:
            if filepath.lower().endswith('.csv'):
                df = pd.read_csv(filepath, header=header_opt)
            else:
                try:
                    df = pd.read_csv(filepath, header=header_opt)
                    if len(df.columns) <= 1:
                        df = pd.read_csv(filepath, sep=r'\s+', engine='python', header=header_opt)
                except Exception:
                    df = pd.read_csv(filepath, sep=r'\s+', engine='python', header=header_opt)

            for c in df.columns:
                if df[c].dtype == object:
                    conv = pd.to_numeric(df[c], errors='coerce')
                    if conv.notna().sum() > 0:
                        df[c] = conv

            self._custom_dfs_cache[filepath] = df
            return df
        except Exception:
            return None

    def _reload_current_source(self):
        """Xoa cache va doc lai tu dia tep dang chon (ap dung ngay lua chon
        'Tep co Header' moi, hoac khi noi dung tep tren dia da thay doi)."""
        source = self.cbo_source.get()
        folder = self.state.current_dir
        if folder and source:
            filepath = os.path.join(folder, source)
            self._custom_dfs_cache.pop(filepath, None)
        self._update_column_list()
        self._update_plot()

    def _on_source_changed(self):
        self._update_column_list()
        self._update_plot()

    def _on_data_changed(self):
        self._custom_dfs_cache.clear()
        self._update_source_list()
        self._update_column_list()
        self._update_plot()

    def _update_column_list(self):
        source = self.cbo_source.get()
        df = self._get_df_for_source(source)
        if df is not None:
            num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
            if not num_cols:
                num_cols = list(df.columns)

            if "GPS" in source:
                cols = [c for c in num_cols if c not in ["UTC", "date"]]
            else:
                cols = list(num_cols)

            self.cbo_column["values"] = cols
            if cols:
                curr = self.cbo_column.get()
                if curr in cols:
                    self.cbo_column.set(curr)
                else:
                    if "speed_kmh" in cols:
                        self.cbo_column.set("speed_kmh")
                    elif "acc_y" in cols:
                        self.cbo_column.set("acc_y")
                    else:
                        self.cbo_column.set(cols[0])
        else:
            self.cbo_column["values"] = []
            self.cbo_column.set("")

    def _update_plot(self):
        source = self.cbo_source.get()
        col = self.cbo_column.get()
        chart_type = self.cbo_chart_type.get()

        df = self._get_df_for_source(source)
        if df is None or col not in df.columns or col == "":
            self.ax.clear()
            self.ax.text(0.5, 0.5, "Chưa nạp dữ liệu hoặc chọn cột hợp lệ", ha="center", va="center")
            self.canvas.draw()
            return

        data = df[col].dropna()
        data = pd.to_numeric(data, errors='coerce').dropna()
        if len(data) == 0:
            self.ax.clear()
            self.ax.text(0.5, 0.5, f"Cột '{col}' không có dữ liệu số hợp lệ", ha="center", va="center")
            self.canvas.draw()
            return

        stats = {
            "Count (Mẫu)": len(data),
            "Min (Nhỏ Nhất)": data.min(),
            "Max (Lớn Nhất)": data.max(),
            "Mean (Trung Bình)": data.mean(),
            "Std (Độ Lệch Chuẩn)": data.std(),
            "Median (Trung Vị)": data.median(),
            "Q25 (Bách Phân Vị 25%)": data.quantile(0.25),
            "Q75 (Bách Phân Vị 75%)": data.quantile(0.75)
        }

        for item in self.tree_stats.get_children():
            self.tree_stats.delete(item)
        for k, v in stats.items():
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
            self.tree_stats.insert("", tk.END, values=(k, val_str))

        self.ax.clear()
        time_col = None
        for tc in ["t_now", "time", "Timestamp", "t"]:
            if tc in df.columns and tc != col:
                time_col = tc
                break

        if time_col:
            t_vec = df[time_col]
            x_label = f"Mốc Thời Gian ({time_col})"
        else:
            t_vec = np.arange(len(data))
            x_label = "Mẫu Dữ Liệu (Index)"

        if "Chuỗi Thời Gian" in chart_type:
            self.ax.plot(t_vec, data, label=f"{col}", color="#1e3d59", linewidth=1.2)
            self.ax.axhline(stats["Mean (Trung Bình)"], color="red", linestyle="--", label=f"Mean: {stats['Mean (Trung Bình)']:.2f}")
            self.ax.axhline(stats["Min (Nhỏ Nhất)"], color="green", linestyle=":", label=f"Min: {stats['Min (Nhỏ Nhất)']:.2f}")
            self.ax.axhline(stats["Max (Lớn Nhất)"], color="orange", linestyle=":", label=f"Max: {stats['Max (Lớn Nhất)']:.2f}")
            self.ax.set_xlabel(x_label)
            self.ax.set_ylabel(col)
            self.ax.set_title(f"Chuỗi Thời Gian & Chỉ Số Thống Kê Dữ Liệu {col}")
            self.ax.legend(loc="upper right")
            self.ax.grid(True, linestyle=":", alpha=0.6)

        elif "Cột Thống Kê" in chart_type:
            metrics_labels = ["Min", "Q25", "Median", "Mean", "Q75", "Max"]
            metrics_keys = ["Min (Nhỏ Nhất)", "Q25 (Bách Phân Vị 25%)", "Median (Trung Vị)", "Mean (Trung Bình)", "Q75 (Bách Phân Vị 75%)", "Max (Lớn Nhất)"]
            vals = [stats[k] for k in metrics_keys]

            bars = self.ax.bar(metrics_labels, vals, color=["#e74c3c", "#f39c12", "#2ecc71", "#3498db", "#9b59b6", "#e67e22"])
            self.ax.set_ylabel("Giá Trị")
            self.ax.set_title(f"Các Đại Lượng Thống Kê Của Cột {col}")
            for bar in bars:
                yval = bar.get_height()
                self.ax.text(bar.get_x() + bar.get_width() / 2.0, yval, f"{yval:.2f}", ha='center', va='bottom' if yval >= 0 else 'top', fontsize=9)
            self.ax.grid(True, axis='y', linestyle=":", alpha=0.6)

        elif "Phân Phối" in chart_type:
            self.ax.hist(data, bins=30, color="#17b978", edgecolor="black", alpha=0.7)
            self.ax.axvline(stats["Mean (Trung Bình)"], color="red", linestyle="--", linewidth=2, label=f"Mean: {stats['Mean (Trung Bình)']:.2f}")
            self.ax.axvline(stats["Median (Trung Vị)"], color="blue", linestyle="-.", linewidth=2, label=f"Median: {stats['Median (Trung Vị)']:.2f}")
            self.ax.set_xlabel(col)
            self.ax.set_ylabel("Tần Suất")
            self.ax.set_title(f"Biểu Đồ Phân Phối Tần Suất Của {col}")
            self.ax.legend(loc="upper right")
            self.ax.grid(True, linestyle=":", alpha=0.6)

        self.fig.tight_layout()
        self.canvas.draw()
