# -*- coding: utf-8 -*-
"""
Module: steps/step4_route_map.py
Chuc nang: Step 4 - Hien thi ban do tong the (Route toan trinh) va lam noi
           bat doan duong #ID duoc chon o Step 3 / Step 5 / Step 7.
"""

import os
import math
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from core.app_state import AppState
from core.sensor_schema import calculate_cumulative_distance
from core.ui_widgets import ScienceInfoPanel, add_chart_zoom_button


# ============================================================
# TIMELINE SWIMLANE (thanh mau theo nhom hanh vi) CHEN VAO trang 3D web
# "AI Detected" (ai_driving_events_map.html) BANG CACH HAU XU LY (post-
# process) chuoi HTML da duoc steps/step9_3d_map_visualizer.py::create_map()
# sinh ra - KHONG SUA 1 DONG NAO trong step9_3d_map_visualizer.py (file do
# dung chung cho ca luong Step 8 heuristic, khong duoc dong cham).
#
# Co so: trang 3D da co san (doc duoc, khong can them du lieu Python nao):
#   - var _motion3DEvents  : mang JS {event_id,event_type,start_time,...}
#     (event_type CHINH LA "+".join(active_flags) hoac "NONE" - tach lai
#     bang .split("+") de biet cac co dang bat cua tung su kien).
#   - var _simT            : thoi gian phat hien tai (cap nhat moi frame).
#   - function motion3DSliderChanged(value) : tua thoi gian co san.
#   - <input id="motion3d-slider" ...>      : diem neo de chen NGAY SAU no
#     (cung khu vuc #motion3d-controls, khong chong lap panel khac).
# ============================================================

_SWIMLANE_SLIDER_ANCHOR = (
    '<input id="motion3d-slider" type="range" min="0" max="100" step="0.05" '
    'value="0" oninput="motion3DSliderChanged(this.value)" '
    'onchange="motion3DSliderChanged(this.value)" style="position:relative;z-index:30;">'
)

# Anchor 2: gop 2 nut Play + Pause rieng biet thanh 1 nut toggle duy nhat.
_SWIMLANE_PLAYPAUSE_ANCHOR = (
    '<button class="motion3d-btn" onclick="motion3DPlay()">▶ Play</button>\n'
    '                    <button class="motion3d-btn" onclick="motion3DPause()">⏸ Pause</button>'
)
_SWIMLANE_PLAYPAUSE_REPLACEMENT = (
    '<button class="motion3d-btn" id="swimlane-playpause-btn" '
    'onclick="swimlaneTogglePlayPause()">▶ Play</button>'
)

# Anchor 3: them nut Phong To / Thu Nho ngay truoc nut dong (X) o header.
_SWIMLANE_CLOSE_BTN_ANCHOR = '<button id="motion3d-close" onclick="close3DSimulator()">×</button>'
_SWIMLANE_MAXIMIZE_BTN_HTML = (
    '<button id="swimlane-maximize-btn" onclick="swimlaneToggleMaximize()" '
    'title="Phóng to / Thu nhỏ" style="border:0;background:#2a5a7a;color:white;'
    'width:34px;height:32px;border-radius:5px;cursor:pointer;font-size:15px;'
    'font-weight:bold;margin-right:8px;">⤢</button>'
)

