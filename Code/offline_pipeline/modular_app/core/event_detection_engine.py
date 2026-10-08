# -*- coding: utf-8 -*-
"""
Module: core/event_detection_engine.py
Chuc nang: Logic phat hien su kien lai xe (10/20/40/80 km/h + 8 nhan
           Residual dat ten tieng Anh theo dinh nghia - xem
           DEFAULT_RESIDUAL_LABELS), port tu
           proc_data/preprocessing/event_detector_unified_claude_advanced.py
           nhung tham so hoa toan bo nguong (threshold) qua cac dict cfg
           thay vi hang so module-level co dinh, de steps/step8 co the
           chinh sua tu GUI va chay lai voi gia tri moi.
Co so khoa hoc: Sliding window + rule-based threshold classification cho
           Human Activity Recognition tu cam bien IMU/GPS gan xe.
"""

import os
import glob
import copy
import operator as _op

import numpy as np
import pandas as pd


# ============================================================
# DEFAULT THRESHOLDS (gia tri goc tu file event_detector_unified_claude_advanced.py)
# ============================================================

DEFAULT_GENERAL = {
    "GPS_SPEED_MEDIAN_WINDOW": 11,
    "GPS_ZERO_SPEED": 0.5,
    "GPS_IMU_TOLERANCE": 0.15,
    "WINDOW_SECONDS": 2.0,
    "STEP_SECONDS": 0.1,
    "ACCEL_SMOOTH_WINDOW": 7,
    "EVENT_MERGE_GAP": 0.5,
}

DEFAULT_G10 = {
    "SPEED_MAX": 10.0,
    "SPEED_TOLERANCE": 0.5,
    "STOP_SPEED_THRESHOLD": 2.0,
    "STOP_ACCEL_NORM_THRESHOLD": 0.8,
    "STOP_MIN_DURATION": 3.0,
    "STOP_MAX_GYRO": 0.35,
    "BRAKE_SPEED_DROP": 2.0,
    "BRAKE_FINAL_SPEED": 1.0,
    "BRAKE_ACCEL": -1.2,
    "BRAKE_JERK": -3.0,
    "ACCEL_START_SPEED_MAX": 7.0,
    "ACCEL_FINAL_SPEED_MIN": 9.0,
    "ACCEL_SPEED_RISE": 2.0,
    "ACCEL_ACCEL": 1.2,
    "ACCEL_JERK": 3.0,
    "STEERING_GYRO": 0.35,
    "STEERING_MIN_DURATION": 0.3,
    "NORMAL_SPEED_CHANGE": 1.0,
    "NORMAL_ACCEL": 1.0,
}

DEFAULT_G20 = {
    "SPEED_MIN": 10.0,
    "SPEED_MAX": 30.0,
    "BRAKE_SPEED_DROP": 3.5,
    "BRAKE_ACCEL": -1.2,
    "BRAKE_JERK": -3.0,
    "ACCEL_SPEED_RISE": 3.5,
    "ACCEL_ACCEL": 1.2,
    "ACCEL_JERK": 3.0,
    "STEERING_GYRO": 0.35,
    "TURN_MIN_DUR": 0.3,
    "NORMAL_SPEED_CHANGE": 2.0,
    "NORMAL_ACCEL": 1.0,
}

DEFAULT_G40 = {
    "SPEED_MIN": 30.0,
    "SPEED_MAX": 50.0,
    "BRAKE_SPEED_DROP": 5.0,
    "BRAKE_ACCEL": -1.2,
    "BRAKE_JERK": -3.0,
    "ACCEL_SPEED_RISE": 5.0,
    "ACCEL_ACCEL": 1.2,
    "ACCEL_JERK": 3.0,
    "STEERING_GYRO": 0.35,
    "TURN_MIN_DUR": 0.3,
    "NORMAL_SPEED_CHANGE": 3.0,
    "NORMAL_ACCEL": 1.0,
}

DEFAULT_G80 = {
    "SPEED_MIN": 50.0,
    "SPEED_MAX": 80.0,
    "BRAKE_SPEED_DROP": 5.0,
    "BRAKE_ACCEL": -1.2,
    "BRAKE_JERK": -3.0,
    "ACCEL_SPEED_RISE": 5.0,
    "ACCEL_ACCEL": 1.2,
    "ACCEL_JERK": 3.0,
    "STEERING_GYRO": 0.32,
    "TURN_MIN_DUR": 0.3,
    "NORMAL_SPEED_CHANGE": 3.0,
    "NORMAL_ACCEL": 1.0,
}

RESIDUAL_GROUP_MOTION  = "RESIDUAL_MOTION"
RESIDUAL_GROUP_QUALITY = "DATA_QUALITY"

LABEL_SOURCE_PRIMARY  = "HEURISTIC_PRIMARY"
LABEL_SOURCE_RESIDUAL = "RESIDUAL_HEURISTIC"
LABEL_SOURCE_QUALITY  = "DATA_QUALITY"

