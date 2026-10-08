# -*- coding: utf-8 -*-
"""
Module: core/label_schema.py
Chuc nang: NGUON CHAN LY DUY NHAT cho toan bo nhan hanh vi lai xe dung de
           huan luyen / suy luan mo hinh AI (Step 6 - Step 7). Dinh nghia
           ro rang 33 nhan (25 Primary tu Step 8 nhom G10/G20/G40/G80 + 8
           Residual - dat ten tieng Anh theo dung dinh nghia dieu kien,
           khong con dung placeholder "UnknownNN"), khop CHINH XAC voi
           event_type do core/event_detection_engine.py sinh ra trong
           detected_driving_events_unified.csv.

Lich su: ban dau co 9 nhan Residual dat ten "Unknown01".."Unknown09". Da
          doi ten sang tieng Anh mo ta dung dieu kien phat hien (vd
          "Unknown01" -> "MODERATE_DECEL"), VA LOAI BO "Unknown09 /
          Uncovered 10.5-30 km/h Motion": doi chieu lai code thi G20
          (DEFAULT_G20 trong event_detection_engine.py) da bao phu dung
          [10.0, 30.0] km/h - khong co "vung chua bao phu" thuc su tai
          day (tan tich tu 1 phien ban code cu, trung voi 1 loi dead-code
          da sua o _speed_zone_label()). Chi tiet: xem
          docs/hard_braking_scientific_definition.md va comment tai
          DEFAULT_RESIDUAL_LABELS trong event_detection_engine.py.

Thiet ke: File nay CHU Y khong import pandas/numpy/tensorflow - chi dung
          thu vien chuan (json, datetime) de co the import duoc tren moi
          moi truong, ke ca runtime suy luan toi gian tren Raspberry Pi
          (chi can doc file label_map.json da xuat, khong can load lai
          toan bo pipeline huan luyen).

Co so khoa hoc: Closed-set multi-class label space - tap nhan co dinh,
          moi mau du lieu (window) duoc gan DUNG 1 trong 33 nhan nay (cac
          hanh vi phoi hop nhu "thang + danh lai" da duoc gop thanh 1 nhan
          hop nhat vd BRAKE_TURN_40, khong can multi-label dong thoi).
"""

import json
from datetime import datetime, timezone

GROUP_MOTION_PRIMARY = "HEURISTIC_PRIMARY"
GROUP_RESIDUAL_MOTION = "RESIDUAL_HEURISTIC"
GROUP_DATA_QUALITY = "DATA_QUALITY"

# ============================================================
# 25 NHAN PRIMARY (Step 8: detect_10kmh / 20 / 40 / 80)
# ============================================================

_PRIMARY_LABELS = [
    # --- Nhom ~10 km/h (7 nhan) ---
    ("STOP_RED_LIGHT", "Dừng / Có thể dừng đèn đỏ", "G10"),
    ("HARD_BRAKE", "Thắng gấp (<10 km/h)", "G10"),
    ("HARD_BRAKE_STEERING", "Thắng gấp + Đánh lái (<10 km/h)", "G10"),
    ("SUDDEN_ACCEL", "Giật ga đột ngột (<10 km/h)", "G10"),
    ("SUDDEN_ACCEL_STEERING", "Giật ga + Đánh lái (<10 km/h)", "G10"),
    ("NORMAL_STEERING", "Bình thường + Đánh lái (<10 km/h)", "G10"),
    ("NORMAL", "Đi bình thường (<10 km/h)", "G10"),

    # --- Nhom ~20 km/h, 10-30 km/h (6 nhan) ---
    ("NORMAL_20", "Đi bình thường (~20 km/h)", "G20"),
    ("BRAKE_20", "Thắng gấp (~20 km/h)", "G20"),
    ("ACCEL_20", "Giật ga (~20 km/h)", "G20"),
    ("TURN_20", "Rẽ bình thường (~20 km/h)", "G20"),
    ("ACCEL_TURN_20", "Giật ga + Đánh lái (~20 km/h)", "G20"),
    ("BRAKE_TURN_20", "Thắng gấp + Đánh lái (~20 km/h)", "G20"),

    # --- Nhom ~40 km/h, 30-50 km/h (6 nhan) ---
    ("NORMAL_40", "Đi bình thường (~40 km/h)", "G40"),
    ("BRAKE_40", "Thắng gấp (~40 km/h)", "G40"),
    ("ACCEL_40", "Giật ga (~40 km/h)", "G40"),
    ("TURN_40", "Rẽ bình thường (~40 km/h)", "G40"),
    ("ACCEL_TURN_40", "Giật ga + Đánh lái (~40 km/h)", "G40"),
    ("BRAKE_TURN_40", "Thắng gấp + Đánh lái (~40 km/h)", "G40"),

    # --- Nhom ~80 km/h, 50-80 km/h (6 nhan) ---
    ("NORMAL_80", "Đi bình thường (~80 km/h)", "G80"),
    ("BRAKE_80", "Thắng gấp (~80 km/h)", "G80"),
    ("ACCEL_80", "Giật ga (~80 km/h)", "G80"),
    ("TURN_80", "Rẽ bình thường (~80 km/h)", "G80"),
    ("ACCEL_TURN_80", "Giật ga + Đánh lái (~80 km/h)", "G80"),
    ("BRAKE_TURN_80", "Thắng gấp + Đánh lái (~80 km/h)", "G80"),
]

