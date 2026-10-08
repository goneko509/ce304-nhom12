# -*- coding: utf-8 -*-
"""
Module: collector/realtime_inference.py
Chuc nang: Suy luan AI THOI GIAN THUC tren model INT8 (.tflite, da nhan -
           xem modular_app/core/label_schema.py), chay trong 1
           multiprocessing.Process RIENG voi main.py (khong chia GIL voi
           imu_worker/gps_worker - xem ly do trong README/plan thao luan
           ve do tre I2C 100Hz).

Day la BAN PORT THUAN numpy/scipy cua 4 ham tai tao dac trung dung khi
train (modular_app/core/event_detection_engine.py::clean_gps_speed /
merge_imu_gps / calculate_features / add_trig_features) - KHONG import
truc tiep module do (tranh keo theo pandas/tensorflow nang len Pi, hien
Pi chi co numpy+scipy - xem requirements.txt). Cong thuc PHAI khop CHINH
XAC voi ban pandas train (da doi chieu tung dong qua core/
event_detection_engine.py va core/inference_pipeline.py::
prepare_inference_features) de khong bi lech phan phoi feature (training-
serving skew).

Do tre vat ly CHU DICH (khong phai loi): GPS speed smoothing dung rolling
median CENTERED (nhin ca tuong lai) voi GPS_SPEED_MEDIAN_WINDOW=11 mau
@10Hz => nua cua so = 0.5s - day la nguon do tre bao cao chinh (~0.5s),
GIU NGUYEN de nhat quan tuyet doi voi luc train (xem Context trong plan).
"""

import os
import time
import json
import queue
import threading
import multiprocessing as mp
from collections import deque

import numpy as np

# ============================================================
# HANG SO PORT TU modular_app/core/event_detection_engine.py::DEFAULT_GENERAL
# (gia tri goc, KHONG scale o day - viec scale theo Hz thuc te lam o
# build_features_from_buffer, giong prepare_inference_features())
# ============================================================
GPS_SPEED_MEDIAN_WINDOW = 11   # mau, tai GPS 10Hz goc => nua cua so 0.5s
GPS_ZERO_SPEED = 0.5           # km/h, duoi nguong nay coi la dung yen
GPS_IMU_TOLERANCE = 0.15       # giay, dung sai noi IMU<->GPS gan nhat
ACCEL_SMOOTH_WINDOW = 7        # mau, tai IMU 100Hz goc => nua cua so 30ms

DEFAULT_LOG_NAME = "realtime_events.log"


# ============================================================
# PHAN 1: TAI TAO DAC TRUNG THUAN numpy/scipy (PORT TU
# core/event_detection_engine.py, KHONG import pandas)
# ============================================================

def resample_uniform(t, values, hz):
    """Noi suy tuyen tinh ve luoi thoi gian deu tai tan so hz. `values` la
    dict ten-cot -> array cung do dai voi `t`. Port cua
    core/inference_pipeline.py::resample_uniform (ban numpy, khong DataFrame)."""

    t = np.asarray(t, dtype=float)
    if len(t) < 2 or hz <= 0:
        return t, {k: np.asarray(v, dtype=float) for k, v in values.items()}

    t0, t1 = float(t[0]), float(t[-1])
    n = int(np.floor((t1 - t0) * hz)) + 1
    if n < 2:
        return t, {k: np.asarray(v, dtype=float) for k, v in values.items()}

    new_t = t0 + np.arange(n) / hz
    out = {k: np.interp(new_t, t, np.asarray(v, dtype=float)) for k, v in values.items()}
    return new_t, out


def centered_rolling_median(x, window):
    """Port cua pandas `.rolling(window, center=True, min_periods=1).median()`
    - moi diem lay median cua cac mau trong ban kinh `window//2` quanh no,
    CO RUT NGAN o 2 bien (giong min_periods=1, khong zero-pad nhu
    scipy.signal.medfilt). Buffer nho (vai tram diem) nen vong lap thuong
    la du nhanh, khong can toi uu vector hoa."""

    x = np.asarray(x, dtype=float)
    n = len(x)
    half = window // 2
    out = np.empty(n, dtype=float)
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out


