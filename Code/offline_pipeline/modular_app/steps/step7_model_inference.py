# -*- coding: utf-8 -*-
"""
Module: steps/step7_model_inference.py
Chuc nang: Step 7 - Suy Luan AI (DAP DI XAY LAI theo logic nhan 34-lop cua
           Step 6/8): kiem tra mo hinh INT8 (.tflite) chay dung tren CAP
           FILE THO moi (RAW_GPS.txt + RAW_ACCELEROMETERS.txt), cho phep
           nhap tan so quet THUC TE cua tung tin hieu (IMU & GPS) de tai
           tao dung pipeline dac trung nhu luc huan luyen, suy luan truot
           cua so, hien ket qua tai giao dien (Treeview + bieu do nhan
           theo thoi gian) va XUAT DONG THOI 1 file report dang VAN BAN
           (.txt) vao detected_events_unified/ai_inference_report.txt.

Toan bo logic tinh toan nam o core/inference_pipeline.py - module nay
chi la GUI/threading.
"""

import os
import threading
import tkinter as tk
import numpy as np
from tkinter import ttk, filedialog, messagebox

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from core.app_state import AppState
from core.ui_widgets import ScienceInfoPanel, lock_treeview_columns, add_chart_zoom_button
from core.label_schema import LABEL_DEFINITIONS
from core import inference_pipeline as inf

NAME_TO_GROUP = {d["name"]: d["group"] for d in LABEL_DEFINITIONS}

_SPEED_FLAGS = ("SPEED_G10", "SPEED_G20", "SPEED_G40", "SPEED_G80")


def _alert_flags(active_flags):
    """Loc bo co NORMAL khoi danh sach hien thi/canh bao - NORMAL van duoc
    model du doan day du (khong doi gi o tang training/inference), chi
    KHONG can noi bat len nguoi dung vi khong phai su kien dang chu y.
    predicted_events/report .txt/class_distribution (core/
    inference_pipeline.py) van giu DAY DU NORMAL cho muc dich phan tich -
    chi loc o lop hien thi GUI nay."""
    return [f for f in active_flags if f != "NORMAL"]


def _infer_speed_group(active_flags):
    """Thay the NAME_TO_GROUP (dua tren 1 nhan dong-tru) khi gom nhom su
    kien AI du doan - mo hinh gio da nhan (active_flags la list cac co doc
    lap dang bat), nen nhom theo co SPEED_* dang bat (gan dung voi "nhom
    toc do" cu G10/G20/G40/G80), hoac "OTHER" neu khong co co toc do nao."""
    for g in _SPEED_FLAGS:
        if g in active_flags:
            return g
    return "OTHER"