# Da bo nhan "Unknown09 / Uncovered 10.5-30 km/h Motion" (xem
# docs/hard_braking_scientific_definition.md): G20 (DEFAULT_G20) da bao
# phu dung [SPEED_MIN=10.0, SPEED_MAX=30.0] km/h - khong co "vung chua
# bao phu" thuc su tai day. Nhan nay la tan tich tu phien ban code cu
# (trung voi loi dead-code da sua o _speed_zone_label()) va trung lap ve
# ban chat voi MODERATE_DECEL/MODERATE_ACCEL (cung la "bien dong nhe chua
# du nguong Primary", khong gioi han theo dai toc do, ap dung DONG NHAT
# o moi ranh gioi toc do - thay vi vi them 1 nhan rieng CHI cho ranh gioi
# 10.5-30, trong khi cac ranh gioi 30/50/80 khong co nhan tuong tu).
DEFAULT_RESIDUAL_LABELS = {
    "MODERATE_DECEL": {
        "name": "Moderate Deceleration",
        "group": RESIDUAL_GROUP_MOTION,
        "conditions": {
            "speed_change": {"operator": "<=", "value": -2.0},
            "min_accel":    {"operator": "<=", "value": -0.7},
            "min_jerk":     {"operator": "<=", "value": -1.5},
        },
    },
    "MODERATE_ACCEL": {
        "name": "Moderate Acceleration",
        "group": RESIDUAL_GROUP_MOTION,
        "conditions": {
            "speed_change": {"operator": ">=", "value": 2.0},
            "max_accel":    {"operator": ">=", "value": 0.7},
            "max_jerk":     {"operator": ">=", "value": 1.5},
        },
    },
    "STEERING_ANOMALY": {
        "name": "Steering Non-Normal Motion",
        "group": RESIDUAL_GROUP_MOTION,
        "conditions": {
            "max_gyr_z":         {"operator": ">=", "value": 0.35},
            "steering_duration": {"operator": ">=", "value": 0.3},
            "abs_speed_change":  {"operator": ">",  "value": 1.0},
        },
    },
    "MODERATE_BRAKE_STEER": {
        "name": "Moderate Braking + Steering",
        "group": RESIDUAL_GROUP_MOTION,
        "conditions": {
            "speed_change":      {"operator": "<=", "value": -2.0},
            "min_accel":         {"operator": "<=", "value": -0.7},
            "min_jerk":          {"operator": "<=", "value": -1.5},
            "max_gyr_z":         {"operator": ">=", "value": 0.35},
            "steering_duration": {"operator": ">=", "value": 0.3},
        },
    },
    "MODERATE_ACCEL_STEER": {
        "name": "Moderate Acceleration + Steering",
        "group": RESIDUAL_GROUP_MOTION,
        "conditions": {
            "speed_change":      {"operator": ">=", "value": 2.0},
            "max_accel":         {"operator": ">=", "value": 0.7},
            "max_jerk":          {"operator": ">=", "value": 1.5},
            "max_gyr_z":         {"operator": ">=", "value": 0.35},
            "steering_duration": {"operator": ">=", "value": 0.3},
        },
    },
    "JERK_TRANSIENT": {
        "name": "Jerk-Dominant Transient",
        "group": RESIDUAL_GROUP_MOTION,
        "conditions": {
            "max_jerk":    {"operator": ">=", "value": 4.0},
            "OR_min_jerk": {"operator": "<=", "value": -4.0},
        },
    },
    "SPEED_BOUNDARY_TRANSITION": {
        "name": "Speed Boundary Transition",
        "group": RESIDUAL_GROUP_MOTION,
        "transitions": {
            "low_to_mid":        {"speed_start_max": 10.5, "speed_end_min": 30.0},
            "mid_to_low":        {"speed_start_min": 30.0, "speed_end_max": 10.5},
            "cross_80_boundary": {"speed_start_max": 80.0, "speed_end_min_exclusive": 80.0},
        },
        "conditions": {
            "minimum_abs_speed_change": {"operator": ">=", "value": 2.0},
        },
    },
    "IMU_GPS_MISMATCH": {
        "name": "IMU-GPS Inconsistency",
        "group": RESIDUAL_GROUP_QUALITY,
        "conditions": {
            "imu_gps_accel_difference": {"operator": ">=", "value": 1.0},
        },
    },
}

RESIDUAL_PRIORITY = [
    "MODERATE_BRAKE_STEER", "MODERATE_ACCEL_STEER", "MODERATE_DECEL", "MODERATE_ACCEL",
    "STEERING_ANOMALY", "JERK_TRANSIENT", "SPEED_BOUNDARY_TRANSITION",
    "IMU_GPS_MISMATCH",
]

DEFAULT_UNKNOWN_EVENT_MERGE_GAP = 0.5

PRIMARY_LABEL_GROUP = {
    "10kmh": "G10", "20kmh": "G20", "40kmh": "G40", "80kmh": "G80",
}

_OPERATORS = {
    "<=": _op.le, ">=": _op.ge, "<": _op.lt, ">": _op.gt,
    "==": _op.eq, "!=": _op.ne,
}


def default_cfg_bundle():
    """Tra ve 1 bo cfg day du (deep copy) de GUI chinh sua an toan ma
    khong anh huong cac hang so DEFAULT_* o tren."""
    return {
        "general":  copy.deepcopy(DEFAULT_GENERAL),
        "g10":      copy.deepcopy(DEFAULT_G10),
        "g20":      copy.deepcopy(DEFAULT_G20),
        "g40":      copy.deepcopy(DEFAULT_G40),
        "g80":      copy.deepcopy(DEFAULT_G80),
        "residual_labels":  copy.deepcopy(DEFAULT_RESIDUAL_LABELS),
        "residual_priority": list(RESIDUAL_PRIORITY),
        "residual_merge_gap": DEFAULT_UNKNOWN_EVENT_MERGE_GAP,
    }


# ============================================================
# FILE DISCOVERY / READ
# ============================================================

def find_input_files(folder):
    txt_files = sorted(glob.glob(os.path.join(folder, "*.txt")))

    if len(txt_files) < 2:
        raise FileNotFoundError(f"Không tìm thấy đủ 2 file .txt trong thư mục:\n{folder}")

    imu_file = None
    gps_file = None

    for path in txt_files:
        name = os.path.basename(path).lower()
        if imu_file is None and "raw_accelerometers" in name:
            imu_file = path
        if gps_file is None and "raw_gps" in name:
            gps_file = path

    for path in txt_files:
        name = os.path.basename(path).lower()
        if imu_file is None and "imu" in name:
            imu_file = path
        if gps_file is None and "gps" in name:
            gps_file = path

    for path in txt_files:
        if imu_file is not None and gps_file is not None:
            break
        try:
            sample = pd.read_csv(path, sep=r"\s+", header=None, nrows=5, engine="python")
            ncols = sample.shape[1]
            if imu_file is None and ncols == 14:
                imu_file = path
            elif gps_file is None and ncols == 12:
                gps_file = path
        except Exception:
            continue

    if imu_file is None:
        raise FileNotFoundError(f"Không xác định được file IMU .txt trong: {folder}")
    if gps_file is None:
        raise FileNotFoundError(f"Không xác định được file GPS .txt trong: {folder}")
    if os.path.abspath(imu_file) == os.path.abspath(gps_file):
        raise RuntimeError(f"File IMU và GPS đang bị nhận diện là cùng một file.\n{imu_file}")

    return imu_file, gps_file