_SWIMLANE_INJECTION_HTML = r"""
<style>
    #motion3d-panel.swimlane-maximized { width:98vw !important; height:96vh !important; }
</style>
<div id="swimlane-wrap" style="margin-top:8px;">
    <div style="display:flex;justify-content:space-between;font-size:11px;color:#9fb0bf;margin-bottom:3px;">
        <span>Timeline Sự Kiện AI (cuộn ngang / click để tua)</span>
        <span id="swimlane-time-label">0.0s</span>
    </div>
    <div id="swimlane-scroll" style="position:relative;overflow-x:auto;overflow-y:hidden;height:112px;background:#0f141a;border:1px solid #34404a;border-radius:6px;white-space:nowrap;">
        <div id="swimlane-labels" style="position:sticky;left:0;top:0;display:inline-block;width:90px;height:112px;background:rgba(15,20,26,0.95);z-index:5;vertical-align:top;border-right:1px solid #34404a;box-sizing:border-box;"></div>
        <div id="swimlane-track" style="position:relative;display:inline-block;height:112px;vertical-align:top;"></div>
    </div>
</div>
<script>
(function() {
    var LANE_MAP = {
        "BRAKE_HARD":0, "BRAKE_MODERATE":0, "ACCEL_HARD":0, "ACCEL_MODERATE":0, "STOP":0,
        "STEERING":1, "STEERING_ANOMALY":1,
        "JERK_TRANSIENT":2, "SPEED_TRANSITION":2, "DATA_QUALITY_ISSUE":2
    };
    var LANE_LABELS = ["Phanh / Ga", "Đánh lái", "Bất thường"];
    var FLAG_COLORS = {
        "BRAKE_HARD":"#C0392B", "BRAKE_MODERATE":"#E74C3C",
        "ACCEL_HARD":"#D35400", "ACCEL_MODERATE":"#F1C40F",
        "STOP":"#8E44AD",
        "STEERING":"#3498DB", "STEERING_ANOMALY":"#9B59B6",
        "JERK_TRANSIENT":"#7F8C8D", "SPEED_TRANSITION":"#34495E", "DATA_QUALITY_ISSUE":"#2C3E50"
    };
    var BG_FLAGS = {"SPEED_G10":1, "SPEED_G20":1, "SPEED_G40":1, "SPEED_G80":1};
    var BG_COLORS = {"SPEED_G10":"#D5F5E3", "SPEED_G20":"#A9DFBF", "SPEED_G40":"#58D68D", "SPEED_G80":"#1E8449"};
    var PX_PER_SEC = 40, LANE_H = 26, BG_H = 14;
    var swimlaneMinT = 0;

    function activeFlagsOf(ev) {
        return (!ev.event_type || ev.event_type === "NONE") ? [] : ev.event_type.split("+");
    }

    function buildLabels() {
        var panel = document.getElementById("swimlane-labels");
        if (!panel) return;
        panel.innerHTML = "";
        LANE_LABELS.forEach(function(label, i) {
            var lbl = document.createElement("div");
            lbl.textContent = label;
            lbl.style.cssText = "position:absolute;left:4px;top:" + (BG_H + i * LANE_H + 5) +
                "px;font-size:9px;font-weight:bold;color:#ccd6dd;white-space:normal;width:82px;";
            panel.appendChild(lbl);
        });
        var bgLbl = document.createElement("div");
        bgLbl.textContent = "Tốc độ";
        bgLbl.style.cssText = "position:absolute;left:4px;top:1px;font-size:8px;color:#9fb0bf;";
        panel.appendChild(bgLbl);
    }

    function buildSwimlane() {
        var events = window._motion3DEvents || [];
        var track = document.getElementById("swimlane-track");
        if (!track || events.length === 0) return;
        track.innerHTML = "";

        var minT = events[0].start_time, maxT = events[0].end_time;
        events.forEach(function(e) {
            if (e.start_time < minT) minT = e.start_time;
            if (e.end_time > maxT) maxT = e.end_time;
        });
        swimlaneMinT = minT;
        track.style.width = Math.max(400, (maxT - minT) * PX_PER_SEC + 40) + "px";

        events.forEach(function(e) {
            activeFlagsOf(e).forEach(function(f) {
                if (BG_FLAGS[f]) {
                    var div = document.createElement("div");
                    div.style.cssText = "position:absolute;top:0;height:" + BG_H + "px;left:" +
                        ((e.start_time - minT) * PX_PER_SEC) + "px;width:" +
                        Math.max(2, (e.end_time - e.start_time) * PX_PER_SEC) + "px;background:" +
                        BG_COLORS[f] + ";";
                    track.appendChild(div);
                }
            });
        });

        events.forEach(function(e) {
            activeFlagsOf(e).forEach(function(f) {
                if (f in LANE_MAP) {
                    var lane = LANE_MAP[f];
                    var bar = document.createElement("div");
                    bar.title = e.event_type + " | " + e.start_time.toFixed(2) + "s - " +
                        e.end_time.toFixed(2) + "s";
                    bar.style.cssText = "position:absolute;top:" + (BG_H + lane * LANE_H + 2) +
                        "px;height:" + (LANE_H - 4) + "px;left:" + ((e.start_time - minT) * PX_PER_SEC) +
                        "px;width:" + Math.max(3, (e.end_time - e.start_time) * PX_PER_SEC) +
                        "px;background:" + FLAG_COLORS[f] + ";border-radius:2px;cursor:pointer;" +
                        "border:1px solid #1a1a1a;";
                    bar.onclick = function() { seekTo(e.start_time); };
                    track.appendChild(bar);
                }
            });
        });

        var cursor = document.createElement("div");
        cursor.id = "swimlane-cursor";
        cursor.style.cssText = "position:absolute;top:0;bottom:0;width:2px;background:#ffffff;" +
            "z-index:10;pointer-events:none;";
        track.appendChild(cursor);
    }

    function seekTo(t) {
        if (typeof motion3DSliderChanged !== "function") return;
        var startT = (typeof _isAllMode !== "undefined" && _isAllMode)
            ? _globalStartTime : _activeBufferStartTime;
        motion3DSliderChanged((t - startT).toFixed(3));
    }

    // Nut Play/Pause gop chung (xem _SWIMLANE_PLAYPAUSE_ANCHOR) - doc/ghi
    // dung bien _motion3DPlaying va goi dung ham motion3DPlay()/Pause() co
    // san cua trang, KHONG viet lai logic play/pause.
    window.swimlaneTogglePlayPause = function() {
        if (typeof _motion3DPlaying !== "undefined" && _motion3DPlaying) {
            if (typeof motion3DPause === "function") motion3DPause();
        } else {
            if (typeof motion3DPlay === "function") motion3DPlay();
        }
        updatePlayPauseLabel();
    };

    function updatePlayPauseLabel() {
        var btn = document.getElementById("swimlane-playpause-btn");
        if (btn) btn.textContent = (typeof _motion3DPlaying !== "undefined" && _motion3DPlaying)
            ? "⏸ Pause" : "▶ Play";
    }

    // Nut Phong To / Thu Nho panel (xem _SWIMLANE_CLOSE_BTN_ANCHOR) - toggle
    // class CSS, khong dung Fullscreen API (tranh xin quyen trinh duyet).
    window.swimlaneToggleMaximize = function() {
        var panel = document.getElementById("motion3d-panel");
        if (!panel) return;
        panel.classList.toggle("swimlane-maximized");
        var btn = document.getElementById("swimlane-maximize-btn");
        if (btn) btn.textContent = panel.classList.contains("swimlane-maximized") ? "⤡" : "⤢";
    };

    function tick() {
        var cursor = document.getElementById("swimlane-cursor");
        var scrollDiv = document.getElementById("swimlane-scroll");
        if (cursor && scrollDiv && typeof _simT !== "undefined") {
            var x = (_simT - swimlaneMinT) * PX_PER_SEC;
            cursor.style.left = x + "px";
            if (x < scrollDiv.scrollLeft || x > scrollDiv.scrollLeft + scrollDiv.clientWidth - 20) {
                scrollDiv.scrollLeft = Math.max(0, x - scrollDiv.clientWidth / 3);
            }
            var lbl = document.getElementById("swimlane-time-label");
            if (lbl) lbl.textContent = _simT.toFixed(1) + "s";
        }

        // Dong bo 100% hop "Su kien dang theo doi" voi dung cong thuc loc
        // su kien dang hoat dong ma trang step9 da dung cho canvas popup
        // (_motion3DEvents.filter theo _simT, xem step9 dong ~1503) - chi
        // doc du lieu co san, khong goi/sua ham noi bo nao cua step9.
        if (typeof _simT !== "undefined" && window._motion3DEvents) {
            var active = window._motion3DEvents.filter(function(e) {
                return _simT >= e.start_time && _simT <= e.end_time;
            });
            if (active.length > 0) {
                var ae = active[0];
                var idEl = document.getElementById("m3d-event-id");
                var typeEl = document.getElementById("m3d-event-type");
                var rangeEl = document.getElementById("m3d-time-range");
                if (idEl) idEl.textContent = ae.event_id;
                if (typeEl) typeEl.textContent = ae.event_type;
                if (rangeEl) rangeEl.textContent = ae.start_time.toFixed(2) + "s - " + ae.end_time.toFixed(2) + "s";
            }
        }

        updatePlayPauseLabel();
        requestAnimationFrame(tick);
    }

    function waitAndInit(retriesLeft) {
        var scrollDiv = document.getElementById("swimlane-scroll");
        if (window._motion3DEvents && typeof motion3DSliderChanged === "function" && scrollDiv) {
            buildLabels();
            buildSwimlane();
            scrollDiv.addEventListener("click", function(ev) {
                var rect = this.getBoundingClientRect();
                var x = ev.clientX - rect.left + this.scrollLeft - 90;
                seekTo(swimlaneMinT + x / PX_PER_SEC);
            });
            requestAnimationFrame(tick);
        } else if (retriesLeft > 0) {
            setTimeout(function() { waitAndInit(retriesLeft - 1); }, 200);
        }
    }
    waitAndInit(50);
})();
</script>
"""


