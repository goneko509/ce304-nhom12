# -*- coding: utf-8 -*-
# ============================================================
# STEP 9 — 3D MOTION & EVENT MAP VISUALIZER  (10 / 20 / 40 / 80 km/h)
# Port tu proc_data/preprocessing/event_visualizer_3d_pro_gemini.py
# ============================================================
#
# File goc:
#     event_visualizer_3d_pro_gemini.py
#
# Mo ta:
#     Doc CSV duoc tao boi Step 8 (Advanced Label Detection) va tao ban do
#     Folium tuong tac + mo phong dong luc hoc 3D (Three.js).
#
# Input:
#     Thu muc detected_events_unified/ (mac dinh lay tu thu muc lam viec
#     dang chon o Panel Nap Du Lieu Tho) chua:
#         merged_motion_features.csv
#         detected_driving_events_unified.csv
#
# Output:
#     detected_events_unified/
#         driving_events_map.html
#
# Chuc nang (giu nguyen 100% logic va phong cach the hien tu file goc):
#     - Hien thi GPS Track day du
#     - Marker theo mau cho 33 nhan su kien (25 Primary + 8 Residual)
#     - LayerControl bat/tat tung loai su kien
#     - Checkbox "Tat ca su kien" (bat/tat mot lan)
#     - Legend + Event Summary tren ban do
#     - Popup chi tiet cho moi su kien
#     - Mo phong dong luc hoc 3D khi click #ID su kien (Three.js)
#     - Sliding Window Buffer 10s: Chong giat lag khi tai trip lon
#     - Nut dieu huong Next/Prev Event
#     - BANG CAI DAT REALTIME CAC THONG SO VAT LY TREN GIAO DIEN
#     - Fullscreen, MeasureControl, MiniMap
#     - Mo trinh duyet tu dong qua local HTTP server
#
# THAY DOI DUY NHAT SO VOI BAN GOC (theo yeu cau):
#     - Tu dong RELOAD website neu server/tab dang chay, thay vi mo 1 tab
#       moi moi lan bam "Tao Ban Do": server local duoc tai su dung (chi
#       khoi tao 1 lan / 1 lan doi thu muc), va trang HTML duoc nhung 1
#       doan script nho tu kiem tra header Last-Modified cua chinh no (HEAD
#       request) de tu goi location.reload() ngay khi phat hien file HTML
#       vua duoc ghi lai - khong can nguoi dung F5 thu cong.
# ============================================================

import os
import json
import threading
import traceback
import webbrowser
import http.server
import socketserver
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pandas as pd
import folium
from folium.plugins import (
    Fullscreen,
    MiniMap,
    MeasureControl,
    MarkerCluster
)

from core.app_state import AppState
from core.ui_widgets import ScienceInfoPanel


# ============================================================
# CONFIG
# ============================================================

MAP_MAX_ZOOM  = 24
DEFAULT_ZOOM  = 16


# ============================================================
# MÀU SẮC EVENT (19 nhãn + fallback)
# ============================================================

EVENT_COLORS = {

    # --------------------------------------------------------
    # Nhóm ~10 km/h
    # --------------------------------------------------------

    "STOP_RED_LIGHT":           "#8E44AD",   # tím
    "HARD_BRAKE":               "#E74C3C",   # đỏ
    "HARD_BRAKE_STEERING":      "#C0392B",   # đỏ đậm
    "SUDDEN_ACCEL":             "#F39C12",   # vàng cam
    "SUDDEN_ACCEL_STEERING":    "#D35400",   # cam đậm
    "NORMAL_STEERING":          "#3498DB",   # xanh dương
    "NORMAL":                   "#27AE60",   # xanh lá

    # --------------------------------------------------------
    # Nhóm ~20 km/h (10 - 30 km/h)
    # --------------------------------------------------------
    "NORMAL_20":                "#2ECC71",   # xanh lá sáng
    "BRAKE_20":                 "#E74C3C",   # đỏ
    "ACCEL_20":                 "#F1C40F",   # vàng
    "TURN_20":                  "#3498DB",   # xanh dương
    "ACCEL_TURN_20":            "#E67E22",   # cam
    "BRAKE_TURN_20":            "#C0392B",   # đỏ sẫm

    # --------------------------------------------------------
    # Nhóm ~40 km/h
    # --------------------------------------------------------

    "NORMAL_40":                "#1ABC9C",   # xanh ngọc
    "BRAKE_40":                 "#E74C3C",   # đỏ
    "ACCEL_40":                 "#E67E22",   # cam
    "TURN_40":                  "#2980B9",   # xanh dương đậm
    "ACCEL_TURN_40":            "#D35400",   # cam đậm
    "BRAKE_TURN_40":            "#922B21",   # đỏ đậm

    # --------------------------------------------------------
    # Nhóm ~80 km/h
    # --------------------------------------------------------

    "NORMAL_80":                "#16A085",   # xanh đậm
    "BRAKE_80":                 "#CB4335",   # đỏ
    "ACCEL_80":                 "#B7950B",   # vàng
    "TURN_80":                  "#1F618D",   # xanh navy
    "ACCEL_TURN_80":            "#A04000",   # nâu cam
    "BRAKE_TURN_80":            "#641E16",   # đỏ rất đậm

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    "UNCLASSIFIED":             "#7F8C8D",   # xám

    # --------------------------------------------------------
    # Nhóm Residual (8 nhãn — đặt tên tiếng Anh theo đúng định nghĩa điều
    # kiện; đã bỏ "Unknown09/Uncovered 10.5-30km/h" vì G20 đã bao phủ
    # đúng dải này, xem core/label_schema.py)
    # --------------------------------------------------------

    "MODERATE_DECEL":           "#E67E73",   # đỏ nhạt — Moderate Deceleration
    "MODERATE_ACCEL":           "#73C6B6",   # xanh ngọc nhạt — Moderate Acceleration
    "STEERING_ANOMALY":         "#A9CCE3",   # xanh dương nhạt — Steering Non-Normal
    "MODERATE_BRAKE_STEER":     "#F0B27A",   # cam nhạt — Moderate Braking+Steering
    "MODERATE_ACCEL_STEER":     "#A9DFBF",   # xanh lá nhạt — Moderate Accel+Steering
    "JERK_TRANSIENT":           "#F7DC6F",   # vàng — Jerk-Dominant Transient
    "SPEED_BOUNDARY_TRANSITION": "#D7BDE2",  # tím nhạt — Speed Boundary Transition
    "IMU_GPS_MISMATCH":         "#AEB6BF",   # xám xanh — IMU-GPS Inconsistency
}


# ============================================================
# TÊN HIỂN THỊ EVENT
# ============================================================

EVENT_NAMES = {

    "STOP_RED_LIGHT":           "Dừng / Có thể dừng đèn đỏ",
    "HARD_BRAKE":               "Thắng gấp (<10 km/h)",
    "HARD_BRAKE_STEERING":      "Thắng gấp + Đánh lái (<10 km/h)",
    "SUDDEN_ACCEL":             "Giật ga đột ngột (<10 km/h)",
    "SUDDEN_ACCEL_STEERING":    "Giật ga + Đánh lái (<10 km/h)",
    "NORMAL_STEERING":          "Bình thường + Đánh lái (<10 km/h)",
    "NORMAL":                   "Đi bình thường (<10 km/h)",

    "NORMAL_20":                "Đi bình thường (~20 km/h)",
    "BRAKE_20":                 "Thắng gấp (~20 km/h)",
    "ACCEL_20":                 "Giật ga (~20 km/h)",
    "TURN_20":                  "Rẽ bình thường (~20 km/h)",
    "ACCEL_TURN_20":            "Giật ga + Đánh lái (~20 km/h)",
    "BRAKE_TURN_20":            "Thắng gấp + Đánh lái (~20 km/h)",

    "NORMAL_40":                "Đi bình thường (~40 km/h)",
    "BRAKE_40":                 "Thắng gấp (~40 km/h)",
    "ACCEL_40":                 "Giật ga (~40 km/h)",
    "TURN_40":                  "Rẽ bình thường (~40 km/h)",
    "ACCEL_TURN_40":            "Giật ga + Đánh lái (~40 km/h)",
    "BRAKE_TURN_40":            "Thắng gấp + Đánh lái (~40 km/h)",

    "NORMAL_80":                "Đi bình thường (~80 km/h)",
    "BRAKE_80":                 "Thắng gấp (~80 km/h)",
    "ACCEL_80":                 "Giật ga (~80 km/h)",
    "TURN_80":                  "Rẽ bình thường (~80 km/h)",
    "ACCEL_TURN_80":            "Giật ga + Đánh lái (~80 km/h)",
    "BRAKE_TURN_80":            "Thắng gấp + Đánh lái (~80 km/h)",

    "UNCLASSIFIED":             "Không phân loại",

    # Residual (8 nhãn)
    "MODERATE_DECEL":            "Moderate Deceleration",
    "MODERATE_ACCEL":            "Moderate Acceleration",
    "STEERING_ANOMALY":          "Steering Non-Normal Motion",
    "MODERATE_BRAKE_STEER":      "Moderate Braking + Steering",
    "MODERATE_ACCEL_STEER":      "Moderate Acceleration + Steering",
    "JERK_TRANSIENT":            "Jerk-Dominant Transient",
    "SPEED_BOUNDARY_TRANSITION": "Speed Boundary Transition",
    "IMU_GPS_MISMATCH":          "IMU-GPS Inconsistency",
}


# ============================================================
# ICON EVENT (glyphicon)
# ============================================================

EVENT_ICONS = {

    "STOP_RED_LIGHT":           "pause",
    "HARD_BRAKE":               "stop",
    "HARD_BRAKE_STEERING":      "warning-sign",
    "SUDDEN_ACCEL":             "flash",
    "SUDDEN_ACCEL_STEERING":    "warning-sign",
    "NORMAL_STEERING":          "road",
    "NORMAL":                   "ok",

    "NORMAL_20":                "ok",
    "BRAKE_20":                 "stop",
    "ACCEL_20":                 "flash",
    "TURN_20":                  "road",
    "ACCEL_TURN_20":            "warning-sign",
    "BRAKE_TURN_20":            "warning-sign",

    "NORMAL_40":                "ok",
    "BRAKE_40":                 "stop",
    "ACCEL_40":                 "flash",
    "TURN_40":                  "road",
    "ACCEL_TURN_40":            "warning-sign",
    "BRAKE_TURN_40":            "warning-sign",

    "NORMAL_80":                "ok",
    "BRAKE_80":                 "stop",
    "ACCEL_80":                 "flash",
    "TURN_80":                  "road",
    "ACCEL_TURN_80":            "warning-sign",
    "BRAKE_TURN_80":            "warning-sign",

    "UNCLASSIFIED":             "question-sign",

    # Residual (8 nhãn)
    "MODERATE_DECEL":            "arrow-down",
    "MODERATE_ACCEL":            "arrow-up",
    "STEERING_ANOMALY":          "transfer",
    "MODERATE_BRAKE_STEER":      "alert",
    "MODERATE_ACCEL_STEER":      "alert",
    "JERK_TRANSIENT":            "flash",
    "SPEED_BOUNDARY_TRANSITION": "random",
    "IMU_GPS_MISMATCH":          "signal",
}


# ============================================================
# NHÓM TỐC ĐỘ ĐỂ PHÂN NHÓM LEGEND
# ============================================================

SPEED_GROUP_ORDER = [
    ("Nhóm < 10 km/h", [
        "STOP_RED_LIGHT",
        "HARD_BRAKE",
        "HARD_BRAKE_STEERING",
        "SUDDEN_ACCEL",
        "SUDDEN_ACCEL_STEERING",
        "NORMAL_STEERING",
        "NORMAL",
    ]),
    ("Nhóm ~20 km/h", [
        "NORMAL_20", 
        "BRAKE_20", 
        "ACCEL_20", 
        "TURN_20", 
        "ACCEL_TURN_20", 
        "BRAKE_TURN_20",
    ]),
    ("Nhóm ~40 km/h", [
        "NORMAL_40",
        "BRAKE_40",
        "ACCEL_40",
        "TURN_40",
        "ACCEL_TURN_40",
        "BRAKE_TURN_40",
    ]),
    ("Nhóm ~80 km/h", [
        "NORMAL_80",
        "BRAKE_80",
        "ACCEL_80",
        "TURN_80",
        "ACCEL_TURN_80",
        "BRAKE_TURN_80",
    ]),
    ("Nhóm Residual / Chưa xác định ngữ nghĩa", [
        "MODERATE_DECEL",
        "MODERATE_ACCEL",
        "STEERING_ANOMALY",
        "MODERATE_BRAKE_STEER",
        "MODERATE_ACCEL_STEER",
        "JERK_TRANSIENT",
        "SPEED_BOUNDARY_TRANSITION",
        "IMU_GPS_MISMATCH",
        "UNCLASSIFIED",
    ]),
]


