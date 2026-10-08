# -*- coding: utf-8 -*-
"""
Module: steps/step8_advanced_event_detection.py
Chuc nang: Step 8 - Advanced Label Detection. Port tu
           proc_data/preprocessing/event_detector_unified_claude_advanced.py,
           tach UI theo tung nhom toc do (G10/G20/G40/G80/Residual) + 1 Tab
           General dung chung, moi Tab tu chinh threshold + nut Default +
           chay rieng xuat CSV tuong ung; nut tong "Run All" combine toan
           bo va xuat detected_driving_events_unified.csv. Toan bo output
           luu vao <folder>/detected_events_unified/.
"""

import os
import copy
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core.app_state import AppState
from core.ui_widgets import ScienceInfoPanel, lock_treeview_columns
from core import event_detection_engine as eng


G10_CATEGORIES = [
    ("Speed Zone", [("SPEED_MAX", "SpeedMax"), ("SPEED_TOLERANCE", "Tolerance")]),
    ("Stop", [("STOP_SPEED_THRESHOLD", "Speed<="), ("STOP_ACCEL_NORM_THRESHOLD", "AccNorm<="),
              ("STOP_MIN_DURATION", "MinDur"), ("STOP_MAX_GYRO", "MaxGyro")]),
    ("Hard Brake", [("BRAKE_SPEED_DROP", "SpeedDrop"), ("BRAKE_FINAL_SPEED", "FinalSpeed"),
                    ("BRAKE_ACCEL", "Accel"), ("BRAKE_JERK", "Jerk")]),
    ("Sudden Accel", [("ACCEL_START_SPEED_MAX", "StartMax"), ("ACCEL_FINAL_SPEED_MIN", "FinalMin"),
                      ("ACCEL_SPEED_RISE", "SpeedRise"), ("ACCEL_ACCEL", "Accel"), ("ACCEL_JERK", "Jerk")]),
    ("Steering", [("STEERING_GYRO", "Gyro"), ("STEERING_MIN_DURATION", "MinDur")]),
    ("Normal", [("NORMAL_SPEED_CHANGE", "SpeedChg"), ("NORMAL_ACCEL", "Accel")]),
]

MID_HIGH_CATEGORIES = [
    ("Speed Zone", [("SPEED_MIN", "SpeedMin"), ("SPEED_MAX", "SpeedMax")]),
    ("Hard Brake", [("BRAKE_SPEED_DROP", "SpeedDrop"), ("BRAKE_ACCEL", "Accel"), ("BRAKE_JERK", "Jerk")]),
    ("Sudden Accel", [("ACCEL_SPEED_RISE", "SpeedRise"), ("ACCEL_ACCEL", "Accel"), ("ACCEL_JERK", "Jerk")]),
    ("Steering", [("STEERING_GYRO", "Gyro"), ("TURN_MIN_DUR", "MinDur")]),
    ("Normal", [("NORMAL_SPEED_CHANGE", "SpeedChg"), ("NORMAL_ACCEL", "Accel")]),
]

GENERAL_FIELDS = [
    ("GPS_SPEED_MEDIAN_WINDOW", "GPS Median Win"),
    ("GPS_ZERO_SPEED", "GPS Zero Speed"),
    ("GPS_IMU_TOLERANCE", "GPS-IMU Tol(s)"),
    ("WINDOW_SECONDS", "Window(s)"),
    ("STEP_SECONDS", "Step(s)"),
    ("ACCEL_SMOOTH_WINDOW", "Accel Smooth Win"),
    ("EVENT_MERGE_GAP", "Merge Gap(s)"),
]

GROUP_TABS = [
    ("G10", "G10 (0-10 km/h)", G10_CATEGORIES, eng.DEFAULT_G10, eng.detect_10kmh,
     "detected_window_candidates_10kmh.csv"),
    ("G20", "G20 (10-30 km/h)", MID_HIGH_CATEGORIES, eng.DEFAULT_G20, eng.detect_20kmh,
     "detected_window_candidates_20kmh.csv"),
    ("G40", "G40 (30-50 km/h)", MID_HIGH_CATEGORIES, eng.DEFAULT_G40, eng.detect_40kmh,
     "detected_window_candidates_40kmh.csv"),
    ("G80", "G80 (50-80 km/h)", MID_HIGH_CATEGORIES, eng.DEFAULT_G80, eng.detect_80kmh,
     "detected_window_candidates_80kmh.csv"),
]


