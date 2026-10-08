# -*- coding: utf-8 -*-
"""
Module: core/inference_pipeline.py
Chuc nang: Logic suy luan AI (Step 7) - kiem tra mo hinh INT8 (.tflite)
           huan luyen o Step 6 co chay dung tren CAP FILE THO moi hay
           khong (RAW_ACCELEROMETERS.txt + RAW_GPS.txt), tai tao LAI
           CHINH XAC pipeline dac trung da dung khi huan luyen (tai su
           dung core/event_detection_engine.py + core/sensor_schema.py)
           de tranh training-serving skew.

           Diem khac biet quan trong so voi luc huan luyen: du lieu RAW
           dau vao co the duoc ghi o tan so lay mau KHAC (jitter / mat
           mau), nen nguoi dung duoc nhap tan so quet THUC TE cua tung
           tin hieu (IMU & GPS) de:
             1. Resample IMU ve luoi thoi gian deu, DUNG BANG tan so ma
                model da duoc huan luyen (doc tu label_map.json) - dam
                bao moi cua so dua vao model co dung so mau (timesteps)
                VA dung thoi luong (window_sec) nhu luc train.
             2. Scale lai cac tham so lam muot theo SO MAU (vd
                ACCEL_SMOOTH_WINDOW, GPS_SPEED_MEDIAN_WINDOW) ti le voi
                tan so khai bao, giu nguyen thoi luong lam muot thuc te
                (giay) nhu dinh nghia goc o 100Hz IMU / 10Hz GPS.

Dau ra: danh sach "su kien du doan" (gop cac cua so lien tiep cung nhan),
        bao cao van ban (.txt) ro rang, va (neu co) so sanh voi nhan
        heuristic cua Step 8 tren cung thu muc de doi chieu nhanh.
"""

import os
import json
from datetime import datetime

import numpy as np
import pandas as pd

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
import tensorflow as tf

from core import event_detection_engine as eng
from core.sensor_schema import add_trig_features, EVENT_CLASSIFIER_FEATURES
from core.label_schema import NAME_TO_ID, FLAG_NAMES, LABEL_TO_FLAGS

DEFAULT_IMU_HZ = 100.0
DEFAULT_GPS_HZ = 10.0
DEFAULT_STRIDE_SEC = 0.3
DEFAULT_MERGE_GAP_SEC = 0.5
DEFAULT_FLAG_THRESHOLD = 0.5

GT_EVENTS_CSV_NAME = "detected_driving_events_unified.csv"
REPORT_FILE_NAME = "ai_inference_report.txt"


# ============================================================
# GIAI DOAN 0: NAP MO HINH + LABEL MAP
# ============================================================

def load_model_bundle(model_path, label_map_path):
    if not os.path.isfile(model_path):
        raise FileNotFoundError(f"Không tìm thấy model TFLite:\n{model_path}")
    if not os.path.isfile(label_map_path):
        raise FileNotFoundError(f"Không tìm thấy label_map.json:\n{label_map_path}")

    interpreter = tf.lite.Interpreter(model_path=model_path)
    interpreter.allocate_tensors()

    with open(label_map_path, "r", encoding="utf-8") as f:
        label_map = json.load(f)

    meta = label_map.get("meta", {})
    id_to_name = {int(d["id"]): d["name"] for d in label_map["labels"]}
    flag_names = label_map.get("flag_names", list(FLAG_NAMES))

    feature_columns = meta.get("feature_columns", EVENT_CLASSIFIER_FEATURES)
    timesteps = int(meta.get("timesteps", interpreter.get_input_details()[0]["shape"][1]))
    window_sec = float(meta.get("window_sec", 2.0))
    model_fs = float(meta.get("fs", DEFAULT_IMU_HZ))

    return {
        "interpreter": interpreter,
        "id_to_name": id_to_name,
        "flag_names": flag_names,
        "feature_columns": feature_columns,
        "timesteps": timesteps,
        "window_sec": window_sec,
        "model_fs": model_fs,
        "num_classes": label_map.get("num_classes", len(id_to_name)),
        "num_flags": label_map.get("num_flags", len(flag_names)),
    }


# ============================================================
# GIAI DOAN 1: RESAMPLE VE LUOI DEU + TAI TAO DAC TRUNG NHU LUC TRAIN
# ============================================================