def merge_nearest(imu_t, gps_t, gps_values, tolerance):
    """Port cua pandas `merge_asof(..., direction='nearest', tolerance=...)`
    + interpolate(limit_direction='both') + fillna(0) nhu
    core/event_detection_engine.py::merge_imu_gps. Tra ve dict ten-cot GPS
    -> array cung do dai voi `imu_t`."""

    imu_t = np.asarray(imu_t, dtype=float)
    gps_t = np.asarray(gps_t, dtype=float)
    out = {}

    if len(gps_t) == 0:
        for k in gps_values:
            out[k] = np.zeros(len(imu_t), dtype=float)
        return out

    idx = np.searchsorted(gps_t, imu_t)
    idx_lo = np.clip(idx - 1, 0, len(gps_t) - 1)
    idx_hi = np.clip(idx, 0, len(gps_t) - 1)
    dist_lo = np.abs(imu_t - gps_t[idx_lo])
    dist_hi = np.abs(imu_t - gps_t[idx_hi])
    nearest_idx = np.where(dist_lo <= dist_hi, idx_lo, idx_hi)
    nearest_dist = np.minimum(dist_lo, dist_hi)
    within_tol = nearest_dist <= tolerance

    for k, v in gps_values.items():
        v = np.asarray(v, dtype=float)
        mapped = np.where(within_tol, v[nearest_idx], np.nan)

        valid = ~np.isnan(mapped)
        if valid.any():
            mapped = np.interp(imu_t, imu_t[valid], mapped[valid])
        else:
            mapped = np.zeros_like(mapped)

        out[k] = np.nan_to_num(mapped, nan=0.0)

    return out


def calculate_core_features(time_arr, acc_x, acc_y, acc_z, accel_smooth_window):
    """Port cua phan can thiet cua core/event_detection_engine.py::
    calculate_features - CHI tinh dt/acc_norm/acc_y_smooth/jerk (4 cot duy
    nhat trong EVENT_CLASSIFIER_FEATURES can tu buoc nay; bo qua
    gps_accel/yaw_rate/steering* vi KHONG nam trong EVENT_CLASSIFIER_FEATURES)."""

    dt = np.diff(time_arr, prepend=time_arr[0] if len(time_arr) else 0.0)
    dt[dt == 0] = np.nan
    median_dt = np.nanmedian(dt) if np.any(~np.isnan(dt)) else 0.01
    dt = np.where(np.isnan(dt), median_dt, dt)
    dt = np.clip(dt, 0.001, None)

    acc_norm = np.sqrt(acc_x ** 2 + acc_y ** 2 + acc_z ** 2)
    acc_y_smooth = centered_rolling_median(acc_y, accel_smooth_window)

    jerk = np.diff(acc_y_smooth, prepend=acc_y_smooth[0] if len(acc_y_smooth) else 0.0) / dt
    jerk = np.nan_to_num(jerk, nan=0.0, posinf=0.0, neginf=0.0)
    jerk[0] = 0.0  # diff(..., prepend=x[0])[0] luon =0, dong bo voi .diff().fillna(0) cua pandas

    return acc_norm, jerk