class Step8AdvancedEventDetection(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self.cfg = eng.default_cfg_bundle()

        self.features_df = None
        self.gps_clean_df = None
        self.primary_events = {}
        self.candidates = {}
        self.residual_events = []
        self.residual_candidates = []
        self._last_seen_ids = (None, None)

        self._busy = False
        self._config_buttons = []      # Default buttons - disabled only while busy
        self._data_gated_buttons = []  # Load/Run buttons - need data loaded from top panel
        self.group_meta = {}
        self.vars_general = {}
        self.vars_residual = {}

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)
        self._on_data_changed()

    @property
    def folder(self):
        """Luon lay truc tiep tu AppState - dung chung voi folder da chon
        o Panel Nap Du Lieu Tho (part_top), Tab 8 khong co folder rieng."""
        return self.state.current_dir

    # ============================================================
    # BUILD UI
    # ============================================================

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=4, pady=(4, 0))

        top_bar = ttk.LabelFrame(self, text="Working Folder (synced from Data Loader panel) & Combined Export", padding=6)
        top_bar.pack(fill=tk.X, padx=4, pady=(4, 2))

        row = ttk.Frame(top_bar)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Folder:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.lbl_folder = ttk.Label(
            row, text="Not selected",
            foreground="#1e3d59", font=("Consolas", 9)
        )
        self.lbl_folder.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        row2 = ttk.Frame(top_bar)
        row2.pack(fill=tk.X, pady=(4, 0))
        self.btn_run_all = ttk.Button(
            row2, text="▶ Run All (Combine) & Export Unified CSV",
            style="Accent.TButton", command=self._on_run_all
        )
        self.btn_run_all.pack(side=tk.LEFT)
        self._data_gated_buttons.append(self.btn_run_all)

        self.btn_combine_only = ttk.Button(
            row2, text="⊕ Only Combine & Export Unified CSV",
            command=self._on_combine_only
        )
        self.btn_combine_only.pack(side=tk.LEFT, padx=(6, 0))
        self._data_gated_buttons.append(self.btn_combine_only)

        ttk.Label(
            row2, text="Output -> detected_events_unified/detected_driving_events_unified.csv",
            foreground="#777777", font=("Segoe UI", 8, "italic")
        ).pack(side=tk.LEFT, padx=12)

        self.progress = ttk.Progressbar(top_bar, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(4, 2))

        self.status_var = tk.StringVar(value="⚠️ Waiting for GPS/IMU data to be loaded from the Data Loader panel above...")
        ttk.Label(top_bar, textvariable=self.status_var, foreground="#555555", font=("Segoe UI", 8)).pack(
            fill=tk.X, anchor=tk.W
        )

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=(2, 4))

        self._build_general_tab()
        for group_key, title, categories, default_dict, detect_fn, csv_name in GROUP_TABS:
            self._build_group_tab(group_key, title, categories, default_dict, detect_fn, csv_name)
        self._build_residual_tab()

    def _build_threshold_categories(self, parent, categories, default_dict, vars_dict, cols_per_row=3):
        grid_frame = ttk.Frame(parent)
        grid_frame.pack(fill=tk.X, padx=4, pady=4)

        for idx, (cat_name, fields) in enumerate(categories):
            r, c = divmod(idx, cols_per_row)
            cat_frame = ttk.LabelFrame(grid_frame, text=cat_name, padding=4)
            cat_frame.grid(row=r, column=c, padx=3, pady=3, sticky=tk.NW)

            for fi, (key, label_text) in enumerate(fields):
                var = tk.DoubleVar(value=default_dict[key])
                vars_dict[key] = var
                ttk.Label(cat_frame, text=label_text + ":").grid(row=fi, column=0, padx=(2, 2), pady=1, sticky=tk.E)
                ttk.Entry(cat_frame, textvariable=var, width=7).grid(row=fi, column=1, padx=(0, 2), pady=1, sticky=tk.W)

    def _build_general_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="General")

        self._build_threshold_categories(
            tab, [("Pipeline Settings", GENERAL_FIELDS)], eng.DEFAULT_GENERAL, self.vars_general, cols_per_row=1
        )

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill=tk.X, padx=4, pady=(0, 4))

        btn_default = ttk.Button(
            btn_row, text="↺ Default", command=lambda: self._reset_vars(self.vars_general, eng.DEFAULT_GENERAL)
        )
        btn_default.pack(side=tk.LEFT, padx=2)
        self._config_buttons.append(btn_default)

        btn_load = ttk.Button(
            btn_row, text="▶ Load & Prepare Data", style="Accent.TButton", command=self._on_load_data
        )
        btn_load.pack(side=tk.LEFT, padx=2)
        self._data_gated_buttons.append(btn_load)

        self.lbl_general_result = ttk.Label(btn_row, text="Not loaded yet.", foreground="#555555")
        self.lbl_general_result.pack(side=tk.LEFT, padx=10)

        ttk.Label(
            tab,
            text="Outputs: gps_speed_cleaned.csv, merged_motion_features.csv "
                 "(shared input for every group tab below).",
            foreground="#777777", font=("Segoe UI", 8, "italic")
        ).pack(anchor=tk.W, padx=8, pady=(0, 4))

    def _build_group_tab(self, group_key, title, categories, default_dict, detect_fn, csv_name):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text=title)

        vars_dict = {}
        self._build_threshold_categories(tab, categories, default_dict, vars_dict)

        btn_row = ttk.Frame(tab)
        btn_row.pack(fill=tk.X, padx=4, pady=(0, 4))

        btn_default = ttk.Button(btn_row, text="↺ Default", command=lambda: self._reset_vars(vars_dict, default_dict))
        btn_default.pack(side=tk.LEFT, padx=2)
        self._config_buttons.append(btn_default)

        btn_run = ttk.Button(
            btn_row, text=f"▶ Run {group_key} Detection", style="Accent.TButton",
            command=lambda gk=group_key: self._run_group(gk)
        )
        btn_run.pack(side=tk.LEFT, padx=2)
        self._data_gated_buttons.append(btn_run)

        result_label = ttk.Label(btn_row, text="Not run yet.", foreground="#555555")
        result_label.pack(side=tk.LEFT, padx=10)

        tree_frame = ttk.LabelFrame(tab, text=f"{group_key} Merged Events Preview  ->  {csv_name}", padding=4)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        cols = ("Type", "Direction", "Start(s)", "End(s)", "Dur(s)", "SpdChg")
        widths = (150, 90, 70, 70, 70, 70)
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor=tk.CENTER, stretch=False)
        tree.pack(fill=tk.BOTH, expand=True)
        lock_treeview_columns(tree)
        tree.bind("<<TreeviewSelect>>", lambda e, gk=group_key: self._on_group_tree_select(gk))

        self.group_meta[group_key] = {
            "vars": vars_dict, "cfg_key": group_key.lower(), "detect_fn": detect_fn,
            "csv_name": csv_name, "tree": tree, "result_label": result_label,
            "default_dict": default_dict,
        }

    def _build_scrollable(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0)
        vsb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _on_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _on_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_wheel(_e):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(_e):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        return inner

    def _build_residual_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Residual (8 labels)")

        top_row = ttk.Frame(tab)
        top_row.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(top_row, text="MergeGap(s):").pack(side=tk.LEFT, padx=(2, 2))
        self.var_residual_merge_gap = tk.DoubleVar(value=eng.DEFAULT_UNKNOWN_EVENT_MERGE_GAP)
        ttk.Entry(top_row, textvariable=self.var_residual_merge_gap, width=6).pack(side=tk.LEFT, padx=(0, 10))

        btn_default = ttk.Button(top_row, text="↺ Default (All)", command=self._reset_residual_vars)
        btn_default.pack(side=tk.LEFT, padx=2)
        self._config_buttons.append(btn_default)

        btn_run = ttk.Button(
            top_row, text="▶ Run Residual Detection", style="Accent.TButton",
            command=lambda: self._run_residual()
        )
        btn_run.pack(side=tk.LEFT, padx=2)
        self._data_gated_buttons.append(btn_run)

        self.lbl_residual_result = ttk.Label(top_row, text="Not run yet.", foreground="#555555")
        self.lbl_residual_result.pack(side=tk.LEFT, padx=10)

        note = ttk.Label(
            tab,
            text="Runs only on the timeline NOT already covered by G10/G20/G40/G80 primary events "
                 "(auto-runs any missing group first with its current thresholds).",
            foreground="#777777", font=("Segoe UI", 8, "italic")
        )
        note.pack(anchor=tk.W, padx=8, pady=(0, 2))

        body = ttk.PanedWindow(tab, orient=tk.VERTICAL)
        body.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        scroll_holder = ttk.Frame(body)
        body.add(scroll_holder, weight=3)
        inner = self._build_scrollable(scroll_holder)

        priority_rank = {label: i + 1 for i, label in enumerate(eng.RESIDUAL_PRIORITY)}

        for label in sorted(eng.DEFAULT_RESIDUAL_LABELS):
            cfg_label = eng.DEFAULT_RESIDUAL_LABELS[label]
            box = ttk.LabelFrame(
                inner,
                text=f"{label} - {cfg_label['name']}  [{cfg_label['group']}]  (priority #{priority_rank[label]})",
                padding=4
            )
            box.pack(fill=tk.X, padx=4, pady=3)

            cond_vars = {}
            row = 0
            for cond_key, rule in cfg_label["conditions"].items():
                display_key = cond_key[3:] if cond_key.startswith("OR_") else cond_key
                or_tag = "  (OR)" if cond_key.startswith("OR_") else ""
                ttk.Label(box, text=f"{display_key}{or_tag}  {rule['operator']}").grid(
                    row=row, column=0, padx=(2, 6), pady=1, sticky=tk.W
                )
                var = tk.DoubleVar(value=rule["value"])
                cond_vars[cond_key] = var
                ttk.Entry(box, textvariable=var, width=8).grid(row=row, column=1, padx=(0, 10), pady=1, sticky=tk.W)
                row += 1

            if "transitions" in cfg_label:
                tr = cfg_label["transitions"]
                info_text = (
                    "Transitions (fixed, not editable): "
                    f"low→mid(start<={tr['low_to_mid']['speed_start_max']}, end>={tr['low_to_mid']['speed_end_min']})  |  "
                    f"mid→low(start>={tr['mid_to_low']['speed_start_min']}, end<={tr['mid_to_low']['speed_end_max']})  |  "
                    f"cross80(start<={tr['cross_80_boundary']['speed_start_max']}, end>{tr['cross_80_boundary']['speed_end_min_exclusive']})"
                )
                ttk.Label(
                    box, text=info_text, foreground="#777777", font=("Segoe UI", 8, "italic"), wraplength=520
                ).grid(row=row, column=0, columnspan=2, padx=2, pady=(3, 1), sticky=tk.W)

            self.vars_residual[label] = cond_vars

        tree_frame = ttk.LabelFrame(
            body, text="Residual Merged Events Preview  ->  detected_window_candidates_unknown.csv", padding=4
        )
        body.add(tree_frame, weight=2)

        cols = ("Type", "Direction", "SpeedGroup", "Start(s)", "End(s)", "Dur(s)")
        widths = (90, 70, 90, 70, 70, 70)
        self.tree_residual = ttk.Treeview(tree_frame, columns=cols, show="headings", height=6)
        for c, w in zip(cols, widths):
            self.tree_residual.heading(c, text=c)
            self.tree_residual.column(c, width=w, anchor=tk.CENTER, stretch=False)
        self.tree_residual.pack(fill=tk.BOTH, expand=True)
        lock_treeview_columns(self.tree_residual)
        self.tree_residual.bind("<<TreeviewSelect>>", self._on_residual_tree_select)

    # ============================================================
    # VAR HELPERS
    # ============================================================

    @staticmethod
    def _reset_vars(vars_dict, default_dict):
        for k, var in vars_dict.items():
            var.set(default_dict[k])

    @staticmethod
    def _pull_vars(vars_dict, cfg_dict):
        for k, var in vars_dict.items():
            cfg_dict[k] = var.get()

    def _reset_residual_vars(self):
        for label, cond_vars in self.vars_residual.items():
            defaults = eng.DEFAULT_RESIDUAL_LABELS[label]["conditions"]
            for cond_key, var in cond_vars.items():
                var.set(defaults[cond_key]["value"])
        self.var_residual_merge_gap.set(eng.DEFAULT_UNKNOWN_EVENT_MERGE_GAP)

    def _pull_residual_vars(self):
        for label, cond_vars in self.vars_residual.items():
            for cond_key, var in cond_vars.items():
                self.cfg["residual_labels"][label]["conditions"][cond_key]["value"] = var.get()

    def _pull_all_vars(self):
        self._pull_vars(self.vars_general, self.cfg["general"])
        for meta in self.group_meta.values():
            self._pull_vars(meta["vars"], self.cfg[meta["cfg_key"]])
        self._pull_residual_vars()
        self.cfg["residual_merge_gap"] = self.var_residual_merge_gap.get()

    # ============================================================
    # ASYNC RUN HELPER
    # ============================================================

    def _refresh_button_states(self):
        """Nut Default (config) chi bi khoa khi dang chay nen; nut
        Load/Run (data_gated) can CA 2 dieu kien: khong busy VA da co
        GPS+IMU duoc nap tu Panel Nap Du Lieu Tho (part_top)."""
        data_ready = self.state.df_gps is not None and self.state.df_imu is not None

        for b in self._config_buttons:
            b.config(state=tk.DISABLED if self._busy else tk.NORMAL)

        for b in self._data_gated_buttons:
            b.config(state=tk.NORMAL if (data_ready and not self._busy) else tk.DISABLED)

    def _run_async(self, status_msg, work_fn, done_fn):
        if self._busy:
            messagebox.showinfo("Busy", "Another detection task is already running, please wait.")
            return

        self._busy = True
        self._refresh_button_states()
        self.progress.start(10)
        self.status_var.set(status_msg)

        def _worker():
            try:
                result = work_fn()
            except Exception as e:
                self.after(0, lambda: self._finish_async(done_fn, None, str(e)))
                return
            self.after(0, lambda: self._finish_async(done_fn, result, None))

        threading.Thread(target=_worker, daemon=True).start()

    def _finish_async(self, done_fn, result, error):
        self._busy = False
        self._refresh_button_states()
        self.progress.stop()

        if error:
            self.status_var.set("❌ Error during detection.")
            messagebox.showerror("Error", error)
            return

        self.status_var.set("✅ Done.")
        done_fn(result)

    # ============================================================
    # DATA GATE (folder + GPS/IMU luon dong bo voi Panel Nap Du Lieu Tho)
    # ============================================================

    def _on_data_changed(self):
        """Dang ky lang nghe AppState.notify_data_changed(). Chi reset cache
        cua Tab 8 khi object GPS/IMU THAT SU doi (nap lai tu Panel tren),
        tranh bi xoa oan khi cac Tab khac (vd chon doan o Step 3) chi phat
        thong bao chung ma khong lien quan den du lieu tho cua Tab nay."""
        gps_id = id(self.state.df_gps) if self.state.df_gps is not None else None
        imu_id = id(self.state.df_imu) if self.state.df_imu is not None else None
        changed = (gps_id, imu_id) != self._last_seen_ids
        self._last_seen_ids = (gps_id, imu_id)

        if changed:
            self.features_df = None
            self.gps_clean_df = None
            self.primary_events = {}
            self.candidates = {}
            self.residual_events = []
            self.residual_candidates = []
            self.lbl_general_result.config(text="Not loaded yet.")
            for meta in self.group_meta.values():
                meta["tree"].delete(*meta["tree"].get_children())
                meta["result_label"].config(text="Not run yet.")
            self.tree_residual.delete(*self.tree_residual.get_children())
            self.lbl_residual_result.config(text="Not run yet.")

        self._update_data_gate()

    def _update_data_gate(self):
        data_ready = self.state.df_gps is not None and self.state.df_imu is not None

        self.lbl_folder.config(text=self.folder or "Not selected")

        if data_ready:
            if not self._busy:
                self.status_var.set("Ready. (Folder & data synced from the Data Loader panel above)")
        else:
            self.status_var.set(
                "⚠️ Waiting for GPS/IMU data to be loaded from the Data Loader panel above..."
            )

        self._refresh_button_states()

    # ============================================================
    # GENERAL / LOAD DATA
    # ============================================================

    def _ensure_features_then(self, callback):
        if self.features_df is not None:
            callback()
            return

        if not self.folder or not os.path.exists(self.folder):
            messagebox.showwarning(
                "No folder selected",
                "Please select a working folder (with RAW_GPS / RAW_ACCELEROMETERS .txt files) first."
            )
            return

        try:
            self._pull_vars(self.vars_general, self.cfg["general"])
            self._pull_vars(self.group_meta["G10"]["vars"], self.cfg["g10"])
        except tk.TclError:
            messagebox.showerror("Invalid value", "Please check numeric fields in the General / G10 tabs.")
            return

        folder = self.folder
        cfg_general = dict(self.cfg["general"])
        base_steering = self.cfg["g10"]["STEERING_GYRO"]

        def work():
            imu, gps, gps_clean, features, imu_path, gps_path = eng.load_and_prepare(
                folder, cfg_general, base_steering,
                progress_callback=lambda m: self.after(0, lambda: self.status_var.set(m))
            )
            output_dir = eng.get_output_dir(folder)
            gps_clean.to_csv(os.path.join(output_dir, "gps_speed_cleaned.csv"), index=False, encoding="utf-8-sig")
            features.to_csv(os.path.join(output_dir, "merged_motion_features.csv"), index=False, encoding="utf-8-sig")
            return imu, gps, gps_clean, features

        def done(result):
            imu, gps, gps_clean, features = result
            self.features_df = features
            self.gps_clean_df = gps_clean
            self.lbl_general_result.config(
                text=f"✅ IMU {len(imu):,} | GPS {len(gps):,} | Merged {len(features):,} samples"
            )
            callback()

        self._run_async("Loading & preparing data (General settings)...", work, done)

    def _on_load_data(self):
        self.features_df = None
        self._ensure_features_then(lambda: None)

    # ============================================================
    # GROUP DETECTION (G10/G20/G40/G80)
    # ============================================================

    def _run_group(self, group_key, on_done=None):
        meta = self.group_meta[group_key]
        try:
            self._pull_vars(self.vars_general, self.cfg["general"])
            self._pull_vars(meta["vars"], self.cfg[meta["cfg_key"]])
        except tk.TclError:
            messagebox.showerror("Invalid value", f"Please check numeric fields in the {group_key} tab.")
            return

        def after_features_ready():
            cfg_general = dict(self.cfg["general"])
            cfg_group = dict(self.cfg[meta["cfg_key"]])
            features = self.features_df
            output_dir = eng.get_output_dir(self.folder)

            def work():
                cand, events = meta["detect_fn"](features, cfg_general, cfg_group)
                eng.build_candidate_df(features, cand).to_csv(
                    os.path.join(output_dir, meta["csv_name"]), index=False, encoding="utf-8-sig"
                )
                return cand, events

            def done(result):
                cand, events = result
                self.candidates[group_key] = cand
                self.primary_events[group_key] = events
                self._fill_group_tree(group_key, events)
                meta["result_label"].config(
                    text=f"✅ {len(cand)} candidates -> {len(events)} merged events (saved {meta['csv_name']})"
                )
                if on_done:
                    on_done()

            self._run_async(f"Detecting {group_key} events...", work, done)

        self._ensure_features_then(after_features_ready)

    # ============================================================
    # RESIDUAL DETECTION
    # ============================================================

    def _run_missing_groups_then(self, missing, callback):
        if not missing:
            callback()
            return
        group_key = missing.pop(0)
        self._run_group(group_key, on_done=lambda: self._run_missing_groups_then(missing, callback))

    def _run_residual(self, on_done=None):
        try:
            self._pull_residual_vars()
            self.cfg["residual_merge_gap"] = self.var_residual_merge_gap.get()
        except tk.TclError:
            messagebox.showerror("Invalid value", "Please check numeric fields in the Residual tab.")
            return

        def do_residual():
            cfg_general = dict(self.cfg["general"])
            cfg_g10, cfg_g20, cfg_g40, cfg_g80 = (dict(self.cfg[k]) for k in ("g10", "g20", "g40", "g80"))
            residual_labels = copy.deepcopy(self.cfg["residual_labels"])
            residual_priority = list(self.cfg["residual_priority"])
            merge_gap = self.cfg["residual_merge_gap"]
            features = self.features_df
            primary_events = sum(self.primary_events.values(), [])
            output_dir = eng.get_output_dir(self.folder)

            def work():
                cand, events = eng.detect_unknown(
                    features, primary_events, cfg_general, cfg_g10, cfg_g20, cfg_g40, cfg_g80,
                    residual_labels, residual_priority, merge_gap,
                )
                eng.build_candidate_df(features, cand).to_csv(
                    os.path.join(output_dir, "detected_window_candidates_unknown.csv"),
                    index=False, encoding="utf-8-sig"
                )
                return cand, events

            def done(result):
                cand, events = result
                self.residual_candidates = cand
                self.residual_events = events
                self._fill_residual_tree(events)
                self.lbl_residual_result.config(
                    text=f"✅ {len(cand)} candidates -> {len(events)} merged events "
                         f"(saved detected_window_candidates_unknown.csv)"
                )
                if on_done:
                    on_done()

            self._run_async("Detecting residual labels...", work, done)

        missing = [g for g in ("G10", "G20", "G40", "G80") if g not in self.primary_events]
        if missing:
            self._ensure_features_then(lambda: self._run_missing_groups_then(missing, do_residual))
        else:
            self._ensure_features_then(do_residual)

    # ============================================================
    # RUN ALL / COMBINE
    # ============================================================

    def _on_run_all(self):
        if not self.folder or not os.path.exists(self.folder):
            messagebox.showwarning("No folder selected", "Please select a working folder first.")
            return

        try:
            self._pull_all_vars()
        except tk.TclError:
            messagebox.showerror(
                "Invalid value",
                "Please check numeric fields across all tabs (General / G10 / G20 / G40 / G80 / Residual)."
            )
            return

        cfg_snapshot = copy.deepcopy(self.cfg)
        folder = self.folder

        def work():
            return eng.run_full_pipeline(
                folder, cfg_snapshot,
                progress_callback=lambda m: self.after(0, lambda: self.status_var.set(m))
            )

        def done(result):
            self.features_df = result["features_df"]
            self.gps_clean_df = result["gps_clean_df"]

            self.lbl_general_result.config(
                text=f"✅ IMU {result['imu_samples']:,} | GPS {result['gps_samples']:,} | "
                     f"Merged {result['merged_samples']:,} samples"
            )

            for group_key, (cand, events) in result["group_results"].items():
                self.candidates[group_key] = cand
                self.primary_events[group_key] = events
                meta = self.group_meta[group_key]
                self._fill_group_tree(group_key, events)
                meta["result_label"].config(
                    text=f"✅ {len(cand)} candidates -> {len(events)} merged events (saved {meta['csv_name']})"
                )

            cand_unk, events_unk = result["residual_result"]
            self.residual_candidates = cand_unk
            self.residual_events = events_unk
            self._fill_residual_tree(events_unk)
            self.lbl_residual_result.config(
                text=f"✅ {len(cand_unk)} candidates -> {len(events_unk)} merged events "
                     f"(saved detected_window_candidates_unknown.csv)"
            )

            self.status_var.set(f"✅ Combined export done: {result['total_events']} total events.")

            messagebox.showinfo(
                "Combined Export Done",
                f"Detected {result['total_events']} events total "
                f"({result['events_primary']} primary + {result['events_unknown']} residual).\n\n"
                f"Saved to:\n{result['output_dir']}"
            )

        self._run_async(
            "Running full combined pipeline (General -> G10 -> G20 -> G40 -> G80 -> Residual)...", work, done
        )

    def _on_combine_only(self):
        """Chi gop cac ket qua DA CO san trong cache (tu nhung lan bam Run
        rieng tung nhom truoc do) va xuat detected_driving_events_unified.csv,
        KHONG chay lai bat ky nhom nao - phuc vu truong hop nguoi dung chi
        muon test 1 vai nhom roi gop lai xem thu, khong can Run All lai tu dau."""

        if self.features_df is None:
            messagebox.showwarning(
                "No data loaded",
                "Please load data (General tab) and run at least one group before combining."
            )
            return

        primary_events = []
        missing = []
        for group_key in ("G10", "G20", "G40", "G80"):
            if group_key in self.primary_events:
                primary_events.extend(self.primary_events[group_key])
            else:
                missing.append(group_key)

        residual_events = list(self.residual_events)
        if not self.residual_events:
            missing.append("Residual")

        if not primary_events and not residual_events:
            messagebox.showwarning(
                "Nothing to combine",
                "No group has produced results yet. Run at least one group first "
                "(or use Run All) before combining."
            )
            return

        features = self.features_df
        folder = self.folder
        output_dir = eng.get_output_dir(folder)

        def work():
            all_events = primary_events + residual_events
            all_events.sort(key=lambda x: x["start_idx"])
            events_df = eng.events_to_df(features, all_events)
            events_output = os.path.join(output_dir, "detected_driving_events_unified.csv")
            events_df.to_csv(events_output, index=False, encoding="utf-8-sig")
            return events_df, events_output

        def done(result):
            events_df, events_output = result

            status_msg = f"✅ Combine-only export done: {len(events_df)} events."
            if missing:
                status_msg += f"  (not run yet, counted as 0: {', '.join(missing)})"
            self.status_var.set(status_msg)

            info_lines = [f"Combined {len(events_df)} events from currently available results."]
            if missing:
                info_lines.append(
                    "\nNote: these were NOT run yet and contributed 0 events:\n  " + ", ".join(missing)
                )
            info_lines.append(f"\nSaved to:\n{events_output}")
            messagebox.showinfo("Combine-Only Export Done", "\n".join(info_lines))

        self._run_async("Combining currently available results (no group re-run)...", work, done)

    # ============================================================
    # TREE PREVIEW HELPERS
    # ============================================================

    def _fill_group_tree(self, group_key, events):
        meta = self.group_meta[group_key]
        meta["events"] = events
        tree = meta["tree"]
        tree.delete(*tree.get_children())
        df = self.features_df
        for ev in events:
            seg = df.iloc[ev["start_idx"]: ev["end_idx"] + 1]
            t0 = float(seg["time"].iloc[0])
            t1 = float(seg["time"].iloc[-1])
            spd_chg = float(seg["speed_kmh"].iloc[-1] - seg["speed_kmh"].iloc[0])
            tree.insert("", tk.END, values=(
                ev["event_type"], ev["direction"], f"{t0:.2f}", f"{t1:.2f}", f"{t1 - t0:.2f}", f"{spd_chg:.2f}"
            ))

    def _on_group_tree_select(self, group_key):
        meta = self.group_meta[group_key]
        selected = meta["tree"].selection()
        if not selected or self.features_df is None:
            return
        idx = meta["tree"].index(selected[0])
        events = meta.get("events", [])
        if idx < len(events):
            self._push_selected_segment(group_key, events[idx])

    def _on_residual_tree_select(self, _event=None):
        selected = self.tree_residual.selection()
        if not selected or self.features_df is None:
            return
        idx = self.tree_residual.index(selected[0])
        if idx < len(self.residual_events):
            self._push_selected_segment("RESIDUAL", self.residual_events[idx])

    def _push_selected_segment(self, group_key, ev):
        """Day doan/su kien dang chon sang AppState de Step 4 (Ban Do) va
        cac tab khac tu dong highlight dung khoang timestamp nay."""
        df = self.features_df
        seg = df.iloc[ev["start_idx"]: ev["end_idx"] + 1]

        def _first_valid(col):
            if col in seg.columns and seg[col].notna().any():
                return float(seg[col].dropna().iloc[0])
            return 0.0

        def _last_valid(col):
            if col in seg.columns and seg[col].notna().any():
                return float(seg[col].dropna().iloc[-1])
            return 0.0

        t0 = float(seg["time"].iloc[0])
        t1 = float(seg["time"].iloc[-1])
        avg_speed = float(seg["speed_kmh"].mean()) if "speed_kmh" in seg.columns else 0.0

        self.state.selected_segment = {
            "id": f"{group_key}:{ev['event_type']}",
            "t_start": t0, "t_end": t1,
            "event_type": ev["event_type"],
            "lat_start": _first_valid("lat"), "lon_start": _first_valid("lon"),
            "lat_end": _last_valid("lat"), "lon_end": _last_valid("lon"),
            "distance": 0.0, "avg_speed": avg_speed,
        }
        self.state.notify_data_changed()

    def _fill_residual_tree(self, events):
        tree = self.tree_residual
        tree.delete(*tree.get_children())
        df = self.features_df
        for ev in events:
            seg = df.iloc[ev["start_idx"]: ev["end_idx"] + 1]
            t0 = float(seg["time"].iloc[0])
            t1 = float(seg["time"].iloc[-1])
            tree.insert("", tk.END, values=(
                ev["event_type"], ev["direction"], ev.get("speed_group", ""),
                f"{t0:.2f}", f"{t1:.2f}", f"{t1 - t0:.2f}"
            ))