def resample_uniform(df, time_col, hz, value_cols):
    """Noi suy tuyen tinh 1 DataFrame ve luoi thoi gian DEU tai tan so hz."""

    t = df[time_col].to_numpy(dtype=float)
    if len(t) < 2 or hz <= 0:
        return df[[time_col] + value_cols].copy()

    t0, t1 = float(t[0]), float(t[-1])
    n = int(np.floor((t1 - t0) * hz)) + 1
    if n < 2:
        return df[[time_col] + value_cols].copy()

    new_time = t0 + np.arange(n) / hz
    out = {time_col: new_time}
    for col in value_cols:
        out[col] = np.interp(new_time, t, df[col].to_numpy(dtype=float))

    return pd.DataFrame(out)


def prepare_inference_features(folder, imu_hz, gps_hz, model_fs, log=print):
    """Doc 2 file RAW trong `folder`, resample IMU ve dung tan so model_fs
    (tan so da dung khi huan luyen), tai tao lai TOAN BO pipeline dac
    trung (clean GPS speed -> merge_asof -> calculate_features -> trig
    features) GIONG HET luc huan luyen o core/event_detection_engine.py,
    chi khac: cac tham so lam muot duoc scale theo tan so khai bao."""

    imu_path, gps_path = eng.find_input_files(folder)
    log(f"  IMU: {os.path.basename(imu_path)}")
    log(f"  GPS: {os.path.basename(gps_path)}")

    imu = eng.read_imu(imu_path)
    gps = eng.read_gps(gps_path)

    actual_imu_hz = 1.0 / np.median(np.diff(imu["time"].to_numpy(dtype=float)))
    actual_gps_hz = 1.0 / np.median(np.diff(gps["t_now"].to_numpy(dtype=float)))
    log(f"  IMU: khai báo {imu_hz:.1f} Hz · thực đo {actual_imu_hz:.1f} Hz · {len(imu):,} mẫu")
    log(f"  GPS: khai báo {gps_hz:.1f} Hz · thực đo {actual_gps_hz:.1f} Hz · {len(gps):,} mẫu")

    if abs(actual_imu_hz - imu_hz) / imu_hz > 0.15:
        log(f"  ⚠️ Tần số IMU thực đo lệch >15% so với khai báo - kiểm tra lại cấu hình!")
    if abs(actual_gps_hz - gps_hz) / gps_hz > 0.15:
        log(f"  ⚠️ Tần số GPS thực đo lệch >15% so với khai báo - kiểm tra lại cấu hình!")

    # Scale cac tham so lam muot (tinh bang SO MAU) theo ti le tan so khai
    # bao so voi tan so goc da hieu chinh (IMU 100Hz / GPS 10Hz), giu
    # nguyen THOI LUONG lam muot thuc te (giay) nhu luc huan luyen.
    cfg_general = dict(eng.DEFAULT_GENERAL)
    cfg_general["ACCEL_SMOOTH_WINDOW"] = max(
        3, int(round(eng.DEFAULT_GENERAL["ACCEL_SMOOTH_WINDOW"] * imu_hz / 100.0))
    )
    cfg_general["GPS_SPEED_MEDIAN_WINDOW"] = max(
        3, int(round(eng.DEFAULT_GENERAL["GPS_SPEED_MEDIAN_WINDOW"] * gps_hz / 10.0))
    )

    # Resample IMU ve DUNG tan so model da duoc huan luyen -> moi window
    # truot sau nay se co dung so mau (timesteps) VA dung thoi luong.
    imu_u = resample_uniform(
        imu, "time", model_fs,
        ["acc_x", "acc_y", "acc_z", "gyr_x", "gyr_y", "gyr_z", "yaw", "roll", "pitch"]
    )

    gps_clean = eng.clean_gps_speed(gps, cfg_general)
    merged = eng.merge_imu_gps(imu_u, gps_clean, cfg_general)
    features = eng.calculate_features(merged, cfg_general, base_steering_gyro=0.35)
    features = add_trig_features(features, angles=["yaw", "pitch", "roll"])

    return features, {
        "imu_declared_hz": imu_hz, "imu_actual_hz": float(actual_imu_hz), "imu_samples": len(imu),
        "gps_declared_hz": gps_hz, "gps_actual_hz": float(actual_gps_hz), "gps_samples": len(gps),
        "imu_path": imu_path, "gps_path": gps_path,
    }


# ============================================================
# GIAI DOAN 2: SUY LUAN TRUOT CUA SO TREN MO HINH INT8
# ============================================================