# ============================================================
# FILE DISCOVERY
# ============================================================

def find_events_csv(folder):
    unified = os.path.join(folder, "detected_driving_events_unified.csv")
    if os.path.isfile(unified):
        return unified

    candidates = []
    for fname in os.listdir(folder):
        if (
            fname.lower().endswith(".csv")
            and fname.lower().startswith("detected_driving_events")
        ):
            candidates.append(os.path.join(folder, fname))

    if not candidates:
        raise FileNotFoundError(
            "Không tìm thấy file detected_driving_events*.csv\n"
            "trong thư mục:\n" + folder
        )

    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def find_features_csv(folder):
    preferred = os.path.join(folder, "merged_motion_features.csv")
    if os.path.isfile(preferred):
        return preferred

    for fname in os.listdir(folder):
        if (
            fname.lower().endswith(".csv")
            and "merged_motion_features" in fname.lower()
        ):
            return os.path.join(folder, fname)

    raise FileNotFoundError(
        "Không tìm thấy file merged_motion_features*.csv\n"
        "trong thư mục:\n" + folder
    )


def read_events_csv(folder):
    path = find_events_csv(folder)
    df = pd.read_csv(path, encoding="utf-8-sig")
    required_cols = [
        "event_id", "event_type", "direction",
        "start_time", "end_time", "duration",
        "speed_start", "speed_end", "speed_change",
        "speed_min", "speed_max",
        "min_accel", "max_accel",
        "min_jerk", "max_jerk",
        "max_gyr_z",
        "lat", "lon", "samples"
    ]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError("File CSV thiếu các cột:\n" + "\n".join(missing))
    return df, path


