# -*- coding: utf-8 -*-
"""
Module: core/sensor_schema.py
Chuc nang: Nguon chan ly duy nhat cho ten cot cam bien, cong thuc Haversine,
           bien doi dac trung goc Euler (sin/cos) va danh sach dac trung CNN.
Muc dich: Loai bo code trung lap truoc day nam rai rac o top_module.py,
          tab0_analysis.py, tab3_gps_imu.py, tab6_pipeline.py, tab_predict.py
          va pipeline_cnn_int8.py.
"""

import math
import numpy as np

GPS_COLUMNS = [
    "t_now", "UTC", "lat", "lon", "hdop", "alt",
    "fix", "cog", "speed_kmh", "speed_knots", "date", "satellites"
]

IMU_COLUMNS = [
    "time", "acc_x", "acc_y", "acc_z", "yaw", "roll",
    "pitch", "gyr_x", "gyr_y", "gyr_z", "quat_w", "quat_x", "quat_y", "quat_z"
]

# 11 dac trung dau vao cho mang Conv1D (phien ban Step 6/7 cu - GIU NGUYEN
# de khong lam lech shape input cua cac model .tflite cu da huan luyen)
CNN_FEATURE_COLUMNS = [
    "speed_kmh", "acc_x", "acc_y", "acc_z",
    "gyr_x", "gyr_y", "gyr_z",
    "sin_pitch", "cos_pitch", "sin_roll", "cos_roll"
]

# 15 dac trung cho bo phan loai 34-nhan (Step 6 moi, dong bo voi
# core/label_schema.py va core/event_detection_engine.py): bo sung
# sin/cos_yaw (quan trong cho cac nhan TURN/BRAKE_TURN/ACCEL_TURN) va
# acc_norm/jerk (da tinh san trong merged_motion_features.csv o Step 8).
EVENT_CLASSIFIER_FEATURES = [
    "speed_kmh", "acc_x", "acc_y", "acc_z",
    "gyr_x", "gyr_y", "gyr_z",
    "acc_norm", "jerk",
    "sin_yaw", "cos_yaw", "sin_pitch", "cos_pitch", "sin_roll", "cos_roll",
]

EULER_ANGLES = ["yaw", "roll", "pitch"]


def haversine_distance(lat1, lon1, lat2, lon2):
    """Khoang cach cung tron lon giua 2 toa do GPS (don vi: met).

    Co so khoa hoc: Cong thuc Haversine - xem app_config.json / README.
    """
    R = 6371000.0  # Ban kinh Trai Dat (m)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2.0) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return R * c


def calculate_cumulative_distance(df_segment, lat_col="lat", lon_col="lon"):
    """Tong quang duong di duoc qua tat ca cac diem lien tiep trong 1 doan."""
    if len(df_segment) < 2:
        return 0.0
    lats = df_segment[lat_col].values
    lons = df_segment[lon_col].values
    total = 0.0
    for i in range(len(lats) - 1):
        total += haversine_distance(lats[i], lons[i], lats[i + 1], lons[i + 1])
    return total


def add_trig_features(df, angles=EULER_ANGLES, degrees=True):
    """Them cot sin_<goc>/cos_<goc> cho cac goc Euler da ton tai trong df.

    Khong tinh lai neu cot sin_/cos_ da co san (idempotent).
    """
    df = df.copy()
    for angle in angles:
        if angle in df.columns and f"sin_{angle}" not in df.columns:
            rad = np.radians(df[angle]) if degrees else df[angle]
            df[f"sin_{angle}"] = np.sin(rad)
            df[f"cos_{angle}"] = np.cos(rad)
    return df