def run_sliding_inference(bundle, features_df, stride_sec, threshold=DEFAULT_FLAG_THRESHOLD, log=print):
    interpreter = bundle["interpreter"]
    feature_columns = bundle["feature_columns"]
    timesteps = bundle["timesteps"]
    flag_names = bundle["flag_names"]

    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]
    in_scale, in_zero = input_details["quantization"]
    out_scale, out_zero = output_details["quantization"]

    num_outputs = int(output_details["shape"][-1])
    if num_outputs != len(flag_names):
        raise ValueError(
            f"Model có {num_outputs} đầu ra nhưng label_map.json mô tả {len(flag_names)} cờ "
            f"đa nhãn — KHÔNG KHỚP nhau. Nguyên nhân thường gặp: đang dùng model CŨ (huấn "
            f"luyện trước khi chuyển sang đa nhãn — 33 lớp đơn nhãn, softmax) cùng với "
            f"label_map.json mới (hoặc ngược lại), hoặc model/label_map.json bị lẫn từ 2 "
            f"lần train khác nhau. Hãy train lại model mới (đa nhãn) hoặc chọn đúng cặp "
            f"model (.tflite) + label_map.json được XUẤT CÙNG 1 LẦN TRAIN (luôn nằm cùng "
            f"thư mục)."
        )

    missing = [c for c in feature_columns if c not in features_df.columns]
    if missing:
        raise ValueError(f"Thiếu cột đặc trưng cần cho model: {missing}")

    feat_df = features_df[feature_columns].interpolate(limit_direction="both").bfill().ffill()
    data = feat_df.to_numpy(dtype=np.float32)
    time_arr = features_df["time"].to_numpy(dtype=float)
    n = len(data)

    stride_samples = max(1, int(round(stride_sec * bundle["model_fs"])))

    if n < timesteps:
        raise ValueError(
            f"Dữ liệu sau xử lý quá ngắn ({n} mẫu), cần tối thiểu {timesteps} mẫu "
            f"({bundle['window_sec']}s @ {bundle['model_fs']:.0f} Hz)."
        )

    records = []
    for start in range(0, n - timesteps + 1, stride_samples):
        end = start + timesteps
        window = data[start:end]

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

        # Da nhan DOC LAP (sigmoid) - KHONG ep tong = 1 nhu softmax, chi
        # clip ve [0,1] vi moi gia tri la 1 xac suat rieng cua 1 co.
        probs = np.clip(probs, 0.0, 1.0)
        active_flags = [flag_names[i] for i, p in enumerate(probs) if p >= threshold]
        mean_conf = float(np.mean([probs[flag_names.index(f)] for f in active_flags])) if active_flags else 0.0

        records.append({
            "start_time": float(time_arr[start]), "end_time": float(time_arr[end - 1]),
            "active_flags": active_flags, "confidence": mean_conf,
            "probs": probs.tolist(),
        })

    log(f"  Đã chạy suy luận trên {len(records)} cửa sổ trượt (stride={stride_sec}s).")
    return records


# ============================================================
# GIAI DOAN 3: GOP CUA SO LIEN TIEP CUNG NHAN THANH "SU KIEN DU DOAN"
# ============================================================

def merge_predicted_events(records, id_to_name=None, merge_gap=DEFAULT_MERGE_GAP_SEC):
    """Gop cac cua so lien tiep co CUNG TAP CO DANG BAT (active_flags, so
    sanh nhu 1 set khong phan biet thu tu) thanh 1 "su kien du doan" - thay
    the viec so khop 1 pred_id duy nhat (don nhan cu) boi tap hop co da
    nhan. `id_to_name` giu lam tham so de tuong thich loi goi cu, khong
    con dung (event_type gio la cac co da nhan noi lai, khong phai 1 ten
    lop dong-tru nua)."""

    if not records:
        return []

    raw_events = []
    cur = None
    for r in records:
        flag_set = frozenset(r["active_flags"])
        if cur is None:
            cur = {"flag_set": flag_set, "start_time": r["start_time"],
                   "end_time": r["end_time"], "confidences": [r["confidence"]]}
            continue

        gap = r["start_time"] - cur["end_time"]
        if flag_set == cur["flag_set"] and gap <= merge_gap:
            cur["end_time"] = r["end_time"]
            cur["confidences"].append(r["confidence"])
        else:
            raw_events.append(cur)
            cur = {"flag_set": flag_set, "start_time": r["start_time"],
                   "end_time": r["end_time"], "confidences": [r["confidence"]]}
    raw_events.append(cur)

    events = []
    for i, e in enumerate(raw_events, start=1):
        active_flags = sorted(e["flag_set"])
        events.append({
            "event_id": i,
            "active_flags": active_flags,
            "event_type": "+".join(active_flags) if active_flags else "NONE",
            "start_time": e["start_time"], "end_time": e["end_time"],
            "duration": e["end_time"] - e["start_time"],
            "mean_confidence": float(np.mean(e["confidences"])),
            "num_windows": len(e["confidences"]),
        })
    return events