def build_features_from_buffer(imu_rows, gps_rows, model_fs, declared_imu_hz, declared_gps_hz):
    """Dau vao: imu_rows = list[(t, acc_x, acc_y, acc_z, yaw, roll, pitch,
    gyr_x, gyr_y, gyr_z)], gps_rows = list[(t, speed_kmh)] - CHINH XAC cac
    cot can cho EVENT_CLASSIFIER_FEATURES (xem modular_app/core/
    sensor_schema.py). Tra ve (time_arr, feature_matrix) voi
    feature_matrix co THU TU COT giong EVENT_CLASSIFIER_FEATURES."""

    imu_arr = np.asarray(imu_rows, dtype=float)
    gps_arr = np.asarray(gps_rows, dtype=float)
    if len(imu_arr) < 2:
        return np.empty(0), np.empty((0, 15))

    t_imu = imu_arr[:, 0]
    accel_window = max(3, int(round(ACCEL_SMOOTH_WINDOW * declared_imu_hz / 100.0)))
    gps_window = max(3, int(round(GPS_SPEED_MEDIAN_WINDOW * declared_gps_hz / 10.0)))

    # 1) Resample IMU ve dung tan so model (giong prepare_inference_features)
    t_rs, vals_rs = resample_uniform(t_imu, {
        "acc_x": imu_arr[:, 1], "acc_y": imu_arr[:, 2], "acc_z": imu_arr[:, 3],
        "yaw": imu_arr[:, 4], "roll": imu_arr[:, 5], "pitch": imu_arr[:, 6],
        "gyr_x": imu_arr[:, 7], "gyr_y": imu_arr[:, 8], "gyr_z": imu_arr[:, 9],
    }, model_fs)

    if len(t_rs) < 2:
        return np.empty(0), np.empty((0, 15))

    # 2) Clean GPS speed (centered median + zero-threshold)
    if len(gps_arr) > 0:
        t_gps = gps_arr[:, 0]
        speed_raw = np.clip(gps_arr[:, 1], 0.0, None)
        speed_clean = centered_rolling_median(speed_raw, gps_window)
        speed_clean[speed_clean <= GPS_ZERO_SPEED] = 0.0
    else:
        t_gps = np.empty(0)
        speed_clean = np.empty(0)

    # 3) Merge GPS speed vao luoi IMU (nearest-asof trong dung sai)
    merged = merge_nearest(t_rs, t_gps, {"speed_kmh": speed_clean}, GPS_IMU_TOLERANCE)

    # 4) calculate_features (chi acc_norm/jerk can cho model)
    acc_norm, jerk = calculate_core_features(
        t_rs, vals_rs["acc_x"], vals_rs["acc_y"], vals_rs["acc_z"], accel_window
    )

    # 5) add_trig_features (sin/cos yaw/roll/pitch, do goc tinh bang)
    sin_yaw, cos_yaw = np.sin(np.radians(vals_rs["yaw"])), np.cos(np.radians(vals_rs["yaw"]))
    sin_pitch, cos_pitch = np.sin(np.radians(vals_rs["pitch"])), np.cos(np.radians(vals_rs["pitch"]))
    sin_roll, cos_roll = np.sin(np.radians(vals_rs["roll"])), np.cos(np.radians(vals_rs["roll"]))

    # THU TU COT PHAI KHOP EVENT_CLASSIFIER_FEATURES trong core/sensor_schema.py:
    #   speed_kmh, acc_x, acc_y, acc_z, gyr_x, gyr_y, gyr_z, acc_norm, jerk,
    #   sin_yaw, cos_yaw, sin_pitch, cos_pitch, sin_roll, cos_roll
    feature_matrix = np.column_stack([
        merged["speed_kmh"], vals_rs["acc_x"], vals_rs["acc_y"], vals_rs["acc_z"],
        vals_rs["gyr_x"], vals_rs["gyr_y"], vals_rs["gyr_z"],
        acc_norm, jerk,
        sin_yaw, cos_yaw, sin_pitch, cos_pitch, sin_roll, cos_roll,
    ])
    return t_rs, feature_matrix


# ============================================================
# PHAN 2: NAP MODEL TFLITE + SUY LUAN 1 CUA SO (giong core/
# inference_pipeline.py::run_sliding_inference, ban rut gon)
# ============================================================

def _load_interpreter(model_path):
    """Thu lan luot 3 nguon Interpreter, tu nhe nhat den nang nhat - uu
    tien goi nhe cho Pi (tflite_runtime da bi Google ngung phat hanh wheel
    cho nhieu cau hinh ARM/Python moi, ai_edge_litert la ban ke thua chinh
    thuc); fallback cuoi cung ve tensorflow day du (chi hop ly tren PC dev
    de test, qua nang cho Pi)."""
    try:
        from tflite_runtime.interpreter import Interpreter
        return Interpreter(model_path=model_path)
    except ImportError:
        pass
    try:
        from ai_edge_litert.interpreter import Interpreter
        return Interpreter(model_path=model_path)
    except ImportError:
        pass
    import tensorflow as tf
    return tf.lite.Interpreter(model_path=model_path)