# ============================================================
# 8 NHAN RESIDUAL (Step 8: detect_unknown) - dat ten tieng Anh theo
# DUNG dieu kien phat hien (xem DEFAULT_RESIDUAL_LABELS trong
# event_detection_engine.py). Chi xet tren timeline CHUA duoc 25 nhan
# Primary bao phu. Da bo "Unknown09 / Uncovered 10.5-30km/h" - xem
# ghi chu lich su o dau file.
# ============================================================

_RESIDUAL_LABELS = [
    ("MODERATE_DECEL", "Giảm tốc vừa phải (chưa xác nhận ngữ nghĩa)", GROUP_RESIDUAL_MOTION),
    ("MODERATE_ACCEL", "Tăng tốc vừa phải (chưa xác nhận ngữ nghĩa)", GROUP_RESIDUAL_MOTION),
    ("STEERING_ANOMALY", "Đánh lái ngoài trạng thái bình thường", GROUP_RESIDUAL_MOTION),
    ("MODERATE_BRAKE_STEER", "Thắng vừa phải + Đánh lái (chưa xác nhận)", GROUP_RESIDUAL_MOTION),
    ("MODERATE_ACCEL_STEER", "Tăng tốc vừa phải + Đánh lái (chưa xác nhận)", GROUP_RESIDUAL_MOTION),
    ("JERK_TRANSIENT", "Độ giật (Jerk) chiếm ưu thế - chuyển tiếp ngắn", GROUP_RESIDUAL_MOTION),
    ("SPEED_BOUNDARY_TRANSITION", "Chuyển tiếp qua vùng ranh giới tốc độ", GROUP_RESIDUAL_MOTION),
    ("IMU_GPS_MISMATCH", "Bất nhất IMU-GPS (vấn đề chất lượng dữ liệu)", GROUP_DATA_QUALITY),
]


def _build_definitions():
    defs = []
    idx = 0
    for name, display_name, group in _PRIMARY_LABELS:
        defs.append({
            "id": idx, "name": name, "display_name": display_name,
            "group": group, "label_source": GROUP_MOTION_PRIMARY,
        })
        idx += 1
    for name, display_name, group in _RESIDUAL_LABELS:
        defs.append({
            "id": idx, "name": name, "display_name": display_name,
            "group": group, "label_source": group,
        })
        idx += 1
    return defs


# Danh sach day du, THU TU CO DINH = chinh la class_id dung de huan luyen.
# KHONG duoc doi thu tu sau khi da co model/.tflite dang dung, neu can
# them nhan moi hay them vao CUOI danh sach de khong lam lech id cu.
LABEL_DEFINITIONS = _build_definitions()

LABEL_NAMES = [d["name"] for d in LABEL_DEFINITIONS]
NUM_CLASSES = len(LABEL_NAMES)

NAME_TO_ID = {d["name"]: d["id"] for d in LABEL_DEFINITIONS}
ID_TO_NAME = {d["id"]: d["name"] for d in LABEL_DEFINITIONS}
ID_TO_DISPLAY_NAME = {d["id"]: d["display_name"] for d in LABEL_DEFINITIONS}

# Nhung event_type KHONG thuoc tap 33 nhan nay (vd do loi du lieu / phien
# ban detector khac) se duoc xem la "khong xac dinh" khi xay dung window -
# cac ham o core/ai_pipeline.py se loai bo thay vi gan nham class.


def label_to_id(name):
    """Tra ve class_id (int) cho 1 ten nhan, hoac None neu khong thuoc
    tap 33 nhan da dinh nghia (vd nhan la hoc tu phien ban detector cu)."""
    return NAME_TO_ID.get(name)