def read_imu(path):
    df = pd.read_csv(path, sep=r"\s+", header=None, engine="python")
    if df.shape[1] < 14:
        raise ValueError(f"File IMU có {df.shape[1]} cột, cần ít nhất 14:\n{path}")

    df = df.iloc[:, :14].copy()
    df.columns = [
        "time", "acc_x", "acc_y", "acc_z", "yaw", "roll", "pitch",
        "gyr_x", "gyr_y", "gyr_z", "quat_w", "quat_x", "quat_y", "quat_z",
    ]
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["time", "acc_x", "acc_y", "acc_z", "gyr_x", "gyr_y", "gyr_z"]).copy()
    df = df.sort_values("time").drop_duplicates(subset=["time"], keep="first").reset_index(drop=True)

    if len(df) == 0:
        raise ValueError("File IMU không có dữ liệu hợp lệ.")
    return df


def read_gps(path):
    df = pd.read_csv(path, sep=r"\s+", header=None, engine="python")
    if df.shape[1] < 12:
        raise ValueError(f"File GPS có {df.shape[1]} cột, cần ít nhất 12:\n{path}")

    df = df.iloc[:, :12].copy()
    df.columns = [
        "t_now", "utc_time", "lat", "lon", "hdop", "alt", "fix",
        "cog", "speed_kmh", "speed_knots", "date", "satellites",
    ]
    numeric_cols = ["t_now", "lat", "lon", "hdop", "alt", "fix", "cog",
                     "speed_kmh", "speed_knots", "satellites"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["t_now"]).copy()
    df = df.sort_values("t_now").drop_duplicates(subset=["t_now"], keep="first").reset_index(drop=True)

    if len(df) == 0:
        raise ValueError("File GPS không có dữ liệu hợp lệ.")
    return df


def clean_gps_speed(gps, cfg_general):
    df = gps.copy()
    raw_speed = pd.to_numeric(df["speed_kmh"], errors="coerce").clip(lower=0)
    df["speed_raw"] = raw_speed

    df["speed_kmh"] = raw_speed.rolling(
        window=int(cfg_general["GPS_SPEED_MEDIAN_WINDOW"]), center=True, min_periods=1
    ).median()

    df.loc[df["speed_kmh"] <= cfg_general["GPS_ZERO_SPEED"], "speed_kmh"] = 0.0
    return df


def merge_imu_gps(imu, gps, cfg_general):
    imu_df = imu.sort_values("time").copy()
    gps_df = gps.sort_values("t_now").copy()

    merged = pd.merge_asof(
        imu_df, gps_df, left_on="time", right_on="t_now",
        direction="nearest", tolerance=cfg_general["GPS_IMU_TOLERANCE"],
    )

    for col in ["speed_kmh", "lat", "lon"]:
        merged[col] = pd.to_numeric(merged[col], errors="coerce")
        merged[col] = merged[col].interpolate(limit_direction="both")

    merged["speed_kmh"] = merged["speed_kmh"].fillna(0)
    merged["lat"] = pd.to_numeric(merged["lat"], errors="coerce")
    merged["lon"] = pd.to_numeric(merged["lon"], errors="coerce")
    return merged


def calculate_features(df, cfg_general, base_steering_gyro):
    data = df.copy()

    data["dt"] = data["time"].diff().replace(0, np.nan)
    data["dt"] = data["dt"].fillna(data["dt"].median()).clip(lower=0.001)

    data["acc_norm"] = np.sqrt(data["acc_x"] ** 2 + data["acc_y"] ** 2 + data["acc_z"] ** 2)

    data["acc_y_smooth"] = data["acc_y"].rolling(
        window=int(cfg_general["ACCEL_SMOOTH_WINDOW"]), center=True, min_periods=1
    ).median()

    data["jerk"] = (data["acc_y_smooth"].diff() / data["dt"]).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0)

    data["speed_diff"] = data["speed_kmh"].diff().fillna(0)

    speed_ms = data["speed_kmh"] * 1000.0 / 3600.0
    data["gps_accel"] = (speed_ms.diff() / data["dt"]).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0)

    data["yaw_rate"] = data["gyr_z"]
    data["steering"] = data["gyr_z"].abs() >= base_steering_gyro
    data["steering_dir"] = np.where(
        data["gyr_z"] > base_steering_gyro, "LEFT",
        np.where(data["gyr_z"] < -base_steering_gyro, "RIGHT", "NONE"),
    )
    return data


def generate_windows(df, cfg_general):
    if len(df) < 2:
        return []

    time_values = df["time"].values
    start_time = float(time_values[0])
    final_time = float(time_values[-1])
    window_seconds = cfg_general["WINDOW_SECONDS"]
    step_seconds = cfg_general["STEP_SECONDS"]

    windows = []
    current_start = start_time

    while current_start + window_seconds <= final_time:
        current_end = current_start + window_seconds
        mask = (df["time"] >= current_start) & (df["time"] <= current_end)
        indices = np.where(mask.values)[0]
        if len(indices) >= 2:
            windows.append((int(indices[0]), int(indices[-1])))
        current_start += step_seconds

    return windows


