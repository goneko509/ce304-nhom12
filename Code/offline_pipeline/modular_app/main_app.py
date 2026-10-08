#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DU AN: UNG DUNG PHAN TICH DU LIEU TELEMETRY CAU HINH BANG JSON
Mo ta: Doc file cau hinh app_config.json va tu dong dung giao dien phan cap:
       - Part Top: Nap file tho
       - Part Body: Split 2 Frame canh nhau (PanedWindow keo gian duoc),
         moi Frame mang 1 Notebook doc lap chua day du toan bo Tab pipeline
         - cho phep xem song song 2 Tab bat ky cung luc
       - Part Bottom: Status bar
"""

import os
import sys
import json
import importlib
import tkinter as tk
from tkinter import ttk, messagebox

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


class MainApp(tk.Tk):
    def __init__(self, config_file="app_config.json"):
        super().__init__()

        self.config_file = config_file
        self.config_data = self._load_config(config_file)

        self.title(self.config_data.get("app_name", "Telemetry Pipeline Analyzer"))
        self.geometry("1900x980")
        self.minsize(1300, 760)

        self._setup_styles()
        self._build_ui_from_config()

    def _load_config(self, filepath):
        if not os.path.exists(filepath):
            messagebox.showerror("Lỗi Cấu Hình", f"Không tìm thấy file cấu hình: {filepath}")
            sys.exit(1)
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)

    def _setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")

        PRIMARY_COLOR = "#1e3d59"
        ACCENT_COLOR = "#17b978"
        BG_COLOR = "#f5f7fa"

        self.configure(bg=BG_COLOR)
        self.style.configure(".", background=BG_COLOR, font=("Segoe UI", 10))
        self.style.configure("TFrame", background=BG_COLOR)
        self.style.configure("TLabelframe", background=BG_COLOR, font=("Segoe UI", 10, "bold"))
        self.style.configure("TLabelframe.Label", background=BG_COLOR, foreground=PRIMARY_COLOR)
        self.style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=5)
        self.style.configure("Accent.TButton", background=ACCENT_COLOR, foreground="white")
        self.style.configure("Treeview", font=("Segoe UI", 9), rowheight=24)
        self.style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#dcdfe6")
        self.style.configure("InfoToggle.TButton", font=("Segoe UI", 9), anchor="w", padding=(6, 3))

    def _instantiate_module(self, tab_config, parent_widget):
        file_py = tab_config.get("file_py", "")
        class_name = tab_config.get("class_name", "")
        module_name = file_py.replace(".py", "").replace("/", ".").replace("\\", ".")

        try:
            mod_obj = importlib.import_module(module_name)
            cls_obj = getattr(mod_obj, class_name)
            instance = cls_obj(parent_widget, tab_config)
            return instance
        except Exception as e:
            err_frame = ttk.Frame(parent_widget, padding=10)
            lbl_err = ttk.Label(
                err_frame,
                text=f"❌ Lỗi nạp Module [{tab_config.get('ten')}]:\nFile: {file_py} -> Class: {class_name}\nChi tiết: {str(e)}",
                foreground="red"
            )
            lbl_err.pack(padx=10, pady=10, expand=True)
            return err_frame

    def _build_ui_from_config(self):
        parts = self.config_data.get("parts", [])

        part_top_cfg = next((p for p in parts if p["id"] == "part_top"), None)
        part_body_cfg = next((p for p in parts if p["id"] == "part_body"), None)
        part_bottom_cfg = next((p for p in parts if p["id"] == "part_bottom"), None)

        if part_top_cfg:
            top_part_container = ttk.Frame(self)
            top_part_container.pack(fill=tk.X, side=tk.TOP, padx=6, pady=(6, 2))

            for frame_cfg in part_top_cfg.get("frames", []):
                for tab_cfg in frame_cfg.get("tabs", []):
                    top_mod = self._instantiate_module(tab_cfg, top_part_container)
                    top_mod.pack(fill=tk.X, expand=True)

        if part_bottom_cfg:
            bottom_part_container = ttk.Frame(self)
            bottom_part_container.pack(fill=tk.X, side=tk.BOTTOM, padx=6, pady=(2, 6))

            for frame_cfg in part_bottom_cfg.get("frames", []):
                for tab_cfg in frame_cfg.get("tabs", []):
                    bot_mod = self._instantiate_module(tab_cfg, bottom_part_container)
                    bot_mod.pack(fill=tk.X, expand=True)

        if part_body_cfg:
            body_container = ttk.Frame(self)
            body_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

            # Giao dien Split 2 Frame doc lap canh nhau (keo gian duoc qua
            # thanh sash o giua), moi Frame mang 1 Notebook rieng chua DAY
            # DU toan bo cac Tab pipeline. Cho phep xem song song 2 Tab bat
            # ky cung luc (vd: Step 3 phan doan o ben trai & Step 4 Ban Do
            # phong to o ben phai) ma khong bi bop nho noi dung.
            split_pane = ttk.PanedWindow(body_container, orient=tk.HORIZONTAL)
            split_pane.pack(fill=tk.BOTH, expand=True)

            for frame_cfg in part_body_cfg.get("frames", []):
                tabs_cfg = frame_cfg.get("tabs", [])

                left_frame = ttk.Frame(split_pane)
                right_frame = ttk.Frame(split_pane)
                split_pane.add(left_frame, weight=1)
                split_pane.add(right_frame, weight=1)

                notebook_left = ttk.Notebook(left_frame)
                notebook_left.pack(fill=tk.BOTH, expand=True)

                notebook_right = ttk.Notebook(right_frame)
                notebook_right.pack(fill=tk.BOTH, expand=True)

                map_tab_index = None
                for idx, tab_cfg in enumerate(tabs_cfg):
                    tab_instance_left = self._instantiate_module(tab_cfg, notebook_left)
                    notebook_left.add(tab_instance_left, text=tab_cfg.get("ten", "Tab"))

                    tab_instance_right = self._instantiate_module(tab_cfg, notebook_right)
                    notebook_right.add(tab_instance_right, text=tab_cfg.get("ten", "Tab"))

                    if tab_cfg.get("id") == "step4_route_map":
                        map_tab_index = idx

                # Mac dinh Frame ben phai mo san Tab Ban Do (khong gian rong
                # hon), Frame ben trai giu Tab dau tien de theo dung pipeline.
                if map_tab_index is not None:
                    notebook_right.select(map_tab_index)


if __name__ == "__main__":
    app = MainApp("app_config.json")
    app.mainloop()
