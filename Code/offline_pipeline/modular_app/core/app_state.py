# -*- coding: utf-8 -*-
"""
Module: core/app_state.py
Chuc nang: Quan ly trang thai chia se (Shared State) va Bus su kien giua
           cac Panel/Step trong he thong theo mo hinh Observer/Pub-Sub.
Co so khoa hoc: Observer pattern (Gamma et al., Design Patterns, 1994) -
           cho phep nhieu module dang ky lang nghe va duoc thong bao dong
           bo khi trang thai du lieu dung chung thay doi, khong can cac
           module tham chieu truc tiep lan nhau.
"""

import os


class AppState:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AppState, cls).__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        self.current_dir = os.getcwd()
        self.gps_path = None
        self.imu_path = None
        self.df_gps = None
        self.df_imu = None
        self.detected_segments = []
        self.selected_segment = None
        self.last_ai_inference = None  # ket qua suy luan AI gan nhat tu Step 7

        self._data_change_listeners = []
        self._log_listeners = []

    def register_data_listener(self, callback):
        if callback not in self._data_change_listeners:
            self._data_change_listeners.append(callback)

    def register_log_listener(self, callback):
        if callback not in self._log_listeners:
            self._log_listeners.append(callback)

    def log(self, message):
        """Phat tin nhan nhat ky he thong toi Panel Nhat Ky."""
        for listener in self._log_listeners:
            try:
                listener(message)
            except Exception:
                pass

    def notify_data_changed(self):
        """Phat thong bao khi du lieu hoac doan duong duoc chon thay doi."""
        for listener in self._data_change_listeners:
            try:
                listener()
            except Exception:
                pass

    def subscribe(self, callback):
        self.register_log_listener(callback)
        self.register_data_listener(callback)

    def notify(self, event_name, data=None):
        if event_name == "LOG":
            self.log(str(data))
        elif event_name in ["DATA_LOADED", "SEGMENT_UPDATED", "SEGMENT_SELECTED"]:
            self.notify_data_changed()

    def clear(self):
        self.df_gps = None
        self.df_imu = None
        self.detected_segments = []
        self.selected_segment = None
        self.last_ai_inference = None
        self.notify_data_changed()


global_state = AppState()
