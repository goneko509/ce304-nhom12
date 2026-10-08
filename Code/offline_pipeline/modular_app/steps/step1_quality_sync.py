# -*- coding: utf-8 -*-
"""
Module: steps/step1_quality_sync.py
Chuc nang: Step 1 - ALL-IN-ONE (Phan tich Do tre, Tim phan doan gian doan,
           Dong bo tan so & Gop file CSV)
Dac tinh: Bat buoc dung cot 1 lam timestamp, ep kieu so de bao toan GPS,
          Left Join theo IMU, Noi suy 100% khoang trong (khong con NaN)
          va sinh co chat luong (Data Quality Flags).
"""

import os
import platform
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
import pandas as pd
import numpy as np

from core.app_state import AppState
from core.sensor_schema import haversine_distance
from core.ui_widgets import bind_instant_combobox, ScienceInfoPanel


class Step1QualitySync(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}
        self._custom_dfs_cache = {}
        self.last_saved_path = None

        # Nguong gian doan mac dinh (ms)
        self.threshold_gps_ms = 200
        self.threshold_imu_ms = 1000

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)
        self._update_source_list()

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=5, pady=(5, 0))

        # ==========================================
        # 1. THANH DIEU KHIEN (CONTROL PANEL)
        # ==========================================
        ctrl_frame = ttk.Frame(self)
        ctrl_frame.pack(fill=tk.X, padx=5, pady=5)

        analysis_group = ttk.LabelFrame(ctrl_frame, text="1. Cấu Hình Chọn File & Phân Tích Độ Trễ", padding=8)
        analysis_group.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        ttk.Label(analysis_group, text="Tệp GPS:").grid(row=0, column=0, padx=4, pady=4, sticky=tk.W)
        self.cbo_gps = ttk.Combobox(analysis_group, state="readonly", width=30)
        self.cbo_gps.grid(row=0, column=1, padx=4, pady=4)
        bind_instant_combobox(self.cbo_gps, self._analyze_data)

        ttk.Label(analysis_group, text="Tệp IMU:").grid(row=0, column=2, padx=(15, 4), pady=4, sticky=tk.W)
        self.cbo_imu = ttk.Combobox(analysis_group, state="readonly", width=30)
        self.cbo_imu.grid(row=0, column=3, padx=4, pady=4)
        bind_instant_combobox(self.cbo_imu, self._analyze_data)

        ttk.Label(analysis_group, text="Ngưỡng đứt GPS (ms):").grid(row=1, column=0, padx=4, pady=4, sticky=tk.W)
        self.spin_gps_gap = ttk.Spinbox(analysis_group, from_=50, to=5000, width=10, command=self._analyze_data)
        self.spin_gps_gap.set(self.threshold_gps_ms)
        self.spin_gps_gap.grid(row=1, column=1, padx=4, pady=4, sticky=tk.W)
        self.spin_gps_gap.bind("<Return>", lambda e: self._analyze_data())

        ttk.Label(analysis_group, text="Ngưỡng đứt IMU (ms):").grid(row=1, column=2, padx=(15, 4), pady=4, sticky=tk.W)
        self.spin_imu_gap = ttk.Spinbox(analysis_group, from_=50, to=5000, width=10, command=self._analyze_data)
        self.spin_imu_gap.set(self.threshold_imu_ms)
        self.spin_imu_gap.grid(row=1, column=3, padx=4, pady=4, sticky=tk.W)
        self.spin_imu_gap.bind("<Return>", lambda e: self._analyze_data())

        ttk.Button(analysis_group, text="🔍 Phân Tích", command=self._analyze_data).grid(row=1, column=4, padx=10, pady=4)

        merge_group = ttk.LabelFrame(ctrl_frame, text="2. Cấu Hình Đồng Bộ & Gộp File (Xuất CSV)", padding=8)
        merge_group.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)

        ttk.Label(merge_group, text="Tần số mục tiêu (Hz):").grid(row=0, column=0, padx=4, pady=4, sticky=tk.W)
        self.spin_freq = ttk.Spinbox(merge_group, from_=1, to=1000, width=10)
        self.spin_freq.set(50)
        self.spin_freq.grid(row=0, column=1, padx=4, pady=4, sticky=tk.W)

        btn_run_merge = ttk.Button(merge_group, text="⚡ Gộp & Đồng Bộ", style="Accent.TButton", command=self._run_sync_merge)
        btn_run_merge.grid(row=0, column=2, padx=10, pady=4)

        self.btn_open_folder = ttk.Button(merge_group, text="📂 Mở Thư Mục", command=self._open_output_folder, state=tk.DISABLED)
        self.btn_open_folder.grid(row=1, column=0, columnspan=3, pady=4, sticky=tk.EW)

        # ==========================================
        # 2. KHU VUC NOI DUNG (PANED WINDOW)
        # ==========================================
        self.paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Hang tren: 2 khoi dat canh nhau tren cung 1 dong (Thong tin chung | Cac phan doan)
        top_row = ttk.PanedWindow(self.paned, orient=tk.HORIZONTAL)
        self.paned.add(top_row, weight=3)

        stats_frame = ttk.LabelFrame(top_row, text="Thông Tin Chung & Phân Tích Tần Số", padding=4)
        top_row.add(stats_frame, weight=1)

        self.tree_stats = ttk.Treeview(stats_frame, columns=("Cảm Biến", "Đại Lượng", "Giá Trị"), show="headings", height=8)
        self.tree_stats.heading("Cảm Biến", text="Cảm Biến")
        self.tree_stats.heading("Đại Lượng", text="Đại Lượng")
        self.tree_stats.heading("Giá Trị", text="Giá Trị")
        self.tree_stats.column("Cảm Biến", width=90, minwidth=90, anchor=tk.CENTER, stretch=False)
        self.tree_stats.column("Đại Lượng", width=300, minwidth=300, anchor=tk.W, stretch=False)
        self.tree_stats.column("Giá Trị", width=160, minwidth=160, anchor=tk.E, stretch=False)
        self.tree_stats.pack(fill=tk.BOTH, expand=True)
        self._lock_column_resize(self.tree_stats)

        # Khung gian doan dat ben phai, rong hon vi co nhieu cot du lieu hon
        gaps_frame = ttk.LabelFrame(top_row, text="Các Phân Đoạn Bị Gián Đoạn (Nháy đúp để xem bản đồ)", padding=4)
        top_row.add(gaps_frame, weight=2)

        columns = ("Loại", "Bắt Đầu (s)", "Kết Thúc (s)", "Khoảng Trễ (ms)", "Lat", "Lon", "Link")
        self.tree_gaps = ttk.Treeview(gaps_frame, columns=columns, show="headings", height=8)
        for col in columns:
            self.tree_gaps.heading(col, text=col)

        self.tree_gaps.column("Loại", width=55, minwidth=55, anchor=tk.CENTER, stretch=False)
        self.tree_gaps.column("Bắt Đầu (s)", width=110, minwidth=110, anchor=tk.CENTER, stretch=False)
        self.tree_gaps.column("Kết Thúc (s)", width=110, minwidth=110, anchor=tk.CENTER, stretch=False)
        self.tree_gaps.column("Khoảng Trễ (ms)", width=130, minwidth=130, anchor=tk.CENTER, stretch=False)
        self.tree_gaps.column("Lat", width=85, minwidth=85, anchor=tk.CENTER, stretch=False)
        self.tree_gaps.column("Lon", width=85, minwidth=85, anchor=tk.CENTER, stretch=False)
        self.tree_gaps.column("Link", width=260, minwidth=260, anchor=tk.W, stretch=False)
        self.tree_gaps.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._lock_column_resize(self.tree_gaps)

        scrollbar_gaps = ttk.Scrollbar(gaps_frame, orient=tk.VERTICAL, command=self.tree_gaps.yview)
        self.tree_gaps.configure(yscrollcommand=scrollbar_gaps.set)
        scrollbar_gaps.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_gaps.bind("<Double-1>", self._on_gap_double_click)

        log_frame = ttk.LabelFrame(self.paned, text="Nhật Ký Quá Trình Gộp File & Đồng Bộ Tần Số", padding=4)
        self.paned.add(log_frame, weight=1)

        self.txt_log = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 9), bg="#f8f9fa", fg="#212529", height=6)
        self.txt_log.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        scrollbar_log = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=scrollbar_log.set)
        scrollbar_log.pack(side=tk.RIGHT, fill=tk.Y)

    # ==========================================
    # CAC HAM TIEN ICH UI & APP STATE
    # ==========================================
    def _log(self, message):
        self.txt_log.insert(tk.END, message + "\n")
        self.txt_log.see(tk.END)

    def _lock_column_resize(self, tree):
        """Chan thao tac keo gian do rong cot bang chuot, giu nguyen width da fix sẵn."""
        def _block_drag(event):
            if tree.identify_region(event.x, event.y) == "separator":
                return "break"
        tree.bind("<Button-1>", _block_drag)

    def _update_source_list(self):
        sources = ["Không chọn"]
        if self.state.df_gps is not None or self.state.gps_path:
            gps_file = os.path.basename(self.state.gps_path) if self.state.gps_path else "RAW_GPS"
            sources.append(f"Dữ liệu đã load: GPS ({gps_file})")
        if self.state.df_imu is not None or self.state.imu_path:
            imu_file = os.path.basename(self.state.imu_path) if self.state.imu_path else "RAW_IMU"
            sources.append(f"Dữ liệu đã load: IMU ({imu_file})")

        folder = self.state.current_dir
        if folder and os.path.exists(folder) and os.path.isdir(folder):
            try:
                all_files = sorted(os.listdir(folder))
                for f in all_files:
                    if f.lower().endswith(('.txt', '.csv')):
                        sources.append(f)
            except Exception:
                pass

        sources = list(dict.fromkeys(sources))
        curr_gps, curr_imu = self.cbo_gps.get(), self.cbo_imu.get()
        self.cbo_gps["values"] = self.cbo_imu["values"] = sources

        if curr_gps in sources:
            self.cbo_gps.set(curr_gps)
        elif len(sources) > 1:
            self.cbo_gps.current(1)
        if curr_imu in sources:
            self.cbo_imu.set(curr_imu)
        elif len(sources) > 2:
            self.cbo_imu.current(2)
        else:
            self.cbo_imu.set(self.cbo_gps.get())

    def _get_df_for_source(self, source_name):
        if not source_name or source_name == "Không chọn":
            return None
        if "Dữ liệu đã load: GPS" in source_name:
            return self.state.df_gps
        if "Dữ liệu đã load: IMU" in source_name:
            return self.state.df_imu

        folder = self.state.current_dir
        if not folder or not os.path.exists(folder):
            return None
        filepath = os.path.join(folder, source_name)
        if not os.path.exists(filepath):
            return None
        if filepath in self._custom_dfs_cache:
            return self._custom_dfs_cache[filepath]

        try:
            if filepath.lower().endswith('.csv'):
                df = pd.read_csv(filepath)
            else:
                try:
                    df = pd.read_csv(filepath)
                    if len(df.columns) <= 1:
                        df = pd.read_csv(filepath, sep=r'\s+', engine='python')
                except Exception:
                    df = pd.read_csv(filepath, sep=r'\s+', engine='python')
            self._custom_dfs_cache[filepath] = df
            return df
        except Exception:
            return None

    def _on_data_changed(self):
        self._custom_dfs_cache.clear()
        self._update_source_list()
        self._analyze_data()

    def _on_gap_double_click(self, event):
        region = self.tree_gaps.identify("region", event.x, event.y)
        if region != "cell":
            return
        selected_item = self.tree_gaps.selection()
        if not selected_item:
            return
        item_values = self.tree_gaps.item(selected_item[0], "values")
        link = item_values[6]
        if link.startswith("http"):
            webbrowser.open(link)

    # ==========================================
    # LOGIC TOAN HOC & PHAN TICH
    # ==========================================
    def _analyze_data(self):
        self.tree_stats.delete(*self.tree_stats.get_children())
        self.tree_gaps.delete(*self.tree_gaps.get_children())

        df_gps_raw = self._get_df_for_source(self.cbo_gps.get())
        df_imu_raw = self._get_df_for_source(self.cbo_imu.get())

        try:
            self.threshold_gps_ms = float(self.spin_gps_gap.get())
            self.threshold_imu_ms = float(self.spin_imu_gap.get())
        except ValueError:
            pass

        # --- PHAN TICH GPS ---
        if df_gps_raw is not None and not df_gps_raw.empty:
            df_gps = df_gps_raw.copy()
            gps_names = ["timestamp", "UTC_Time", "lat", "lon", "HDOP", "Altitude", "Fix_Quality", "COG", "speed_kmh", "speed_knots", "Date_DDMMYY", "Satellites"]
            df_gps.columns = gps_names[:len(df_gps.columns)] + list(df_gps.columns[len(gps_names):])

            gps_timestamps = pd.to_numeric(df_gps.iloc[:, 0], errors='coerce')
            t_diff_gps = gps_timestamps.diff() * 1000

            total_time_s = float(gps_timestamps.iloc[-1]) - float(gps_timestamps.iloc[0])
            total_time_m = total_time_s / 60.0

            lats = pd.to_numeric(df_gps['lat'], errors='coerce').values if 'lat' in df_gps.columns else np.array([])
            lons = pd.to_numeric(df_gps['lon'], errors='coerce').values if 'lon' in df_gps.columns else np.array([])

            total_dist = 0.0
            if len(lats) > 1 and len(lons) > 1:
                valid_mask = ~np.isnan(lats) & ~np.isnan(lons)
                valid_lats, valid_lons = lats[valid_mask], lons[valid_mask]
                if len(valid_lats) > 1:
                    total_dist = sum(haversine_distance(valid_lats[i], valid_lons[i], valid_lats[i + 1], valid_lons[i + 1]) for i in range(len(valid_lats) - 1))

            t_diff_clean = t_diff_gps.dropna()
            med_gps = t_diff_clean.median()
            freq_gps = 1000 / med_gps if med_gps > 0 else 0

            gap_indices = t_diff_gps[t_diff_gps > self.threshold_gps_ms].index

            self.tree_stats.insert("", tk.END, values=("GPS", "Tổng thời gian ghi nhận (phút)", f"{total_time_m:.2f}"))
            self.tree_stats.insert("", tk.END, values=("GPS", "Tổng quãng đường (m)", f"{total_dist:.2f}"))
            self.tree_stats.insert("", tk.END, values=("GPS", "Số đoạn bị gián đoạn", f"{len(gap_indices)}"))
            self.tree_stats.insert("", tk.END, values=("GPS", "Độ trễ [Min | Median | Max] (ms)", f"{t_diff_clean.min():.2f} | {med_gps:.2f} | {t_diff_clean.max():.2f}"))
            self.tree_stats.insert("", tk.END, values=("GPS", "Tần số quét trung bình (Hz)", f"{freq_gps:.2f}"))

            for idx in gap_indices:
                pos = t_diff_gps.index.get_loc(idx)
                if isinstance(pos, slice):
                    pos = pos.start
                elif getattr(pos, 'shape', None):
                    pos = np.where(pos)[0][0]

                gap_val = t_diff_gps.iloc[pos]
                end_t = gps_timestamps.iloc[pos]
                start_t = gps_timestamps.iloc[pos - 1] if pos > 0 else end_t

                lat = float(lats[pos - 1]) if pos > 0 and len(lats) > 0 else 0.0
                lon = float(lons[pos - 1]) if pos > 0 and len(lons) > 0 else 0.0
                link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if not np.isnan(lat) else "N/A"
                self.tree_gaps.insert("", tk.END, values=("GPS", f"{start_t}", f"{end_t}", f"{gap_val:.2f}", f"{lat:.6f}", f"{lon:.6f}", link))

            gps_t_floats = gps_timestamps.values

        # --- PHAN TICH IMU ---
        if df_imu_raw is not None and not df_imu_raw.empty:
            df_imu = df_imu_raw.copy()
            imu_names = ["timestamp", "acc_x", "acc_y", "acc_z", "euler_x", "euler_y", "euler_z", "gyro_x", "gyro_y", "gyro_z", "quat_0", "quat_1", "quat_2", "quat_3"]
            df_imu.columns = imu_names[:len(df_imu.columns)] + list(df_imu.columns[len(imu_names):])

            imu_timestamps = pd.to_numeric(df_imu.iloc[:, 0], errors='coerce')
            t_diff_imu = imu_timestamps.diff() * 1000

            t_diff_clean = t_diff_imu.dropna()
            if not t_diff_clean.empty:
                med_imu = t_diff_clean.median()
                freq_imu = 1000 / med_imu if med_imu > 0 else 0
                gap_indices_imu = t_diff_imu[t_diff_imu > self.threshold_imu_ms].index

                self.tree_stats.insert("", tk.END, values=("IMU", "Số đoạn bị gián đoạn", f"{len(gap_indices_imu)}"))
                self.tree_stats.insert("", tk.END, values=("IMU", "Độ trễ [Min | Median | Max] (ms)", f"{t_diff_clean.min():.2f} | {med_imu:.2f} | {t_diff_clean.max():.2f}"))
                self.tree_stats.insert("", tk.END, values=("IMU", "Tần số quét trung bình (Hz)", f"{freq_imu:.2f}"))

                for idx in gap_indices_imu:
                    pos = t_diff_imu.index.get_loc(idx)
                    if isinstance(pos, slice):
                        pos = pos.start
                    elif getattr(pos, 'shape', None):
                        pos = np.where(pos)[0][0]

                    gap_val = t_diff_imu.iloc[pos]
                    end_t = imu_timestamps.iloc[pos]
                    start_t = imu_timestamps.iloc[pos - 1] if pos > 0 else end_t
                    lat_str, lon_str, link = "N/A", "N/A", "N/A"

                    if 'gps_t_floats' in locals() and len(lats) > 1:
                        try:
                            diffs = np.abs(gps_t_floats - float(start_t))
                            nearest_pos = np.nanargmin(diffs)
                            nearest_lat, nearest_lon = float(lats[nearest_pos]), float(lons[nearest_pos])
                            if not np.isnan(nearest_lat) and not np.isnan(nearest_lon):
                                lat_str, lon_str = f"{nearest_lat:.6f}", f"{nearest_lon:.6f}"
                                link = f"https://www.google.com/maps/search/?api=1&query={nearest_lat},{nearest_lon}"
                        except Exception:
                            pass
                    self.tree_gaps.insert("", tk.END, values=("IMU", f"{start_t}", f"{end_t}", f"{gap_val:.2f}", lat_str, lon_str, link))

    # ==========================================
    # LOGIC DONG BO TAN SO & GOP FILE (SYNC MERGE) VA NOI SUY (INTERPOLATE)
    # ==========================================
    def _prepare_time_index(self, df):
        time_col = df.columns[0]
        df_clean = df.copy()

        df_clean = df_clean.apply(pd.to_numeric, errors='coerce')

        if not pd.api.types.is_datetime64_any_dtype(df_clean[time_col]):
            df_clean[time_col] = pd.to_datetime(df_clean[time_col], unit='s', errors='coerce')

        df_clean = df_clean.set_index(time_col)
        df_clean = df_clean[df_clean.index.notnull()]
        df_clean = df_clean.sort_index()
        return df_clean[~df_clean.index.duplicated(keep='first')]

    def _resample_and_interpolate(self, df_clean, freq_str, source_name, ref_col=None):
        """Dong bo tan so, gan co chat luong va noi suy tuyen tinh lap day cho trong"""
        df_resampled = df_clean.resample(freq_str).mean()

        if ref_col is None or ref_col not in df_resampled.columns:
            ref_col = df_resampled.columns[0]

        flag_col_name = f"{source_name.lower()}_is_interpolated"
        df_resampled[flag_col_name] = df_resampled[ref_col].isna().astype(int)

        nans_count = df_resampled[flag_col_name].sum()

        df_resampled = df_resampled.interpolate(method='linear').bfill().ffill()

        if nans_count > 0:
            self._log(f"   -> {source_name}: Đã đánh cờ và NỘI SUY {nans_count} dòng bị khuyết (NaN).")
        else:
            self._log(f"   -> {source_name}: Tín hiệu liên tục, không cần nội suy.")

        return df_resampled

    def _run_sync_merge(self):
        df_gps_raw = self._get_df_for_source(self.cbo_gps.get())
        df_imu_raw = self._get_df_for_source(self.cbo_imu.get())

        if df_gps_raw is None or df_gps_raw.empty:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn hoặc nạp dữ liệu GPS!")
            return
        if df_imu_raw is None or df_imu_raw.empty:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn hoặc nạp dữ liệu IMU!")
            return

        try:
            freq_hz = int(self.spin_freq.get())
            if freq_hz <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Lỗi", "Tần số mục tiêu phải là số nguyên dương!")
            return

        freq_str = f"{int(1000 / freq_hz)}ms"
        df_gps = df_gps_raw.copy()
        df_imu = df_imu_raw.copy()

        self.txt_log.delete("1.0", tk.END)
        self._log(f"--- BẮT ĐẦU ĐỒNG BỘ, NỘI SUY & GỘP (Tần số: {freq_hz} Hz) ---\n")

        try:
            self.btn_open_folder.config(state=tk.DISABLED)

            imu_names = ["timestamp", "acc_x", "acc_y", "acc_z", "euler_x", "euler_y", "euler_z", "gyro_x", "gyro_y", "gyro_z", "quat_0", "quat_1", "quat_2", "quat_3"]
            gps_names = ["timestamp", "UTC_Time", "lat", "lon", "HDOP", "Altitude", "Fix_Quality", "COG", "speed_kmh", "speed_knots", "Date_DDMMYY", "Satellites"]

            df_imu.columns = imu_names[:len(df_imu.columns)] + list(df_imu.columns[len(imu_names):])
            df_gps.columns = gps_names[:len(df_gps.columns)] + list(df_gps.columns[len(gps_names):])

            self._log("1. Chuẩn hóa trục thời gian (DatetimeIndex) & Ép kiểu số toàn bộ dữ liệu...")
            imu_aligned = self._prepare_time_index(df_imu)
            gps_aligned = self._prepare_time_index(df_gps)

            self._log(f"2. Bù khuyết và nội suy dữ liệu TỪNG FILE ĐỘC LẬP...")
            imu_resampled = self._resample_and_interpolate(imu_aligned, freq_str, "IMU", ref_col="acc_x")
            gps_resampled = self._resample_and_interpolate(gps_aligned, freq_str, "GPS", ref_col="lat")

            self._log("\n3. Tiến hành gộp dữ liệu (Ép mốc thời gian theo IMU - Left Join)...")
            merged_df = pd.merge(imu_resampled, gps_resampled, left_index=True, right_index=True, how='left')
            merged_df = merged_df.reset_index()
            merged_df.rename(columns={merged_df.columns[0]: 'timestamp'}, inplace=True)

            if 'gps_is_interpolated' in merged_df.columns:
                merged_df['gps_is_interpolated'] = merged_df['gps_is_interpolated'].fillna(1).astype(int)

            merged_df = merged_df.interpolate(method='linear').bfill().ffill()

            if 'imu_is_interpolated' in merged_df.columns:
                merged_df['imu_is_interpolated'] = merged_df['imu_is_interpolated'].astype(int)
            if 'gps_is_interpolated' in merged_df.columns:
                merged_df['gps_is_interpolated'] = merged_df['gps_is_interpolated'].astype(int)

            output_filename = f"{freq_hz}_hz.csv"
            save_path = os.path.join(self.state.current_dir or os.getcwd(), output_filename)
            merged_df.to_csv(save_path, index=False)
            self.last_saved_path = save_path

            total_rows = len(merged_df)
            imu_interpolated = merged_df['imu_is_interpolated'].sum() if 'imu_is_interpolated' in merged_df.columns else 0
            gps_interpolated = merged_df['gps_is_interpolated'].sum() if 'gps_is_interpolated' in merged_df.columns else 0

            self._log("\n--- HOÀN TẤT ---")
            self._log(f"Kích thước file gộp: {total_rows} dòng x {len(merged_df.columns)} cột.")
            self._log(f"Số mẫu IMU đã nội suy: {imu_interpolated} mẫu ({imu_interpolated/total_rows*100:.2f}%)")
            self._log(f"Số mẫu GPS đã nội suy: {gps_interpolated} mẫu ({gps_interpolated/total_rows*100:.2f}%)")

            self.btn_open_folder.config(state=tk.NORMAL)
            messagebox.showinfo("Thành công", f"Gộp và đồng bộ dữ liệu thành công!\n(Không còn NaN/Missing Data)\nĐã xuất file: {output_filename}")

        except Exception as e:
            self._log(f"\n[LỖI] Quá trình xử lý bị lỗi: {str(e)}")
            messagebox.showerror("Lỗi", f"Xử lý thất bại:\n{str(e)}")

    def _open_output_folder(self):
        if self.last_saved_path and os.path.exists(self.last_saved_path):
            folder_path = os.path.dirname(self.last_saved_path)
            try:
                if platform.system() == "Windows":
                    os.startfile(folder_path)
                elif platform.system() == "Darwin":
                    subprocess.run(["open", folder_path])
                else:
                    subprocess.run(["xdg-open", folder_path])
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể mở thư mục: {str(e)}")