def read_gps_track(folder):
    path = find_features_csv(folder)
    df = pd.read_csv(path, encoding="utf-8-sig")
    for col in ["time", "lat", "lon", "speed_kmh"]:
        if col not in df.columns:
            raise ValueError(f"Features CSV thiếu cột: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["lat", "lon"]).copy()
    return df, path


# ============================================================
# HTML HELPERS
# ============================================================

def html_escape(value):
    if pd.isna(value):
        return ""
    text = str(value)
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def make_event_popup(row):
    event_type = html_escape(row["event_type"])
    event_name = html_escape(EVENT_NAMES.get(row["event_type"], row["event_type"]))
    speed_group = html_escape(row.get("speed_group", ""))
    direction   = html_escape(row["direction"])
    event_id    = html_escape(row["event_id"])

    start_time   = html_escape(f"{float(row['start_time']):.2f} s")
    end_time     = html_escape(f"{float(row['end_time']):.2f} s")
    duration     = html_escape(f"{float(row['duration']):.2f} s")
    speed_start  = html_escape(f"{float(row['speed_start']):.1f} km/h")
    speed_end    = html_escape(f"{float(row['speed_end']):.1f} km/h")
    speed_change = html_escape(f"{float(row['speed_change']):+.1f} km/h")
    speed_min    = html_escape(f"{float(row['speed_min']):.1f} km/h")
    speed_max    = html_escape(f"{float(row['speed_max']):.1f} km/h")
    min_accel    = html_escape(f"{float(row['min_accel']):+.3f} m/s²")
    max_accel    = html_escape(f"{float(row['max_accel']):+.3f} m/s²")
    min_jerk     = html_escape(f"{float(row['min_jerk']):+.3f} m/s³")
    max_jerk     = html_escape(f"{float(row['max_jerk']):+.3f} m/s³")
    max_gyr      = html_escape(f"{float(row['max_gyr_z']):.3f} rad/s")
    samples      = html_escape(row["samples"])

    color = EVENT_COLORS.get(row["event_type"], "#7F8C8D")

    group_badge = ""
    if speed_group:
        group_badge = f"""
        <span style="
            display:inline-block;
            background:{color};
            color:white;
            border-radius:4px;
            padding:1px 7px;
            font-size:11px;
            margin-top:4px;
        ">{speed_group}</span>
        """

    html = f"""
    <div style="
        width:340px;
        font-family:'Segoe UI',Arial,sans-serif;
        font-size:13px;
    ">
        <div style="
            background:{color};
            color:white;
            padding:10px 12px;
            margin:-5px -5px 10px -5px;
            border-radius:4px 4px 0 0;
        ">
            <div style="font-size:16px;font-weight:bold;">Event #{event_id}</div>
            <div style="font-size:12px;opacity:0.9;margin-top:3px;">{event_name}</div>
            {group_badge}
        </div>

        <table style="width:100%;border-collapse:collapse;font-size:12.5px;">
            <tr style="background:#f5f5f5;">
                <td style="padding:4px 6px;font-weight:600;width:45%;">Label</td>
                <td style="padding:4px 6px;">{event_type}</td>
            </tr>
            <tr>
                <td style="padding:4px 6px;font-weight:600;">Hướng</td>
                <td style="padding:4px 6px;">{direction}</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:4px 6px;font-weight:600;">Thời gian</td>
                <td style="padding:4px 6px;">{start_time} → {end_time}</td>
            </tr>
            <tr>
                <td style="padding:4px 6px;font-weight:600;">Thời lượng</td>
                <td style="padding:4px 6px;">{duration}</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:4px 6px;font-weight:600;">Tốc độ</td>
                <td style="padding:4px 6px;">{speed_start} → {speed_end}
                    <span style="color:{color};font-weight:bold;">({speed_change})</span>
                </td>
            </tr>
            <tr>
                <td style="padding:4px 6px;font-weight:600;">Speed min/max</td>
                <td style="padding:4px 6px;">{speed_min} / {speed_max}</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:4px 6px;font-weight:600;">Accel (min/max)</td>
                <td style="padding:4px 6px;">{min_accel} / {max_accel}</td>
            </tr>
            <tr>
                <td style="padding:4px 6px;font-weight:600;">Jerk (min/max)</td>
                <td style="padding:4px 6px;">{min_jerk} / {max_jerk}</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:4px 6px;font-weight:600;">Gyro Z max</td>
                <td style="padding:4px 6px;">{max_gyr}</td>
            </tr>
            <tr>
                <td style="padding:4px 6px;font-weight:600;">Samples</td>
                <td style="padding:4px 6px;">{samples}</td>
            </tr>
        </table>

        <div style="margin-top:10px;text-align:center;">
            <button
                onclick="open3DSimulator({event_id})"
                style="
                    width:100%;
                    padding:8px 10px;
                    border:0;
                    border-radius:5px;
                    background:#2C3E50;
                    color:white;
                    font-weight:bold;
                    cursor:pointer;
                    font-size:12px;
                "
            >
                MÔ PHỎNG ĐỘNG LỰC HỌC 3D
            </button>
        </div>
    </div>
    """
    return html


# ============================================================
# LAYER CONTROLS
# ============================================================

def add_layer_controls(m, groups, track_group):
    if not groups:
        return

    map_name = m.get_name()
    track_js = track_group.get_name() if track_group else "null"
    event_layer_names = [groups[et].get_name() for et in groups]
    event_layers_js   = ",\n".join(event_layer_names)

    script = """
    <script>
    document.addEventListener("DOMContentLoaded", function () {
        var map         = __MAP_NAME__;
        var trackLayer  = __TRACK_LAYER__;
        var eventLayers = [__EVENT_LAYERS__];
        var allOverlays = trackLayer ? [trackLayer].concat(eventLayers) : eventLayers.slice();

        var lc = document.querySelector(".leaflet-control-layers");
        if (!lc) return;
        var overlays = lc.querySelector(".leaflet-control-layers-overlays") || lc;

        var allRow = document.createElement("div");
        allRow.id  = "custom-all-overlay-row";
        allRow.style.cssText = "padding:4px 2px 6px 2px;border-bottom:2px solid #bbb;margin-bottom:5px;";

        var allChk = document.createElement("input");
        allChk.type = "checkbox"; allChk.id = "chk-all-overlay";
        allChk.style.marginRight = "7px"; allChk.style.cursor = "pointer";

        var allLbl = document.createElement("label");
        allLbl.htmlFor = "chk-all-overlay";
        allLbl.textContent = "Tất cả overlay";
        allLbl.style.cssText = "font-weight:bold;cursor:pointer;user-select:none;";

        allRow.appendChild(allChk);
        allRow.appendChild(allLbl);

        var evRow = document.createElement("div");
        evRow.id  = "custom-all-events-row";
        evRow.style.cssText = "padding:4px 2px 4px 2px;border-bottom:1px solid #ddd;margin-bottom:5px;";

        var evChk = document.createElement("input");
        evChk.type = "checkbox"; evChk.id = "chk-all-events";
        evChk.style.marginRight = "7px"; evChk.style.cursor = "pointer";

        var evLbl = document.createElement("label");
        evLbl.htmlFor = "chk-all-events";
        evLbl.textContent = "Tất cả sự kiện";
        evLbl.style.cssText = "font-weight:bold;cursor:pointer;user-select:none;";

        evRow.appendChild(evChk);
        evRow.appendChild(evLbl);

        function insertRows() {
            if (!overlays.contains(allRow)) {
                overlays.insertBefore(allRow, overlays.firstChild);
            }
            if (!overlays.contains(evRow)) {
                var after = allRow.nextSibling;
                overlays.insertBefore(evRow, after);
            }
        }

        function syncChk(chk, layers) {
            var n = layers.filter(function(l){ return l && map.hasLayer(l); }).length;
            var t = layers.filter(function(l){ return !!l; }).length;
            if (n === t)  { chk.checked = true;  chk.indeterminate = false; }
            else if (n===0){ chk.checked = false; chk.indeterminate = false; }
            else           { chk.checked = false; chk.indeterminate = true; }
        }

        function syncAll() {
            syncChk(allChk, allOverlays);
            syncChk(evChk, eventLayers);
        }

        allChk.addEventListener("change", function() {
            var on = allChk.checked;
            allOverlays.forEach(function(l) {
                if (!l) return;
                if (on) { if (!map.hasLayer(l)) map.addLayer(l); }
                else    { if ( map.hasLayer(l)) map.removeLayer(l); }
            });
        });

        evChk.addEventListener("change", function() {
            var on = evChk.checked;
            eventLayers.forEach(function(l) {
                if (!l) return;
                if (on) { if (!map.hasLayer(l)) map.addLayer(l); }
                else    { if ( map.hasLayer(l)) map.removeLayer(l); }
            });
        });

        map.on("overlayadd overlayremove", syncAll);

        var observer = new MutationObserver(function() {
            observer.disconnect();
            insertRows();
            syncAll();
            observer.observe(overlays, { childList: true });
        });
        observer.observe(overlays, { childList: true });

        insertRows();
        syncAll();
    });
    </script>
    """
    script = script.replace("__MAP_NAME__", map_name)
    script = script.replace("__TRACK_LAYER__", track_js)
    script = script.replace("__EVENT_LAYERS__", event_layers_js)

    m.get_root().html.add_child(folium.Element(script))


# ============================================================
# SIDEBAR PHẢI
# ============================================================

def build_sidebar_html(events_df, present_types, valid_events, map_name):
    counts = events_df["event_type"].value_counts().to_dict()
    total  = len(events_df)

    pos_entries = []
    for _, row in valid_events.iterrows():
        try:
            eid = int(row["event_id"])
            lat = float(row["lat"])
            lon = float(row["lon"])
            pos_entries.append(f"  {eid}: [{lat:.6f}, {lon:.6f}]")
        except Exception:
            continue

    pos_js = "{\n" + ",\n".join(pos_entries) + "\n}"
    sections = ""

    for group_index, (group_label, type_list) in enumerate(SPEED_GROUP_ORDER):
        items_in_group = [t for t in type_list if t in present_types]
        if not items_in_group:
            continue

        group_items = ""

        for et in items_in_group:
            color = EVENT_COLORS.get(et, "#7F8C8D")
            name  = EVENT_NAMES.get(et, et)
            cnt   = int(counts.get(et, 0))

            if cnt == 0:
                continue

            id_rows = valid_events[valid_events["event_type"] == et]["event_id"]
            id_chips = ""

            for eid in id_rows:
                try:
                    eid_int = int(eid)
                except Exception:
                    eid_int = str(eid)

                id_chips += f"""
                <span
                    class="event-id-chip"
                    data-event-id="{eid_int}"
                    onclick="flyToEvent({eid_int}, this)"
                    style="cursor:pointer;display:inline-block;background:{color};color:white;
                        border-radius:3px;padding:1px 5px;margin:1px;font-size:10px;white-space:nowrap;
                        transition:all .2s ease;"
                    title="Bay tới sự kiện #{eid_int}"
                >#{eid_int}</span>
                """

            group_items += f"""
            <div style="margin:5px 0;">
                <div style="display:flex;align-items:center;justify-content:space-between;">
                    <div style="display:flex;align-items:center;">
                        <span style="display:inline-block;width:11px;height:11px;background:{color};
                            border-radius:50%;margin-right:6px;border:1px solid rgba(0,0,0,.3);
                            flex-shrink:0;"></span>
                        <span style="font-size:12px;">{name}</span>
                    </div>
                    <span style="font-weight:bold;color:{color};font-size:13px;margin-left:8px;
                        background:{color}22;border-radius:10px;padding:0 7px;">{cnt}</span>
                </div>
                <div style="margin-top:3px;margin-left:17px;line-height:1.8;">{id_chips}</div>
            </div>
            """

        if not group_items:
            continue

        group_id = f"event-group-{group_index}"

        sections += f"""
        <div style="margin-top:10px;">
            <div
                class="event-group-header"
                onclick="toggleEventGroup('{group_id}', this)"
                style="
                    display:flex;align-items:center;justify-content:space-between;
                    cursor:pointer;user-select:none;
                    font-weight:700;font-size:11px;color:#666;text-transform:uppercase;
                    letter-spacing:.5px;border-bottom:1px solid #e0e0e0;
                    padding:5px 2px;margin-bottom:4px;
                "
                title="Bung / thu gọn nhóm sự kiện"
            >
                <span>
                    <span
                        class="event-group-arrow"
                        style="display:inline-block;width:14px;color:#555;font-size:12px;"
                    >▼</span>
                    {group_label}
                </span>

                <span style="font-size:10px;color:#999;font-weight:400;text-transform:none;">
                    Click
                </span>
            </div>

            <div
                id="{group_id}"
                class="event-group-content"
                style="display:block;transition:all .2s ease;"
            >
                {group_items}
            </div>
        </div>
        """
        
    sidebar_top = """
    <div style="margin-top:10px;text-align:center;margin-bottom:15px;">
        <button
            onclick="open3DSimulator('ALL')"
            style="width:100%; padding:10px; border:0; border-radius:5px; background:#e74c3c; color:white; font-weight:bold; cursor:pointer; font-size:12px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);"
        >
            ▶ MÔ PHỎNG TOÀN BỘ HÀNH TRÌNH 3D
        </button>
    </div>
    """

    html = f"""
    <div id="event-sidebar" style="position:fixed;top:80px;right:5px;z-index:9999;
        background:white;padding:12px 14px;border:2px solid #aaa;border-radius:8px;
        box-shadow:0 3px 14px rgba(0,0,0,0.18);font-family:'Segoe UI',Arial,sans-serif;
        font-size:12.5px;width:280px;max-height:calc(100vh - 100px);overflow-y:auto;overflow-x:hidden;">
        <div style="font-size:14px;font-weight:bold;margin-bottom:4px;border-bottom:2px solid #e0e0e0;
            padding-bottom:7px;display:flex;justify-content:space-between;align-items:center;">
            <span>&#x1F5FA;&#xFE0F; Event Legend</span>
            <span style="font-size:11px;font-weight:400;color:#888;">
                Tổng: <b style="color:#333;font-size:13px;">{total}</b>
            </span>
        </div>
        {sidebar_top}
        {sections}
    </div>
    """

    js = f"""
    <script>
    var _eventPositions = {pos_js};
    var _selectedEventChip = null;

    function clearSelectedEventChip() {{
        if (!_selectedEventChip) return;

        _selectedEventChip.style.outline = "";
        _selectedEventChip.style.boxShadow = "";
        _selectedEventChip.style.transform = "";
        _selectedEventChip.style.background =
            _selectedEventChip.dataset.originalColor || "";

        _selectedEventChip = null;
    }}

    function selectEventChip(chip) {{
        clearSelectedEventChip();

        if (!chip) return;

        if (!chip.dataset.originalColor) {{
            chip.dataset.originalColor = chip.style.backgroundColor;
        }}

        chip.style.background = "#000000";
        chip.style.outline = "2px solid #FFD700";
        chip.style.boxShadow = "0 0 0 2px rgba(255,215,0,.35)";
        chip.style.transform = "scale(1.12)";

        _selectedEventChip = chip;
    }}

    function toggleEventGroup(groupId, header) {{
        var content = document.getElementById(groupId);
        if (!content) return;

        var arrow = header.querySelector(".event-group-arrow");
        var isHidden = content.style.display === "none";

        if (isHidden) {{
            content.style.display = "block";
            if (arrow) arrow.textContent = "▼";
        }} else {{
            content.style.display = "none";
            if (arrow) arrow.textContent = "▶";
        }}
    }}

    function _findEventMarkerRecursive(layerGroup, eid) {{
        var found = null;
        var target = String(eid);

        function walk(container) {{
            if (found) return;

            container.eachLayer(function(layer) {{
                if (found) return;

                var layerId = null;

                if (layer.options && layer.options.eventId !== undefined) {{
                    layerId = String(layer.options.eventId);
                }} else if (layer._eventId !== undefined) {{
                    layerId = String(layer._eventId);
                }}

                if (
                    layerId === target &&
                    typeof layer.openPopup === "function"
                ) {{
                    found = layer;
                    return;
                }}

                if (
                    layer.eachLayer &&
                    typeof layer.eachLayer === "function"
                ) {{
                    walk(layer);
                }}
            }});
        }}

        walk(layerGroup);
        return found;
    }}

    function flyToEvent(eid, chip) {{
        var pos = _eventPositions[eid];

        selectEventChip(chip);

        if (!pos) return;

        var map = {map_name};

        map.flyTo(
            pos,
            18,
            {{animate: true, duration: 1.2}}
        );

        setTimeout(function() {{
            var marker = _findEventMarkerRecursive(map, eid);

            if (marker && typeof marker.openPopup === "function") {{
                marker.openPopup();

                if (marker._icon) {{
                    marker._icon.style.filter =
                        "drop-shadow(0 0 7px #FFD700)";
                }}
            }}
        }}, 1300);
    }}
    </script>
    """

    return html, js


def add_3d_simulator(m, events_df, features_filename):
    event_meta = []
    events_df_sorted = events_df.sort_values(by="start_time")

    for _, row in events_df_sorted.iterrows():
        try:
            eid = int(row["event_id"])
            def num(col, default=0.0):
                try:
                    value = float(row[col])
                    if pd.isna(value): return default
                    return value
                except Exception: return default

            event_meta.append({
                "event_id": eid,
                "event_type": str(row.get("event_type", "")),
                "direction": str(row.get("direction", "")),
                "start_time": num("start_time"),
                "end_time": num("end_time"),
                "duration": num("duration"),
                "speed_start": num("speed_start"),
                "speed_end": num("speed_end"),
                "speed_change": num("speed_change"),
                "lat": num("lat"),
                "lon": num("lon"),
            })
        except Exception:
            continue

    meta_js = json.dumps(event_meta, ensure_ascii=False, separators=(",", ":"))
    features_js = json.dumps(os.path.basename(features_filename), ensure_ascii=False)

    html = r"""
    <style>
        #motion3d-overlay {
            display:none; position:fixed; inset:0; z-index:999999;
            background:rgba(0,0,0,.78); font-family:'Segoe UI',Arial,sans-serif;
        }
        #motion3d-panel {
            position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
            width:min(1280px,96vw); height:min(860px,94vh); background:#101418;
            color:#ecf0f1; border:1px solid #3d4852; border-radius:10px;
            box-shadow:0 15px 50px rgba(0,0,0,.55); overflow:hidden;
            display:flex; flex-direction:column;
        }
        #motion3d-header {
            min-height:54px; display:flex; align-items:center; justify-content:space-between;
            padding:0 14px 0 18px; background:#182028; border-bottom:1px solid #34404a;
        }
        #motion3d-title { font-size:16px; font-weight:700; }
        #motion3d-close {
            border:0; background:#c0392b; color:white; width:34px; height:32px;
            border-radius:5px; cursor:pointer; font-size:18px; font-weight:bold;
        }
        #motion3d-main { flex:1; min-height:0; display:flex; }
        #motion3d-view { position:relative; flex:1; min-width:0; background:#0b0f12; }
        #motion3d-canvas { width:100%; height:100%; display:block; }
        #motion3d-info {
            width:280px; padding:12px; background:#151b21; border-left:1px solid #34404a;
            overflow-y:auto; box-sizing:border-box;
        }
        .motion3d-section {
            margin-bottom:12px; border:1px solid #2d3740; border-radius:6px;
            padding:9px; background:#10161b;
        }
        .motion3d-section-title {
            font-size:11px; font-weight:700; color:#9fb0bf; text-transform:uppercase;
            margin-bottom:7px; letter-spacing:.4px;
        }
        .motion3d-value { display:flex; justify-content:space-between; gap:8px; margin:4px 0; font-size:12px; }
        .motion3d-value span:first-child { color:#aeb8c1; }
        .motion3d-value span:last-child { font-weight:600; text-align:right; }
        #motion3d-controls {
            padding:10px 14px 12px; background:#182028; border-top:1px solid #34404a;
        }
        #motion3d-buttons { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; align-items:center; }
        .motion3d-btn {
            border:1px solid #52606d; background:#26313b; color:#ecf0f1; border-radius:4px;
            padding:6px 10px; cursor:pointer; font-size:11px; transition: all .15s ease;
        }
        .motion3d-btn:hover { background:#34424e; }
        #motion3d-slider { width:100%; cursor:pointer; }
        #motion3d-status { margin-top:4px; font-size:11px; color:#9fb0bf; }
        #motion3d-loading {
            position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
            padding:10px 15px; border-radius:6px; background:rgba(0,0,0,.72); color:white;
            font-size:13px; display:none; z-index: 20;
        }
        .motion3d-axis-x { color:#ff5c5c; }
        .motion3d-axis-y { color:#63d471; }
        .motion3d-axis-z { color:#5da9ff; }

        .m3d-slider-row { margin-bottom: 8px; }
        .m3d-slider-label { display: flex; justify-content: space-between; font-size: 11px; color: #aeb8c1; margin-bottom: 3px; }
        .m3d-slider-input { width: 100%; cursor: pointer; }

        #motion3d-charts {
            position: absolute; 
            bottom: 8px;
            left: 10px; 
            width: calc(100% - 20px);
            height: 140px; 
            display: flex; 
            gap: 10px; 
            pointer-events: none; 
            z-index: 10;
        }

        .motion3d-chart-box {
            flex: 1; 
            background: rgba(16, 20, 24, 0.75); 
            border: 1px solid #34404a;
            border-radius: 6px; 
            padding: 25px 10px 10px 10px; 
            position: relative;
        }
        .motion3d-chart-title { position: absolute; top: 6px; left: 10px; font-size: 10px; color: #9fb0bf; font-weight: 700; }
        .motion3d-chart-legend { position: absolute; top: 6px; right: 10px; font-size: 10px; display: flex; gap: 8px; }

        #m3d-realtime-event-box {
            position:absolute; top:20px; left:50%; transform:translateX(-50%); 
            background:rgba(0,0,0,0.85); color:#fff; padding:12px 24px; border-radius:8px; 
            display:none; z-index:100; text-align:center; border: 2px solid #e74c3c; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.5); pointer-events: none; min-width: 250px; 
            transition: all .2s ease;
        }

        @media (max-width:850px) { #motion3d-info { display:none; } }
    </style>

    <div id="motion3d-overlay">
        <div id="motion3d-panel">
            <div id="motion3d-header">
                <div id="motion3d-title">Mô phỏng động lực học 3D (Tối ưu hóa Buffer)</div>
                <button id="motion3d-close" onclick="close3DSimulator()">×</button>
            </div>

            <div id="motion3d-main">
                <div id="motion3d-view">
                    
                    <div id="m3d-realtime-event-box">
                        <div id="m3d-rt-id" style="font-size:16px; font-weight:bold; color:#f1c40f;">Event #?</div>
                        <div id="m3d-rt-type" style="font-size:14px; margin-top:5px; font-weight:600;">Type</div>
                    </div>

                    <canvas id="motion3d-canvas"></canvas>
                    
                    <div id="motion3d-charts">
                        <div class="motion3d-chart-box">
                            <div class="motion3d-chart-title">ACCELEROMETER (m/s²)</div>
                            <div class="motion3d-chart-legend">
                                <span class="motion3d-axis-x">■ X</span>
                                <span class="motion3d-axis-y">■ Y</span>
                                <span class="motion3d-axis-z">■ Z</span>
                            </div>
                            <canvas id="chart-accel" style="width:100%; height:100%; display:block;"></canvas>
                        </div>
                        <div class="motion3d-chart-box" style="flex:0 0 auto;width:200px;pointer-events:auto;">
                            <div class="motion3d-chart-title">TRACKING XE</div>
                            <canvas id="chart-tracking" style="width:100%; height:100%; display:block;cursor:default;"></canvas>
                        </div>
                        <div class="motion3d-chart-box">
                            <div class="motion3d-chart-title">GYROSCOPE (rad/s)</div>
                            <div class="motion3d-chart-legend">
                                <span class="motion3d-axis-x">■ X</span>
                                <span class="motion3d-axis-y">■ Y</span>
                                <span class="motion3d-axis-z">■ Z</span>
                            </div>
                            <canvas id="chart-gyro" style="width:100%; height:100%; display:block;"></canvas>
                        </div>
                    </div>
                    <div id="motion3d-loading">Đang tải và tính toán dữ liệu ban đầu...</div>
                </div>

                <div id="motion3d-info">
                    <div class="motion3d-section">
                        <div class="motion3d-section-title">Sự kiện đang theo dõi</div>
                        <div class="motion3d-value"><span>ID</span><span id="m3d-event-id">-</span></div>
                        <div class="motion3d-value"><span>Loại</span><span id="m3d-event-type">-</span></div>
                        <div class="motion3d-value"><span>Khoảng TG</span><span id="m3d-time-range">-</span></div>
                    </div>
                    <div class="motion3d-section">
                        <div class="motion3d-section-title">Trạng thái xe tức thời</div>
                        <div class="motion3d-value"><span>t</span><span id="m3d-time">-</span></div>
                        <div class="motion3d-value"><span>Tốc độ</span><span id="m3d-speed">-</span></div>
                        <div class="motion3d-value"><span>Accel X</span><span id="m3d-ax">-</span></div>
                        <div class="motion3d-value"><span>Accel Y</span><span id="m3d-ay">-</span></div>
                        <div class="motion3d-value"><span>Accel Z</span><span id="m3d-az">-</span></div>
                    </div>
                    <div class="motion3d-section">
                        <div class="motion3d-section-title">Euler BNO055</div>
                        <div class="motion3d-value"><span class="motion3d-axis-z">Yaw (Z)</span><span id="m3d-yaw">-</span></div>
                        <div class="motion3d-value"><span class="motion3d-axis-x">Roll (X)</span><span id="m3d-roll">-</span></div>
                        <div class="motion3d-value"><span class="motion3d-axis-y">Pitch (Y)</span><span id="m3d-pitch">-</span></div>
                    </div>
                    
                    <div class="motion3d-section">
                        <div class="motion3d-section-title" style="display:flex;justify-content:space-between;align-items:center;">
                            <span>Tinh chỉnh Vật lý</span>
                            <button onclick="m3dResetCfg()" style="font-size:10px;padding:2px 7px;border:1px solid #52606d;background:#26313b;color:#ecf0f1;border-radius:4px;cursor:pointer;">↺ Reset</button>
                        </div>
                        <div class="m3d-slider-row">
                            <div class="m3d-slider-label"><span>Độ chúi mũi (Pitch)</span><span id="lbl-cfg-pitch">0.6</span></div>
                            <input type="range" class="m3d-slider-input" id="cfg-pitch" min="0" max="3" step="0.1" value="0.6" oninput="m3dUpdateCfg()">
                        </div>
                        <div class="m3d-slider-row">
                            <div class="m3d-slider-label"><span>Độ nghiêng lườn (Roll)</span><span id="lbl-cfg-roll">0.5</span></div>
                            <input type="range" class="m3d-slider-input" id="cfg-roll" min="0" max="3" step="0.1" value="0.5" oninput="m3dUpdateCfg()">
                        </div>
                        <div class="m3d-slider-row">
                            <div class="m3d-slider-label"><span>Làm mượt gia tốc (ms)</span><span id="lbl-cfg-accel-smooth">120</span></div>
                            <input type="range" class="m3d-slider-input" id="cfg-accel-smooth" min="0" max="500" step="10" value="120" oninput="m3dUpdateCfg()">
                        </div>
                    </div>
                </div>
            </div>

            <div id="motion3d-controls">
                <div id="motion3d-buttons">
                    <button class="motion3d-btn" onclick="motion3DPlay()">▶ Play</button>
                    <button class="motion3d-btn" onclick="motion3DPause()">⏸ Pause</button>
                    <button class="motion3d-btn" onclick="motion3DReset()">↺ Reset</button>
                    
                    <span style="color:#444;">|</span>
                    <button class="motion3d-btn" onclick="motion3DPrevEvent()" id="btn-prev-event" style="background:#d35400;border-color:#e67e22;" title="Nhảy tới sự kiện phía trước">⏮</button>
                    <button class="motion3d-btn" onclick="motion3DNextEvent()" id="btn-next-event" style="background:#d35400;border-color:#e67e22;" title="Nhảy tới sự kiện phía sau">⏭</button>

                    <span style="color:#444;">|</span>
                    <button class="motion3d-btn" onclick="motion3DView('rear')">Rear</button>
                    <button class="motion3d-btn" onclick="motion3DView('top')">Top</button>
                    <button class="motion3d-btn" onclick="motion3DView('side')">Side</button>
                    <button class="motion3d-btn" onclick="motion3DView('fit')" title="Xem toàn bộ hành trình" style="background:#1a3a4a;border-color:#4a8fa8;">&#x1F5FA; Fix View</button>
                    
                    <span style="flex:1;"></span>
                    <button class="motion3d-btn" onclick="motion3DToggleTracking()" id="btn-tracking" title="Bật/tắt theo dõi tâm xe" style="background:#2a5a3a;border-color:#6abf8a;">&#x1F3AF; Tracking: Bật</button>
                    <button class="motion3d-btn" onclick="motion3DToggleShowEvent()" id="btn-show-event" title="Bật/tắt hiện thông tin sự kiện khi xe đi qua" style="background:#8e44ad;border-color:#9b59b6;">🔔 Báo sự kiện: Bật</button>
                </div>

                <input id="motion3d-slider" type="range" min="0" max="100" step="0.05" value="0" oninput="motion3DSliderChanged(this.value)" onchange="motion3DSliderChanged(this.value)" style="position:relative;z-index:30;">

                <div style="display:flex;align-items:center;gap:8px;margin-top:7px;">
                    <span style="font-size:11px;color:#9fb0bf;white-space:nowrap;">Timeline (s): </span>
                    <span id="motion3d-slider-time" style="font-size:11px;color:#ecf0f1;font-weight:bold;width:45px;">0.00</span>
                    
                    <span style="font-size:11px;color:#9fb0bf;white-space:nowrap;margin-left:15px;">Tốc độ phát:</span>
                    <input id="motion3d-speed-slider" type="range" min="0.25" max="4" step="0.25" value="3" oninput="motion3DSpeedChanged(this.value)" style="flex:1;cursor:pointer;">
                    <span id="motion3d-speed-value" style="font-size:11px;color:#ecf0f1;min-width:42px;text-align:right;">3.00×</span>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>

    <script>
    var _motion3DEvents = __EVENT_META__;
    var _motion3DFeaturesFile = __FEATURES_FILE__;
    
    // --- GLOBAL DATA & BUFFERING ---
    var _csvLines = [];
    var _csvHeaderIdx = {};
    var _globalAllTimes = null;       // Float32Array for fast binary search
    var _globalAllPositions = null;   // Pre-calculated global X,Y trajectory
    var _globalStartTime = 0;
    var _globalEndTime = 0;
    
    // The active sliding window
    var _motion3DData = []; 
    var _activeBufferStartTime = 0;
    var _activeBufferEndTime = 0;
    var _bufferDuration = 10.0; // Seconds to load ahead

    // Simulation State
    var _simT = 0; // Current physical time in seconds
    var _isAllMode = false;
    var _currentEventId = null;
    
    var _motion3DPlaying = false;
    var _motion3DPlaybackSpeed = 3.0;
    var _motion3DLastTick = 0;

    // Three.js State
    var _motion3DReady = false;
    var _motion3DScene, _motion3DCamera, _motion3DRenderer, _motion3DControls;
    var _motion3DCar, _motion3DAccelArrow, _motion3DTrajectory;
    
    // UI State
    var _motion3DTracking = true;
    var _motion3DShowRealtimeEvents = true;
    var _motion3DLastEventShown = "";
    var _fitViewCycle = ['rear', 'top', 'side', 'front'];
    var _fitViewIndex = 0;
    var _fitViewLocked = false;

    // Physics Tuning
    var _cfgPitch = 0.6, _cfgRoll = 0.5, _cfgAccelSmooth = 120;
    var _cfgAccelGain = 0.85, _cfgBodyLag = 0.18, _cfgMaxAngle = 4.0, _cfgStop = 3.0;
    
    // Vehicle Dynamics State
    var _motion3DFilteredAy = 0, _motion3DFilteredAx = 0;
    var _motion3DFilteredAccelInit = false;
    var _motion3DBodyPitch = 0, _motion3DBodyRoll = 0;
    var _motion3DBodyInit = false;
    
    var _chartAccelMin = -1, _chartAccelMax = 1;
    var _chartGyroMin = -1, _chartGyroMax = 1;

    // ----------------------------------------------------
    // UTILS & CONFIG
    // ----------------------------------------------------
    function _motion3DNum(v) { var n = Number(v); return Number.isFinite(n) ? n : 0; }
    function _motion3DSetText(id, value) { var el = document.getElementById(id); if (el) el.textContent = value; }
    
    function m3dUpdateCfg() {
        _cfgPitch = Number(document.getElementById("cfg-pitch").value);
        _cfgRoll = Number(document.getElementById("cfg-roll").value);
        _cfgAccelSmooth = Number(document.getElementById("cfg-accel-smooth").value);
        document.getElementById("lbl-cfg-pitch").textContent = _cfgPitch.toFixed(1);
        document.getElementById("lbl-cfg-roll").textContent = _cfgRoll.toFixed(1);
        document.getElementById("lbl-cfg-accel-smooth").textContent = _cfgAccelSmooth.toFixed(0);
    }
    
    function m3dResetCfg() {
        document.getElementById("cfg-pitch").value = "0.6";
        document.getElementById("cfg-roll").value = "0.5";
        document.getElementById("cfg-accel-smooth").value = "120";
        m3dUpdateCfg();
    }

    // ----------------------------------------------------
    // GLOBAL CSV PARSING (Run Once)
    // ----------------------------------------------------
    function _binarySearchTime(t) {
        var lo = 1, hi = _globalAllTimes.length - 1;
        while (lo <= hi) {
            var mid = (lo + hi) >> 1;
            if (_globalAllTimes[mid] < t) lo = mid + 1;
            else if (_globalAllTimes[mid] > t) hi = mid - 1;
            else return mid;
        }
        return Math.min(Math.max(1, lo), _globalAllTimes.length - 1);
    }

    function _processGlobalCSV(text) {
        var rawLines = text.replace(/\r/g, "").split("\n");
        _csvLines = [];
        for(var i=0; i<rawLines.length; i++) {
            if(rawLines[i].trim().length > 0) _csvLines.push(rawLines[i]);
        }
        
        var headers = _csvLines[0].split(",").map(function(h) { return h.trim().replace(/^"|"$/g, ""); });
        _csvHeaderIdx = {};
        for(var i=0; i<headers.length; i++) _csvHeaderIdx[headers[i]] = i;

        var numRows = _csvLines.length;
        _globalAllTimes = new Float32Array(numRows);
        _globalAllPositions = new Array(numRows);

        var pos = new THREE.Vector3(0,0,0);
        _globalAllPositions[0] = pos.clone();
        _globalAllTimes[0] = 0;

        var prevSpeed = 0, prevTime = -1;
        var hTime = _csvHeaderIdx.time, hSpeed = _csvHeaderIdx.speed_kmh, hYaw = _csvHeaderIdx.yaw;

        for (var i = 1; i < numRows; i++) {
            var p = _csvLines[i].split(",");
            if (p.length < 5) continue;
            
            var t = _motion3DNum(p[hTime]);
            _globalAllTimes[i] = t;

            var rawSpeed = Math.max(0, _motion3DNum(p[hSpeed])) / 3.6;
            var rawYaw = _motion3DNum(p[hYaw]);

            if (i === 1) prevTime = t;
            var dt = t - prevTime;
            
            // --- FIX ĐƠN GIẢN VÀ HIỆU QUẢ ---
            // Tuyệt đối không ép dt = 0.01 nếu gap > 0.5s.
            // Nâng giới hạn lên 3.0 giây để xe vẫn đi hết quãng đường khi dữ liệu bị lag ở khúc cua.
            if (dt <= 0) dt = 0.01;
            if (dt > 3.0) dt = 3.0; 

            // Tính vận tốc bằng bộ lọc làm mượt
            var speedTau = Math.max(_cfgAccelSmooth / 1000, 0.02);
            var speedAlpha = 1 - Math.exp(-dt / speedTau);
            var speed = prevSpeed + (rawSpeed - prevSpeed) * speedAlpha;
            
            // Tính quãng đường bằng Tích phân hình thang (Trapezoidal Rule) để mượt hơn
            var distance = 0.5 * (prevSpeed + speed) * dt; 
            if (distance < 0) distance = 0;

            var yawRad = -rawYaw * Math.PI / 180;
            
            // Cộng dồn vector quỹ đạo strictly theo hệ quy chiếu gốc của IMU
            pos.x += -Math.sin(yawRad) * distance;
            pos.y +=  Math.cos(yawRad) * distance;
            
            _globalAllPositions[i] = pos.clone();

            prevSpeed = speed;
            prevTime = t;
        }

        _globalStartTime = _globalAllTimes[1];
        _globalEndTime = _globalAllTimes[numRows - 1];
    }

    function _buildGlobalTrajectoryLine() {
        if (_motion3DTrajectory) {
            _motion3DScene.remove(_motion3DTrajectory);
            _motion3DTrajectory.geometry.dispose();
            _motion3DTrajectory.material.dispose();
        }
        if (!_globalAllPositions || _globalAllPositions.length < 2) return;
        
        // Subsample global line so ThreeJS doesn't render 300k vertices
        var subsampled = [];
        var step = Math.ceil(_globalAllPositions.length / 5000); 
        for(var i = 1; i < _globalAllPositions.length; i += step) {
            subsampled.push(_globalAllPositions[i]);
        }
        subsampled.push(_globalAllPositions[_globalAllPositions.length - 1]);
        
        var geometry = new THREE.BufferGeometry().setFromPoints(subsampled);
        var material = new THREE.LineBasicMaterial({ color: 0xffd166, transparent: true, opacity: 0.6 });
        _motion3DTrajectory = new THREE.Line(geometry, material);
        _motion3DScene.add(_motion3DTrajectory);
    }

    // ----------------------------------------------------
    // BUFFERING LOGIC (10s Sliding Window)
    // ----------------------------------------------------
    function _parseLineToSample(lineIdx) {
        var p = _csvLines[lineIdx].split(",");
        return {
            lineIdx: lineIdx,
            time:   _motion3DNum(p[_csvHeaderIdx.time]),
            ax:     _motion3DNum(p[_csvHeaderIdx.acc_x]),
            ay:     _motion3DNum(p[_csvHeaderIdx.acc_y]),
            az:     _motion3DNum(p[_csvHeaderIdx.acc_z]),
            gx:     _motion3DNum(p[_csvHeaderIdx.gyr_x]),
            gy:     _motion3DNum(p[_csvHeaderIdx.gyr_y]),
            gz:     _motion3DNum(p[_csvHeaderIdx.gyr_z]),
            yaw:    _motion3DNum(p[_csvHeaderIdx.yaw]),
            roll:   _motion3DNum(p[_csvHeaderIdx.roll]),
            pitch:  _motion3DNum(p[_csvHeaderIdx.pitch]),
            speed:  _motion3DNum(p[_csvHeaderIdx.speed_kmh])
        };
    }

    function _recalcChartBounds() {
        if (_motion3DData.length === 0) return;
        _chartAccelMin = _motion3DData[0].ax; _chartAccelMax = _chartAccelMin;
        _chartGyroMin = _motion3DData[0].gx; _chartGyroMax = _chartGyroMin;
        
        for (var i = 0; i < _motion3DData.length; i++) {
            var d = _motion3DData[i];
            _chartAccelMin = Math.min(_chartAccelMin, d.ax, d.ay, d.az);
            _chartAccelMax = Math.max(_chartAccelMax, d.ax, d.ay, d.az);
            _chartGyroMin = Math.min(_chartGyroMin, d.gx, d.gy, d.gz);
            _chartGyroMax = Math.max(_chartGyroMax, d.gx, d.gy, d.gz);
        }
        var aPad = (_chartAccelMax - _chartAccelMin) * 0.15 || 2.0;
        _chartAccelMin -= aPad; _chartAccelMax += aPad;
        var gPad = (_chartGyroMax - _chartGyroMin) * 0.15 || 0.5;
        _chartGyroMin -= gPad; _chartGyroMax += gPad;
    }

    function _loadBuffer(startT, duration) {
        var endT = startT + duration;
        var startIdx = _binarySearchTime(startT);
        var endIdx = _binarySearchTime(endT);
        if (endIdx < startIdx) endIdx = startIdx;
        
        var newData = [];
        for(var i = startIdx; i <= endIdx; i++) {
            newData.push(_parseLineToSample(i));
        }
        return newData;
    }

    function _checkAndExtendBuffer(simT) {
        if (_motion3DData.length === 0) return;
        var lastT = _motion3DData[_motion3DData.length - 1].time;
        
        // If we are within 2 seconds of buffer end, load next chunk
        if (simT > lastT - 2.0 && lastT < _globalEndTime) {
            var extData = _loadBuffer(lastT + 0.001, _bufferDuration);
            if (extData.length > 0) {
                _motion3DData = _motion3DData.concat(extData);
                
                // Shift out data older than simT - 4s to keep arrays small
                var cutoff = simT - 4.0;
                var removeCount = 0;
                while(removeCount < _motion3DData.length && _motion3DData[removeCount].time < cutoff) {
                    removeCount++;
                }
                if (removeCount > 0) {
                    _motion3DData.splice(0, removeCount);
                }
                _recalcChartBounds();
            }
        }
    }

    // ----------------------------------------------------
    // PLAYBACK & PHYSICS
    // ----------------------------------------------------
    function _motion3DApplySample(s, interpF) {
        if (!s || !_motion3DCar) return;

        var alpha = _cfgAccelSmooth <= 0 ? 1 : 1 - Math.exp(-0.016 / (_cfgAccelSmooth / 1000));
        if (!_motion3DFilteredAccelInit) {
            _motion3DFilteredAy = s.ay; _motion3DFilteredAx = s.ax;
            _motion3DFilteredAccelInit = true;
        } else {
            _motion3DFilteredAy += (s.ay - _motion3DFilteredAy) * alpha;
            _motion3DFilteredAx += (s.ax - _motion3DFilteredAx) * alpha;
        }

        var pitchTarget = (_motion3DFilteredAy * _cfgAccelGain) * _cfgPitch;
        var rollTarget = (-_motion3DFilteredAx * _cfgAccelGain) * _cfgRoll;
        pitchTarget = Math.max(-_cfgMaxAngle, Math.min(_cfgMaxAngle, pitchTarget));
        rollTarget = Math.max(-_cfgMaxAngle, Math.min(_cfgMaxAngle, rollTarget));

        var bodyAlpha = 1 - Math.exp(-0.016 / Math.max(_cfgBodyLag, 0.03));
        if (!_motion3DBodyInit) {
            _motion3DBodyPitch = pitchTarget; _motion3DBodyRoll = rollTarget;
            _motion3DBodyInit = true;
        } else {
            _motion3DBodyPitch += (pitchTarget - _motion3DBodyPitch) * bodyAlpha;
            _motion3DBodyRoll += (rollTarget - _motion3DBodyRoll) * bodyAlpha;
        }

        var damping = 1.0;
        if (_cfgStop > 0 && s.speed < _cfgStop) damping = Math.max(0, s.speed / _cfgStop);

        var finalPitch = _motion3DBodyPitch * damping;
        var finalRoll = _motion3DBodyRoll * damping;
        var deg = Math.PI / 180;
        var visualYaw = -s.yaw;

        var oldCarPos = _motion3DCar.position.clone();

        _motion3DCar.rotation.order = "ZXY";
        _motion3DCar.rotation.z = visualYaw * deg;
        _motion3DCar.rotation.x = finalPitch * deg;
        _motion3DCar.rotation.y = finalRoll * deg;

        // Position lookup from global trajectory!
        var idx1 = s.lineIdx;
        if (interpF !== undefined && interpF > 0 && idx1 < _globalAllPositions.length - 1) {
            var p1 = _globalAllPositions[idx1];
            var p2 = _globalAllPositions[idx1 + 1];
            _motion3DCar.position.set(
                p1.x + (p2.x - p1.x) * interpF,
                p1.y + (p2.y - p1.y) * interpF, 0
            );
        } else {
            _motion3DCar.position.copy(_globalAllPositions[idx1]);
        }

        // Camera tracking
        if (_motion3DTracking && _motion3DCamera && _motion3DControls) {
            var delta = new THREE.Vector3().subVectors(_motion3DCar.position, oldCarPos);
            _motion3DCamera.position.add(delta);
            _motion3DControls.target.copy(_motion3DCar.position);
        }

        // Accel vector
        var av = new THREE.Vector3(s.ax, s.ay, s.az);
        var amag = av.length();
        if (amag > 0.0001) {
            av.normalize();
            var visualLength = Math.min(Math.max(amag * 0.18, 0.25), 4.0);
            _motion3DAccelArrow.setDirection(av);
            _motion3DAccelArrow.setLength(visualLength, Math.min(visualLength * 0.25, 0.45), Math.min(visualLength * 0.14, 0.25));
        }

        // UI Updates
        _motion3DSetText("m3d-time", s.time.toFixed(3) + " s");
        _motion3DSetText("m3d-speed", s.speed.toFixed(2) + " km/h");
        _motion3DSetText("m3d-ax", s.ax.toFixed(3) + " m/s²");
        _motion3DSetText("m3d-ay", s.ay.toFixed(3) + " m/s²");
        _motion3DSetText("m3d-az", s.az.toFixed(3) + " m/s²");
        _motion3DSetText("m3d-yaw", s.yaw.toFixed(2) + "°");
        _motion3DSetText("m3d-roll", s.roll.toFixed(2) + "°");
        _motion3DSetText("m3d-pitch", s.pitch.toFixed(2) + "°");
        
        var slider = document.getElementById("motion3d-slider");
        if (slider) slider.value = (_simT - (_isAllMode ? _globalStartTime : _activeBufferStartTime)).toFixed(3);
        _motion3DSetText("motion3d-slider-time", _simT.toFixed(1));

        // Realtime Event notification
        if (_motion3DShowRealtimeEvents) {
            var active = _motion3DEvents.filter(e => s.time >= e.start_time && s.time <= e.end_time);
            var box = document.getElementById('m3d-realtime-event-box');
            if (active.length > 0) {
                var e = active[0];
                var hash = e.event_id + "_" + e.event_type;
                if (_motion3DLastEventShown !== hash) {
                    document.getElementById('m3d-rt-id').textContent = "Sự kiện #" + e.event_id;
                    document.getElementById('m3d-rt-type').textContent = e.event_type;
                    box.style.display = 'block';
                    _motion3DLastEventShown = hash;
                }
            } else if (_motion3DLastEventShown !== "") {
                box.style.display = 'none';
                _motion3DLastEventShown = "";
            }
        }
    }

    function _motion3DRenderLoop() {
        requestAnimationFrame(_motion3DRenderLoop);
        if (!_motion3DRenderer || !_motion3DScene || !_motion3DCamera) return;

        if (_motion3DPlaying && _motion3DData.length > 0) {
            var now = performance.now();
            if (!_motion3DLastTick) _motion3DLastTick = now;
            var realDt = Math.min((now - _motion3DLastTick) / 1000, 0.05);
            _motion3DLastTick = now;

            _simT += realDt * _motion3DPlaybackSpeed;
            var maxT = _isAllMode ? _globalEndTime : _activeBufferEndTime;

            if (_simT >= maxT) {
                _simT = maxT;
                _motion3DPlaying = false;
            }

            _checkAndExtendBuffer(_simT);

            // Find current sample in active sliding window
            var lo = 0, hi = _motion3DData.length - 1;
            while (lo < hi - 1) {
                var mid = (lo + hi) >> 1;
                if (_motion3DData[mid].time <= _simT) lo = mid; else hi = mid;
            }
            var a = _motion3DData[lo], b = _motion3DData[Math.min(lo + 1, _motion3DData.length - 1)];
            var span = b.time - a.time;
            var f = span > 0 ? (_simT - a.time) / span : 0;
            
            var sInterp = {
                lineIdx: a.lineIdx, time: _simT, speed: a.speed + (b.speed - a.speed)*f,
                ax: a.ax + (b.ax - a.ax)*f, ay: a.ay + (b.ay - a.ay)*f, az: a.az + (b.az - a.az)*f,
                gx: a.gx + (b.gx - a.gx)*f, gy: a.gy + (b.gy - a.gy)*f, gz: a.gz + (b.gz - a.gz)*f,
                yaw: a.yaw + (b.yaw - a.yaw)*f, roll: a.roll + (b.roll - a.roll)*f, pitch: a.pitch + (b.pitch - a.pitch)*f
            };
            
            _motion3DApplySample(sInterp, f);
            
            _motion3DDrawAllCharts(lo);
            _motion3DDrawTrackingChart(a.lineIdx);
        }

        if (_motion3DControls) _motion3DControls.update();
        _motion3DRenderer.render(_motion3DScene, _motion3DCamera);
    }

    // ----------------------------------------------------
    // CHARTS
    // ----------------------------------------------------
    function _drawSingleChart(canvasId, keys, colors, minV, maxV, localIndex) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || _motion3DData.length < 2) return;

        var dpr = window.devicePixelRatio || 1;
        canvas.width = canvas.clientWidth * dpr; canvas.height = canvas.clientHeight * dpr;
        var ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);

        var w = canvas.clientWidth, h = canvas.clientHeight;
        ctx.clearRect(0, 0, w, h);
        ctx.lineJoin = 'round'; ctx.lineCap = 'round';

        var zeroY = h - (0 - minV) / (maxV - minV) * h;
        if (zeroY > 0 && zeroY < h) {
            ctx.strokeStyle = 'rgba(255,255,255,0.15)'; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(0, zeroY); ctx.lineTo(w, zeroY); ctx.stroke();
        }

        var n = _motion3DData.length;
        var t0 = _motion3DData[0].time;
        var tSpan = _motion3DData[n - 1].time - t0;
        if (tSpan <= 0) tSpan = 1;

        for (var k = 0; k < keys.length; k++) {
            ctx.strokeStyle = colors[k]; ctx.lineWidth = 1.5; ctx.beginPath();
            for (var i = 0; i < n; i++) {
                var x = ((_motion3DData[i].time - t0) / tSpan) * w;
                var y = h - ((_motion3DData[i][keys[k]] - minV) / (maxV - minV)) * h;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.stroke();
        }

        var curX = ((_motion3DData[localIndex].time - t0) / tSpan) * w;
        ctx.strokeStyle = 'rgba(255,255,255,0.7)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(curX, 0); ctx.lineTo(curX, h); ctx.stroke();

        for (var k = 0; k < keys.length; k++) {
            var y = h - ((_motion3DData[localIndex][keys[k]] - minV) / (maxV - minV)) * h;
            ctx.fillStyle = colors[k];
            ctx.beginPath(); ctx.arc(curX, y, 3, 0, 2 * Math.PI); ctx.fill();
        }
    }

    function _motion3DDrawAllCharts(localIndex) {
        _drawSingleChart("chart-accel", ['ax', 'ay', 'az'], ['#ff5c5c', '#63d471', '#5da9ff'], _chartAccelMin, _chartAccelMax, localIndex);
        _drawSingleChart("chart-gyro", ['gx', 'gy', 'gz'], ['#ff5c5c', '#63d471', '#5da9ff'], _chartGyroMin, _chartGyroMax, localIndex);
    }

    function _motion3DDrawTrackingChart(globalLineIdx) {
        var canvas = document.getElementById("chart-tracking");
        if (!canvas || !_globalAllPositions) return;

        var dpr = window.devicePixelRatio || 1;
        canvas.width  = canvas.clientWidth * dpr; canvas.height = canvas.clientHeight * dpr;
        var ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
        var W = canvas.clientWidth, H = canvas.clientHeight;
        ctx.clearRect(0, 0, W, H);

        var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        // Viewport bounds calculation
        if (_isAllMode) {
            for(var i=1; i<_globalAllPositions.length; i+=100) {
                var p = _globalAllPositions[i];
                if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
                if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
            }
        } else {
            var sL = _binarySearchTime(_activeBufferStartTime);
            var eL = _binarySearchTime(_activeBufferEndTime);
            for (var i = sL; i <= eL; i++) {
                var p = _globalAllPositions[i];
                if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
                if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
            }
        }
        var spanX = maxX - minX || 1, spanY = maxY - minY || 1;
        var pad = 14;
        function toScreen(px, py) { return { x: pad + ((px - minX) / spanX) * (W - 2*pad), y: H - pad - ((py - minY) / spanY) * (H - 2*pad) }; }

        ctx.fillStyle = "rgba(16,20,24,0.92)"; ctx.fillRect(0, 0, W, H);
        
        ctx.beginPath(); ctx.strokeStyle = "#ffd16640"; ctx.lineWidth = 1.0;
        var step = _isAllMode ? Math.ceil(_globalAllPositions.length/1000) : 1;
        for (var i = 1; i < _globalAllPositions.length; i+=step) {
            var sc = toScreen(_globalAllPositions[i].x, _globalAllPositions[i].y);
            if (i === 1) ctx.moveTo(sc.x, sc.y); else ctx.lineTo(sc.x, sc.y);
        }
        ctx.stroke();

        if (globalLineIdx > 0 && globalLineIdx < _globalAllPositions.length) {
            var cp = toScreen(_globalAllPositions[globalLineIdx].x, _globalAllPositions[globalLineIdx].y);
            ctx.beginPath(); ctx.fillStyle = "#63d471"; ctx.arc(cp.x, cp.y, 4, 0, 2 * Math.PI); ctx.fill();
            
            var yaw = -_csvLines[globalLineIdx].split(",")[_csvHeaderIdx.yaw];
            var rad = yaw * Math.PI / 180;
            ctx.beginPath(); ctx.strokeStyle = "#63d471"; ctx.lineWidth = 1.5;
            ctx.moveTo(cp.x, cp.y); ctx.lineTo(cp.x + Math.sin(-rad)*10, cp.y - Math.cos(-rad)*10);
            ctx.stroke();
        }
    }

    // ----------------------------------------------------
    // INITIALIZATION & THREE.JS
    // ----------------------------------------------------
    function _motion3DInitScene() {
        if (_motion3DReady) return;

        var canvas = document.getElementById("motion3d-canvas");
        var view = document.getElementById("motion3d-view");

        _motion3DScene = new THREE.Scene(); _motion3DScene.background = new THREE.Color(0x0b0f12);
        _motion3DCamera = new THREE.PerspectiveCamera(45, view.clientWidth / Math.max(view.clientHeight, 1), 0.01, 1000);
        _motion3DCamera.up.set(0, 0, 1); _motion3DCamera.position.set(0, -18, 5); _motion3DCamera.lookAt(0, 0, 0);

        _motion3DRenderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
        _motion3DRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        _motion3DRenderer.setSize(view.clientWidth, view.clientHeight, false);

        _motion3DControls = new THREE.OrbitControls(_motion3DCamera, _motion3DRenderer.domElement);
        _motion3DControls.enableDamping = true; _motion3DControls.dampingFactor = 0.075;
        _motion3DControls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
        _motion3DControls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
        _motion3DControls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
        
        var ambient = new THREE.AmbientLight(0xffffff, 0.72); _motion3DScene.add(ambient);
        var light = new THREE.DirectionalLight(0xffffff, 0.9); light.position.set(5, -6, 10); _motion3DScene.add(light);
        var ground = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), new THREE.MeshPhongMaterial({ color: 0x20262b, side: THREE.DoubleSide }));
        ground.position.z = -0.02; _motion3DScene.add(ground);
        var grid = new THREE.GridHelper(200, 100, 0x56616b, 0x2d353b);
        grid.rotation.x = Math.PI / 2; grid.position.z = 0; _motion3DScene.add(grid);
        
        _motion3DCar = new THREE.Group();
        var body = new THREE.Mesh(new THREE.BoxGeometry(2.2, 4.4, 0.9), new THREE.MeshPhongMaterial({ color: 0x3f8ecb, shininess: 80 }));
        body.position.z = 0.65; _motion3DCar.add(body);
        var cabin = new THREE.Mesh(new THREE.BoxGeometry(1.65, 2.0, 0.72), new THREE.MeshPhongMaterial({ color: 0x9ec4df, transparent: true, opacity: 0.78 }));
        cabin.position.set(0, -0.15, 1.35); _motion3DCar.add(cabin);
        _motion3DCar.add(new THREE.ArrowHelper(new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1.0), 2.4, 0xffa500, 0.45, 0.25));
        
        _motion3DAccelArrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, 1.8), 1, 0xffffff, 0.25, 0.15);
        _motion3DCar.add(_motion3DAccelArrow);
        _motion3DScene.add(_motion3DCar);

        window.addEventListener("resize", function() {
            if (!_motion3DRenderer || !_motion3DCamera) return;
            var w = view.clientWidth, h = Math.max(view.clientHeight, 1);
            _motion3DCamera.aspect = w / h; _motion3DCamera.updateProjectionMatrix();
            _motion3DRenderer.setSize(w, h, false);
        });

        _motion3DReady = true;
        _motion3DRenderLoop();
    }

    // ----------------------------------------------------
    // UI CONTROLS
    // ----------------------------------------------------
    function motion3DView(viewName) {
        if (!_motion3DCamera) return;
        var target = (_motion3DTracking && _motion3DCar) ? _motion3DCar.position.clone() : new THREE.Vector3(0, 0, 0);
        
        if (viewName === 'fit') {
            _motion3DCamera.position.set(target.x, target.y - 40, target.z + 40);
            _motion3DCamera.up.set(0, 0, 1);
        } else if (viewName === 'rear') {
            _motion3DCamera.position.set(target.x, target.y - 18, target.z + 5);
            _motion3DCamera.up.set(0, 0, 1);
        } else if (viewName === 'front') {
            _motion3DCamera.position.set(target.x, target.y + 18, target.z + 5);
            _motion3DCamera.up.set(0, 0, 1);
        } else if (viewName === 'top') {
            _motion3DCamera.position.set(target.x, target.y, target.z + 20);
            _motion3DCamera.up.set(0, 1, 0);
        } else if (viewName === 'side') {
            _motion3DCamera.position.set(target.x + 18, target.y, target.z + 5);
            _motion3DCamera.up.set(0, 0, 1);
        }
        if (_motion3DControls) { _motion3DControls.target.copy(target); _motion3DControls.update(); }
    }

    function motion3DToggleTracking() {
        _motion3DTracking = !_motion3DTracking;
        var btn = document.getElementById('btn-tracking');
        if (btn) {
            btn.style.background = _motion3DTracking ? '#2a5a3a' : '#1a3a2a';
            btn.style.borderColor = _motion3DTracking ? '#6abf8a' : '#4a8f5a';
            btn.textContent = _motion3DTracking ? '\uD83C\uDFAF Tracking: Bật' : '\uD83C\uDFAF Tracking tâm xe';
        }
        if (_motion3DTracking && _motion3DControls && _motion3DCar) {
            var offset = new THREE.Vector3().subVectors(_motion3DCamera.position, _motion3DControls.target);
            _motion3DControls.target.copy(_motion3DCar.position);
            _motion3DCamera.position.copy(_motion3DCar.position).add(offset);
        }
    }

    function motion3DToggleShowEvent() {
        _motion3DShowRealtimeEvents = !_motion3DShowRealtimeEvents;
        var btn = document.getElementById('btn-show-event');
        if (btn) {
            btn.style.background = _motion3DShowRealtimeEvents ? '#8e44ad' : '#2c3e50';
            btn.style.borderColor = _motion3DShowRealtimeEvents ? '#9b59b6' : '#34495e';
            btn.textContent = _motion3DShowRealtimeEvents ? '🔔 Báo sự kiện: Bật' : '🔕 Báo sự kiện: Tắt';
        }
        if (!_motion3DShowRealtimeEvents) document.getElementById('m3d-realtime-event-box').style.display = 'none';
        _motion3DLastEventShown = "";
    }

    function motion3DSpeedChanged(value) {
        var speed = Number(value); if (!Number.isFinite(speed)) speed = 1.0;
        _motion3DPlaybackSpeed = Math.max(0.25, Math.min(4.0, speed));
        _motion3DSetText("motion3d-speed-value", _motion3DPlaybackSpeed.toFixed(2) + "×");
    }

    function motion3DPlay() {
        var maxT = _isAllMode ? _globalEndTime : _activeBufferEndTime;
        if (_simT >= maxT) _simT = _isAllMode ? _globalStartTime : _activeBufferStartTime;
        _motion3DPlaying = true;
        _motion3DLastTick = performance.now();
    }
    function motion3DPause() { _motion3DPlaying = false; }
    function motion3DReset() {
        _motion3DPlaying = false;
        _simT = _isAllMode ? _globalStartTime : _activeBufferStartTime;
        _motion3DData = _loadBuffer(_simT, _bufferDuration);
        _recalcChartBounds();
        _motion3DFilteredAccelInit = false; _motion3DBodyInit = false;
        _motion3DApplySample(_motion3DData[0], 0);
        motion3DView("rear");
    }

    function motion3DSliderChanged(value) {
        _motion3DPlaying = false;
        var startT = _isAllMode ? _globalStartTime : _activeBufferStartTime;
        _simT = startT + parseFloat(value);
        _motion3DData = _loadBuffer(_simT, _bufferDuration);
        _recalcChartBounds();
        _motion3DFilteredAccelInit = false; _motion3DBodyInit = false;
        _motion3DApplySample(_motion3DData[0], 0);
        _motion3DDrawAllCharts(0);
        _motion3DDrawTrackingChart(_motion3DData[0].lineIdx);
    }

    // ----------------------------------------------------
    // NEXT / PREVIOUS EVENT NAVIGATION
    // ----------------------------------------------------
    function motion3DPrevEvent() {
        if (!_motion3DEvents || _motion3DEvents.length === 0) return;
        var sorted = _motion3DEvents.slice().sort(function(a,b){ return a.start_time - b.start_time; });
        if (_isAllMode) {
            open3DSimulator(sorted[0].event_id);
            return;
        }
        var idx = sorted.findIndex(function(e){ return e.event_id == _currentEventId; });
        if (idx > 0) open3DSimulator(sorted[idx - 1].event_id);
    }

    function motion3DNextEvent() {
        if (!_motion3DEvents || _motion3DEvents.length === 0) return;
        var sorted = _motion3DEvents.slice().sort(function(a,b){ return a.start_time - b.start_time; });
        if (_isAllMode) {
            open3DSimulator(sorted[0].event_id);
            return;
        }
        var idx = sorted.findIndex(function(e){ return e.event_id == _currentEventId; });
        if (idx !== -1 && idx < sorted.length - 1) open3DSimulator(sorted[idx + 1].event_id);
    }

    // ----------------------------------------------------
    // LAUNCH SIMULATOR
    // ----------------------------------------------------
    function close3DSimulator() {
        _motion3DPlaying = false;
        var overlay = document.getElementById("motion3d-overlay");
        if (overlay) overlay.style.display = "none";
    }

    function open3DSimulator(eid) {
        _isAllMode = (String(eid) === 'ALL');
        _currentEventId = _isAllMode ? null : eid;
        var ev = null;
        if (!_isAllMode) {
            ev = _motion3DEvents.find(e => e.event_id == eid);
            if (!ev) { alert("Không tìm thấy sự kiện #" + eid); return; }
        }

        var overlay = document.getElementById("motion3d-overlay");
        var loading = document.getElementById("motion3d-loading");
        overlay.style.display = "block"; loading.style.display = "block";
        document.getElementById('m3d-realtime-event-box').style.display = 'none';
        
        _motion3DPlaying = false; _motion3DFilteredAccelInit = false; _motion3DBodyInit = false;
        _motion3DTracking = true;
        var btnT = document.getElementById('btn-tracking');
        if (btnT) { btnT.style.background = '#2a5a3a'; btnT.style.borderColor = '#6abf8a'; btnT.textContent = '\uD83C\uDFAF Tracking: Bật'; }

        // Fetch CSV Only Once Globally!
        if (!_globalAllTimes) {
            fetch(_motion3DFeaturesFile + "?t=" + Date.now())
                .then(r => r.text())
                .then(csvText => {
                    _processGlobalCSV(csvText);
                    _motion3DInitScene();         // SỬA: Phải khởi tạo Scene (Không gian 3D) trước
                    _buildGlobalTrajectoryLine(); // SỬA: Sau đó mới vẽ quỹ đạo (add) vào Scene
                    _setupSimulation(ev);
                    loading.style.display = "none";
                })
                .catch(err => {
                    loading.textContent = "Lỗi tải dữ liệu: " + err.message;
                });
        } else {
            _setupSimulation(ev);
            loading.style.display = "none";
        }
    }

    function _setupSimulation(ev) {
        if (_isAllMode) {
            _simT = _globalStartTime;
            _activeBufferStartTime = _globalStartTime;
            _activeBufferEndTime = _globalEndTime;
            _motion3DSetText("m3d-event-id", "Toàn bộ");
            _motion3DSetText("m3d-event-type", "ALL");
            _motion3DSetText("m3d-time-range", _globalStartTime.toFixed(1) + " → " + _globalEndTime.toFixed(1) + " s");
        } else {
            _simT = ev.start_time;
            _activeBufferStartTime = ev.start_time;
            _activeBufferEndTime = ev.end_time;
            _motion3DSetText("m3d-event-id", "#" + ev.event_id);
            _motion3DSetText("m3d-event-type", ev.event_type);
            _motion3DSetText("m3d-time-range", ev.start_time.toFixed(1) + " → " + ev.end_time.toFixed(1) + " s");
        }
        
        _motion3DData = _loadBuffer(_simT, _bufferDuration);
        _recalcChartBounds();

        var slider = document.getElementById("motion3d-slider");
        slider.min = "0";
        slider.max = String(_isAllMode ? (_globalEndTime - _globalStartTime) : (_activeBufferEndTime - _activeBufferStartTime));
        slider.value = "0";

        _motion3DApplySample(_motion3DData[0], 0);
        _motion3DDrawAllCharts(0);
        _motion3DDrawTrackingChart(_motion3DData[0].lineIdx);
        motion3DView("rear");
    }

    document.addEventListener("keydown", function(e) {
        var overlay = document.getElementById("motion3d-overlay");
        if (e.key === "Escape" && overlay && overlay.style.display !== "none") close3DSimulator();
    });
    </script>
    """
    html = html.replace("__EVENT_META__", meta_js)
    html = html.replace("__FEATURES_FILE__", features_js)
    m.get_root().html.add_child(folium.Element(html))


# ============================================================
# CREATE MAP
# ============================================================

def create_map(folder, events_df, gps_df, features_filename=None, output_filename="driving_events_map.html"):
    """features_filename / output_filename: cho phep noi goi (vd Step 4 khi
    mo phong 3D tu #ID cua Step 7) chi dinh ro file CSV dac trung + ten
    file HTML xuat ra khac voi mac dinh cua Step 9 (merged_motion_features.csv
    / driving_events_map.html), tranh ghi de len ket qua cua Step 8."""
    center_lat = float(gps_df["lat"].mean())
    center_lon = float(gps_df["lon"].mean())

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=DEFAULT_ZOOM,
        min_zoom=1,
        max_zoom=MAP_MAX_ZOOM,
        control_scale=True,
        tiles=None
    )

    folium.TileLayer(tiles="OpenStreetMap", name="OpenStreetMap", control=True).add_to(m)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
        control=True
    ).add_to(m)

    track_group = folium.FeatureGroup(name="GPS Track", show=True)
    coordinates = list(zip(gps_df["lat"], gps_df["lon"]))
    if len(coordinates) >= 2:
        folium.PolyLine(
            coordinates, color="#555555", weight=3, opacity=0.7, tooltip="GPS Track"
        ).add_to(track_group)
    track_group.add_to(m)

    present_types = []
    canonical_order = []
    for _, type_list in SPEED_GROUP_ORDER:
        canonical_order.extend(type_list)
    canonical_order.append("UNCLASSIFIED")
    
    all_types_in_data = events_df["event_type"].dropna().unique().tolist()
    for et in canonical_order:
        if et in all_types_in_data:
            present_types.append(et)
    for et in all_types_in_data:
        if et not in present_types:
            present_types.append(et)

    groups   = {}
    clusters = {}

    for et in present_types:
        event_name = EVENT_NAMES.get(et, et)
        groups[et] = folium.FeatureGroup(name=event_name, show=True)
        clusters[et] = MarkerCluster(
            overlay=False, control=False,
            options={
                "spiderfyOnMaxZoom": True, "showCoverageOnHover": False,
                "zoomToBoundsOnClick": True, "disableClusteringAtZoom": 20, "maxClusterRadius": 35
            }
        )
        groups[et].add_to(m)
        clusters[et].add_to(groups[et])

    valid = events_df.copy()
    valid["lat"] = pd.to_numeric(valid["lat"], errors="coerce")
    valid["lon"] = pd.to_numeric(valid["lon"], errors="coerce")
    valid = valid.dropna(subset=["lat", "lon"])

    feat_sorted = gps_df.sort_values("time").copy()
    feat_sorted["lat"] = pd.to_numeric(feat_sorted["lat"], errors="coerce")
    feat_sorted["lon"] = pd.to_numeric(feat_sorted["lon"], errors="coerce")
    feat_sorted = feat_sorted.dropna(subset=["lat", "lon"])

    for _, row in valid.iterrows():
        et = str(row["event_type"])
        if et not in groups: continue

        color     = EVENT_COLORS.get(et, "#7F8C8D")
        icon_name = EVENT_ICONS.get(et, "question-sign")
        name      = EVENT_NAMES.get(et, et)
        lat = float(row["lat"])
        lon = float(row["lon"])
        popup_html = make_event_popup(row)
        
        try: eid = str(int(row["event_id"]))
        except: eid = str(row["event_id"])

        if et != "STOP_RED_LIGHT":
            try:
                t_start = float(row["start_time"])
                t_end   = float(row["end_time"])
                seg = feat_sorted[(feat_sorted["time"] >= t_start) & (feat_sorted["time"] <= t_end)].copy()
                route_coords = list(zip(seg["lat"], seg["lon"]))

                if len(route_coords) >= 2:
                    folium.PolyLine(
                        route_coords, color=color, weight=5, opacity=0.85,
                        tooltip=f"Route Event #{eid} — {name}", dash_array=None
                    ).add_to(groups[et])
            except Exception:
                pass

        event_circle = folium.CircleMarker(
            location=[lat, lon], radius=9, color=color, weight=3, fill=True,
            fill_color=color, fill_opacity=0.85, tooltip=f"#{eid} {name}",
            popup=folium.Popup(popup_html, max_width=380)
        )
        event_circle.options["eventId"] = eid
        event_circle.add_to(groups[et])

        event_marker = folium.Marker(
            location=[lat, lon], tooltip=f"Event #{eid} — {name}",
            popup=folium.Popup(popup_html, max_width=380),
            icon=folium.Icon(color="gray", icon=icon_name, prefix="glyphicon")
        )
        event_marker.options["eventId"] = eid
        event_marker.add_to(clusters[et])

        marker_id_js = (
            "<script>\n"
            "document.addEventListener(\"DOMContentLoaded\", function() {\n"
            f"    var marker = {event_marker.get_name()};\n"
            f"    marker.options.eventId = \"{eid}\";\n"
            f"    marker._eventId = \"{eid}\";\n"
            "});\n"
            "</script>"
        )
        m.get_root().html.add_child(folium.Element(marker_id_js))

    if len(coordinates) >= 2:
        folium.Marker(
            location=coordinates[0], tooltip="▶ Bắt đầu hành trình",
            icon=folium.Icon(color="green", icon="play", prefix="glyphicon")
        ).add_to(m)
        folium.Marker(
            location=coordinates[-1], tooltip="■ Kết thúc hành trình",
            icon=folium.Icon(color="black", icon="flag", prefix="glyphicon")
        ).add_to(m)

    map_name_for_js = m.get_name()
    sidebar_html, sidebar_js = build_sidebar_html(events_df, present_types, valid, map_name_for_js)
    m.get_root().html.add_child(folium.Element(sidebar_js))
    m.get_root().html.add_child(folium.Element(sidebar_html))

    if features_filename is None:
        features_filename = os.path.basename(find_features_csv(folder))
    add_3d_simulator(m, events_df, features_filename)

    Fullscreen(position="topleft", title="Toàn màn hình", title_cancel="Thoát toàn màn hình", force_separate_button=True).add_to(m)
    MeasureControl(position="topleft", primary_length_unit="meters", secondary_length_unit="kilometers", primary_area_unit="sqmeters").add_to(m)
    MiniMap(toggle_display=True, minimized=True).add_to(m)
    folium.LayerControl(collapsed=False, position="topleft").add_to(m)

    if groups:
        add_layer_controls(m, groups, track_group)

    map_name = m.get_name()
    m.get_root().html.add_child(folium.Element(f"""
    <script>
    document.addEventListener("DOMContentLoaded", function() {{
        var map = {map_name};
        map.scrollWheelZoom.enable();
        map.doubleClickZoom.enable();
        map.touchZoom.enable();
        map.boxZoom.enable();
        map.keyboard.enable();
        map.setMaxZoom(24);
    }});
    </script>
    """))

    if coordinates:
        min_lat, max_lat = min(p[0] for p in coordinates), max(p[0] for p in coordinates)
        min_lon, max_lon = min(p[1] for p in coordinates), max(p[1] for p in coordinates)
        m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=[20, 20])

    # --------------------------------------------------------
    # LIVE-RELOAD WATCHER (THAY DOI DUY NHAT SO VOI BAN GOC)
    # Tu HEAD-poll chinh trang nay; khi file HTML duoc ghi lai (vd nguoi
    # dung bam "Tao Ban Do" lan nua voi du lieu moi), header Last-Modified
    # tra ve se khac gia tri ban dau -> tu dong location.reload(), khong
    # can nguoi dung F5 thu cong hay mo tab moi.
    # --------------------------------------------------------
    m.get_root().html.add_child(folium.Element("""
    <script>
    (function () {
        var _liveReloadLastModified = null;
        function _liveReloadCheck() {
            fetch(window.location.href, { method: "HEAD", cache: "no-store" })
                .then(function (resp) {
                    var lm = resp.headers.get("Last-Modified") || resp.headers.get("ETag");
                    if (_liveReloadLastModified === null) {
                        _liveReloadLastModified = lm;
                    } else if (lm && lm !== _liveReloadLastModified) {
                        window.location.reload();
                    }
                })
                .catch(function () {});
        }
        setInterval(_liveReloadCheck, 1500);
    })();
    </script>
    """))

    # --------------------------------------------------------
    # JUMP-TO-RANGE TU URL QUERY (vd ?range=12.5,45.8&label=HARD_BRAKE
    # hoac ?range=ALL) - cho phep Step 4 (Ban Do) hoac bat ky noi nao khac
    # mo trang nay voi 1 khoang timestamp/ #ID tuy y (khong nhat thiet
    # phai ton tai san trong detected_driving_events_unified.csv) va tu
    # dong mo mo phong 3D dung doan do ngay khi trang tai xong, bang cach
    # nhung 1 "su kien tam" vao _motion3DEvents roi goi lai chinh ham
    # open3DSimulator() co san - khong thay doi logic 3D goc.
    # --------------------------------------------------------
    m.get_root().html.add_child(folium.Element("""
    <script>
    function open3DSimulatorRange(t0, t1, label) {
        var syntheticId = "__custom_range__";
        var ev = {
            event_id: syntheticId, event_type: label || "Đoạn đang chọn", direction: "",
            start_time: t0, end_time: t1, speed_start: 0, speed_end: 0,
            speed_change: 0, lat: 0, lon: 0
        };
        var idx = _motion3DEvents.findIndex(function (e) { return e.event_id === syntheticId; });
        if (idx >= 0) { _motion3DEvents[idx] = ev; } else { _motion3DEvents.push(ev); }
        open3DSimulator(syntheticId);
    }

    document.addEventListener("DOMContentLoaded", function () {
        var params = new URLSearchParams(window.location.search);
        var rangeParam = params.get("range");
        if (!rangeParam) return;
        var labelParam = params.get("label") || "";

        setTimeout(function () {
            if (rangeParam === "ALL") {
                open3DSimulator("ALL");
            } else {
                var parts = rangeParam.split(",");
                var t0 = parseFloat(parts[0]), t1 = parseFloat(parts[1]);
                if (!isNaN(t0) && !isNaN(t1)) {
                    open3DSimulatorRange(t0, t1, labelParam);
                }
            }
        }, 600);
    });
    </script>
    """))

    output_path = os.path.join(folder, output_filename)
    m.save(output_path)
    return output_path