def _event_to_record(df, event, event_id):
    seg = df.iloc[event["start_idx"]: event["end_idx"] + 1].copy()

    start_time = float(seg["time"].iloc[0])
    end_time = float(seg["time"].iloc[-1])
    duration = end_time - start_time

    speed_start = float(seg["speed_kmh"].iloc[0])
    speed_end = float(seg["speed_kmh"].iloc[-1])
    speed_min = float(seg["speed_kmh"].min())
    speed_max = float(seg["speed_kmh"].max())
    speed_change = speed_end - speed_start

    min_accel = float(seg["acc_y_smooth"].min())
    max_accel = float(seg["acc_y_smooth"].max())
    min_jerk = float(seg["jerk"].min())
    max_jerk = float(seg["jerk"].max())
    max_gyr_z = float(seg["gyr_z"].abs().max())

    imu_gps_diff = float(abs(
        float(seg["acc_y_smooth"].median()) - float(seg["gps_accel"].median())
    ))

    valid_pos = seg[seg["lat"].notna() & seg["lon"].notna()]
    if len(valid_pos) > 0:
        lat = float(valid_pos["lat"].iloc[0])
        lon = float(valid_pos["lon"].iloc[0])
    else:
        lat = np.nan
        lon = np.nan

    speed_group = event.get("speed_group", "")
    label_source = event.get("label_source", LABEL_SOURCE_PRIMARY)
    label_group = event.get("label_group", PRIMARY_LABEL_GROUP.get(speed_group, ""))

    return {
        "event_id": event_id, "event_type": event["event_type"],
        "label_source": label_source, "label_group": label_group,
        "direction": event["direction"], "speed_group": speed_group,
        "start_time": start_time, "end_time": end_time, "duration": duration,
        "speed_start": speed_start, "speed_end": speed_end, "speed_change": speed_change,
        "speed_min": speed_min, "speed_max": speed_max,
        "min_accel": min_accel, "max_accel": max_accel,
        "min_jerk": min_jerk, "max_jerk": max_jerk, "max_gyr_z": max_gyr_z,
        "imu_gps_accel_difference": imu_gps_diff,
        "lat": lat, "lon": lon, "samples": len(seg),
    }


def _merge_candidates(df, candidates, merge_gap):
    if not candidates:
        return []

    candidates = sorted(candidates, key=lambda x: (x["start_idx"], x["end_idx"]))
    merged = []
    current = None

    for c in candidates:
        if current is None:
            current = c.copy()
            continue

        same_type = (c["event_type"] == current["event_type"])
        time_gap = (
            float(df.iloc[c["start_idx"]]["time"]) - float(df.iloc[current["end_idx"]]["time"])
        )
        close = time_gap <= merge_gap
        overlap = c["start_idx"] <= current["end_idx"] + 1

        if same_type and (close or overlap):
            current["end_idx"] = max(current["end_idx"], c["end_idx"])
            if current["direction"] == "NONE" and c["direction"] != "NONE":
                current["direction"] = c["direction"]
        else:
            merged.append(current)
            current = c.copy()

    if current is not None:
        merged.append(current)
    return merged


# ============================================================
# DETECTOR NHOM 10 km/h
# ============================================================

def _detect_stop_events(df, cfg):
    condition = (
        (df["speed_kmh"] <= cfg["STOP_SPEED_THRESHOLD"])
        & (df["acc_norm"] <= cfg["STOP_ACCEL_NORM_THRESHOLD"])
        & (df["gyr_z"].abs() <= cfg["STOP_MAX_GYRO"])
    )

    events = []
    start_idx = None

    def _flush(end_idx):
        duration = float(df.iloc[end_idx]["time"]) - float(df.iloc[start_idx]["time"])
        if duration >= cfg["STOP_MIN_DURATION"]:
            events.append({
                "start_idx": start_idx, "end_idx": end_idx,
                "event_type": "STOP_RED_LIGHT", "direction": "NONE",
                "speed_group": "10kmh",
            })

    for i in range(len(df)):
        is_stop = bool(condition.iloc[i])
        if is_stop and start_idx is None:
            start_idx = i
        elif not is_stop and start_idx is not None:
            _flush(i - 1)
            start_idx = None

    if start_idx is not None:
        _flush(len(df) - 1)

    return events


def _classify_window_10(window_df, cfg):
    if len(window_df) < 2:
        return None

    speed_start = float(window_df["speed_kmh"].iloc[0])
    speed_end = float(window_df["speed_kmh"].iloc[-1])
    speed_change = speed_end - speed_start
    speed_min = float(window_df["speed_kmh"].min())

    min_accel = float(window_df["acc_y_smooth"].min())
    max_accel = float(window_df["acc_y_smooth"].max())
    min_jerk = float(window_df["jerk"].min())
    max_jerk = float(window_df["jerk"].max())

    low_speed_zone = (
        speed_start <= cfg["SPEED_MAX"] + cfg["SPEED_TOLERANCE"]
        or speed_end <= cfg["SPEED_MAX"] + cfg["SPEED_TOLERANCE"]
        or speed_min <= cfg["SPEED_MAX"]
    )
    if not low_speed_zone:
        return None

    steering_mask = window_df["gyr_z"].abs() >= cfg["STEERING_GYRO"]
    steering_dur = 0.0
    if steering_mask.any():
        rows = window_df[steering_mask]
        if len(rows) >= 2:
            steering_dur = float(rows["time"].iloc[-1]) - float(rows["time"].iloc[0])

    steering = steering_dur >= cfg["STEERING_MIN_DURATION"]

    direction = "NONE"
    if steering:
        mean_gyr = float(window_df.loc[steering_mask, "gyr_z"].mean())
        direction = "LEFT" if mean_gyr > 0 else "RIGHT" if mean_gyr < 0 else "TURN"

    hard_brake = (
        speed_change <= -cfg["BRAKE_SPEED_DROP"]
        and speed_end <= cfg["BRAKE_FINAL_SPEED"]
        and min_accel <= cfg["BRAKE_ACCEL"]
        and min_jerk <= cfg["BRAKE_JERK"]
    )

    sudden_accel = (
        speed_start <= cfg["ACCEL_START_SPEED_MAX"]
        and speed_end >= cfg["ACCEL_FINAL_SPEED_MIN"]
        and speed_change >= cfg["ACCEL_SPEED_RISE"]
        and max_accel >= cfg["ACCEL_ACCEL"]
        and max_jerk >= cfg["ACCEL_JERK"]
    )

    normal_motion = (
        abs(speed_change) <= cfg["NORMAL_SPEED_CHANGE"]
        and abs(min_accel) <= cfg["NORMAL_ACCEL"]
        and abs(max_accel) <= cfg["NORMAL_ACCEL"]
    )

    if hard_brake and steering:
        return ("HARD_BRAKE_STEERING", direction)
    if sudden_accel and steering:
        return ("SUDDEN_ACCEL_STEERING", direction)
    if hard_brake:
        return ("HARD_BRAKE", "NONE")
    if sudden_accel:
        return ("SUDDEN_ACCEL", "NONE")
    if normal_motion and steering:
        return ("NORMAL_STEERING", direction)
    if normal_motion:
        return ("NORMAL", "NONE")
    return None