def load_model_bundle(model_path, label_map_path):
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"Không tìm thấy model TFLite:\n{model_path}")
    if not os.path.isfile(label_map_path):
        raise FileNotFoundError(f"Không tìm thấy label_map.json:\n{label_map_path}")

    interpreter = _load_interpreter(model_path)
    interpreter.allocate_tensors()

    with open(label_map_path, "r", encoding="utf-8") as f:
        label_map = json.load(f)

    meta = label_map.get("meta", {})
    flag_names = label_map.get("flag_names", [])

    return {
        "interpreter": interpreter,
        "flag_names": flag_names,
        "timesteps": int(meta.get("timesteps", interpreter.get_input_details()[0]["shape"][1])),
        "window_sec": float(meta.get("window_sec", 2.0)),
        "model_fs": float(meta.get("fs", 100.0)),
    }


def decode_window(bundle, window, threshold=0.5):
    """1 cua so (timesteps, num_features) -> list cac co dang bat. Tai su
    dung dung logic quantize/invoke/dequantize/nguong cua core/
    inference_pipeline.py::run_sliding_inference (khong sua cong thuc)."""

    interpreter = bundle["interpreter"]
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]
    in_scale, in_zero = input_details["quantization"]
    out_scale, out_zero = output_details["quantization"]

    if input_details["dtype"] == np.int8:
        q = np.round(window / in_scale + in_zero).astype(np.int8)
    else:
        q = window.astype(np.float32)

    interpreter.set_tensor(input_details["index"], np.expand_dims(q, axis=0))
    interpreter.invoke()
    raw_out = interpreter.get_tensor(output_details["index"])[0]

    if output_details["dtype"] == np.int8:
        probs = (raw_out.astype(np.float32) - out_zero) * out_scale
    else:
        probs = raw_out.astype(np.float32)

    probs = np.clip(probs, 0.0, 1.0)
    flag_names = bundle["flag_names"]
    return [flag_names[i] for i, p in enumerate(probs) if p >= threshold], probs


# ============================================================
# PHAN 3: VONG LAP PROCESS CON (chay trong multiprocessing.Process)
# ============================================================

def _alert_flags(active_flags):
    """Loc bo co NORMAL - dung chung cho ca log file/console VA file
    trang thai doc boi web_annotation.py (/api/status), giu nhat quan 1
    quy uoc "khong lam phien nguoi dung ve NORMAL" o moi noi hien thi."""
    return [f for f in active_flags if f != "NORMAL"]


def _log_result(log_path, t_end, active_flags):
    """Chi in/ghi log CONG DON khi co it nhat 1 co dang chu y (khac
    NORMAL) - day la file LICH SU su kien, khong phai trang thai hien tai
    (xem _write_status ben duoi cho muc dich do)."""

    alert_flags = _alert_flags(active_flags)
    if not alert_flags:
        return

    ts = time.strftime("%H:%M:%S")
    tag = "+".join(alert_flags)
    line = f"[{ts}] t={t_end:7.2f}s  {tag}"
    print(f"[REALTIME-AI] {line}", flush=True)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[REALTIME-AI] Lỗi ghi log: {e}", flush=True)


def _write_status(status_path, t_end, active_flags):
    """Ghi DE moi tick (khac _log_result chi ghi khi co canh bao) - phan
    anh dung trang thai HIEN TAI, de web_annotation.py (/api/status) doc
    va hien badge tren HUD: khi lai binh thuong, file nay se co flags=[]
    (khong phai "khong cap nhat gi" - phai ghi DE de UI biet la da het
    canh bao, tranh badge bi "dong cung" o lan canh bao cuoi cung)."""

    tmp_path = status_path + ".tmp"
    payload = {
        "t": t_end,
        "flags": _alert_flags(active_flags),
        "updated_at": time.time(),
    }
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, status_path)  # atomic tren POSIX - web doc khong bi file do dang
    except Exception:
        pass