# ============================================================
# LOCAL HTTP SERVER
# ============================================================

def open_map_local_server(html_path, auto_open=True):
    """auto_open=False: chi khoi tao server + tra ve URL, KHONG tu mo
    trinh duyet (dung khi noi goi - vd Step 4 - muon tu mo 1 URL khac cu
    the hon, nhu kem query ?range=..., tranh mo du 2 tab cung luc)."""
    directory = os.path.dirname(html_path)
    filename  = os.path.basename(html_path)

    class SilentHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = None
    for port in range(8000, 8100):
        try:
            server = socketserver.TCPServer(("127.0.0.1", port), SilentHandler)
            break
        except OSError:
            continue

    if server is None:
        raise RuntimeError("Không tìm được port trống từ 8000 đến 8099.")

    os.chdir(directory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/{filename}"
    if auto_open:
        webbrowser.open(url)
    return server, url


# ============================================================
# GUI
# ============================================================

class Step9MapVisualizer(ttk.Frame):
    """Giu nguyen 100% logic/phong cach giao dien cua VisualizerApp goc
    (event_visualizer_3d_pro_gemini.py), chi dua vao 1 ttk.Frame de nhung
    thanh Module trong pipeline modular_app, va THAY DOI DUY NHAT: server
    local duoc tai su dung giua cac lan bam "Tạo Bản Đồ" (khong mo tab moi
    moi lan), tab dang mo se TU DONG RELOAD nho script nhung san trong
    HTML (xem create_map())."""

    def __init__(self, parent, module_config=None):
        super().__init__(parent)
        self.state = AppState()
        self.module_config = module_config or {}

        self.folder = None
        self._server = None
        self._server_url = None
        self._served_dir = None
        self._last_seen_dir = self.state.current_dir

        default_events_dir = os.path.join(self.state.current_dir or "", "detected_events_unified")
        if self.state.current_dir and os.path.isdir(default_events_dir):
            self.folder = default_events_dir

        self._build_ui()
        self.state.register_data_listener(self._on_data_changed)

    def _build_ui(self):
        ScienceInfoPanel(self, self.module_config).pack(fill=tk.X, padx=4, pady=(4, 0))

        top_bar = ttk.LabelFrame(self, text="Thư Mục detected_events_unified/ & Tạo Bản Đồ", padding=6)
        top_bar.pack(fill=tk.X, padx=4, pady=(4, 2))

        row = ttk.Frame(top_bar)
        row.pack(fill=tk.X)
        ttk.Label(row, text="Thư mục:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.folder_label = ttk.Label(
            row, text=self.folder or "Chưa chọn thư mục",
            foreground="#1e3d59", font=("Consolas", 9)
        )
        self.folder_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(row, text="Chọn...", command=self._select_folder).pack(side=tk.RIGHT, padx=2)

        row2 = ttk.Frame(top_bar)
        row2.pack(fill=tk.X, pady=(4, 0))
        self.create_btn = ttk.Button(
            row2, text="🗺️ Tạo Bản Đồ", style="Accent.TButton", command=self._start_create
        )
        self.create_btn.pack(side=tk.LEFT)

        ttk.Label(
            row2, text="Output -> driving_events_map.html (tự động reload tab đang mở nếu đang chạy)",
            foreground="#777777", font=("Segoe UI", 8, "italic")
        ).pack(side=tk.LEFT, padx=12)

        self.progress = ttk.Progressbar(top_bar, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(4, 2))

        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(top_bar, textvariable=self.status_var, foreground="#555555", font=("Segoe UI", 8)).pack(
            fill=tk.X, anchor=tk.W
        )

        result_frame = ttk.LabelFrame(self, text="Nhật Ký Tạo Bản Đồ", padding=4)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        scrollbar = ttk.Scrollbar(result_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.output_text = tk.Text(
            result_frame, height=15, font=("Consolas", 9), yscrollcommand=scrollbar.set,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="white", relief=tk.FLAT
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.output_text.yview)

    def _select_folder(self):
        folder = filedialog.askdirectory(
            title="Chọn thư mục detected_events_unified/ (chứa detected_driving_events_unified.csv)",
            initialdir=self.folder or self.state.current_dir or os.getcwd()
        )
        if folder:
            self.folder = folder
            self.folder_label.config(text=folder)
            self.status_var.set("Đã chọn thư mục.")

    def _on_data_changed(self):
        """Tu dong cap nhat thu muc detected_events_unified/ mac dinh ngay
        khi thu muc lam viec doi (Panel Nap Du Lieu Tho chon lai thu muc)."""
        current_dir = self.state.current_dir
        if not current_dir or current_dir == self._last_seen_dir:
            return
        self._last_seen_dir = current_dir

        events_dir = os.path.join(current_dir, "detected_events_unified")
        if os.path.isdir(events_dir):
            self.folder = events_dir
            self.folder_label.config(text=self.folder)
            self.status_var.set("Đã đổi thư mục làm việc — đường dẫn mặc định đã tự động cập nhật.")

    def _start_create(self):
        if not self.folder:
            messagebox.showwarning("Chưa chọn thư mục", "Vui lòng chọn thư mục detected_events_unified/.")
            return

        self.create_btn.config(state=tk.DISABLED)
        self.progress.start(10)
        self.output_text.delete("1.0", tk.END)
        self.status_var.set("Đang tạo bản đồ...")

        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _worker(self):
        try:
            self.after(0, lambda: self.status_var.set("Đang đọc CSV sự kiện..."))
            events_df, events_path = read_events_csv(self.folder)

            self.after(0, lambda: self.status_var.set("Đang đọc GPS track..."))
            gps_df, gps_path = read_gps_track(self.folder)

            self.after(0, lambda: self.status_var.set("Đang tạo bản đồ Folium..."))
            html_path = create_map(self.folder, events_df, gps_df)

            served_dir = os.path.dirname(html_path)

            # ----------------------------------------------------
            # THAY DOI DUY NHAT SO VOI BAN GOC: tai su dung server local
            # dang chay (neu co) thay vi luon mo 1 tab browser moi. Tab
            # dang mo se tu dong location.reload() nho script da nhung
            # trong HTML (create_map) ngay khi phat hien file vua duoc
            # ghi lai - chi chdir lai server khi doi sang thu muc khac.
            # ----------------------------------------------------
            if self._server is None:
                self.after(0, lambda: self.status_var.set("Đang mở bản đồ trong trình duyệt..."))
                server, url = open_map_local_server(html_path)
                self._server = server
                self._server_url = url
                self._served_dir = served_dir
                did_reload = False
            else:
                if served_dir != self._served_dir:
                    os.chdir(served_dir)
                    self._served_dir = served_dir
                url = self._server_url
                self.after(0, lambda: self.status_var.set("Website đang chạy sẽ tự động reload..."))
                did_reload = True

            self.after(0, lambda e=events_df, h=html_path, u=url, r=did_reload: self._done(e, h, u, r))
        except Exception as e:
            err = str(e) + "\n\n" + traceback.format_exc()
            self.after(0, lambda m=err: self._error(m))

    def _done(self, events_df, html_path, url, did_reload=False):
        self.progress.stop()
        self.create_btn.config(state=tk.NORMAL)
        self.status_var.set(
            "✅  Bản đồ đã được cập nhật — tab đang mở sẽ tự động reload."
            if did_reload else
            "✅  Bản đồ đã được tạo và mở trong trình duyệt."
        )

        counts = events_df["event_type"].value_counts()
        total  = len(events_df)
        lines = [
            "=" * 62, "  BẢN ĐỒ ĐÃ ĐƯỢC TẠO THÀNH CÔNG", "=" * 62, "",
            f"  File HTML : {os.path.basename(html_path)}", f"  URL       : {url}",
            f"  Chế độ    : {'Tự động reload tab đang mở' if did_reload else 'Mở tab trình duyệt mới'}",
            "",
            f"  Tổng sự kiện: {total}", "", "  THỐNG KÊ NHÃN:",
        ]
        for label, cnt in counts.items():
            lines.append(f"    {label:<32} {cnt}")
        lines.append("=" * 62)

        self.output_text.delete("1.0", tk.END)
        self.output_text.insert(tk.END, "\n".join(lines))

    def _error(self, message):
        self.progress.stop()
        self.create_btn.config(state=tk.NORMAL)
        self.status_var.set("❌  Có lỗi trong quá trình tạo bản đồ.")
        self.output_text.insert(tk.END, message)
        messagebox.showerror("Lỗi", message[:500])