def detect_10kmh(df, cfg_general, cfg_g10):
    windows = generate_windows(df, cfg_general)
    candidates = []

    for s_idx, e_idx in windows:
        w = df.iloc[s_idx: e_idx + 1]
        result = _classify_window_10(w, cfg_g10)
        if result is None:
            continue
        event_type, direction = result
        candidates.append({
            "start_idx": s_idx, "end_idx": e_idx, "event_type": event_type,
            "direction": direction, "speed_group": "10kmh",
        })

    stop_events = _detect_stop_events(df, cfg_g10)
    motion_merged = _merge_candidates(df, candidates, cfg_general["EVENT_MERGE_GAP"])

    all_events_raw = list(stop_events)

    for e in motion_merged:
        covered = False
        for stop in stop_events:
            ov_start = max(e["start_idx"], stop["start_idx"])
            ov_end = min(e["end_idx"], stop["end_idx"])
            if ov_start <= ov_end:
                ov_len = ov_end - ov_start + 1
                event_len = e["end_idx"] - e["start_idx"] + 1
                if ov_len >= 0.7 * event_len:
                    covered = True
                    break
        if not covered:
            all_events_raw.append(e)

    all_events_raw.sort(key=lambda x: x["start_idx"])
    return candidates, all_events_raw


# ============================================================
# DETECTOR NHOM 20 km/h (10-30 km/h)
# ============================================================

def _classify_window_20(window_df, cfg):
    if len(window_df) < 2:
        return None

    target_ratio = np.mean(
        (window_df["speed_kmh"] >= cfg["SPEED_MIN"]) & (window_df["speed_kmh"] <= cfg["SPEED_MAX"])
    )
    if target_ratio < 0.50:
        return None

    speed_start = float(window_df["speed_kmh"].iloc[0])
    speed_end = float(window_df["speed_kmh"].iloc[-1])
    speed_change = speed_end - speed_start

    min_accel = float(window_df["acc_y_smooth"].min())
    max_accel = float(window_df["acc_y_smooth"].max())
    min_jerk = float(window_df["jerk"].min())
    max_jerk = float(window_df["jerk"].max())

    is_steer = (window_df["gyr_z"].abs() >= cfg["STEERING_GYRO"]).values
    times = window_df["time"].values

    max_steer_dur = 0.0
    in_steer = False
    start_t = 0.0
    for i in range(len(is_steer)):
        if is_steer[i]:
            if not in_steer:
                start_t = times[i]
                in_steer = True
            dur = times[i] - start_t
            if dur > max_steer_dur:
                max_steer_dur = dur
        else:
            in_steer = False

    steering = max_steer_dur >= cfg["TURN_MIN_DUR"]

    direction = "NONE"
    if steering:
        mean_gyr = float(window_df.loc[is_steer, "gyr_z"].mean())
        direction = "LEFT" if mean_gyr > 0 else "RIGHT"

    hard_brake = (
        speed_change <= -cfg["BRAKE_SPEED_DROP"]
        and min_accel <= cfg["BRAKE_ACCEL"]
        and min_jerk <= cfg["BRAKE_JERK"]
    )
    sudden_accel = (
        speed_change >= cfg["ACCEL_SPEED_RISE"]
        and max_accel >= cfg["ACCEL_ACCEL"]
        and max_jerk >= cfg["ACCEL_JERK"]
    )
    normal_speed = abs(speed_change) <= cfg["NORMAL_SPEED_CHANGE"]
    normal_accel = abs(min_accel) <= cfg["NORMAL_ACCEL"] and abs(max_accel) <= cfg["NORMAL_ACCEL"]

    if hard_brake and steering:
        return {"event_type": "BRAKE_TURN_20", "direction": direction}
    if sudden_accel and steering:
        return {"event_type": "ACCEL_TURN_20", "direction": direction}
    if hard_brake:
        return {"event_type": "BRAKE_20", "direction": "NONE"}
    if sudden_accel:
        return {"event_type": "ACCEL_20", "direction": "NONE"}
    if steering and normal_speed:
        return {"event_type": "TURN_20", "direction": direction}
    if normal_speed and normal_accel and not steering:
        return {"event_type": "NORMAL_20", "direction": "STRAIGHT"}
    if normal_speed and normal_accel:
        return {"event_type": "NORMAL_20", "direction": "NONE"}
    return None


def detect_20kmh(df, cfg_general, cfg_g20):
    windows = generate_windows(df, cfg_general)
    candidates = []

    for s_idx, e_idx in windows:
        w = df.iloc[s_idx: e_idx + 1]
        result = _classify_window_20(w, cfg_g20)
        if result is None:
            continue
        candidates.append({
            "start_idx": s_idx, "end_idx": e_idx, "event_type": result["event_type"],
            "direction": result["direction"], "speed_group": "20kmh",
        })

    final_events = _merge_candidates(df, candidates, cfg_general["EVENT_MERGE_GAP"])
    final_events.sort(key=lambda x: x["start_idx"])
    return candidates, final_events