def _replace_anchor(html, anchor, replacement, label, log):
    if anchor not in html:
        log(f"  ⚠️ Không tìm thấy điểm neo '{label}' trong trang 3D - bỏ qua cải tiến này.")
        return html
    return html.replace(anchor, replacement, 1)


def _inject_swimlane_timeline(html_path, log=print):
    """Chen timeline swimlane + cac cai tien UI (gop Play/Pause, nut Phong
    To, dong bo hop su kien) vao file HTML DA DUOC
    step9_3d_map_visualizer.py::create_map() ghi xong - CHI doc/ghi de lai
    file nay, KHONG import/sua step9_3d_map_visualizer.py. Moi diem neo
    duoc thay THE DOC LAP - neu 1 diem neo khong tim thay (vd cau truc
    trang doi khac trong tuong lai), chi bo qua DUNG cai tien do, khong
    lam hong cac cai tien con lai hay luong mo 3D hien co."""
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        html = _replace_anchor(
            html, _SWIMLANE_SLIDER_ANCHOR,
            _SWIMLANE_SLIDER_ANCHOR + _SWIMLANE_INJECTION_HTML,
            "timeline swimlane", log,
        )
        html = _replace_anchor(
            html, _SWIMLANE_PLAYPAUSE_ANCHOR, _SWIMLANE_PLAYPAUSE_REPLACEMENT,
            "gộp nút Play/Pause", log,
        )
        html = _replace_anchor(
            html, _SWIMLANE_CLOSE_BTN_ANCHOR,
            _SWIMLANE_MAXIMIZE_BTN_HTML + _SWIMLANE_CLOSE_BTN_ANCHOR,
            "nút Phóng To/Thu Nhỏ", log,
        )

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        log(f"  ⚠️ Lỗi chèn cải tiến UI 3D (bỏ qua, không ảnh hưởng mô phỏng 3D): {e}")


