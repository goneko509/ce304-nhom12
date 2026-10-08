# -*- coding: utf-8 -*-
"""
Module: core/ai_pipeline.py
Pipeline SOTA (Step 6): Labeled Windowing -> Train Multi-Label Classifier
                        (NUM_FLAGS co doc lap, giai ma tu NUM_CLASSES nhan
                        dong-tru goc - xem core/label_schema.py, du lieu
                        chuoi thoi gian IMU+GPS) ->
                        INT8 Quantization (TFLite, trien khai Raspberry Pi)

Nguon nhan: detected_driving_events_unified.csv + merged_motion_features.csv
            (xuat tu Step 8 - core/event_detection_engine.py). Dinh nghia
            nhan RO RANG, co dinh: xem core/label_schema.py.

Day la module LOI dung chung, duoc Step 6 (steps/step6_model_training.py)
goi truc tiep tren GUI. Van giu kha nang chay doc lap qua dong lenh:
    python -m core.ai_pipeline <folder_detected_events_unified>
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.keras import layers, models, regularizers

from core.sensor_schema import add_trig_features, EVENT_CLASSIFIER_FEATURES
from core.label_schema import (
    LABEL_NAMES, NUM_CLASSES, NAME_TO_ID, export_label_map,
    FLAG_NAMES, NUM_FLAGS, label_id_to_flag_vector, PURITY_GROUP_ID,
)

_PURITY_GROUP_ID_ARR = np.asarray(PURITY_GROUP_ID, dtype=np.int64)
from core import model_audit

# ==========================================
# CAU HINH PIPELINE MAC DINH
# ==========================================
FEATURES_CSV_NAME = "merged_motion_features.csv"
EVENTS_CSV_NAME = "detected_driving_events_unified.csv"
MODEL_BASE_NAME = "driversafe_event_classifier"
TFLITE_OUTPUT_NAME = f"{MODEL_BASE_NAME}_int8.tflite"
LABEL_MAP_OUTPUT_NAME = "label_map.json"
TRAINING_REPORT_NAME = "training_report.json"
AUDIT_REPORT_NAME = "model_audit_report.txt"

FS_IMU = 100.0
WINDOW_SEC = 2.0
STRIDE_SEC = 0.3
GAP_SECONDS = 1.0           # cat doan khi dut gay tin hieu > 1s (giong Step 8)
PURITY_THRESHOLD = 0.8      # % mau trong window phai cung 1 nhan moi giu lai
TRAIN_RATIO = 0.70
VAL_RATIO = 0.15            # phan con lai (0.15) la Test
RANDOM_SEED = 42


# ==========================================
# GIAI DOAN 0: DOC DU LIEU
# ==========================================

def load_training_frames(folder):
    """Doc merged_motion_features.csv (chuoi thoi gian) +
    detected_driving_events_unified.csv (nhan theo doan) tu thu muc
    detected_events_unified/ (xuat boi Step 8)."""

    features_path = os.path.join(folder, FEATURES_CSV_NAME)
    events_path = os.path.join(folder, EVENTS_CSV_NAME)

    if not os.path.isfile(features_path):
        raise FileNotFoundError(f"Không tìm thấy {FEATURES_CSV_NAME} trong:\n{folder}")
    if not os.path.isfile(events_path):
        raise FileNotFoundError(f"Không tìm thấy {EVENTS_CSV_NAME} trong:\n{folder}")

    features_df = pd.read_csv(features_path, encoding="utf-8-sig")
    events_df = pd.read_csv(events_path, encoding="utf-8-sig")

    features_df = add_trig_features(features_df, angles=["yaw", "pitch", "roll"])
    features_df = features_df.sort_values("time").reset_index(drop=True)

    missing = [c for c in EVENT_CLASSIFIER_FEATURES if c not in features_df.columns]
    if missing:
        raise ValueError(f"{FEATURES_CSV_NAME} thiếu các cột đặc trưng: {missing}")

    return features_df, events_df


# ==========================================
# GIAI DOAN 1: XAY DUNG CUA SO CO NHAN (LABELED WINDOWING)
# ==========================================

def build_labeled_windows(features_df, events_df, timesteps, stride_samples,
                           purity_threshold=PURITY_THRESHOLD,
                           feature_columns=EVENT_CLASSIFIER_FEATURES,
                           gap_seconds=GAP_SECONDS, log=print):
    """
    Gan nhan event_type (theo khoang [start_time, end_time] trong
    detected_driving_events_unified.csv) cho tung mau trong chuoi thoi
    gian, sau do truot cua so co do dai co dinh (timesteps) va CHI GIU
    LAI cua so co ty le "mau cung 1 nhan" >= purity_threshold (SOTA
    practice: tranh nhan nhieu/mo ho tai bien doan su kien).

    Tra ve:
        X          : (N, timesteps, len(feature_columns)) float32
        y          : (N, NUM_FLAGS) float32 - vector co da nhan DOC LAP
                     (xem core/label_schema.py::label_id_to_flag_vector),
                     giai ma tu class_id (dong-tru) "chiem da so" trong
                     cua so - BAN THAN viec chon class_id/purity/event_id
                     van dua tren 33 nhan dong-tru Step 8 nhu cu, CHI co
                     buoc MA HOA target cuoi cung la doi (33 lop -> 15 co).
        event_ids  : (N,) int64 - event_id goc (dung de chia Train/Val/
                     Test theo NHOM, tranh leak giua cac window gan nhau
                     cua CUNG 1 su kien)
        stats      : dict thong ke (so window giu/bo, phan bo lop theo
                     33 nhan goc...)
    """

    n = len(features_df)
    time_arr = features_df["time"].to_numpy(dtype=float)

    sample_label_id = np.full(n, -1, dtype=np.int64)
    sample_event_id = np.full(n, -1, dtype=np.int64)

    unmapped_types = set()
    for _, ev in events_df.iterrows():
        cid = NAME_TO_ID.get(str(ev["event_type"]))
        if cid is None:
            unmapped_types.add(str(ev["event_type"]))
            continue
        mask = (time_arr >= float(ev["start_time"])) & (time_arr <= float(ev["end_time"]))
        sample_label_id[mask] = cid
        sample_event_id[mask] = int(ev["event_id"])

    if unmapped_types:
        log(f"  ⚠️ Bỏ qua {len(unmapped_types)} event_type không khớp core/label_schema.py: "
            f"{sorted(unmapped_types)}")

    # Danh gioi doan lien tuc (gap-aware), giong logic Step 8
    dt = np.diff(time_arr, prepend=time_arr[0] if n > 0 else 0.0)
    segment_id = np.cumsum(dt > gap_seconds)

    feat_matrix = features_df[feature_columns].to_numpy(dtype=np.float32)
    # Noi suy / lap day NaN con sot (bien hoac cam bien mat mau thoang qua)
    feat_df_filled = pd.DataFrame(feat_matrix, columns=feature_columns)
    feat_df_filled = feat_df_filled.interpolate(limit_direction="both").bfill().ffill()
    feat_matrix = feat_df_filled.to_numpy(dtype=np.float32)

    X_list, y_list, eid_list, mode_label_list = [], [], [], []
    n_total_windows = 0
    n_kept = 0
    n_dropped_impure = 0
    n_dropped_unlabeled = 0

    unique_segments = np.unique(segment_id)
    for seg in unique_segments:
        idx = np.where(segment_id == seg)[0]
        seg_start, seg_end = idx[0], idx[-1] + 1
        seg_len = seg_end - seg_start
        if seg_len < timesteps:
            continue

        for start in range(seg_start, seg_end - timesteps + 1, stride_samples):
            end = start + timesteps
            n_total_windows += 1

            win_labels = sample_label_id[start:end]
            valid = win_labels[win_labels >= 0]

            if len(valid) == 0:
                n_dropped_unlabeled += 1
                continue

            # Tinh purity theo NHOM (xem core/label_schema.py::PURITY_GROUP_ID) -
            # NORMAL/NORMAL_20/NORMAL_40/NORMAL_80 duoc gop CHUNG 1 nhom o buoc
            # nay, tranh loai window chi vi lech giua 2 "NORMAL ho" khac toc do.
            valid_groups = _PURITY_GROUP_ID_ARR[valid]
            group_counts = np.bincount(valid_groups, minlength=NUM_CLASSES)
            dominant_group = int(np.argmax(group_counts))
            purity = group_counts[dominant_group] / float(timesteps)

            if purity < purity_threshold:
                n_dropped_impure += 1
                continue

            # Trong nhom da thang purity, chon mode_label la nhan GOC (trong
            # 33 nhan) chiem da so THAT SU - giu nguyen chi tiet toc do cua
            # window, khong ap dat 1 nhan dai dien co dinh cho ca nhom.
            sub_valid = valid[valid_groups == dominant_group]
            mode_label = int(np.argmax(np.bincount(sub_valid, minlength=NUM_CLASSES)))

            win_events = sample_event_id[start:end]
            candidates = win_events[(win_labels == mode_label) & (win_events >= 0)]
            window_event_id = int(candidates[len(candidates) // 2]) if len(candidates) else -1

            X_list.append(feat_matrix[start:end, :])
            mode_label_list.append(mode_label)
            y_list.append(label_id_to_flag_vector(mode_label))
            eid_list.append(window_event_id)
            n_kept += 1

    if n_kept == 0:
        raise ValueError(
            "Không tạo được window nào đạt ngưỡng purity. Hãy giảm 'Purity Threshold' "
            "hoặc kiểm tra lại detected_driving_events_unified.csv."
        )

    X = np.stack(X_list, axis=0).astype(np.float32)
    y = np.asarray(y_list, dtype=np.float32)
    event_ids = np.asarray(eid_list, dtype=np.int64)
    mode_labels = np.asarray(mode_label_list, dtype=np.int64)

    class_counts = {LABEL_NAMES[c]: int((mode_labels == c).sum())
                     for c in range(NUM_CLASSES) if (mode_labels == c).sum() > 0}

    stats = {
        "total_candidate_windows": n_total_windows,
        "kept": n_kept,
        "dropped_unlabeled_gap": n_dropped_unlabeled,
        "dropped_impure": n_dropped_impure,
        "num_classes_present": len(class_counts),
        "class_counts": class_counts,
    }

    log(f"  Tổng cửa sổ khả dụng : {n_total_windows}")
    log(f"  Giữ lại (đạt purity) : {n_kept}")
    log(f"  Bỏ (rơi vào khoảng trống chưa gán nhãn) : {n_dropped_unlabeled}")
    log(f"  Bỏ (không đạt ngưỡng purity={purity_threshold:.2f}) : {n_dropped_impure}")
    log(f"  Số nhãn thực tế xuất hiện trong dữ liệu: {len(class_counts)}/{NUM_CLASSES}")

    return X, y, event_ids, stats


# ==========================================
# GIAI DOAN 2: CHIA TRAIN / VAL / TEST THEO NHOM EVENT (CHONG LEAK)
# ==========================================

def split_by_event_group(event_ids, train_ratio=TRAIN_RATIO, val_ratio=VAL_RATIO, seed=RANDOM_SEED):
    """Chia theo NHOM event_id (khong theo tung window rieng le) de cac
    window gan nhau/chong lap cua CUNG 1 su kien khong bi leak qua nhieu
    tap - SOTA practice bat buoc cho du lieu sliding-window."""

    rng = np.random.RandomState(seed)
    unique_events = np.unique(event_ids)
    rng.shuffle(unique_events)

    n = len(unique_events)
    n_train = max(1, int(round(n * train_ratio)))
    n_val = max(0, int(round(n * val_ratio)))
    n_train = min(n_train, n)
    n_val = min(n_val, n - n_train)

    train_events = set(unique_events[:n_train].tolist())
    val_events = set(unique_events[n_train:n_train + n_val].tolist())
    test_events = set(unique_events[n_train + n_val:].tolist())

    train_mask = np.isin(event_ids, list(train_events))
    val_mask = np.isin(event_ids, list(val_events))
    test_mask = np.isin(event_ids, list(test_events))

    return train_mask, val_mask, test_mask


def compute_flag_pos_weights(y_multihot, max_weight=50.0):
    """Trong so duong (pos_weight) cho TUNG CO DOC LAP, dung trong weighted
    binary crossentropy (xem train_classifier) de giam thien vi khi 1 co
    qua hiem (vd DATA_QUALITY_ISSUE). pos_weight[f] = so_mau_am/so_mau_duong
    cua co f, gioi han boi max_weight de tranh loss no voi co cuc hiem."""

    y_multihot = np.asarray(y_multihot, dtype=np.float32)
    n = len(y_multihot)
    pos = y_multihot.sum(axis=0)
    neg = n - pos
    pos_weight = np.where(pos > 0, neg / np.maximum(pos, 1.0), 1.0)
    pos_weight = np.clip(pos_weight, 1.0, max_weight)
    return pos_weight.astype(np.float32)


# ==========================================
# GIAI DOAN 3: KIEN TRUC MO HINH (Multi-scale Conv1D + SE + Residual)
# SOTA cho HAR/time-series tren Edge Device: toan bo la Conv1D/Dense/
# GlobalAveragePooling/Add/Multiply - deu nam trong tap builtin ops ma
# TFLite ho tro luong tu hoa INT8 toan phan (da kiem chung qua Mobile-
# Net/Inception INT8), tranh LSTM/GRU/Attention (kho/khong on dinh khi
# quantize INT8 tren TFLite runtime cho microcontroller/Raspberry Pi).
# ==========================================

def _conv_bn_act(x, filters, kernel_size, l2_reg, strides=1):
    x = layers.Conv1D(
        filters, kernel_size, strides=strides, padding="same", use_bias=False,
        kernel_regularizer=regularizers.l2(l2_reg)
    )(x)
    x = layers.BatchNormalization()(x)
    return layers.ReLU()(x)


def _se_block(x, reduction=8):
    channels = x.shape[-1]
    se = layers.GlobalAveragePooling1D()(x)
    se = layers.Dense(max(channels // reduction, 4), activation="relu")(se)
    se = layers.Dense(channels, activation="sigmoid")(se)
    se = layers.Reshape((1, channels))(se)
    return layers.Multiply()([x, se])


def build_classifier_model(timesteps, num_features, num_outputs=NUM_FLAGS,
                            dropout=0.3, l2_reg=1e-4):
    inputs = layers.Input(shape=(timesteps, num_features))

    # Stem: bat dac trung tan so cao (rung dong ngan)
    x = _conv_bn_act(inputs, 32, 7, l2_reg)
    x = layers.MaxPooling1D(2)(x)

    # Multi-scale block (Inception-lite): nhieu kernel_size song song de
    # bat ca bien dong ngan han (phanh/giat ga) va xu huong dai han (re)
    b1 = _conv_bn_act(x, 32, 3, l2_reg)
    b2 = _conv_bn_act(x, 32, 5, l2_reg)
    b3 = _conv_bn_act(x, 32, 9, l2_reg)
    x = layers.Concatenate()([b1, b2, b3])
    x = _se_block(x)
    x = layers.MaxPooling1D(2)(x)

    # Residual block de on dinh gradient khi mang sau hon
    shortcut = x
    r = _conv_bn_act(x, 96, 3, l2_reg)
    r = layers.Conv1D(96, 3, padding="same", use_bias=False,
                       kernel_regularizer=regularizers.l2(l2_reg))(r)
    r = layers.BatchNormalization()(r)
    if shortcut.shape[-1] != 96:
        shortcut = layers.Conv1D(96, 1, padding="same")(shortcut)
        shortcut = layers.BatchNormalization()(shortcut)
    x = layers.Add()([shortcut, r])
    x = layers.ReLU()(x)
    x = _se_block(x)

    x = layers.GlobalAveragePooling1D()(x)
    x = layers.Dropout(dropout)(x)
    x = layers.Dense(64, activation="relu", kernel_regularizer=regularizers.l2(l2_reg))(x)
    x = layers.Dropout(dropout)(x)
    # Sigmoid DOC LAP tung co (da nhan thuc su) - KHONG ep tong = 1 nhu
    # softmax, cho phep 1 cua so mang nhieu co dong thoi (vd vua BRAKE_HARD
    # vua STEERING).
    outputs = layers.Dense(num_outputs, activation="sigmoid")(x)

    return models.Model(inputs=inputs, outputs=outputs, name="driversafe_event_classifier")


# ==========================================
# GIAI DOAN 4: HUAN LUYEN
# ==========================================

def _make_weighted_binary_crossentropy(pos_weight):
    """BCE voi trong so duong RIENG TUNG CO (xem compute_flag_pos_weights).
    Loss nay CHI dung luc train - khong nam trong concrete function duoc
    luu/lượng tu hoa (xem quantize_to_int8_tflite/convert_tflite_fp32, chi
    build tu model(inputs, training=False)), nen khong anh huong TFLite."""

    pos_weight_t = tf.constant(pos_weight, dtype=tf.float32)

    def loss_fn(y_true, y_pred):
        eps = 1e-7
        y_pred = tf.clip_by_value(y_pred, eps, 1.0 - eps)
        loss = -(pos_weight_t * y_true * tf.math.log(y_pred)
                  + (1.0 - y_true) * tf.math.log(1.0 - y_pred))
        return tf.reduce_mean(loss)

    return loss_fn


def train_classifier(model, X_train, y_train, X_val, y_val, pos_weight,
                      epochs=30, batch_size=64, learning_rate=1e-3, log=print):

    has_val = X_val is not None and len(X_val) > 0
    monitor = "val_loss" if has_val else "loss"

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate),
        loss=_make_weighted_binary_crossentropy(pos_weight),
        metrics=[tf.keras.metrics.BinaryAccuracy(name="accuracy", threshold=0.5)],
    )

    class _LogCallback(tf.keras.callbacks.Callback):
        def on_epoch_end(self, epoch, logs=None):
            logs = logs or {}
            msg = f"  Epoch {epoch + 1}/{epochs}: loss={logs.get('loss', 0):.4f} acc={logs.get('accuracy', 0):.4f}"
            if has_val:
                msg += f"  val_loss={logs.get('val_loss', 0):.4f} val_acc={logs.get('val_accuracy', 0):.4f}"
            log(msg)

    callbacks = [
        tf.keras.callbacks.EarlyStopping(monitor=monitor, patience=6, restore_best_weights=True),
        tf.keras.callbacks.ReduceLROnPlateau(monitor=monitor, factor=0.5, patience=3, min_lr=1e-6),
        _LogCallback(),
    ]

    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val) if has_val else None,
        epochs=epochs, batch_size=batch_size,
        callbacks=callbacks, verbose=0,
    )
    return history


# ==========================================
# GIAI DOAN 5: DANH GIA (da nhan - dung chung cong thuc voi model_audit.py)
# ==========================================

def evaluate_classifier(model, X, y, flag_names=FLAG_NAMES, log=print):
    """Tom tat nhanh sau khi train (khong thay the Chuong trinh Kiem dinh
    3 Tang - xem model_audit.run_three_tier_audit). y la (N, NUM_FLAGS)
    multi-hot. Dung chung cong thuc tinh toan voi model_audit.py (DRY) -
    chi khac phan trinh bay/log."""

    if X is None or len(X) == 0:
        log("  (Bỏ qua - không có mẫu nào trong tập này)")
        return {}

    probs = model.predict(X, verbose=0)
    y_pred, per_flag, global_m = model_audit.compute_multilabel_metrics(y, probs, flag_names)

    log(f"  Exact Match Ratio (khớp toàn bộ {len(flag_names)} cờ): "
        f"{global_m['exact_match_ratio'] * 100:.2f}%  |  "
        f"Macro-F1: {global_m['macro_f1'] * 100:.2f}%  |  "
        f"Micro-F1: {global_m['micro_f1'] * 100:.2f}%")
    for name, m in per_flag.items():
        if m["support"] > 0:
            log(f"    {name:<20} P={m['precision']*100:5.1f}%  R={m['recall']*100:5.1f}%  "
                f"F1={m['f1']*100:5.1f}%  n={m['support']}")

    return {
        "exact_match_ratio": global_m["exact_match_ratio"],
        "macro_f1": global_m["macro_f1"], "micro_f1": global_m["micro_f1"],
        "per_flag": per_flag,
    }


# ==========================================
# GIAI DOAN 6: LUONG TU HOA INT8 (TFLITE) - GIU NGUYEN CO CHE DA KIEM
# CHUNG (concrete function + representative dataset + full-integer INT8)
# ==========================================

def quantize_to_int8_tflite(model, X_calibration, output_tflite_path, timesteps,
                             num_features, log=print):
    log("  Đang lượng tử hóa INT8 (full-integer, cho Raspberry Pi)...")

    input_spec = tf.TensorSpec(shape=[1, timesteps, num_features], dtype=tf.float32)

    @tf.function(input_signature=[input_spec])
    def inference_func(inputs):
        return model(inputs, training=False)

    concrete_func = inference_func.get_concrete_function(input_spec)

    converter = tf.lite.TFLiteConverter.from_concrete_functions([concrete_func])
    converter.optimizations = [tf.lite.Optimize.DEFAULT]

    def representative_dataset_gen():
        n_calib = min(300, len(X_calibration))
        indices = np.random.choice(len(X_calibration), size=n_calib, replace=False)
        for i in indices:
            sample = X_calibration[i:i + 1].astype(np.float32)
            yield [sample]

    converter.representative_dataset = representative_dataset_gen
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8

    tflite_quant_model = converter.convert()

    with open(output_tflite_path, "wb") as f:
        f.write(tflite_quant_model)

    original_size_kb = model.count_params() * 4 / 1024
    quantized_size_kb = os.path.getsize(output_tflite_path) / 1024

    log(f"  ✅ Đã xuất mô hình TFLite INT8: {output_tflite_path}")
    log(f"  📉 Dung lượng: Float32 ~{original_size_kb:.1f} KB ➡️ INT8 {quantized_size_kb:.1f} KB "
        f"(giảm {100 * (1 - quantized_size_kb / max(original_size_kb, 1e-9)):.1f}%)")

    return original_size_kb, quantized_size_kb


# ==========================================
# ORCHESTRATION: TOAN BO PIPELINE (goi tu GUI Step 6 hoac CLI)
# ==========================================

def run_full_training_pipeline(folder, params, log=print):
    """
    params (dict):
        fs, window_sec, stride_sec, purity_threshold,
        train_ratio, val_ratio, epochs, batch_size, learning_rate
    Tra ve dict tom tat (dung cho GUI hien thi).
    """

    timesteps = max(2, int(round(params["fs"] * params["window_sec"])))
    stride_samples = max(1, int(round(params["fs"] * params["stride_sec"])))
    num_features = len(EVENT_CLASSIFIER_FEATURES)

    log("=" * 60)
    log("[1/7] ĐANG ĐỌC CSV ĐẶC TRƯNG & NHÃN SỰ KIỆN (Step 8 output)...")
    log("=" * 60)
    features_df, events_df = load_training_frames(folder)
    log(f"  merged_motion_features.csv: {len(features_df):,} dòng")
    log(f"  detected_driving_events_unified.csv: {len(events_df):,} sự kiện")

    log("\n" + "=" * 60)
    log(f"[2/7] ĐANG XÂY DỰNG CỬA SỔ CÓ NHÃN (window={params['window_sec']}s, "
        f"stride={params['stride_sec']}s, purity>={params['purity_threshold']})...")
    log("=" * 60)
    X, y, event_ids, window_stats = build_labeled_windows(
        features_df, events_df, timesteps=timesteps, stride_samples=stride_samples,
        purity_threshold=params["purity_threshold"], log=log,
    )

    log("\n" + "=" * 60)
    log("[3/7] ĐANG CHIA TRAIN / VAL / TEST THEO NHÓM EVENT (chống rò rỉ dữ liệu)...")
    log("=" * 60)
    train_mask, val_mask, test_mask = split_by_event_group(
        event_ids, train_ratio=params["train_ratio"], val_ratio=params["val_ratio"],
        seed=RANDOM_SEED,
    )
    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    event_ids_test = event_ids[test_mask]
    log(f"  Train: {len(X_train)} cửa sổ  |  Val: {len(X_val)} cửa sổ  |  Test: {len(X_test)} cửa sổ")

    log("\n" + "=" * 60)
    log("[4/7] ĐANG XÂY DỰNG KIẾN TRÚC & HUẤN LUYỆN MÔ HÌNH...")
    log("=" * 60)
    pos_weight = compute_flag_pos_weights(y_train)
    n_flags_present = int((y_train.sum(axis=0) > 0).sum())
    log(f"  Số cờ (flag) xuất hiện trong Train: {n_flags_present}/{NUM_FLAGS}")

    model = build_classifier_model(timesteps, num_features, NUM_FLAGS)
    log(f"  Tổng số tham số mô hình: {model.count_params():,}")

    history = train_classifier(
        model, X_train, y_train, X_val, y_val, pos_weight,
        epochs=params["epochs"], batch_size=params["batch_size"],
        learning_rate=params.get("learning_rate", 1e-3), log=log,
    )

    log("\n" + "=" * 60)
    log("[5/7] ĐÁNH GIÁ NHANH TRÊN TẬP TEST (tóm tắt)...")
    log("=" * 60)
    test_report = evaluate_classifier(model, X_test, y_test, FLAG_NAMES, log=log)

    log("\n" + "=" * 60)
    log("[6/7] ĐANG XUẤT 3 PHIÊN BẢN MÔ HÌNH (Keras FP32 → TFLite FP32 → TFLite INT8)...")
    log("=" * 60)
    model_paths = model_audit.save_model_versions(
        model, folder, X_train, timesteps, num_features,
        quantize_fn=quantize_to_int8_tflite, base_name=MODEL_BASE_NAME, log=log,
    )
    tflite_path = model_paths["tflite_int8"]
    orig_kb = os.path.getsize(model_paths["tflite_fp32"]) / 1024.0
    quant_kb = os.path.getsize(tflite_path) / 1024.0

    label_map_path = os.path.join(folder, LABEL_MAP_OUTPUT_NAME)
    export_label_map(label_map_path, extra_meta={
        "feature_columns": EVENT_CLASSIFIER_FEATURES,
        "timesteps": timesteps,
        "window_sec": params["window_sec"],
        "fs": params["fs"],
        "tflite_model": TFLITE_OUTPUT_NAME,
        "model_versions": {k: os.path.basename(v) for k, v in model_paths.items()},
    })
    log(f"  ✅ Đã xuất file định nghĩa nhãn: {label_map_path}")

    log("\n" + "=" * 60)
    log("[7/7] CHƯƠNG TRÌNH KIỂM ĐỊNH MÔ HÌNH 3 TẦNG (DRIVESAFE AUDIT)...")
    log("=" * 60)
    audit_report_path = os.path.join(folder, AUDIT_REPORT_NAME)
    audit_result = model_audit.run_three_tier_audit(
        model_paths, X_test, y_test, event_ids_test, FLAG_NAMES,
        audit_report_path, dataset_info={"Thư mục": folder}, log=log,
    )

    report = {
        "window_stats": window_stats,
        "n_train": len(X_train), "n_val": len(X_val), "n_test": len(X_test),
        "test_report": test_report,
        "original_kb": orig_kb, "quantized_kb": quant_kb,
        "tflite_path": tflite_path, "label_map_path": label_map_path,
        "model_paths": model_paths,
        "audit_report_path": audit_result["report_path"],
        "audit_f1_results": audit_result["f1_results"],
        "audit_event_f1_results": audit_result["event_f1_results"],
        "audit_benchmark_results": audit_result["benchmark_results"],
        "audit_verdict": audit_result["audit_verdict"],
        "timesteps": timesteps, "num_features": num_features,
        "num_classes": NUM_CLASSES, "num_flags": NUM_FLAGS,
    }

    report_path = os.path.join(folder, TRAINING_REPORT_NAME)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    report["report_path"] = report_path

    log(f"\n🎉 PIPELINE HOÀN TẤT: Conv1D Multi-scale + SE + Residual (đa nhãn, {NUM_FLAGS} cờ) → "
        f"Keras FP32 / TFLite FP32 / TFLite INT8 + DriveSafe Audit")
    return report


# ==========================================
# CLI
# ==========================================
if __name__ == "__main__":
    target_folder = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    cli_params = {
        "fs": FS_IMU, "window_sec": WINDOW_SEC, "stride_sec": STRIDE_SEC,
        "purity_threshold": PURITY_THRESHOLD, "train_ratio": TRAIN_RATIO,
        "val_ratio": VAL_RATIO, "epochs": 30, "batch_size": 64, "learning_rate": 1e-3,
    }
    run_full_training_pipeline(target_folder, cli_params)