# ============================================================
# DETECTOR NHOM 40 / 80 km/h (cung logic, khac dai toc do)
# ============================================================

def _classify_window_mid_high(window_df, cfg, suffix):
    if len(window_df) < 2:
        return None

    speed_start = float(window_df["speed_kmh"].iloc[0])
    speed_end = float(window_df["speed_kmh"].iloc[-1])
    speed_change = speed_end - speed_start

    min_accel = float(window_df["acc_y_smooth"].min())
    max_accel = float(window_df["acc_y_smooth"].max())
    min_jerk = float(window_df["jerk"].min())
    max_jerk = float(window_df["jerk"].max())
    max_gyr_z = float(window_df["gyr_z"].abs().max())
    duration = float(window_df["time"].iloc[-1]) - float(window_df["time"].iloc[0])

    target_ratio = np.mean(
        (window_df["speed_kmh"] >= cfg["SPEED_MIN"]) & (window_df["speed_kmh"] <= cfg["SPEED_MAX"])
    )
    if target_ratio < 0.50:
        return None

    steering = max_gyr_z >= cfg["STEERING_GYRO"]

    hard_brake = (
        speed_change <= -cfg["BRAKE_SPEED_DROP"]
        and min_accel <= cfg["BRAKE_ACCEL"]
        and min_jerk <= cfg["BRAKE_JERK"]
    )
    sudden_accel = (
        speed_change >= cfg["ACCEL_SPEED_RISE"]
        and max_accel >= cfg["ACCEL_ACCEL"]
        and max_jerk >= cfg["ACCEL_JERK"]
    )
    normal_speed = abs(speed_change) <= cfg["NORMAL_SPEED_CHANGE"]
    normal_accel = abs(min_accel) <= cfg["NORMAL_ACCEL"] and abs(max_accel) <= cfg["NORMAL_ACCEL"]

    if hard_brake and steering:
        return {"event_type": f"BRAKE_TURN_{suffix}", "direction": "braking_and_steering"}
    if sudden_accel and steering:
        return {"event_type": f"ACCEL_TURN_{suffix}", "direction": "acceleration_and_steering"}
    if hard_brake:
        return {"event_type": f"BRAKE_{suffix}", "direction": "braking"}
    if sudden_accel:
        return {"event_type": f"ACCEL_{suffix}", "direction": "acceleration"}
    if steering and duration >= cfg["TURN_MIN_DUR"] and normal_speed:
        return {"event_type": f"TURN_{suffix}", "direction": "left_or_right_steering"}
    if normal_speed and normal_accel and not steering:
        return {"event_type": f"NORMAL_{suffix}", "direction": "straight_or_lane_change"}
    if normal_speed and normal_accel:
        return {"event_type": f"NORMAL_{suffix}", "direction": "normal_driving"}
    return None


def detect_40kmh(df, cfg_general, cfg_g40):
    windows = generate_windows(df, cfg_general)
    candidates = []
    for s_idx, e_idx in windows:
        w = df.iloc[s_idx: e_idx + 1]
        result = _classify_window_mid_high(w, cfg_g40, "40")
        if result is None:
            continue
        candidates.append({
            "start_idx": s_idx, "end_idx": e_idx, "event_type": result["event_type"],
            "direction": result["direction"], "speed_group": "40kmh",
        })
    merged = _merge_candidates(df, candidates, cfg_general["EVENT_MERGE_GAP"])
    return candidates, merged


def detect_80kmh(df, cfg_general, cfg_g80):
    windows = generate_windows(df, cfg_general)
    candidates = []
    for s_idx, e_idx in windows:
        w = df.iloc[s_idx: e_idx + 1]
        result = _classify_window_mid_high(w, cfg_g80, "80")
        if result is None:
            continue
        candidates.append({
            "start_idx": s_idx, "end_idx": e_idx, "event_type": result["event_type"],
            "direction": result["direction"], "speed_group": "80kmh",
        })
    merged = _merge_candidates(df, candidates, cfg_general["EVENT_MERGE_GAP"])
    return candidates, merged


# ============================================================
# RESIDUAL DETECTOR (8 nhan - xem DEFAULT_RESIDUAL_LABELS)
# ============================================================

def _check_rule(value, rule):
    return bool(_OPERATORS[rule["operator"]](value, rule["value"]))


def _match_simple_conditions(feat, conditions):
    results = []
    has_or = False

    for key, rule in conditions.items():
        name = key
        if name.startswith("OR_"):
            has_or = True
            name = name[3:]
        if name == "minimum_abs_speed_change":
            name = "abs_speed_change"
        results.append(_check_rule(feat[name], rule))

    if not results:
        return False
    return any(results) if has_or else all(results)


def _match_speed_transition(feat, cfg):
    tr = cfg["transitions"]
    s = feat["speed_start"]
    e = feat["speed_end"]

    l2m = tr["low_to_mid"]
    m2l = tr["mid_to_low"]
    c80 = tr["cross_80_boundary"]

    low_to_mid = s <= l2m["speed_start_max"] and e >= l2m["speed_end_min"]
    mid_to_low = s >= m2l["speed_start_min"] and e <= m2l["speed_end_max"]
    cross_80 = s <= c80["speed_start_max"] and e > c80["speed_end_min_exclusive"]

    if not (low_to_mid or mid_to_low or cross_80):
        return False
    return _match_simple_conditions(feat, cfg["conditions"])


def _match_residual_label(label, feat, residual_labels):
    cfg = residual_labels[label]
    if "transitions" in cfg:
        return _match_speed_transition(feat, cfg)
    return _match_simple_conditions(feat, cfg["conditions"])


