# -*- coding: utf-8 -*-
"""
Module: steps/step3_segment_filter.py
Chuc nang: Step 3 - Tu dong loc doan GPS theo van toc va tin hieu IMU,
           phan nhom xac suat quang duong, dong bo tin hieu IMU (Sin/Cos
           Euler & phan nhom dong hoc BNO055), va lien thong xuat #ID doan
           duong sang Step 4 (ban do).
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
from core.sensor_schema import calculate_cumulative_distance, add_trig_features
from core.ui_widgets import bind_instant_combobox, ScienceInfoPanel, lock_treeview_columns, add_chart_zoom_button


class Step3SegmentFilter(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}
        self.detected_segments = []
        self.active_segment = None

        # Danh muc phan nhom cac bien cam bien BNO055 chuan xac theo tai lieu
        self.longitudinal_cols = ["acc_y", "gyr_x", "sin_pitch", "cos_pitch"]
        self.lateral_cols = ["acc_x", "gyr_y", "gyr_z", "sin_roll", "cos_roll", "sin_yaw", "cos_yaw"]
        self.vertical_cols = ["acc_z"]
        self.all_cols = self.longitudinal_cols + self.lateral_cols + self.vertical_cols

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=4, pady=(4, 0))

        # NUA TREN (chieu cao co dinh, khong keo gian): Cau hinh loc GPS &
        # Danh sach phan nhom theo xac suat quang duong
        top_frame = ttk.Frame(self)
        top_frame.pack(fill=tk.X, padx=4, pady=(4, 2))

        filter_cfg_frame = ttk.LabelFrame(top_frame, text="Filter Config", padding=4)
        filter_cfg_frame.pack(fill=tk.X, pady=(0, 4))

        def _lbl(text, col):
            ttk.Label(filter_cfg_frame, text=text).grid(row=0, column=col, padx=(6, 1), pady=3, sticky=tk.E)

        def _entry(default, col, width=5):
            e = ttk.Entry(filter_cfg_frame, width=width)
            e.insert(0, default)
            e.grid(row=0, column=col, padx=(0, 2), pady=3, sticky=tk.W)
            return e

        _lbl("VMin:", 0)
        self.ent_v_min = _entry("40.0", 1)

        _lbl("VMax:", 2)
        self.ent_v_max = _entry("60.0", 3)

        _lbl("VExcl:", 4)
        self.ent_v_exclude = _entry("5.0", 5)

        _lbl("MinDur:", 6)
        self.ent_min_dur = _entry("1.0", 7)

        _lbl("IMUCol:", 8)
        self.cbo_imu_filter_col = ttk.Combobox(filter_cfg_frame, values=["None"] + self.all_cols, width=10, state="readonly")
        self.cbo_imu_filter_col.current(0)
        self.cbo_imu_filter_col.grid(row=0, column=9, padx=(0, 2), pady=3, sticky=tk.W)

        _lbl("Min:", 10)
        self.ent_imu_min = _entry("-10.0", 11)

        _lbl("Max:", 12)
        self.ent_imu_max = _entry("10.0", 13)

        _lbl("SpdMode:", 14)
        self.cbo_speed_mode = ttk.Combobox(filter_cfg_frame, values=["Include", "Exclude"], state="readonly", width=8)
        self.cbo_speed_mode.current(0)
        self.cbo_speed_mode.grid(row=0, column=15, padx=(0, 2), pady=3, sticky=tk.W)

        _lbl("IMUMode:", 16)
        self.cbo_imu_mode = ttk.Combobox(filter_cfg_frame, values=["Include", "Exclude"], state="readonly", width=8)
        self.cbo_imu_mode.current(0)
        self.cbo_imu_mode.grid(row=0, column=17, padx=(0, 2), pady=3, sticky=tk.W)

        btn_run = ttk.Button(filter_cfg_frame, text="🔍 Run Filter", style="Accent.TButton", command=self._run_gps_filter)
        btn_run.grid(row=0, column=18, padx=(10, 4), pady=3, sticky=tk.NSEW)

        # 2 block khit theo chieu rong noi dung (khong expand) de khong con
        # khoang trong thua trong khung: Block trai = cac Tab Nhom (Treeview
        # Fix Width), Block phai = Thong Tin Chi Tiet Doan Duong.
        results_split = ttk.Frame(top_frame)
        results_split.pack(fill=tk.X)

        list_container = ttk.LabelFrame(results_split, text="Segment Groups (#ID)", padding=4)
        list_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))

        self.nb_groups = ttk.Notebook(list_container)
        self.nb_groups.pack(fill=tk.BOTH, expand=True)

        info_frame = ttk.LabelFrame(results_split, text="Segment Info", padding=4)
        info_frame.pack(side=tk.LEFT, fill=tk.Y)

        self.txt_info = tk.Text(info_frame, wrap=tk.WORD, font=("Consolas", 9, "bold"), width=42, height=10, bg="#f0f4f8", fg="#1e3d59")
        self.txt_info.pack(fill=tk.BOTH, expand=True)

        # NUA DUOI (chiem toan bo khong gian con lai): Do thi IMU & Bo chon
        # nhom dong hoc BNO055
        bottom_frame = ttk.LabelFrame(self, text="Biểu Đồ Tín Hiệu IMU Đồng Bộ Theo #ID Đoạn Đường", padding=4)
        bottom_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        imu_ctrl_bar = ttk.Frame(bottom_frame)
        imu_ctrl_bar.pack(fill=tk.X, padx=4, pady=2)

        ttk.Label(imu_ctrl_bar, text="Chọn Nhóm Biến IMU:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 4))
        self.cbo_imu_group = ttk.Combobox(
            imu_ctrl_bar,
            values=["Tất Cả Khí Cụ", "Động Học Dọc (Longitudinal)", "Động Học Ngang (Lateral)", "Động Học Đứng (Vertical)", "Xóa Chọn Tất Cả"],
            state="readonly", width=26
        )
        self.cbo_imu_group.current(0)
        self.cbo_imu_group.pack(side=tk.LEFT, padx=4)
        bind_instant_combobox(self.cbo_imu_group, self._on_imu_group_changed)

        btn_export = ttk.Button(imu_ctrl_bar, text="💾 Gán Nhãn & Xuất Đoạn IMU", command=self._export_segment)
        btn_export.pack(side=tk.RIGHT, padx=4)

        add_chart_zoom_button(
            imu_ctrl_bar, lambda: self.fig, title="Biểu Đồ Tín Hiệu IMU - Step 3"
        ).pack(side=tk.RIGHT, padx=4)

        chk_container = ttk.Frame(bottom_frame)
        chk_container.pack(fill=tk.X, padx=4, pady=2)

        self.imu_check_vars = {}

        frame_long = ttk.LabelFrame(chk_container, text="↔️ Trục Dọc Thân Xe", padding=2)
        frame_long.pack(side=tk.LEFT, padx=3, fill=tk.Y)
        for col in self.longitudinal_cols:
            var = tk.BooleanVar(value=True)
            self.imu_check_vars[col] = var
            ttk.Checkbutton(frame_long, text=col, variable=var, command=self._plot_active_segment_imu).pack(side=tk.LEFT, padx=2)

        frame_lat = ttk.LabelFrame(chk_container, text="↕️ Trục Ngang Thân Xe", padding=2)
        frame_lat.pack(side=tk.LEFT, padx=3, fill=tk.Y)
        for col in self.lateral_cols:
            var = tk.BooleanVar(value=True)
            self.imu_check_vars[col] = var
            ttk.Checkbutton(frame_lat, text=col, variable=var, command=self._plot_active_segment_imu).pack(side=tk.LEFT, padx=2)

        frame_vert = ttk.LabelFrame(chk_container, text="⬆️ Trục Đứng", padding=2)
        frame_vert.pack(side=tk.LEFT, padx=3, fill=tk.Y)
        for col in self.vertical_cols:
            var = tk.BooleanVar(value=True)
            self.imu_check_vars[col] = var
            ttk.Checkbutton(frame_vert, text=col, variable=var, command=self._plot_active_segment_imu).pack(side=tk.LEFT, padx=2)

        self.fig = Figure(figsize=(7, 3.5), dpi=100)
        self.ax = self.fig.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(self.fig, master=bottom_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.toolbar = NavigationToolbar2Tk(self.canvas, bottom_frame)
        self.toolbar.update()

        self._plot_active_segment_imu()

    def _on_imu_group_changed(self):
        choice = self.cbo_imu_group.get()
        if "Tất Cả" in choice and "Xóa" not in choice:
            for col, var in self.imu_check_vars.items():
                var.set(True)
        elif "Xóa" in choice:
            for col, var in self.imu_check_vars.items():
                var.set(False)
        elif "Dọc" in choice:
            for col, var in self.imu_check_vars.items():
                var.set(col in self.longitudinal_cols)
        elif "Ngang" in choice:
            for col, var in self.imu_check_vars.items():
                var.set(col in self.lateral_cols)
        elif "Đứng" in choice:
            for col, var in self.imu_check_vars.items():
                var.set(col in self.vertical_cols)

        self._plot_active_segment_imu()

    def _on_data_changed(self):
        pass

    def _run_gps_filter(self):
        if self.state.df_gps is None:
            messagebox.showwarning("Cảnh báo", "Chưa nạp dữ liệu GPS!")
            return

        try:
            v_min = float(self.ent_v_min.get())
            v_max = float(self.ent_v_max.get())
            v_ex = float(self.ent_v_exclude.get())
            min_dur = float(self.ent_min_dur.get())

            self.current_imu_col = self.cbo_imu_filter_col.get()
            if self.current_imu_col != "None":
                self.current_imu_min = float(self.ent_imu_min.get())
                self.current_imu_max = float(self.ent_imu_max.get())
            else:
                self.current_imu_min = None
                self.current_imu_max = None

            self.current_speed_mode = "exclude" if self.cbo_speed_mode.get() == "Exclude" else "include"
            self.current_imu_mode = "exclude" if self.cbo_imu_mode.get() == "Exclude" else "include"
        except ValueError:
            messagebox.showerror("Lỗi", "Các giá trị bộ lọc phải là số!")
            return

        if self.current_imu_col != "None" and self.state.df_imu is None:
            messagebox.showwarning("Cảnh báo", "Bạn đã cấu hình lọc IMU nhưng hệ thống chưa được nạp dữ liệu IMU. Sẽ bỏ qua điều kiện lọc IMU!")

        df = self.state.df_gps[self.state.df_gps["speed_kmh"] >= v_ex].copy()
        if len(df) == 0:
            messagebox.showinfo("Thông báo", f"Không có dữ liệu GPS nào thỏa mãn speed >= {v_ex} km/h!")
            return

        df = df.sort_values("t_now").reset_index(drop=True)

        in_speed_range = (df["speed_kmh"] >= v_min) & (df["speed_kmh"] <= v_max)
        cond = ~in_speed_range if self.current_speed_mode == "exclude" else in_speed_range

        self.detected_segments = []
        current_rows = []

        for idx, row in df.iterrows():
            if cond.iloc[idx]:
                if current_rows and (row["t_now"] - current_rows[-1]["t_now"] > 1.5):
                    self._save_segment(current_rows, min_dur)
                    current_rows = []
                current_rows.append(row)
            else:
                if current_rows:
                    self._save_segment(current_rows, min_dur)
                    current_rows = []

        if current_rows:
            self._save_segment(current_rows, min_dur)

        if not self.detected_segments:
            messagebox.showinfo("Thông báo", "Không tìm thấy đoạn đường nào thỏa mãn các điều kiện lọc (vận tốc hoặc IMU)!")
            self.state.detected_segments = []
            self.state.selected_segment = None
            self.state.notify_data_changed()
            return

        self.detected_segments.sort(key=lambda x: x["distance"], reverse=True)
        self.state.detected_segments = self.detected_segments

        self._group_segments_by_probability()

    def _save_segment(self, rows, min_dur):
        df_seg = pd.DataFrame(rows)
        t_start = df_seg["t_now"].iloc[0]
        t_end = df_seg["t_now"].iloc[-1]
        dur = t_end - t_start

        if dur >= min_dur or len(df_seg) == 1:
            if getattr(self, "current_imu_col", "None") != "None" and self.state.df_imu is not None:
                imu_sub = self.state.df_imu[(self.state.df_imu["time"] >= t_start) & (self.state.df_imu["time"] <= t_end)]
                if not imu_sub.empty and self.current_imu_col in imu_sub.columns:
                    col_min = imu_sub[self.current_imu_col].min()
                    col_max = imu_sub[self.current_imu_col].max()
                    fully_in_range = (col_min >= self.current_imu_min) and (col_max <= self.current_imu_max)
                    if getattr(self, "current_imu_mode", "include") == "exclude":
                        if fully_in_range:
                            return
                    else:
                        if not fully_in_range:
                            return

            dist = calculate_cumulative_distance(df_seg)
            avg_speed = df_seg["speed_kmh"].mean()
            min_speed = df_seg["speed_kmh"].min()
            max_speed = df_seg["speed_kmh"].max()
            lat_start, lon_start = df_seg["lat"].iloc[0], df_seg["lon"].iloc[0]
            lat_end, lon_end = df_seg["lat"].iloc[-1], df_seg["lon"].iloc[-1]

            self.detected_segments.append({
                "t_start": t_start,
                "t_end": t_end,
                "duration": dur,
                "distance": dist,
                "avg_speed": avg_speed,
                "min_speed": min_speed,
                "max_speed": max_speed,
                "lat_start": lat_start,
                "lon_start": lon_start,
                "lat_end": lat_end,
                "lon_end": lon_end,
            })

    def _group_segments_by_probability(self):
        """Chia cac doan duong thanh cac nhom xac suat quang duong va hien thi thanh cac Tab"""
        for tab in self.nb_groups.tabs():
            self.nb_groups.forget(tab)

        dists = [s["distance"] for s in self.detected_segments]
        total_n = len(self.detected_segments)

        if total_n >= 3:
            q33 = np.percentile(dists, 33.3)
            q66 = np.percentile(dists, 66.7)

            g_short = [s for s in self.detected_segments if s["distance"] < q33]
            g_mid = [s for s in self.detected_segments if q33 <= s["distance"] < q66]
            g_long = [s for s in self.detected_segments if s["distance"] >= q66]

            groups = [
                ("Short", g_short),
                ("Mid", g_mid),
                ("Long", g_long),
            ]
        elif total_n == 2:
            med = np.median(dists)
            g1 = [s for s in self.detected_segments if s["distance"] < med]
            g2 = [s for s in self.detected_segments if s["distance"] >= med]
            groups = [
                ("Short", g1),
                ("Long", g2),
            ]
        else:
            groups = [("All", self.detected_segments)]

        global_id = 1
        for _, seg_list in groups:
            for seg in seg_list:
                seg["id"] = global_id
                global_id += 1

        if total_n >= 2:
            groups.append(("All", self.detected_segments))

        for group_title, seg_list in groups:
            if not seg_list:
                continue

            frame_tab = ttk.Frame(self.nb_groups)
            self.nb_groups.add(frame_tab, text=f"{group_title} ({len(seg_list)})")

            cols = ("ID", "Distance", "Duration", "AvgSpeed", "Start", "End")
            tree = ttk.Treeview(frame_tab, columns=cols, show="headings", height=10)
            tree.heading("ID", text="ID")
            tree.heading("Distance", text="Distance(m)")
            tree.heading("Duration", text="Duration(s)")
            tree.heading("AvgSpeed", text="AvgSpeed(km/h)")
            tree.heading("Start", text="Start(s)")
            tree.heading("End", text="End(s)")

            tree.column("ID", width=40, anchor=tk.CENTER, stretch=False)
            tree.column("Distance", width=95, anchor=tk.E, stretch=False)
            tree.column("Duration", width=85, anchor=tk.E, stretch=False)
            tree.column("AvgSpeed", width=100, anchor=tk.E, stretch=False)
            tree.column("Start", width=70, anchor=tk.E, stretch=False)
            tree.column("End", width=70, anchor=tk.E, stretch=False)

            # Fix ca chieu doc (height=10 dong, khong cho pack keo gian them)
            # lan cot (stretch=False + khoa thao tac keo thu cong tren header)
            tree.pack(fill=tk.X, expand=False)
            lock_treeview_columns(tree)

            for seg in seg_list:
                tree.insert("", tk.END, values=(
                    f"#{seg['id']}",
                    f"{seg['distance']:.2f}",
                    f"{seg['duration']:.3f}",
                    f"{seg['avg_speed']:.2f}",
                    f"{seg['t_start']:.3f}",
                    f"{seg['t_end']:.3f}"
                ))

            tree.bind("<<TreeviewSelect>>", lambda e, t=tree, sl=seg_list: self._on_tree_select(t, sl))

        if self.nb_groups.tabs():
            first_tab = self.nb_groups.tabs()[0]
            first_frame = self.nb_groups.nametowidget(first_tab)
            for child in first_frame.winfo_children():
                if isinstance(child, ttk.Treeview):
                    items = child.get_children()
                    if items:
                        child.selection_set(items[0])
                        self._on_tree_select(child, groups[0][1])
                        break

    def _on_tree_select(self, tree_widget, seg_list):
        selected = tree_widget.selection()
        if not selected:
            return

        item = tree_widget.item(selected[0])
        seg_id_str = str(item["values"][0]).replace("#", "")
        seg_id = int(seg_id_str)

        seg = next((s for s in seg_list if s["id"] == seg_id), None)
        if not seg:
            seg = next((s for s in self.detected_segments if s["id"] == seg_id), None)

        if seg:
            self.active_segment = seg
            self.state.selected_segment = seg

            output_text = (
                f"-> Tìm thấy đoạn đường:\n"
                f"   + Timestamp bắt đầu -> kết thúc: {seg['t_start']:.3f} -> {seg['t_end']:.3f} ({seg['duration']:.3f} giây)\n"
                f"   + Tọa độ bắt đầu [lat, lon]: [{seg['lat_start']:.6f}, {seg['lon_start']:.6f}]\n"
                f"   + Tọa độ kết thúc [lat, lon]: [{seg['lat_end']:.6f}, {seg['lon_end']:.6f}]\n"
                f"   + Quãng đường đi được: {seg['distance']:.2f} mét\n"
                f"   + Vận tốc trung bình đoạn: {seg['avg_speed']:.2f} km/h (Min-Max: {seg['min_speed']:.2f} - {seg['max_speed']:.2f})"
            )

            self.txt_info.delete("1.0", tk.END)
            self.txt_info.insert(tk.END, output_text)

            self._plot_active_segment_imu(seg)
            self.state.notify_data_changed()

    def _plot_active_segment_imu(self, seg=None):
        if seg is None:
            seg = getattr(self, "active_segment", None)

        if seg is None:
            self.ax.clear()
            self.ax.text(0.5, 0.5, "Chưa chọn đoạn đường - hãy Run Filter và chọn 1 dòng trong danh sách", ha="center", va="center")
            self.fig.tight_layout()
            self.canvas.draw()
            return

        if self.state.df_imu is None:
            self.ax.clear()
            self.ax.text(0.5, 0.5, "Chưa nạp dữ liệu IMU", ha="center", va="center")
            self.fig.tight_layout()
            self.canvas.draw()
            return

        t0, t1 = seg["t_start"], seg["t_end"]
        df_sub = self.state.df_imu[(self.state.df_imu["time"] >= t0) & (self.state.df_imu["time"] <= t1)].copy()

        self.ax.clear()
        if len(df_sub) == 0:
            self.ax.text(0.5, 0.5, "Không có mẫu IMU trong mốc thời gian này", ha="center", va="center")
        else:
            selected_cols = [col for col, var in self.imu_check_vars.items() if var.get()]
            if not selected_cols:
                self.ax.text(0.5, 0.5, "Chọn ít nhất 1 cột IMU để hiển thị", ha="center", va="center")
            else:
                df_sub = add_trig_features(df_sub)

                colors = ["#e74c3c", "#2ecc71", "#3498db", "#9b59b6", "#f1c40f", "#e67e22", "#1abc9c", "#e84393", "#6c5ce7"]

                for idx, col in enumerate(selected_cols):
                    if col in df_sub.columns:
                        vals = df_sub[col].values
                        v_min, v_max = np.nanmin(vals), np.nanmax(vals)

                        if v_max - v_min > 1e-6:
                            norm_vals = (vals - v_min) / (v_max - v_min)
                        else:
                            norm_vals = np.zeros_like(vals) + 0.5

                        color = colors[idx % len(colors)]
                        label_str = f"{col} (Thực tế: {v_min:.1f} ~ {v_max:.1f})"

                        self.ax.plot(
                            df_sub["time"], norm_vals,
                            label=label_str,
                            color=color,
                            linewidth=1.8,
                            alpha=0.85,
                            zorder=idx + 2
                        )

                self.ax.set_xlabel("Timestamp (s)")
                self.ax.set_ylabel("Biên Độ Chuẩn Hóa [0, 1]")
                self.ax.set_title(f"Tín Hiệu IMU Đã Chuẩn Hóa Theo #ID Đoạn #{seg['id']} ({t0:.3f}s -> {t1:.3f}s)")
                self.ax.legend(loc="upper right", framealpha=0.9, fontsize=8)
                self.ax.grid(True, linestyle=":", alpha=0.6)

        self.fig.tight_layout()
        self.canvas.draw()

    def _export_segment(self):
        if not self.active_segment:
            messagebox.showwarning("Cảnh báo", "Vui lòng chọn 1 đoạn đường!")
            return

        seg = self.active_segment
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            initialfile=f"labeled_imu_segment_{seg['id']}.csv"
        )

        if save_path:
            t0, t1 = seg["t_start"], seg["t_end"]
            df_sub = self.state.df_imu[(self.state.df_imu["time"] >= t0) & (self.state.df_imu["time"] <= t1)].copy()
            df_calc = add_trig_features(df_sub)

            df_calc["segment_id"] = seg["id"]
            df_calc["avg_speed_kmh"] = seg["avg_speed"]
            df_calc["distance_m"] = seg["distance"]

            df_calc.to_csv(save_path, index=False)