def run_worker(mp_imu_queue, mp_gps_queue, model_path, label_map_path,
               stride_sec, buffer_sec, declared_imu_hz, declared_gps_hz,
               log_path, stop_event, status_path=None):
    """Entry point chay trong process con. KHONG chia GIL voi
    imu_worker/gps_worker o main.py - an toan nhip 100Hz du invoke() co
    nha GIL hay khong (xem Context trong plan thao luan)."""

    print("[REALTIME-AI] Đang nạp model...", flush=True)
    try:
        bundle = load_model_bundle(model_path, label_map_path)
    except Exception as e:
        print(f"[REALTIME-AI] LỖI nạp model, tắt suy luận realtime: {e}", flush=True)
        return

    timesteps = bundle["timesteps"]
    model_fs = bundle["model_fs"]
    print(f"[REALTIME-AI] Sẵn sàng: timesteps={timesteps} fs={model_fs:.0f}Hz "
          f"{len(bundle['flag_names'])} cờ | stride={stride_sec}s buffer={buffer_sec}s", flush=True)

    imu_buf = deque()
    gps_buf = deque()
    next_tick = time.perf_counter()

    while not stop_event.is_set():
        saw_reset = False
        try:
            while True:
                item = mp_imu_queue.get_nowait()
                if item is None:
                    # Tin hieu RESET (xem collector/main.py::start_logging VA
                    # stop_logging) - phien ghi moi vua bat dau HOAC phien vua
                    # ket thuc, T0 da/sap reset ve 0 nen buffer cu (timestamp
                    # phien TRUOC) phai xoa sach, khong de lan vao cua so tinh
                    # dac trung cua phien khac.
                    imu_buf.clear()
                    saw_reset = True
                    continue
                imu_buf.append(item)
        except queue.Empty:
            pass
        try:
            while True:
                item = mp_gps_queue.get_nowait()
                if item is None:
                    gps_buf.clear()
                    saw_reset = True
                    continue
                gps_buf.append(item)
        except queue.Empty:
            pass

        if saw_reset and status_path:
            # Xoa trang thai AI tren HUD NGAY LAP TUC thay vi cho tick suy
            # luan tiep theo (co the khong bao gio den neu day la tin hieu
            # dung ghi - buffer rong thi vong lap ben duoi se khong goi
            # _write_status nua, HUD se "ket treo" canh bao cu neu khong co
            # dong nay).
            _write_status(status_path, 0.0, [])

        if imu_buf:
            t_latest = imu_buf[-1][0]
            while imu_buf and (t_latest - imu_buf[0][0]) > buffer_sec:
                imu_buf.popleft()
            while gps_buf and (t_latest - gps_buf[0][0]) > buffer_sec:
                gps_buf.popleft()

        now = time.perf_counter()
        if now >= next_tick:
            next_tick = now + stride_sec
            if len(imu_buf) >= 2:
                try:
                    t_arr, feats = build_features_from_buffer(
                        list(imu_buf), list(gps_buf), model_fs, declared_imu_hz, declared_gps_hz
                    )
                    if len(t_arr) >= timesteps:
                        window = feats[-timesteps:]
                        active_flags, _ = decode_window(bundle, window)
                        _log_result(log_path, float(t_arr[-1]), active_flags)
                        if status_path:
                            _write_status(status_path, float(t_arr[-1]), active_flags)
                except Exception as e:
                    print(f"[REALTIME-AI] Lỗi suy luận 1 cửa sổ (bỏ qua, tiếp tục): {e}", flush=True)

        time.sleep(0.01)

    print("[REALTIME-AI] Đã dừng.", flush=True)


def start_process(mp_imu_queue, mp_gps_queue, model_path, label_map_path,
                   stride_sec, buffer_sec, declared_imu_hz, declared_gps_hz, log_path,
                   status_path=None):
    """Goi tu collector/main.py: tao + start 1 multiprocessing.Process
    chay run_worker(), tra ve (process, stop_event) de main.py co the
    dung an toan luc thoat (stop_event.set() + process.join(timeout)).
    `status_path` (tuy chon): file JSON ghi de moi tick de
    web_annotation.py (/api/status) doc va hien badge AI tren HUD."""

    stop_event = mp.Event()
    proc = mp.Process(
        target=run_worker,
        args=(mp_imu_queue, mp_gps_queue, model_path, label_map_path,
              stride_sec, buffer_sec, declared_imu_hz, declared_gps_hz,
              log_path, stop_event, status_path),
        daemon=True,
    )
    proc.start()
    return proc, stop_event