def _speed_zone_label(speed, speed_boundaries):
    """Sua loi dead-code: nhanh "10.5-30kmh" cu luon chan truoc g20_max
    (vi g40_min == g20_max == 30.0 theo cfg mac dinh), khien "20kmh" KHONG
    BAO GIO duoc tra ve. G20 trong code da bao phu dung [10.0, 30.0] -
    khong co "vung chua bao phu" thuc su tai day (xem quyet dinh bo nhan
    Unknown09 / Uncovered 10.5-30 km/h trong DEFAULT_RESIDUAL_LABELS)."""
    g10_max, g10_tol, g20_max, g40_max, g80_max = speed_boundaries

    if speed <= g10_max + g10_tol:
        return "10kmh"
    if speed <= g20_max:
        return "20kmh"
    if speed <= g40_max:
        return "40kmh"
    if speed <= g80_max:
        return "80kmh"
    return ">80kmh"


def _build_primary_mask(n_samples, primary_events):
    mask = np.zeros(n_samples, dtype=bool)
    for ev in primary_events:
        mask[ev["start_idx"]: ev["end_idx"] + 1] = True
    cumsum = np.concatenate(([0], np.cumsum(mask.astype(np.int64))))
    return mask, cumsum


def _merge_unknown_candidates(df, candidates, merge_gap):
    return _merge_candidates(df, candidates, merge_gap)


def detect_unknown(df, primary_events, cfg_general, cfg_g10, cfg_g20, cfg_g40, cfg_g80,
                    residual_labels, residual_priority, residual_merge_gap):
    """Tang residual: phat hien 8 nhan bo sung tren timeline chua bi primary chiem."""

    n = len(df)
    if n < 2:
        return [], []

    windows = generate_windows(df, cfg_general)
    _, mask_cumsum = _build_primary_mask(n, primary_events)

    base_steering_gyro = cfg_g10["STEERING_GYRO"]
    base_steering_min_dur = cfg_g10["STEERING_MIN_DURATION"]

    t_arr = df["time"].to_numpy(dtype=float)
    speed_arr = df["speed_kmh"].to_numpy(dtype=float)
    acc_arr = df["acc_y_smooth"].to_numpy(dtype=float)
    jerk_arr = df["jerk"].to_numpy(dtype=float)
    gyr_arr = df["gyr_z"].to_numpy(dtype=float)
    gyr_abs_arr = np.abs(gyr_arr)
    gps_acc_arr = df["gps_accel"].to_numpy(dtype=float)

    candidates = []

    for s_idx, e_idx in windows:
        if mask_cumsum[e_idx + 1] - mask_cumsum[s_idx] > 0:
            continue

        sl = slice(s_idx, e_idx + 1)
        speed_w = speed_arr[sl]
        gyr_abs = gyr_abs_arr[sl]
        t_w = t_arr[sl]

        speed_start = float(speed_w[0])
        speed_end = float(speed_w[-1])
        speed_change = speed_end - speed_start

        steer_idx = np.where(gyr_abs >= base_steering_gyro)[0]
        steering_dur = 0.0
        if len(steer_idx) >= 2:
            steering_dur = float(t_w[steer_idx[-1]] - t_w[steer_idx[0]])

        feat = {
            "speed_start": speed_start, "speed_end": speed_end,
            "speed_min": float(speed_w.min()), "speed_max": float(speed_w.max()),
            "speed_change": speed_change, "abs_speed_change": abs(speed_change),
            "min_accel": float(acc_arr[sl].min()), "max_accel": float(acc_arr[sl].max()),
            "min_jerk": float(jerk_arr[sl].min()), "max_jerk": float(jerk_arr[sl].max()),
            "max_gyr_z": float(gyr_abs.max()), "steering_duration": steering_dur,
            "imu_gps_accel_difference": float(abs(
                np.median(acc_arr[sl]) - np.median(gps_acc_arr[sl])
            )),
        }

        for label in residual_priority:
            if not _match_residual_label(label, feat, residual_labels):
                continue

            direction = "NONE"
            if steering_dur >= base_steering_min_dur:
                mean_gyr = float(gyr_arr[sl][gyr_abs >= base_steering_gyro].mean())
                direction = "LEFT" if mean_gyr > 0 else "RIGHT" if mean_gyr < 0 else "TURN"

            group = residual_labels[label]["group"]
            candidates.append({
                "start_idx": s_idx, "end_idx": e_idx, "event_type": label,
                "direction": direction, "speed_group": "RESIDUAL", "label_group": group,
                "label_source": LABEL_SOURCE_QUALITY if group == RESIDUAL_GROUP_QUALITY else LABEL_SOURCE_RESIDUAL,
            })
            break

    merged = _merge_unknown_candidates(df, candidates, residual_merge_gap)

    speed_boundaries = (
        cfg_g10["SPEED_MAX"], cfg_g10["SPEED_TOLERANCE"],
        cfg_g20["SPEED_MAX"], cfg_g40["SPEED_MAX"], cfg_g80["SPEED_MAX"],
    )
    for ev in merged:
        seg_speed = speed_arr[ev["start_idx"]: ev["end_idx"] + 1]
        ev["speed_group"] = _speed_zone_label(float(np.median(seg_speed)), speed_boundaries)

    return candidates, merged


# ============================================================
# CANDIDATE CSV HELPER
# ============================================================

def build_candidate_df(df, candidates):
    records = []
    for i, event in enumerate(candidates, start=1):
        seg = df.iloc[event["start_idx"]: event["end_idx"] + 1]
        records.append({
            "candidate_id": i, "event_type": event["event_type"],
            "direction": event["direction"], "speed_group": event.get("speed_group", ""),
            "start_time": float(seg["time"].iloc[0]), "end_time": float(seg["time"].iloc[-1]),
            "duration": float(seg["time"].iloc[-1] - seg["time"].iloc[0]),
            "speed_start": float(seg["speed_kmh"].iloc[0]), "speed_end": float(seg["speed_kmh"].iloc[-1]),
            "speed_change": float(seg["speed_kmh"].iloc[-1] - seg["speed_kmh"].iloc[0]),
        })
    return pd.DataFrame(records)