# ============================================================
# BUILD EVENTS_DF CHO MO PHONG 3D (Step 4 -> Step 9), NGUON = STEP 7 AI,
# KHONG PHAI detected_driving_events_unified.csv (Step 8).
# ============================================================

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


def build_events_df_for_map(features_df, predicted_events):
    """Dung cho Step 4 khi nguoi dung chon 1 #ID tu Step 7: dung lai CHINH
    features_df + predicted_events cua lan suy luan AI gan nhat (KHONG
    doc lai detected_driving_events_unified.csv cua Step 8), tao ra 1
    DataFrame CUNG CAU TRUC voi EVENTS_DF_COLUMNS de Step 9 (create_map /
    add_3d_simulator / popup / sidebar) co the tai su dung nguyen ven."""

    records = []
    for e in predicted_events:
        seg = features_df[
            (features_df["time"] >= e["start_time"]) & (features_df["time"] <= e["end_time"])
        ]
        if len(seg) == 0:
            continue

        speed_start = float(seg["speed_kmh"].iloc[0]) if "speed_kmh" in seg.columns else 0.0
        speed_end = float(seg["speed_kmh"].iloc[-1]) if "speed_kmh" in seg.columns else 0.0

        valid_pos = seg[seg["lat"].notna() & seg["lon"].notna()] if "lat" in seg.columns else seg.iloc[0:0]
        if len(valid_pos) > 0:
            lat = float(valid_pos["lat"].iloc[0])
            lon = float(valid_pos["lon"].iloc[0])
        else:
            lat = float("nan")
            lon = float("nan")

        records.append({
            "event_id": e["event_id"], "event_type": e["event_type"],
            "label_source": "AI_PREDICTED", "label_group": "",
            "direction": "NONE", "speed_group": "",
            "start_time": e["start_time"], "end_time": e["end_time"], "duration": e["duration"],
            "speed_start": speed_start, "speed_end": speed_end, "speed_change": speed_end - speed_start,
            "speed_min": float(seg["speed_kmh"].min()) if "speed_kmh" in seg.columns else 0.0,
            "speed_max": float(seg["speed_kmh"].max()) if "speed_kmh" in seg.columns else 0.0,
            "min_accel": float(seg["acc_y_smooth"].min()) if "acc_y_smooth" in seg.columns else 0.0,
            "max_accel": float(seg["acc_y_smooth"].max()) if "acc_y_smooth" in seg.columns else 0.0,
            "min_jerk": float(seg["jerk"].min()) if "jerk" in seg.columns else 0.0,
            "max_jerk": float(seg["jerk"].max()) if "jerk" in seg.columns else 0.0,
            "max_gyr_z": float(seg["gyr_z"].abs().max()) if "gyr_z" in seg.columns else 0.0,
            "imu_gps_accel_difference": 0.0,
            "lat": lat, "lon": lon, "samples": len(seg),
        })

    return pd.DataFrame(records, columns=EVENTS_DF_COLUMNS)


# ============================================================
# GIAI DOAN 4 (TUY CHON): DOI CHIEU NHANH VOI NHAN HEURISTIC STEP 8
# ============================================================

