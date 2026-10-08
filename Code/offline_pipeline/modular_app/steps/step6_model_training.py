# -*- coding: utf-8 -*-
"""
Module: steps/step6_model_training.py
Chuc nang: Step 6 - Huan Luyen Mo Hinh AI (CAU HINH LAI TOAN BO theo logic
           nhan cua Step 8 + file CSV final):

    Labeled Windowing (merged_motion_features.csv + nhan tu
    detected_driving_events_unified.csv)
        -> Train Multi-Class Classifier 33 nhan (du lieu chuoi thoi gian
           IMU+GPS, kien truc Multi-scale Conv1D + SE + Residual)
        -> INT8 Quantization (TFLite, trien khai Raspberry Pi)

Dinh nghia nhan RO RANG, co dinh: core/label_schema.py (co nut "Xem Dinh
Nghia Nhan" ngay tren GUI) + file label_map.json xuat kem model .tflite.

Toan bo logic tinh toan nam o core/ai_pipeline.py (dung chung voi CLI:
python -m core.ai_pipeline <folder>) - module nay chi la GUI/threading.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'

from core.app_state import AppState
from core.ui_widgets import ScienceInfoPanel, lock_treeview_columns
from core.label_schema import LABEL_DEFINITIONS, NUM_CLASSES, NUM_FLAGS
from core import ai_pipeline as pipe


DEFAULT_PARAMS = {
    "fs": pipe.FS_IMU,
    "window_sec": pipe.WINDOW_SEC,
    "stride_sec": pipe.STRIDE_SEC,
    "purity_threshold": pipe.PURITY_THRESHOLD,
    "train_ratio": pipe.TRAIN_RATIO,
    "val_ratio": pipe.VAL_RATIO,
    "epochs": 30,
    "batch_size": 64,
    "learning_rate": 1e-3,
}

PARAM_FIELDS = [
    ("Windowing & Nhãn", [
        ("fs", "FS IMU (Hz)", 7),
        ("window_sec", "Window (s)", 6),
        ("stride_sec", "Stride (s)", 6),
        ("purity_threshold", "Purity (0-1)", 6),
    ]),
    ("Train / Val / Test & Huấn Luyện", [
        ("train_ratio", "Train Ratio", 6),
        ("val_ratio", "Val Ratio", 6),
        ("epochs", "Epochs", 6),
        ("batch_size", "Batch Size", 6),
        ("learning_rate", "Learning Rate", 8),
    ]),
]


class Step6ModelTraining(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self.folder = self._auto_events_dir()
        self._last_seen_dir = self.state.current_dir

        self.vars = {}
        self._busy = False
        self._action_buttons = []

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)

    # ============================================================
    # BUILD UI
    # ============================================================

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=4, pady=(4, 0))

        top_bar = ttk.LabelFrame(self, text="Thư Mục detected_events_unified/ (CSV Final từ Step 8)", padding=6)
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

        cfg_frame = ttk.LabelFrame(self, text="Cấu Hình Pipeline (Windowing → Train → INT8 Quantize)", padding=6)
        cfg_frame.pack(fill=tk.X, padx=4, pady=2)

        grid_frame = ttk.Frame(cfg_frame)
        grid_frame.pack(fill=tk.X)
        for col, (cat_name, fields) in enumerate(PARAM_FIELDS):
            cat_box = ttk.LabelFrame(grid_frame, text=cat_name, padding=4)
            cat_box.grid(row=0, column=col, padx=3, pady=3, sticky=tk.NW)
            for fi, (key, label_text, width) in enumerate(fields):
                var = tk.DoubleVar(value=DEFAULT_PARAMS[key])
                self.vars[key] = var
                ttk.Label(cat_box, text=label_text + ":").grid(row=fi, column=0, padx=(2, 2), pady=1, sticky=tk.E)
                ttk.Entry(cat_box, textvariable=var, width=width).grid(row=fi, column=1, padx=(0, 2), pady=1, sticky=tk.W)

        btn_row = ttk.Frame(cfg_frame)
        btn_row.pack(fill=tk.X, pady=(4, 0))

        ttk.Button(btn_row, text="↺ Default", command=self._reset_defaults).pack(side=tk.LEFT, padx=2)

        ttk.Button(
            btn_row, text=f"📄 Xem Định Nghĩa Nhãn ({NUM_CLASSES} lớp)", command=self._show_label_definitions
        ).pack(side=tk.LEFT, padx=2)

        self.btn_run = ttk.Button(
            btn_row, text="🚀 Chạy Pipeline (Windowing → Train → INT8)",
            style="Accent.TButton", command=self._start_pipeline
        )
        self.btn_run.pack(side=tk.LEFT, padx=2)
        self._action_buttons.append(self.btn_run)

        self.progress = ttk.Progressbar(cfg_frame, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(6, 2))

        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(cfg_frame, textvariable=self.status_var, foreground="#555555", font=("Segoe UI", 8)).pack(
            fill=tk.X, anchor=tk.W
        )

        result_split = ttk.PanedWindow(self, orient=tk.VERTICAL)
        result_split.pack(fill=tk.BOTH, expand=True, padx=4, pady=(2, 4))

        tree_frame = ttk.LabelFrame(result_split, text="Phân Bố Nhãn Trong Cửa Sổ Huấn Luyện (sau Windowing)", padding=4)
        result_split.add(tree_frame, weight=1)

        cols = ("Label", "Group", "Count")
        self.tree_dist = ttk.Treeview(tree_frame, columns=cols, show="headings", height=6)
        widths = (220, 140, 90)
        for c, w in zip(cols, widths):
            self.tree_dist.heading(c, text=c)
            self.tree_dist.column(c, width=w, anchor=tk.W if c != "Count" else tk.E, stretch=False)
        self.tree_dist.pack(fill=tk.BOTH, expand=True)
        lock_treeview_columns(self.tree_dist)

        log_frame = ttk.LabelFrame(result_split, text="Console Huấn Luyện AI (Log)", padding=4)
        result_split.add(log_frame, weight=2)

        self.txt_log = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 9), bg="#1e1e1e", fg="#00ff88")
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.txt_log.yview)
        self.txt_log.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # ============================================================
    # LABEL DEFINITIONS POPUP (YEU CAU: "CO FILE DINH NGHIA NHAN RO RANG")
    # ============================================================

    def _show_label_definitions(self):
        win = tk.Toplevel(self)
        win.title(f"Định Nghĩa Nhãn ({NUM_CLASSES} lớp) — core/label_schema.py")
        win.geometry("760x480")

        ttk.Label(
            win, text=f"Tập nhãn cố định dùng cho huấn luyện & suy luận ({NUM_CLASSES} lớp, "
                      f"xuất kèm model dưới dạng label_map.json)",
            font=("Segoe UI", 9, "bold"), padding=6
        ).pack(fill=tk.X)

        cols = ("ID", "Name", "Display Name", "Group", "Label Source")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=20)
        widths = (36, 170, 260, 90, 140)
        anchors = (tk.CENTER, tk.W, tk.W, tk.CENTER, tk.W)
        for c, w, a in zip(cols, widths, anchors):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor=a, stretch=False)
        tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        lock_treeview_columns(tree)

        for d in LABEL_DEFINITIONS:
            tree.insert("", tk.END, values=(d["id"], d["name"], d["display_name"], d["group"], d["label_source"]))

        scrollbar = ttk.Scrollbar(win, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)

    # ============================================================
    # CONFIG HELPERS
    # ============================================================

    def _reset_defaults(self):
        for key, var in self.vars.items():
            var.set(DEFAULT_PARAMS[key])

    def _pull_params(self):
        return {key: var.get() for key, var in self.vars.items()}

    # ============================================================
    # FOLDER
    # ============================================================

    def _select_folder(self):
        folder = filedialog.askdirectory(
            title="Chọn thư mục detected_events_unified/ (chứa merged_motion_features.csv & "
                  "detected_driving_events_unified.csv)",
            initialdir=self.folder or self.state.current_dir or os.getcwd()
        )
        if folder:
            self.folder = folder
            self.lbl_folder.config(text=folder)

    def _auto_events_dir(self):
        current_dir = self.state.current_dir
        if not current_dir:
            return None
        events_dir = os.path.join(current_dir, "detected_events_unified")
        return events_dir if os.path.isdir(events_dir) else None

    def _on_data_changed(self):
        """Tu dong cap nhat thu muc detected_events_unified/ mac dinh ngay
        khi thu muc lam viec doi (Panel Nap Du Lieu Tho chon lai thu muc)."""
        current_dir = self.state.current_dir
        if not current_dir or current_dir == self._last_seen_dir:
            return
        self._last_seen_dir = current_dir

        auto_dir = self._auto_events_dir()
        if auto_dir:
            self.folder = auto_dir
            self.lbl_folder.config(text=self.folder)
            self.status_var.set("Đã đổi thư mục làm việc — đường dẫn mặc định đã tự động cập nhật.")

    # ============================================================
    # RUN PIPELINE (ASYNC)
    # ============================================================

    def _set_buttons_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        for b in self._action_buttons:
            b.config(state=state)

    def _log(self, msg):
        self.after(0, lambda: (self.txt_log.insert(tk.END, str(msg) + "\n"), self.txt_log.see(tk.END)))

    def _start_pipeline(self):
        if not self.folder or not os.path.isdir(self.folder):
            messagebox.showwarning(
                "Chưa chọn thư mục",
                "Vui lòng chọn thư mục detected_events_unified/ (xuất từ Step 8) trước."
            )
            return

        try:
            raw_params = self._pull_params()
            params = {
                "fs": float(raw_params["fs"]),
                "window_sec": float(raw_params["window_sec"]),
                "stride_sec": float(raw_params["stride_sec"]),
                "purity_threshold": float(raw_params["purity_threshold"]),
                "train_ratio": float(raw_params["train_ratio"]),
                "val_ratio": float(raw_params["val_ratio"]),
                "epochs": max(1, int(round(raw_params["epochs"]))),
                "batch_size": max(1, int(round(raw_params["batch_size"]))),
                "learning_rate": float(raw_params["learning_rate"]),
            }
        except (tk.TclError, ValueError):
            messagebox.showerror("Lỗi", "Các tham số cấu hình phải là số hợp lệ!")
            return

        if self._busy:
            messagebox.showinfo("Đang chạy", "Pipeline đang chạy, vui lòng đợi hoàn tất.")
            return

        self._busy = True
        self._set_buttons_enabled(False)
        self.progress.start(10)
        self.txt_log.delete("1.0", tk.END)
        self.tree_dist.delete(*self.tree_dist.get_children())
        self.status_var.set("Đang chạy pipeline...")

        folder = self.folder
        thread = threading.Thread(target=self._run_pipeline_thread, args=(folder, params), daemon=True)
        thread.start()

    def _run_pipeline_thread(self, folder, params):
        try:
            report = pipe.run_full_training_pipeline(folder, params, log=self._log)
            self.after(0, lambda r=report: self._done(r))
        except Exception as e:
            err = str(e)
            self.after(0, lambda m=err: self._error(m))

    def _done(self, report):
        self._busy = False
        self._set_buttons_enabled(True)
        self.progress.stop()

        test_report = report.get("test_report") or {}
        test_emr = test_report.get("exact_match_ratio", 0.0)
        test_macro_f1 = test_report.get("macro_f1", 0.0)
        self.status_var.set(
            f"✅ Hoàn tất — Exact Match Ratio {test_emr*100:.1f}% — INT8 {report['quantized_kb']:.1f} KB"
        )

        name_to_group = {d["name"]: d["group"] for d in LABEL_DEFINITIONS}
        class_counts = report["window_stats"]["class_counts"]
        for name, count in sorted(class_counts.items(), key=lambda kv: kv[1], reverse=True):
            self.tree_dist.insert("", tk.END, values=(name, name_to_group.get(name, ""), count))

        messagebox.showinfo(
            "Hoàn tất Pipeline",
            f"Đã huấn luyện xong mô hình đa nhãn ({NUM_FLAGS} cờ, giải mã từ {NUM_CLASSES} nhãn gốc).\n\n"
            f"Train/Val/Test: {report['n_train']}/{report['n_val']}/{report['n_test']} cửa sổ\n"
            f"Test Exact Match Ratio: {test_emr*100:.1f}%  (khớp cả {NUM_FLAGS} cờ cùng lúc)\n"
            f"Test Macro F1-Score: {test_macro_f1*100:.1f}%\n"
            f"Kích thước INT8: {report['quantized_kb']:.1f} KB\n\n"
            f"Model: {os.path.basename(report['tflite_path'])}\n"
            f"Định nghĩa nhãn: {os.path.basename(report['label_map_path'])}\n\n"
            f"Lưu tại:\n{os.path.dirname(report['tflite_path'])}"
        )

    def _error(self, message):
        self._busy = False
        self._set_buttons_enabled(True)
        self.progress.stop()
        self.status_var.set("❌ Có lỗi trong quá trình huấn luyện.")
        self._log(f"\n❌ LỖI: {message}")
        messagebox.showerror("Lỗi Pipeline", message[:600])