EVENTS_DF_COLUMNS = [
    "event_id", "event_type", "label_source", "label_group",
    "direction", "speed_group",
    "start_time", "end_time", "duration",
    "speed_start", "speed_end", "speed_change",
    "speed_min", "speed_max",
    "min_accel", "max_accel",
    "min_jerk", "max_jerk",
    "max_gyr_z",
    "imu_gps_accel_difference",
    "lat", "lon",
    "samples",
]


def events_to_df(features_df, events):
    records = [_event_to_record(features_df, ev, i) for i, ev in enumerate(events, start=1)]
    return pd.DataFrame(records, columns=EVENTS_DF_COLUMNS)


OUTPUT_DIR_NAME = "detected_events_unified"


def get_output_dir(folder):
    output_dir = os.path.join(folder, OUTPUT_DIR_NAME)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def load_and_prepare(folder, cfg_general, base_steering_gyro, progress_callback=None):
    """Buoc chung (General): tim file -> doc -> lam sach GPS -> merge ->
    tinh dac trung. Tra ve (imu, gps, gps_clean, features_df, imu_path, gps_path)."""

    def _cb(msg):
        if progress_callback:
            progress_callback(msg)

    _cb("Finding IMU/GPS files...")
    imu_path, gps_path = find_input_files(folder)

    _cb("Reading IMU file...")
    imu = read_imu(imu_path)

    _cb("Reading GPS file...")
    gps = read_gps(gps_path)

    _cb("Cleaning GPS speed...")
    gps_clean = clean_gps_speed(gps, cfg_general)

    _cb("Merging IMU + GPS...")
    merged = merge_imu_gps(imu, gps_clean, cfg_general)

    _cb("Computing acceleration / jerk / yaw_rate features...")
    features = calculate_features(merged, cfg_general, base_steering_gyro)

    return imu, gps, gps_clean, features, imu_path, gps_path


def run_full_pipeline(folder, cfg, progress_callback=None):
    """Chay toan bo pipeline (General + 4 nhom primary + Residual), ghi
    tat ca CSV vao <folder>/detected_events_unified/. Tra ve dict tom tat."""

    def _cb(msg):
        if progress_callback:
            progress_callback(msg)

    cfg_general = cfg["general"]
    cfg_g10, cfg_g20, cfg_g40, cfg_g80 = cfg["g10"], cfg["g20"], cfg["g40"], cfg["g80"]

    imu, gps, gps_clean, features, imu_path, gps_path = load_and_prepare(
        folder, cfg_general, cfg_g10["STEERING_GYRO"], progress_callback
    )

    output_dir = get_output_dir(folder)

    gps_clean.to_csv(os.path.join(output_dir, "gps_speed_cleaned.csv"), index=False, encoding="utf-8-sig")
    features.to_csv(os.path.join(output_dir, "merged_motion_features.csv"), index=False, encoding="utf-8-sig")

    _cb("Detecting ~10 km/h events...")
    cand_10, events_10 = detect_10kmh(features, cfg_general, cfg_g10)
    build_candidate_df(features, cand_10).to_csv(
        os.path.join(output_dir, "detected_window_candidates_10kmh.csv"), index=False, encoding="utf-8-sig"
    )

    _cb("Detecting ~20 km/h events...")
    cand_20, events_20 = detect_20kmh(features, cfg_general, cfg_g20)
    build_candidate_df(features, cand_20).to_csv(
        os.path.join(output_dir, "detected_window_candidates_20kmh.csv"), index=False, encoding="utf-8-sig"
    )

    _cb("Detecting ~40 km/h events...")
    cand_40, events_40 = detect_40kmh(features, cfg_general, cfg_g40)
    build_candidate_df(features, cand_40).to_csv(
        os.path.join(output_dir, "detected_window_candidates_40kmh.csv"), index=False, encoding="utf-8-sig"
    )

    _cb("Detecting ~80 km/h events...")
    cand_80, events_80 = detect_80kmh(features, cfg_general, cfg_g80)
    build_candidate_df(features, cand_80).to_csv(
        os.path.join(output_dir, "detected_window_candidates_80kmh.csv"), index=False, encoding="utf-8-sig"
    )

    _cb("Detecting residual labels...")
    primary_events = events_10 + events_20 + events_40 + events_80
    cand_unk, events_unk = detect_unknown(
        features, primary_events, cfg_general, cfg_g10, cfg_g20, cfg_g40, cfg_g80,
        cfg["residual_labels"], cfg["residual_priority"], cfg["residual_merge_gap"],
    )
    build_candidate_df(features, cand_unk).to_csv(
        os.path.join(output_dir, "detected_window_candidates_unknown.csv"), index=False, encoding="utf-8-sig"
    )

    _cb("Combining all events...")
    all_events = primary_events + events_unk
    all_events.sort(key=lambda x: x["start_idx"])
    events_df = events_to_df(features, all_events)

    events_output = os.path.join(output_dir, "detected_driving_events_unified.csv")
    events_df.to_csv(events_output, index=False, encoding="utf-8-sig")

    unknown_counts = {label: 0 for label in sorted(cfg["residual_labels"])}
    if not events_df.empty:
        for label, count in events_df["event_type"].value_counts().items():
            if label in unknown_counts:
                unknown_counts[label] = int(count)

    return {
        "imu_file": imu_path, "gps_file": gps_path, "output_dir": output_dir,
        "imu_samples": len(imu), "gps_samples": len(gps), "merged_samples": len(features),
        "events_10": len(events_10), "events_20": len(events_20),
        "events_40": len(events_40), "events_80": len(events_80),
        "events_primary": len(primary_events), "events_unknown": len(events_unk),
        "unknown_counts": unknown_counts, "total_events": len(events_df),
        "events_output": events_output, "events_df": events_df,
        "features_df": features, "gps_clean_df": gps_clean,
        "group_results": {
            "G10": (cand_10, events_10), "G20": (cand_20, events_20),
            "G40": (cand_40, events_40), "G80": (cand_80, events_80),
        },
        "residual_result": (cand_unk, events_unk),
    }