def compare_with_heuristic(folder, predicted_events, log=print):
    """Neu thu muc RAW dang test co san thu muc con detected_events_unified/
    (da chay qua Step 8 tren CHINH trip nay), doi chieu % thoi gian ma
    nhan AI du doan TRUNG voi nhan heuristic - CHI MANG TINH THAM KHAO vi
    nhan heuristic cung la rule-based, khong phai ground truth tuyet doi."""

    gt_path = os.path.join(folder, "detected_events_unified", GT_EVENTS_CSV_NAME)
    if not os.path.isfile(gt_path):
        return None

    gt_df = pd.read_csv(gt_path, encoding="utf-8-sig")
    if gt_df.empty or not predicted_events:
        return None

    total_overlap = 0.0
    agree_overlap = 0.0

    for pe in predicted_events:
        p_start, p_end = pe["start_time"], pe["end_time"]
        pred_flag_set = frozenset(pe["active_flags"])
        overlaps = gt_df[(gt_df["end_time"] >= p_start) & (gt_df["start_time"] <= p_end)]
        if overlaps.empty:
            continue
        for _, ge in overlaps.iterrows():
            ov_start = max(p_start, float(ge["start_time"]))
            ov_end = min(p_end, float(ge["end_time"]))
            ov_len = max(0.0, ov_end - ov_start)
            if ov_len <= 0:
                continue
            total_overlap += ov_len
            # So sanh TAP CO (da nhan), khong con so string 1 nhan dong-tru:
            # giai ma nhan heuristic (1 trong 33 nhan Step 8) thanh tap co
            # qua LABEL_TO_FLAGS roi so khop CHINH XAC (set equality) voi
            # tap co AI du doan.
            gt_flag_set = frozenset(LABEL_TO_FLAGS.get(str(ge["event_type"]), ()))
            if gt_flag_set == pred_flag_set:
                agree_overlap += ov_len

    if total_overlap <= 0:
        return None

    agreement_pct = 100.0 * agree_overlap / total_overlap
    log(f"  Đối chiếu với nhãn heuristic (Step 8): {agreement_pct:.1f}% thời lượng trùng nhãn "
        f"(tham khảo, không phải ground truth tuyệt đối).")
    return {"agreement_pct": agreement_pct, "overlap_seconds": total_overlap, "gt_events": len(gt_df)}


# ============================================================
# GIAI DOAN 5: BAO CAO VAN BAN (.txt)
# ============================================================

def build_text_report(folder, raw_info, bundle, model_path, label_map_path,
                       inference_cfg, predicted_events, class_distribution,
                       comparison=None, num_windows=0):
    lines = []
    lines.append("=" * 68)
    lines.append("  BÁO CÁO SUY LUẬN AI (AI INFERENCE REPORT) — DriverSafe")
    lines.append("=" * 68)
    lines.append(f"Thời điểm chạy        : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Thư mục dữ liệu thô   : {folder}")
    lines.append(f"  - IMU : {os.path.basename(raw_info['imu_path'])} "
                 f"({raw_info['imu_samples']:,} mẫu, khai báo {raw_info['imu_declared_hz']:.1f} Hz, "
                 f"thực đo {raw_info['imu_actual_hz']:.1f} Hz)")
    lines.append(f"  - GPS : {os.path.basename(raw_info['gps_path'])} "
                 f"({raw_info['gps_samples']:,} mẫu, khai báo {raw_info['gps_declared_hz']:.1f} Hz, "
                 f"thực đo {raw_info['gps_actual_hz']:.1f} Hz)")
    lines.append(f"Mô hình               : {os.path.basename(model_path)}")
    lines.append(f"Label map             : {os.path.basename(label_map_path)} "
                 f"({bundle['num_flags']} cờ đa nhãn, giải mã từ {bundle['num_classes']} nhãn gốc)")
    lines.append(f"Cấu hình suy luận     : window={bundle['window_sec']}s @ {bundle['model_fs']:.0f}Hz, "
                 f"stride={inference_cfg['stride_sec']}s, merge_gap={inference_cfg['merge_gap']}s")
    lines.append("")

    total_dur = sum(e["duration"] for e in predicted_events)
    mean_conf = float(np.mean([e["mean_confidence"] for e in predicted_events])) if predicted_events else 0.0

    lines.append("-" * 68)
    lines.append("TỔNG QUAN")
    lines.append("-" * 68)
    lines.append(f"Tổng số sự kiện dự đoán (đã gộp) : {len(predicted_events)}")
    lines.append(f"Tổng thời lượng bao phủ          : {total_dur:.1f} s")
    lines.append(f"Độ tin cậy trung bình             : {mean_conf*100:.1f}%")
    lines.append("")

    lines.append("-" * 68)
    lines.append("PHÂN BỐ CỜ DỰ ĐOÁN (đa nhãn — % trên tổng số cửa sổ, 1 cửa sổ")
    lines.append("có thể mang NHIỀU cờ cùng lúc nên tổng % có thể vượt 100%)")
    lines.append("-" * 68)
    total_windows = num_windows or 1
    for name, count in sorted(class_distribution.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"  {name:<26} {count:>6}  ({100*count/total_windows:5.1f}%)")
    lines.append("")

    lines.append("-" * 68)
    lines.append(f"DANH SÁCH SỰ KIỆN DỰ ĐOÁN (tổng {len(predicted_events)})")
    lines.append("-" * 68)
    for e in predicted_events:
        lines.append(
            f"  #{e['event_id']:<4} {e['event_type']:<42} "
            f"{e['start_time']:8.2f}s -> {e['end_time']:8.2f}s  "
            f"({e['duration']:6.2f}s)  conf={e['mean_confidence']*100:5.1f}%"
        )
    lines.append("")

    if comparison:
        lines.append("-" * 68)
        lines.append("ĐỐI CHIẾU VỚI NHÃN HEURISTIC (Step 8) — CHỈ MANG TÍNH THAM KHẢO")
        lines.append("-" * 68)
        lines.append(f"Độ khớp theo thời lượng trùng nhãn : {comparison['agreement_pct']:.1f}%")
        lines.append(f"(heuristic cũng là rule-based, không phải ground truth tuyệt đối)")
        lines.append("")

    lines.append("=" * 68)

    return "\n".join(lines)