class Step7ModelInference(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self.folder = None
        default_folder = self.state.current_dir
        if default_folder and os.path.isdir(default_folder):
            self.folder = default_folder
        self._last_seen_dir = self.folder

        self._busy = False
        self._action_buttons = []
        self.last_result = None
        self._selected_event = None
        self.label_map_path = None

        self._build_ui()
        self._sync_default_model_path()
        self.state.register_data_listener(self._on_data_changed)

    # ============================================================
    # BUILD UI
    # ============================================================

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=4, pady=(4, 0))

        top_bar = ttk.LabelFrame(self, text="Thư Mục Dữ Liệu Thô (RAW_GPS.txt + RAW_ACCELEROMETERS.txt)", padding=6)
        top_bar.pack(fill=tk.X, padx=4, pady=(4, 2))

        row = ttk.Frame(top_bar)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Thư mục:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_folder = ttk.Label(
            row, text=self.folder or "Chưa chọn thư mục",
            foreground="#1e3d59", font=("Consolas", 9)
        )
        self.lbl_folder.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(row, text="Chọn...", command=self._select_folder).pack(side=tk.RIGHT, padx=2)

        cfg_frame = ttk.LabelFrame(self, text="Cấu Hình Suy Luận AI", padding=6)
        cfg_frame.pack(fill=tk.X, padx=4, pady=2)

        grid_frame = ttk.Frame(cfg_frame)
        grid_frame.pack(fill=tk.X)

        freq_box = ttk.LabelFrame(grid_frame, text="Tần Số Quét Tín Hiệu (Hz)", padding=4)
        freq_box.grid(row=0, column=0, padx=3, pady=3, sticky=tk.NW)

        ttk.Label(freq_box, text="IMU (Hz):").grid(row=0, column=0, padx=(2, 2), pady=1, sticky=tk.E)
        self.var_imu_hz = tk.DoubleVar(value=inf.DEFAULT_IMU_HZ)
        ttk.Entry(freq_box, textvariable=self.var_imu_hz, width=8).grid(row=0, column=1, padx=(0, 2), pady=1, sticky=tk.W)

        ttk.Label(freq_box, text="GPS (Hz):").grid(row=1, column=0, padx=(2, 2), pady=1, sticky=tk.E)
        self.var_gps_hz = tk.DoubleVar(value=inf.DEFAULT_GPS_HZ)
        ttk.Entry(freq_box, textvariable=self.var_gps_hz, width=8).grid(row=1, column=1, padx=(0, 2), pady=1, sticky=tk.W)

        infer_box = ttk.LabelFrame(grid_frame, text="Cửa Sổ Suy Luận", padding=4)
        infer_box.grid(row=0, column=1, padx=3, pady=3, sticky=tk.NW)

        ttk.Label(infer_box, text="Stride (s):").grid(row=0, column=0, padx=(2, 2), pady=1, sticky=tk.E)
        self.var_stride = tk.DoubleVar(value=inf.DEFAULT_STRIDE_SEC)
        ttk.Entry(infer_box, textvariable=self.var_stride, width=8).grid(row=0, column=1, padx=(0, 2), pady=1, sticky=tk.W)

        ttk.Label(infer_box, text="Merge Gap (s):").grid(row=1, column=0, padx=(2, 2), pady=1, sticky=tk.E)
        self.var_merge_gap = tk.DoubleVar(value=inf.DEFAULT_MERGE_GAP_SEC)
        ttk.Entry(infer_box, textvariable=self.var_merge_gap, width=8).grid(row=1, column=1, padx=(0, 2), pady=1, sticky=tk.W)

        model_box = ttk.LabelFrame(grid_frame, text="Mô Hình (từ Step 6/Multi-Trip) — Đã Ghim, Không Tự Đổi Theo Thư Mục", padding=4)
        model_box.grid(row=0, column=2, padx=3, pady=3, sticky=tk.NW)

        ttk.Label(model_box, text="Model (.tflite):").grid(row=0, column=0, padx=(2, 2), pady=1, sticky=tk.E)
        self.ent_model_path = ttk.Entry(model_box, width=38)
        self.ent_model_path.grid(row=0, column=1, padx=(0, 2), pady=1, sticky=tk.W)
        ttk.Button(model_box, text="Chọn", width=6, command=self._browse_model).grid(row=0, column=2, padx=2, pady=1)

        # Khong con o chon file nhan rieng - label_map.json duoc TU DONG
        # do tim trong CUNG thu muc voi model .tflite (dung quy uoc xuat
        # file cua core/ai_pipeline.py / multi_trip_pipeline.py: 2 file
        # nay luon nam canh nhau). Chi hien 1 dong trang thai read-only.
        ttk.Label(model_box, text="Label Map (.json):").grid(row=1, column=0, padx=(2, 2), pady=1, sticky=tk.E)
        self.lbl_label_map_status = ttk.Label(
            model_box, text="(chưa có model)", foreground="#777777", font=("Consolas", 8)
        )
        self.lbl_label_map_status.grid(row=1, column=1, columnspan=2, padx=(0, 2), pady=1, sticky=tk.W)

        btn_row = ttk.Frame(cfg_frame)
        btn_row.pack(fill=tk.X, pady=(6, 0))

        self.btn_run = ttk.Button(
            btn_row, text="🔍 Chạy Suy Luận AI", style="Accent.TButton", command=self._start_inference
        )
        self.btn_run.pack(side=tk.LEFT, padx=2)
        self._action_buttons.append(self.btn_run)

        self.btn_view_report = ttk.Button(
            btn_row, text="📄 Xem Báo Cáo Đầy Đủ (.txt)", command=self._show_report, state=tk.DISABLED
        )
        self.btn_view_report.pack(side=tk.LEFT, padx=2)

        self.progress = ttk.Progressbar(cfg_frame, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(6, 2))

        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(cfg_frame, textvariable=self.status_var, foreground="#555555", font=("Segoe UI", 8)).pack(
            fill=tk.X, anchor=tk.W
        )

        result_split = ttk.PanedWindow(self, orient=tk.VERTICAL)
        result_split.pack(fill=tk.BOTH, expand=True, padx=4, pady=(2, 4))

        tree_frame = ttk.LabelFrame(result_split, text="Sự Kiện Dự Đoán (đã gộp cửa sổ liên tiếp cùng nhãn)", padding=4)
        result_split.add(tree_frame, weight=1)

        tree_opts_row = ttk.Frame(tree_frame)
        tree_opts_row.pack(fill=tk.X, pady=(0, 2))
        self.var_group_by_type = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            tree_opts_row, text="Gom theo Nhóm Sự Kiện (G10/G20/G40/G80/Residual)",
            variable=self.var_group_by_type, command=self._populate_tree
        ).pack(side=tk.LEFT)
        ttk.Label(
            tree_opts_row, text="(Gom theo Tab riêng - mỗi Tab đủ cột, click tiêu đề cột để sắp xếp)",
            foreground="#777777", font=("Segoe UI", 8, "italic")
        ).pack(side=tk.LEFT, padx=10)

        # Khung chua: hoac 1 Treeview phang (khong gom nhom) HOAC 1
        # Notebook voi moi Tab = 1 nhom su kien, moi Tab la 1 Treeview DAY
        # DU cot rieng - Tab nhom KHONG o cung cap voi cot du lieu.
        self.tree_body = ttk.Frame(tree_frame)
        self.tree_body.pack(fill=tk.BOTH, expand=True)

        self._tree_cols = ("ID", "Label", "Start(s)", "End(s)", "Dur(s)", "Conf")
        self._tree_col_widths = (50, 190, 80, 80, 70, 70)
        self._tree_col_anchors = (tk.CENTER, tk.W, tk.E, tk.E, tk.E, tk.E)

        self.tree = self._make_event_tree(self.tree_body)
        self._group_notebook = None

        self._sort_reverse = {}

        chart_nb = ttk.Notebook(result_split)
        result_split.add(chart_nb, weight=2)

        # --- Tab A: Nhan Du Doan Theo Thoi Gian (bieu do hien co) ---
        tab_timeline = ttk.Frame(chart_nb)
        chart_nb.add(tab_timeline, text="Nhãn Dự Đoán Theo Thời Gian")

        timeline_toolbar_row = ttk.Frame(tab_timeline)
        timeline_toolbar_row.pack(fill=tk.X)
        add_chart_zoom_button(
            timeline_toolbar_row, lambda: self.fig, title="Nhãn Dự Đoán Theo Thời Gian - Step 7"
        ).pack(side=tk.RIGHT)

        self.fig = Figure(figsize=(8, 3.2), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.text(0.5, 0.5, "Chưa chạy suy luận", ha="center", va="center")

        self.canvas = FigureCanvasTkAgg(self.fig, master=tab_timeline)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, tab_timeline)
        self.toolbar.update()

        # --- Tab B: Tin Hieu IMU cua su kien dang chon (MOI) ---
        tab_signal = ttk.Frame(chart_nb)
        chart_nb.add(tab_signal, text="Tín Hiệu IMU Của Sự Kiện Đã Chọn")

        signal_ctrl_row = ttk.Frame(tab_signal)
        signal_ctrl_row.pack(fill=tk.X, padx=2, pady=2)
        ttk.Label(signal_ctrl_row, text="Kênh:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 4))

        self.signal_channel_vars = {}
        for ch in ("acc_x", "acc_y", "acc_z", "gyr_x", "gyr_y", "gyr_z", "speed_kmh"):
            var = tk.BooleanVar(value=True)
            self.signal_channel_vars[ch] = var
            ttk.Checkbutton(
                signal_ctrl_row, text=ch, variable=var, command=self._plot_selected_event_signal
            ).pack(side=tk.LEFT, padx=2)

        add_chart_zoom_button(
            signal_ctrl_row, lambda: self.fig_signal, title="Tín Hiệu IMU Sự Kiện - Step 7"
        ).pack(side=tk.RIGHT)

        self.fig_signal = Figure(figsize=(8, 3.2), dpi=100)
        self.ax_signal = self.fig_signal.add_subplot(111)
        self.ax_signal.text(0.5, 0.5, "Chọn 1 sự kiện trong danh sách để xem tín hiệu IMU", ha="center", va="center")

        self.canvas_signal = FigureCanvasTkAgg(self.fig_signal, master=tab_signal)
        self.canvas_signal.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.toolbar_signal = NavigationToolbar2Tk(self.canvas_signal, tab_signal)
        self.toolbar_signal.update()

    # ============================================================
    # FOLDER / PATHS
    # ============================================================

    def _sync_default_model_path(self):
        """CHI goi 1 LAN luc mo Tab (khong goi lai khi doi thu muc RAW o
        day hay o Tab khac) - doan 1 goi y ban dau cho model, SAU DO duong
        dan model la co dinh/da ghim cho den khi nguoi dung tu bam "Chọn"
        doi model khac. Model doc lap hoan toan voi thu muc RAW dang test
        (1 model co the dung de test nhieu trip khac nhau)."""
        if not self.folder:
            return
        default_model = os.path.join(
            self.folder, "detected_events_unified", "driversafe_event_classifier_int8.tflite"
        )
        self.ent_model_path.delete(0, tk.END)
        self.ent_model_path.insert(0, default_model)
        self._update_label_map_from_model(default_model)

    def _update_label_map_from_model(self, model_path):
        """Tu dong do tim label_map.json NAM CUNG THU MUC voi model
        .tflite (dung quy uoc xuat file cua core/ai_pipeline.py /
        multi_trip_pipeline.py - 2 file nay luon xuat canh nhau), khong
        can nguoi dung chon file nhan rieng nua."""
        candidate = os.path.join(os.path.dirname(model_path), "label_map.json")
        if os.path.isfile(candidate):
            self.label_map_path = candidate
            self.lbl_label_map_status.config(
                text=f"✓ {os.path.basename(candidate)}", foreground="#1e7d32"
            )
        else:
            self.label_map_path = None
            self.lbl_label_map_status.config(
                text="⚠️ Không tìm thấy label_map.json cùng thư mục model", foreground="#b71c1c"
            )

    def _select_folder(self):
        folder = filedialog.askdirectory(
            title="Chọn thư mục chứa RAW_GPS.txt và RAW_ACCELEROMETERS.txt",
            initialdir=self.folder or self.state.current_dir or os.getcwd()
        )
        if folder:
            self.folder = folder
            self._last_seen_dir = folder
            self.lbl_folder.config(text=folder)

    def _on_data_changed(self):
        """Chi cap nhat thu muc RAW dang test (de biet suy luan tren trip
        nao) - KHONG cham den duong dan model/label_map, vi model da
        duoc GHIM co dinh, doc lap voi thu muc lam viec chung cua app."""
        current_dir = self.state.current_dir
        if not current_dir or current_dir == self._last_seen_dir or not os.path.isdir(current_dir):
            return

        self._last_seen_dir = current_dir
        self.folder = current_dir
        self.lbl_folder.config(text=self.folder)

        self.last_result = None
        self._selected_event = None
        self.btn_view_report.config(state=tk.DISABLED)
        self.tree.delete(*self.tree.get_children())
        self._event_by_iid = {}
        self.ax.clear()
        self.ax.text(0.5, 0.5, "Chưa chạy suy luận", ha="center", va="center")
        self.canvas.draw()
        self._plot_selected_event_signal()
        self.status_var.set("Đã đổi thư mục làm việc — đường dẫn mặc định đã tự động cập nhật.")

    def _browse_model(self):
        f = filedialog.askopenfilename(filetypes=[("TFLite Models", "*.tflite"), ("All Files", "*.*")])
        if f:
            self.ent_model_path.delete(0, tk.END)
            self.ent_model_path.insert(0, f)
            self._update_label_map_from_model(f)

    # ============================================================
    # RUN (ASYNC)
    # ============================================================

    def _set_buttons_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        for b in self._action_buttons:
            b.config(state=state)

    def _log_status(self, msg):
        self.after(0, lambda: self.status_var.set(msg))

    def _start_inference(self):
        if not self.folder or not os.path.isdir(self.folder):
            messagebox.showwarning("Chưa chọn thư mục", "Vui lòng chọn thư mục chứa 2 file RAW trước.")
            return

        model_path = self.ent_model_path.get().strip()

        if not os.path.isfile(model_path):
            messagebox.showerror("Lỗi", f"Không tìm thấy model:\n{model_path}")
            return
        if not self.label_map_path or not os.path.isfile(self.label_map_path):
            messagebox.showerror(
                "Lỗi",
                "Không tìm thấy label_map.json cùng thư mục với model.\n"
                "Hãy chọn lại model (nút \"Chọn\") từ thư mục có đủ 2 file."
            )
            return
        label_map_path = self.label_map_path

        try:
            imu_hz = float(self.var_imu_hz.get())
            gps_hz = float(self.var_gps_hz.get())
            stride_sec = float(self.var_stride.get())
            merge_gap = float(self.var_merge_gap.get())
            if imu_hz <= 0 or gps_hz <= 0 or stride_sec <= 0:
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror("Lỗi", "Tần số IMU/GPS và Stride phải là số dương hợp lệ!")
            return

        if self._busy:
            messagebox.showinfo("Đang chạy", "Suy luận đang chạy, vui lòng đợi hoàn tất.")
            return

        self._busy = True
        self._set_buttons_enabled(False)
        self.btn_view_report.config(state=tk.DISABLED)
        self.progress.start(10)
        self.status_var.set("Đang chạy suy luận AI...")

        folder = self.folder
        thread = threading.Thread(
            target=self._run_thread,
            args=(folder, model_path, label_map_path, imu_hz, gps_hz, stride_sec, merge_gap),
            daemon=True,
        )
        thread.start()

    def _run_thread(self, folder, model_path, label_map_path, imu_hz, gps_hz, stride_sec, merge_gap):
        try:
            result = inf.run_full_inference(
                folder, model_path, label_map_path, imu_hz, gps_hz,
                stride_sec=stride_sec, merge_gap=merge_gap, log=self._log_status,
            )
            self.after(0, lambda r=result: self._done(r))
        except Exception as e:
            err = str(e)
            self.after(0, lambda m=err: self._error(m))

    def _done(self, result):
        self._busy = False
        self._set_buttons_enabled(True)
        self.btn_view_report.config(state=tk.NORMAL)
        self.progress.stop()
        self.last_result = result

        # De Step 4 (Ban Do) mo mo phong 3D dung du lieu cua Step 7 (khong
        # phai doc lai CSV cua Step 8) khi nguoi dung click 1 #ID o day.
        self.state.last_ai_inference = {
            "features_df": result["features_df"],
            "predicted_events": result["predicted_events"],
            "folder": self.folder,
        }

        events = result["predicted_events"]
        n_alert = sum(1 for e in events if _alert_flags(e.get("active_flags", [])))
        comp = result["comparison"]
        status = f"✅ Hoàn tất — {n_alert}/{len(events)} sự kiện đáng chú ý (đã ẩn NORMAL)"
        if comp:
            status += f" | Khớp heuristic: {comp['agreement_pct']:.1f}%"
        status += f" | Báo cáo: {os.path.basename(result['report_path'])}"
        self.status_var.set(status)

        self._populate_tree()
        self._plot_timeline(result)

        messagebox.showinfo(
            "Hoàn tất Suy Luận",
            f"Đã phát hiện {n_alert}/{len(events)} sự kiện đáng chú ý "
            f"(danh sách đã ẩn các sự kiện chỉ có NORMAL — xem báo cáo .txt đầy đủ nếu cần).\n\n"
            + (f"Khớp với nhãn heuristic (Step 8): {comp['agreement_pct']:.1f}%\n\n" if comp else "")
            + f"Báo cáo văn bản đã lưu tại:\n{result['report_path']}"
        )

    def _error(self, message):
        self._busy = False
        self._set_buttons_enabled(True)
        self.progress.stop()
        self.status_var.set("❌ Có lỗi trong quá trình suy luận.")
        messagebox.showerror("Lỗi Suy Luận AI", message[:600])

    # ============================================================
    # TREEVIEW: GROUP BY EVENT-GROUP + SORTABLE COLUMNS
    # ============================================================

    def _event_row_values(self, e):
        alert_flags = _alert_flags(e.get("active_flags", []))
        label = "+".join(alert_flags) if alert_flags else e["event_type"]
        return (
            f"#{e['event_id']}", label,
            f"{e['start_time']:.2f}", f"{e['end_time']:.2f}",
            f"{e['duration']:.2f}", f"{e['mean_confidence']*100:.1f}%"
        )

    def _make_event_tree(self, parent):
        """Tao 1 Treeview DAY DU cac cot (ID/Label/Start/End/Dur/Conf),
        dung lai y het cho ca che do phang va cho TUNG TAB nhom - Tab nhom
        chi la lop dieu huong (Notebook), khong nam cung cap voi cot."""
        tree = ttk.Treeview(parent, columns=self._tree_cols, show="headings", height=7)
        for c, w, a in zip(self._tree_cols, self._tree_col_widths, self._tree_col_anchors):
            tree.heading(c, text=c, command=lambda t=tree, col=c: self._sort_tree_column(t, col))
            tree.column(c, width=w, anchor=a, stretch=False)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        lock_treeview_columns(tree)
        tree.bind("<<TreeviewSelect>>", lambda e, t=tree: self._on_tree_select(t))
        return tree

    def _populate_tree(self):
        self._event_by_iid = {}

        if self._group_notebook is not None:
            self._group_notebook.destroy()
            self._group_notebook = None

        if not self.last_result:
            self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.tree.delete(*self.tree.get_children())
            return

        # Chi hien cac su kien co CO DANG CHU Y (khac NORMAL) trong danh
        # sach "canh bao" nay - predicted_events goc (report .txt, CSV
        # cho Step 4/9, class_distribution) van giu DAY DU, khong doi.
        events = [e for e in self.last_result["predicted_events"]
                  if _alert_flags(e.get("active_flags", []))]

        if self.var_group_by_type.get():
            # An Treeview phang, thay bang 1 Notebook - moi Tab = 1 nhom,
            # moi Tab la 1 Treeview rieng DAY DU 6 cot (ID/Label/Start/
            # End/Dur/Conf), chuyen qua lai giua cac Tab duoc.
            self.tree.pack_forget()

            groups = {}
            for e in events:
                groups.setdefault(_infer_speed_group(e.get("active_flags", [])), []).append(e)

            nb = ttk.Notebook(self.tree_body)
            nb.pack(fill=tk.BOTH, expand=True)
            self._group_notebook = nb

            for group_name in sorted(groups.keys()):
                group_events = groups[group_name]
                tab = ttk.Frame(nb)
                nb.add(tab, text=f"{group_name} ({len(group_events)})")

                group_tree = self._make_event_tree(tab)
                for e in group_events:
                    iid = f"ev_{e['event_id']}"
                    self._event_by_iid[iid] = e
                    group_tree.insert("", tk.END, iid=iid, values=self._event_row_values(e))
        else:
            self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.tree.delete(*self.tree.get_children())
            for e in events:
                iid = f"ev_{e['event_id']}"
                self._event_by_iid[iid] = e
                self.tree.insert("", tk.END, iid=iid, values=self._event_row_values(e))

    def _sort_tree_column(self, tree, col):
        key = (id(tree), col)
        reverse = self._sort_reverse.get(key, False)

        def sort_key(item_id):
            val = tree.set(item_id, col)
            try:
                return (0, float(str(val).replace("%", "").replace("#", "")))
            except ValueError:
                return (1, str(val))

        rows = list(tree.get_children(""))
        rows.sort(key=sort_key, reverse=reverse)
        for i, r in enumerate(rows):
            tree.move(r, "", i)

        self._sort_reverse[key] = not reverse

    def _on_tree_select(self, tree):
        selected = tree.selection()
        if not selected:
            return
        e = self._event_by_iid.get(selected[0])
        if e is None:
            return

        self.state.selected_segment = {
            "id": f"AI:{e['event_id']}",
            "source": "step7_ai",
            "t_start": e["start_time"], "t_end": e["end_time"],
            "event_type": e["event_type"],
            "lat_start": 0.0, "lon_start": 0.0, "lat_end": 0.0, "lon_end": 0.0,
            "distance": 0.0, "avg_speed": 0.0,
        }
        self.state.notify_data_changed()
        self._selected_event = e
        self._plot_selected_event_signal()

    # ============================================================
    # CHART
    # ============================================================

    def _plot_selected_event_signal(self):
        """Ve tin hieu IMU goc (khong chuan hoa truc thoi gian suy luan)
        cho CHINH khoang [start_time, end_time] cua su kien dang duoc
        chon trong Treeview - giup kiem tra truc quan ly do AI du doan
        nhan do (vd thay ro cu phanh gap trong HARD_BRAKE)."""
        self.ax_signal.clear()

        e = getattr(self, "_selected_event", None)
        if e is None or not self.last_result:
            self.ax_signal.text(0.5, 0.5, "Chọn 1 sự kiện trong danh sách để xem tín hiệu IMU", ha="center", va="center")
            self.fig_signal.tight_layout()
            self.canvas_signal.draw()
            return

        features_df = self.last_result.get("features_df")
        if features_df is None:
            self.ax_signal.text(0.5, 0.5, "Không có dữ liệu đặc trưng để hiển thị", ha="center", va="center")
            self.canvas_signal.draw()
            return

        seg = features_df[
            (features_df["time"] >= e["start_time"]) & (features_df["time"] <= e["end_time"])
        ]
        if len(seg) == 0:
            self.ax_signal.text(0.5, 0.5, "Không có mẫu nào trong khoảng thời gian này", ha="center", va="center")
            self.canvas_signal.draw()
            return

        colors = ["#e74c3c", "#2ecc71", "#3498db", "#f1c40f", "#9b59b6", "#1abc9c", "#e67e22"]
        color_idx = 0
        for ch, var in self.signal_channel_vars.items():
            if not var.get() or ch not in seg.columns:
                continue
            vals = seg[ch].to_numpy(dtype=float)
            v_min, v_max = np.nanmin(vals), np.nanmax(vals)
            norm_vals = (vals - v_min) / (v_max - v_min) if (v_max - v_min) > 1e-9 else np.zeros_like(vals) + 0.5

            self.ax_signal.plot(
                seg["time"], norm_vals, label=f"{ch} ({v_min:.2f}~{v_max:.2f})",
                color=colors[color_idx % len(colors)], linewidth=1.5, alpha=0.9
            )
            color_idx += 1

        self.ax_signal.set_title(f"Tín Hiệu IMU — {e['event_type']} (#{e['event_id']})")
        self.ax_signal.set_xlabel("Thời gian (s)")
        self.ax_signal.set_ylabel("Biên độ chuẩn hóa [0, 1]")
        self.ax_signal.grid(True, linestyle=":", alpha=0.6)
        if color_idx > 0:
            self.ax_signal.legend(loc="upper right", fontsize=8)

        self.fig_signal.tight_layout()
        self.canvas_signal.draw()

    def _plot_timeline(self, result):
        """Da nhan: 1 cua so co the mang NHIEU co dong thoi, nen khong con
        ve 1 duong step don (1 gia tri/cua so) nhu don nhan cu - thay bang
        scatter kieu "piano roll": 1 diem cho MOI (cua so, co dang bat)."""
        self.ax.clear()
        records = result["records"]

        if not records:
            self.ax.text(0.5, 0.5, "Không có cửa sổ suy luận nào", ha="center", va="center")
            self.canvas.draw()
            return

        present_flags = sorted({f for r in records for f in r.get("active_flags", [])})
        if not present_flags:
            self.ax.text(0.5, 0.5, "Không có cờ nào được kích hoạt", ha="center", va="center")
            self.canvas.draw()
            return

        y_pos = {name: i for i, name in enumerate(present_flags)}
        xs, ys = [], []
        for r in records:
            for f in r.get("active_flags", []):
                xs.append(r["start_time"])
                ys.append(y_pos[f])

        self.ax.scatter(xs, ys, s=8, color="#1e3d59")
        self.ax.set_yticks(list(y_pos.values()))
        self.ax.set_yticklabels(present_flags, fontsize=8)
        self.ax.set_xlabel("Thời gian (s)")
        self.ax.set_title("Các Cờ Đa Nhãn Theo Thời Gian (1 cửa sổ có thể mang nhiều cờ)")
        self.ax.grid(True, linestyle=":", alpha=0.5)
        self.fig.tight_layout()
        self.canvas.draw()

    # ============================================================
    # REPORT POPUP
    # ============================================================

    def _show_report(self):
        if not self.last_result:
            return

        win = tk.Toplevel(self)
        win.title("Báo Cáo Suy Luận AI (ai_inference_report.txt)")
        win.geometry("760x600")

        ttk.Label(
            win, text=f"Đã lưu tại: {self.last_result['report_path']}",
            font=("Segoe UI", 8, "italic"), padding=6
        ).pack(fill=tk.X)

        frame = ttk.Frame(win)
        frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        txt = tk.Text(frame, wrap=tk.NONE, font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4")
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        txt.insert(tk.END, self.last_result["report_text"])
        txt.config(state=tk.DISABLED)

        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=txt.yview)
        txt.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