class Step4RouteMap(ttk.Frame):
    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}
        self._clickable_lons = None
        self._clickable_lats = None

        self._viz_server = None
        self._viz_server_port = None
        self._viz_served_dir = None
        self._viz_busy = False

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)
        # Goi ngay 1 lan de hien thi dung trang thai hien hanh (vd du lieu
        # GPS da duoc Panel Nap Du Lieu Tho tu dong nap TRUOC khi Tab nay
        # duoc khoi tao - khong doi thong bao trong tuong lai moi ve dung).
        self._update_map()

    def _build_ui(self):
        main_box = ttk.Frame(self, padding=6)
        main_box.pack(fill=tk.BOTH, expand=True)

        ScienceInfoPanel(main_box, self.module_config).pack(fill=tk.X, pady=(0, 4))

        ctrl_frame = ttk.LabelFrame(main_box, text="Thông Tin Đoạn Đường #ID Được Chọn Tự Động", padding=6)
        ctrl_frame.pack(fill=tk.X, pady=(0, 4))

        self.lbl_seg_summary = ttk.Label(
            ctrl_frame,
            text="Chưa lọc hoặc chọn đoạn đường (Đang hiển thị Route toàn trình) - Click lên đường để mở Google Maps",
            font=("Segoe UI", 9, "bold"),
            foreground="#1e3d59"
        )
        self.lbl_seg_summary.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.btn_view_3d = ttk.Button(ctrl_frame, text="🎬 Xem Mô Phỏng 3D", style="Accent.TButton", command=self._on_view_3d)
        self.btn_view_3d.pack(side=tk.RIGHT, padx=2)

        btn_reset = ttk.Button(ctrl_frame, text="🌐 Xem Toàn Trình", command=self._on_reset_to_full_route)
        btn_reset.pack(side=tk.RIGHT, padx=2)

        btn_refresh = ttk.Button(ctrl_frame, text="🔄 Làm Mới Bản Đồ", command=self._update_map)
        btn_refresh.pack(side=tk.RIGHT, padx=2)

        add_chart_zoom_button(ctrl_frame, lambda: self.fig, title="Bản Đồ GPS - Step 4").pack(side=tk.RIGHT, padx=2)

        self.viz_status_var = tk.StringVar(value="")
        ttk.Label(
            main_box, textvariable=self.viz_status_var, foreground="#777777", font=("Segoe UI", 8, "italic")
        ).pack(fill=tk.X, pady=(0, 2))

        map_frame = ttk.LabelFrame(main_box, text="Bản Đồ Định Vị GPS Hành Trình (Route Toàn Trình & Highlight Đoạn #ID)", padding=4)
        map_frame.pack(fill=tk.BOTH, expand=True)

        self.fig = Figure(figsize=(8, 5), dpi=100)
        self.ax = self.fig.add_subplot(111)

        self.canvas = FigureCanvasTkAgg(self.fig, master=map_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_map_click)

        self.toolbar = NavigationToolbar2Tk(self.canvas, map_frame)
        self.toolbar.update()

    def _on_map_click(self, event):
        """Click gan nhat 1 diem GPS cua doan/route dang hien thi se mo
        Google Maps dung toa do diem do trong trinh duyet mac dinh."""
        if event.xdata is None or event.ydata is None:
            return
        if self._clickable_lons is None or len(self._clickable_lons) == 0:
            return

        idx = int(np.argmin((self._clickable_lons - event.xdata) ** 2 + (self._clickable_lats - event.ydata) ** 2))
        lon, lat = self._clickable_lons[idx], self._clickable_lats[idx]

        # Doi sang toa do pixel man hinh de kiem tra nguong click (tranh
        # mo nham khi click vao vung trong xa duong di)
        px_click = self.ax.transData.transform((event.xdata, event.ydata))
        px_point = self.ax.transData.transform((lon, lat))
        px_dist = ((px_click[0] - px_point[0]) ** 2 + (px_click[1] - px_point[1]) ** 2) ** 0.5
        if px_dist > 25:
            return

        webbrowser.open(f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}")

    def _on_data_changed(self):
        self._update_map()

    def _update_map(self):
        self.ax.clear()
        self._zoom_bounds = None
        self._clickable_lons = None
        self._clickable_lats = None

        df_gps = self.state.df_gps
        if df_gps is None or "lat" not in df_gps.columns or "lon" not in df_gps.columns:
            self.ax.text(0.5, 0.5, "Chưa nạp dữ liệu GPS hoặc tệp thiếu cột tọa độ (lat, lon)", ha="center", va="center")
            self.canvas.draw()
            return

        valid_gps = df_gps.dropna(subset=["lat", "lon"])
        if len(valid_gps) == 0:
            self.ax.text(0.5, 0.5, "Dữ liệu GPS không có tọa độ hợp lệ", ha="center", va="center")
            self.canvas.draw()
            return

        lats_full = valid_gps["lat"].values
        lons_full = valid_gps["lon"].values

        self.ax.plot(lons_full, lats_full, color="#90a4ae", linestyle="-", linewidth=1.5, alpha=0.7)
        self.ax.plot(lons_full[0], lats_full[0], marker="o", color="blue", markersize=6)
        self.ax.plot(lons_full[-1], lats_full[-1], marker="s", color="black", markersize=6)

        # Mac dinh cho phep click tren toan tuyen de mo Google Maps; neu co
        # doan #ID dang duoc chon, danh sach diem click se duoc thay the
        # bang rieng doan do ben duoi.
        self._clickable_lons = lons_full
        self._clickable_lats = lats_full

        # Mac dinh (chua co tab nao click id su kien/quang duong nao) LUON
        # hien thi Route toan trinh - khong tu dong fallback ve doan dau
        # tien du co detected_segments, dung theo yeu cau.
        selected_seg = self.state.selected_segment

        if selected_seg:
            seg_id = selected_seg.get("id", "?")
            event_type = selected_seg.get("event_type", "")
            t0, t1 = selected_seg.get("t_start", 0), selected_seg.get("t_end", 0)

            df_seg = valid_gps[(valid_gps["t_now"] >= t0) & (valid_gps["t_now"] <= t1)]

            if len(df_seg) > 0:
                lats_seg = df_seg["lat"].values
                lons_seg = df_seg["lon"].values

                self.ax.plot(lons_seg, lats_seg, color="#e74c3c", linewidth=3.5)
                self.ax.plot(lons_seg[0], lats_seg[0], marker="^", color="#2ecc71", markersize=9)
                self.ax.plot(lons_seg[-1], lats_seg[-1], marker="v", color="#e74c3c", markersize=9)

                self._clickable_lons = lons_seg
                self._clickable_lats = lats_seg

                # Tu dong zoom khung nhin vao tron ven doan duong dang chon,
                # kem bien do (padding) ~25% cho moi phia de khong bi cham vien
                lat_min, lat_max = float(lats_seg.min()), float(lats_seg.max())
                lon_min, lon_max = float(lons_seg.min()), float(lons_seg.max())
                lat_span = max(lat_max - lat_min, 0.0008)
                lon_span = max(lon_max - lon_min, 0.0008)
                pad_lat = lat_span * 0.25
                pad_lon = lon_span * 0.25
                self._zoom_bounds = (lon_min - pad_lon, lon_max + pad_lon, lat_min - pad_lat, lat_max + pad_lat)

                # Luon tu tinh lai toa do/quang duong/van toc TB TRUC TIEP tu
                # du lieu GPS dang nap - khong phu thuoc tab nguon co du day
                # du cac truong lat_start/distance/avg_speed hay khong.
                distance_m = calculate_cumulative_distance(df_seg, lat_col="lat", lon_col="lon")
                avg_speed = float(df_seg["speed_kmh"].mean()) if "speed_kmh" in df_seg.columns else 0.0
                label_part = f" [{event_type}]" if event_type else ""

                self.lbl_seg_summary.config(
                    text=f"📍 Đang Hiển Thị Đoạn #{seg_id}{label_part} | Tọa độ: "
                         f"[{lats_seg[0]:.5f}, {lons_seg[0]:.5f}] ➔ [{lats_seg[-1]:.5f}, {lons_seg[-1]:.5f}] | "
                         f"Quãng đường: {distance_m:.1f}m | Vận tốc TB: {avg_speed:.1f}km/h",
                    foreground="#1e3d59"
                )
            else:
                self.lbl_seg_summary.config(text=f"⚠️ Đoạn #{seg_id}: Không tìm thấy mẫu GPS tương ứng trong khoảng timestamp")
        else:
            self.lbl_seg_summary.config(
                text="Hiển thị Route Toàn Trình (chưa có #ID sự kiện/quãng đường nào được chọn ở các Tab khác) "
                     "- Click lên đường để mở Google Maps"
            )

        mean_lat = float(lats_full.mean())
        cos_lat = math.cos(math.radians(mean_lat))
        if cos_lat > 0:
            self.ax.set_aspect(1.0 / cos_lat)

        if self._zoom_bounds is not None:
            lon_lo, lon_hi, lat_lo, lat_hi = self._zoom_bounds
            self.ax.set_xlim(lon_lo, lon_hi)
            self.ax.set_ylim(lat_lo, lat_hi)

        self.ax.set_xlabel("Kinh Độ (Longitude)")
        self.ax.set_ylabel("Vĩ Độ (Latitude)")
        self.ax.set_title("Bản Đồ Định Vị GPS Hành Trình (Route Toàn Trình & Đoạn Đường Chọn)")
        self.ax.grid(True, linestyle=":", alpha=0.6)

        self.fig.tight_layout()
        self.canvas.draw()

    # ============================================================
    # RESET VE TOAN TRINH
    # ============================================================

    def _on_reset_to_full_route(self):
        self.state.selected_segment = None
        self.state.notify_data_changed()

    # ============================================================
    # MO PHONG 3D (tai su dung logic Folium + Three.js cua Step 9)
    # ============================================================

    def _on_view_3d(self):
        """Mo phong 3D CHI danh cho #ID du doan cua Step 7 (AI) - dung
        dung CHINH features_df + predicted_events cua lan suy luan AI gan
        nhat (state.last_ai_inference), KHONG doc/chay lai file CSV cua
        Step 8 (detected_driving_events_unified.csv)."""
        folder = self.state.current_dir
        if not folder:
            messagebox.showwarning(
                "Chưa có thư mục", "Vui lòng chọn thư mục làm việc trước (Panel Nạp Dữ Liệu Thô)."
            )
            return

        selected_seg = self.state.selected_segment
        if not selected_seg or selected_seg.get("source") != "step7_ai":
            messagebox.showwarning(
                "Chưa chọn sự kiện AI",
                "Mô phỏng 3D chỉ áp dụng cho #ID sự kiện dự đoán ở Step 7 (Suy Luận Mô Hình AI).\n\n"
                "Vui lòng chạy Suy Luận AI ở Step 7 và click chọn 1 sự kiện trong danh sách trước."
            )
            return

        ai_data = getattr(self.state, "last_ai_inference", None)
        if not ai_data or ai_data.get("features_df") is None:
            messagebox.showwarning(
                "Chưa có dữ liệu suy luận AI",
                "Không tìm thấy kết quả suy luận AI gần nhất. Vui lòng chạy lại Suy Luận AI ở Step 7."
            )
            return

        if self._viz_busy:
            messagebox.showinfo("Đang xử lý", "Đang chuẩn bị mô phỏng 3D, vui lòng đợi.")
            return

        range_param = f"{selected_seg.get('t_start', 0):.3f},{selected_seg.get('t_end', 0):.3f}"
        label_param = selected_seg.get("event_type") or f"Sự kiện AI #{selected_seg.get('id', '?')}"

        self._viz_busy = True
        self.btn_view_3d.config(state=tk.DISABLED)
        self.viz_status_var.set("Đang tạo bản đồ & chuẩn bị mô phỏng 3D từ dữ liệu Step 7 (AI)...")

        thread = threading.Thread(
            target=self._run_3d_pipeline_ai, args=(folder, ai_data, range_param, label_param), daemon=True
        )
        thread.start()

    def _run_3d_pipeline_ai(self, folder, ai_data, range_param, label_param):
        try:
            import urllib.parse
            from steps.step9_3d_map_visualizer import create_map, open_map_local_server
            from core.inference_pipeline import build_events_df_for_map

            features_df = ai_data["features_df"]
            predicted_events = ai_data["predicted_events"]

            events_dir = os.path.join(folder, "detected_events_unified")
            os.makedirs(events_dir, exist_ok=True)

            events_df = build_events_df_for_map(features_df, predicted_events)
            features_csv_name = "ai_inference_features_for_3d.csv"
            features_df.to_csv(os.path.join(events_dir, features_csv_name), index=False, encoding="utf-8-sig")

            output_filename = "ai_driving_events_map.html"
            html_path = create_map(
                events_dir, events_df, features_df,
                features_filename=features_csv_name, output_filename=output_filename,
            )
            _inject_swimlane_timeline(html_path)

            served_dir = os.path.dirname(html_path)
            if self._viz_server is None:
                server, url = open_map_local_server(html_path, auto_open=False)
                self._viz_server = server
                self._viz_server_port = server.server_address[1]
            elif served_dir != self._viz_served_dir:
                os.chdir(served_dir)
            self._viz_served_dir = served_dir

            query = urllib.parse.urlencode({"range": range_param, "label": label_param})
            url = f"http://127.0.0.1:{self._viz_server_port}/{output_filename}?{query}"
            webbrowser.open(url)

            self.after(0, lambda: self._on_3d_done(label_param))
        except Exception as e:
            err = str(e)
            self.after(0, lambda m=err: self._on_3d_error(m))

    def _on_3d_done(self, label_param):
        self._viz_busy = False
        self.btn_view_3d.config(state=tk.NORMAL)
        self.viz_status_var.set(f"✅ Đã mở mô phỏng 3D (Step 7 - AI) cho: {label_param}")

    def _on_3d_error(self, message):
        self._viz_busy = False
        self.btn_view_3d.config(state=tk.NORMAL)
        self.viz_status_var.set("❌ Lỗi khi tạo mô phỏng 3D.")
        messagebox.showerror("Lỗi Mô Phỏng 3D", message[:500])