def export_text_report(folder, report_text):
    out_dir = os.path.join(folder, "detected_events_unified")
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, REPORT_FILE_NAME)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    return report_path


# ============================================================
# ORCHESTRATION
# ============================================================

def run_full_inference(folder, model_path, label_map_path, imu_hz, gps_hz,
                        stride_sec=DEFAULT_STRIDE_SEC, merge_gap=DEFAULT_MERGE_GAP_SEC, log=print):

    log("=" * 60)
    log("[1/4] ĐANG NẠP MÔ HÌNH INT8 & LABEL MAP...")
    log("=" * 60)
    bundle = load_model_bundle(model_path, label_map_path)
    log(f"  Model: timesteps={bundle['timesteps']} | fs={bundle['model_fs']:.0f}Hz | "
        f"window={bundle['window_sec']}s | {bundle['num_flags']} cờ đa nhãn")

    log("\n" + "=" * 60)
    log("[2/4] ĐANG ĐỌC & TÁI TẠO ĐẶC TRƯNG TỪ 2 FILE THÔ...")
    log("=" * 60)
    features_df, raw_info = prepare_inference_features(folder, imu_hz, gps_hz, bundle["model_fs"], log=log)

    log("\n" + "=" * 60)
    log("[3/4] ĐANG SUY LUẬN TRƯỢT CỬA SỔ TRÊN MÔ HÌNH INT8...")
    log("=" * 60)
    records = run_sliding_inference(bundle, features_df, stride_sec, log=log)

    predicted_events = merge_predicted_events(records, merge_gap=merge_gap)
    log(f"  Số sự kiện dự đoán sau khi gộp: {len(predicted_events)}")

    # Phan bo DOC LAP tung co (1 cua so co the dong gop vao NHIEU co cung
    # luc, khac ban don nhan cu moi cua so chi dong gop 1 lop duy nhat).
    class_distribution = {name: 0 for name in bundle["flag_names"]}
    for r in records:
        for name in r["active_flags"]:
            class_distribution[name] = class_distribution.get(name, 0) + 1
    class_distribution = {k: v for k, v in class_distribution.items() if v > 0}

    comparison = compare_with_heuristic(folder, predicted_events, log=log)

    log("\n" + "=" * 60)
    log("[4/4] ĐANG XUẤT BÁO CÁO VĂN BẢN (.txt)...")
    log("=" * 60)
    inference_cfg = {"stride_sec": stride_sec, "merge_gap": merge_gap}
    report_text = build_text_report(
        folder, raw_info, bundle, model_path, label_map_path,
        inference_cfg, predicted_events, class_distribution, comparison,
        num_windows=len(records),
    )
    report_path = export_text_report(folder, report_text)
    log(f"  ✅ Đã xuất báo cáo: {report_path}")

    return {
        "predicted_events": predicted_events,
        "class_distribution": class_distribution,
        "comparison": comparison,
        "report_text": report_text,
        "report_path": report_path,
        "raw_info": raw_info,
        "bundle_meta": {k: v for k, v in bundle.items() if k != "interpreter"},
        "records": records,
        "features_df": features_df,
    }