def id_to_label(class_id):
    return ID_TO_NAME.get(int(class_id))


# ============================================================
# GOM NHOM NORMAL (CHI DUNG CHO TINH PURITY LUC CAT CUA SO)
#
# NORMAL / NORMAL_20 / NORMAL_40 / NORMAL_80 van la 4 nhan RIENG trong
# LABEL_NAMES/Step 8 (KHONG doi CSV/GUI) - bang nay CHI anh huong buoc
# tinh purity trong core/ai_pipeline.py::build_labeled_windows(): 1 cua
# so di qua ranh gioi toc do nhung toan la "NORMAL ho" (vd NORMAL_20 ->
# NORMAL_40) se duoc GOM CHUNG 1 nhom khi tinh purity, thay vi bi loai vi
# lech giua 2 class_id khac nhau nhu truoc - giam phan manh/mat mau o
# bien chuyen tiep toc do, KHONG lam mat chi tiet toc do thuc te cua
# window (mode_label cuoi cung van la 1 trong 4 nhan goc, chon theo da so
# CON LAI trong nhom sau khi da xac dinh nhom nay thang purity).
#
# PURITY_GROUP_ID[class_id] = class_id cua chinh no (mac dinh, khong gop)
# HOAC class_id cua "NORMAL" neu class_id do thuoc _PURITY_MERGE_NAMES.
# ============================================================

_PURITY_MERGE_NAMES = ("NORMAL", "NORMAL_20", "NORMAL_40", "NORMAL_80")
_PURITY_MERGE_CANONICAL_ID = NAME_TO_ID["NORMAL"]

PURITY_GROUP_ID = [
    _PURITY_MERGE_CANONICAL_ID if name in _PURITY_MERGE_NAMES else idx
    for idx, name in enumerate(LABEL_NAMES)
]


# ============================================================
# PHAN RA (DECOMPOSE) 33 NHAN THANH CO DOC LAP (MULTI-LABEL)
#
# Van giu nguyen 33 nhan/Step 8/event_detection_engine.py KHONG DOI - day
# CHI la 1 bang anh xa THEM (khong thay the LABEL_DEFINITIONS o tren) de
# cho phep huan luyen mo hinh da nhan thuc su (sigmoid doc lap tung co,
# 1 cua so co the mang NHIEU co dong thoi), thay vi softmax loai tru nhau.
#
# 4 co SPEED_* ve ban chat van loai tru nhau (1 cua so chi o 1 dai toc do),
# nhung duoc hoc nhu sigmoid doc lap (khong ep tong = 1) - mo hinh co the
# du doan 0 hoac >1 co toc do dong thoi, day la danh doi co chu dich khi
# chuyen sang da nhan, khong phai loi.
#
# 8 nhan Residual (RESIDUAL_HEURISTIC) KHONG duoc gan co SPEED_* (chua xac
# nhan dai toc do ro rang theo thiet ke goc cua cac nhan nay).
# ============================================================

FLAG_NAMES = [
    "SPEED_G10", "SPEED_G20", "SPEED_G40", "SPEED_G80",
    "NORMAL", "STOP",
    "BRAKE_HARD", "BRAKE_MODERATE",
    "ACCEL_HARD", "ACCEL_MODERATE",
    "STEERING", "STEERING_ANOMALY",
    "JERK_TRANSIENT", "SPEED_TRANSITION", "DATA_QUALITY_ISSUE",
]
NUM_FLAGS = len(FLAG_NAMES)
FLAG_TO_INDEX = {name: i for i, name in enumerate(FLAG_NAMES)}

LABEL_TO_FLAGS = {
    # --- Nhom ~10 km/h ---
    "STOP_RED_LIGHT": ("SPEED_G10", "STOP"),
    "HARD_BRAKE": ("SPEED_G10", "BRAKE_HARD"),
    "HARD_BRAKE_STEERING": ("SPEED_G10", "BRAKE_HARD", "STEERING"),
    "SUDDEN_ACCEL": ("SPEED_G10", "ACCEL_HARD"),
    "SUDDEN_ACCEL_STEERING": ("SPEED_G10", "ACCEL_HARD", "STEERING"),
    "NORMAL_STEERING": ("SPEED_G10", "NORMAL", "STEERING"),
    "NORMAL": ("SPEED_G10", "NORMAL"),

    # --- Nhom ~20 km/h ---
    "NORMAL_20": ("SPEED_G20", "NORMAL"),
    "BRAKE_20": ("SPEED_G20", "BRAKE_HARD"),
    "ACCEL_20": ("SPEED_G20", "ACCEL_HARD"),
    "TURN_20": ("SPEED_G20", "NORMAL", "STEERING"),
    "ACCEL_TURN_20": ("SPEED_G20", "ACCEL_HARD", "STEERING"),
    "BRAKE_TURN_20": ("SPEED_G20", "BRAKE_HARD", "STEERING"),

    # --- Nhom ~40 km/h ---
    "NORMAL_40": ("SPEED_G40", "NORMAL"),
    "BRAKE_40": ("SPEED_G40", "BRAKE_HARD"),
    "ACCEL_40": ("SPEED_G40", "ACCEL_HARD"),
    "TURN_40": ("SPEED_G40", "NORMAL", "STEERING"),
    "ACCEL_TURN_40": ("SPEED_G40", "ACCEL_HARD", "STEERING"),
    "BRAKE_TURN_40": ("SPEED_G40", "BRAKE_HARD", "STEERING"),

    # --- Nhom ~80 km/h ---
    "NORMAL_80": ("SPEED_G80", "NORMAL"),
    "BRAKE_80": ("SPEED_G80", "BRAKE_HARD"),
    "ACCEL_80": ("SPEED_G80", "ACCEL_HARD"),
    "TURN_80": ("SPEED_G80", "NORMAL", "STEERING"),
    "ACCEL_TURN_80": ("SPEED_G80", "ACCEL_HARD", "STEERING"),
    "BRAKE_TURN_80": ("SPEED_G80", "BRAKE_HARD", "STEERING"),

    # --- Residual (khong gan co SPEED_*) ---
    "MODERATE_DECEL": ("BRAKE_MODERATE",),
    "MODERATE_ACCEL": ("ACCEL_MODERATE",),
    "STEERING_ANOMALY": ("STEERING", "STEERING_ANOMALY"),
    "MODERATE_BRAKE_STEER": ("BRAKE_MODERATE", "STEERING"),
    "MODERATE_ACCEL_STEER": ("ACCEL_MODERATE", "STEERING"),
    "JERK_TRANSIENT": ("JERK_TRANSIENT",),
    "SPEED_BOUNDARY_TRANSITION": ("SPEED_TRANSITION",),
    "IMU_GPS_MISMATCH": ("DATA_QUALITY_ISSUE",),
}

_missing = [n for n in LABEL_NAMES if n not in LABEL_TO_FLAGS]
if _missing:
    raise RuntimeError(
        f"LABEL_TO_FLAGS thieu anh xa cho {len(_missing)} nhan: {_missing} "
        "- moi nhan trong LABEL_NAMES PHAI co 1 entry trong LABEL_TO_FLAGS."
    )


def _build_flag_matrix():
    matrix = []
    for d in LABEL_DEFINITIONS:
        row = [0] * NUM_FLAGS
        for flag_name in LABEL_TO_FLAGS[d["name"]]:
            row[FLAG_TO_INDEX[flag_name]] = 1
        matrix.append(row)
    return matrix


# LABEL_FLAG_MATRIX[class_id] = vector 0/1 do dai NUM_FLAGS (list[int],
# KHONG dung numpy - file nay phai import duoc tren runtime Pi toi gian).
LABEL_FLAG_MATRIX = _build_flag_matrix()


def label_id_to_flag_vector(class_id):
    """Tra ve BAN SAO vector co (list[int], do dai NUM_FLAGS) cho 1 class_id
    (0..32) trong tap 33 nhan dong-tru. Dung khi xay dung target y da nhan
    cho huan luyen (xem core/ai_pipeline.py::build_labeled_windows)."""
    return list(LABEL_FLAG_MATRIX[int(class_id)])


def export_label_map(path, extra_meta=None):
    """Xuat file JSON dinh nghia nhan RO RANG, dung kem voi model .tflite
    khi trien khai tren Raspberry Pi (chi can doc file nay de biet class_id
    -> ten nhan, khong can import core.label_schema neu muon runtime toi
    gian tren thiet bi nhung)."""

    payload = {
        "schema": "driversafe_label_map_v3",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "num_classes": NUM_CLASSES,
        "labels": LABEL_DEFINITIONS,
        "num_flags": NUM_FLAGS,
        "flag_names": FLAG_NAMES,
        "label_to_flags": {name: list(flags) for name, flags in LABEL_TO_FLAGS.items()},
    }
    if extra_meta:
        payload["meta"] = extra_meta

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path
