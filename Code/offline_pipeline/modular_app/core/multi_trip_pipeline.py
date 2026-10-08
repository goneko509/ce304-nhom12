# -*- coding: utf-8 -*-
"""
Module: core/multi_trip_pipeline.py
Chuc nang: QUY TRINH CHINH TREN SERVER - gop nhieu trip raw data lai de
           huan luyen 1 mo hinh DUY NHAT tren TOAN BO du lieu thu thap
           (khac voi core/ai_pipeline.py chi chay tren 1 trip qua GUI
           Step 6). Khong phu thuoc Tkinter - chay headless tren
           server/CI qua dong lenh:

    python -m core.multi_trip_pipeline <raw_data_root> [options]

3 giai doan (dung chung logic voi Step 8 / Step 6, KHONG viet lai):
    1. GOP TRIPS       - quet cac thu muc trip con trong <raw_data_root>,
                         dam bao moi trip da duoc tien xu ly (Step 8).
    2. TRICH DAC TRUNG - voi tung trip: doc merged_motion_features.csv +
                         detected_driving_events_unified.csv, cat cua so
                         co nhan (labeled windowing), roi NOI (concat)
                         tensor X/y/group_id cua TAT CA trip lai.
    3. TRAIN MO HINH   - Train/Val/Test split theo NHOM (event_id) TOAN
                         CUC (khong rieng tung trip) -> Multi-scale
                         Conv1D+SE+Residual -> Luong tu hoa INT8.

Co so khoa hoc / thiet ke chi tiet: xem docs/ai_training_pipeline_multitrip.md
(Muc 3.2 - "Tong Hop Da-Trip") va core/ai_pipeline.py (logic windowing/
train/quantize dung lai 100%, khong sao chep).
"""

import os
import sys
import json
import time
import argparse
import numpy as np

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

from core import event_detection_engine as eng
from core import ai_pipeline as pipe
from core import model_audit
from core.label_schema import NUM_CLASSES, LABEL_NAMES, export_label_map

OUTPUT_DIR_NAME = "trained_model_multi_trip"
MULTI_TRIP_REPORT_NAME = "multi_trip_training_report.json"

# He so nhan group_id de dam bao khong dung do giua 2 trip khac nhau (xem
# canh bao trong docs/ai_training_pipeline_multitrip.md Muc 3.3): event_id
# chi duy nhat TRONG 1 trip, nen phai ma hoa them trip_index vao group_id
# TOAN CUC truoc khi dua vao split_by_event_group().
TRIP_ID_MULTIPLIER = 1_000_000


# ============================================================
# GIAI DOAN 1: GOP TRIPS - PHAT HIEN + TIEN XU LY TUNG TRIP
# ============================================================

def discover_trips(raw_data_root, log=print):
    """Quet cac thu muc con TRUC TIEP trong raw_data_root, giu lai nhung
    thu muc co the tim duoc cap file RAW GPS+IMU hop le (tai su dung
    event_detection_engine.find_input_files - KHONG doi logic phat hien
    file). Tra ve list cac duong dan thu muc trip, da sap xep."""

    if not os.path.isdir(raw_data_root):
        raise FileNotFoundError(f"Không tìm thấy thư mục raw_data_root:\n{raw_data_root}")

    trip_dirs = []
    for name in sorted(os.listdir(raw_data_root)):
        candidate = os.path.join(raw_data_root, name)
        if not os.path.isdir(candidate):
            continue
        try:
            eng.find_input_files(candidate)
        except Exception:
            continue
        trip_dirs.append(candidate)

    if not trip_dirs:
        raise FileNotFoundError(
            f"Không tìm thấy trip hợp lệ nào (cần RAW_GPS.txt + RAW_ACCELEROMETERS.txt) "
            f"trong các thư mục con của:\n{raw_data_root}"
        )

    log(f"  Phát hiện {len(trip_dirs)} trip hợp lệ trong {raw_data_root}:")
    for t in trip_dirs:
        log(f"    - {os.path.basename(t)}")

    return trip_dirs


def ensure_trip_preprocessed(trip_dir, detection_cfg=None, force=False, log=print):
    """Dam bao 1 trip da co san merged_motion_features.csv +
    detected_driving_events_unified.csv trong <trip_dir>/detected_events_unified/
    (san pham cua Step 8). Neu chua co hoac force=True, chay lai toan bo
    event_detection_engine.run_full_pipeline() - dung CHINH logic Step 8,
    khong viet lai. Tra ve duong dan thu muc detected_events_unified/."""

    events_dir = os.path.join(trip_dir, eng.OUTPUT_DIR_NAME)
    features_csv = os.path.join(events_dir, pipe.FEATURES_CSV_NAME)
    events_csv = os.path.join(events_dir, pipe.EVENTS_CSV_NAME)

    if not force and os.path.isfile(features_csv) and os.path.isfile(events_csv):
        log(f"  [{os.path.basename(trip_dir)}] Đã có sẵn kết quả Step 8, bỏ qua tiền xử lý.")
        return events_dir

    log(f"  [{os.path.basename(trip_dir)}] Đang chạy tiền xử lý + trích đặc trưng (Step 8)...")
    cfg = detection_cfg if detection_cfg is not None else eng.default_cfg_bundle()
    result = eng.run_full_pipeline(trip_dir, cfg)
    log(f"  [{os.path.basename(trip_dir)}] -> {result['total_events']} sự kiện "
        f"({result['merged_samples']:,} mẫu).")
    return result["output_dir"]


def ensure_all_trips_preprocessed(trip_dirs, detection_cfg=None, force=False, log=print):
    events_dirs = []
    for trip_dir in trip_dirs:
        events_dirs.append(ensure_trip_preprocessed(trip_dir, detection_cfg, force=force, log=log))
    return events_dirs


# ============================================================
# GIAI DOAN 2: TRICH DAC TRUNG DA-TRIP (LABELED WINDOWING + GOP)
# ============================================================

def build_multi_trip_windows(events_dirs, timesteps, stride_samples, purity_threshold, log=print):
    """Voi TUNG trip: doc CSV (load_training_frames) + cat cua so co nhan
    (build_labeled_windows - HAM GOC cua core/ai_pipeline.py, KHONG sao
    chep lai), roi NOI (concat) X/y/group_id cua TAT CA trip. group_id
    duoc ma hoa lai thanh global_group_id = trip_index * 1_000_000 +
    event_id_cuc_bo, dam bao khong dung do event_id giua 2 trip khac nhau
    khi dua vao split_by_event_group() o giai doan sau."""

    X_list, y_list, group_list = [], [], []
    per_trip_stats = []

    for trip_index, events_dir in enumerate(events_dirs):
        trip_name = os.path.basename(os.path.dirname(events_dir))
        log(f"  [{trip_name}] Đang đọc CSV & cắt cửa sổ có nhãn...")

        features_df, events_df = pipe.load_training_frames(events_dir)
        X, y, event_ids, stats = pipe.build_labeled_windows(
            features_df, events_df, timesteps=timesteps, stride_samples=stride_samples,
            purity_threshold=purity_threshold, log=lambda m, tn=trip_name: log(f"    [{tn}] {m}"),
        )

        global_group_id = trip_index * TRIP_ID_MULTIPLIER + event_ids

        X_list.append(X)
        y_list.append(y)
        group_list.append(global_group_id)

        per_trip_stats.append({
            "trip": trip_name, "trip_index": trip_index,
            "merged_samples": len(features_df), "num_events": len(events_df),
            "windows_kept": stats["kept"], "windows_total_candidate": stats["total_candidate_windows"],
            "class_counts": stats["class_counts"],
        })

    X_all = np.concatenate(X_list, axis=0)
    y_all = np.concatenate(y_list, axis=0)
    group_ids_all = np.concatenate(group_list, axis=0)

    log(f"  Tổng cộng {len(events_dirs)} trip -> {len(X_all)} cửa sổ sau khi gộp.")

    overall_class_counts = {}
    for trip_stat in per_trip_stats:
        for name, count in trip_stat["class_counts"].items():
            overall_class_counts[name] = overall_class_counts.get(name, 0) + count

    multi_trip_stats = {
        "num_trips": len(events_dirs),
        "total_windows": len(X_all),
        "per_trip": per_trip_stats,
        "class_counts": overall_class_counts,
        "num_classes_present": len(overall_class_counts),
    }

    return X_all, y_all, group_ids_all, multi_trip_stats


# ============================================================
# GIAI DOAN 3: TRAIN MO HINH TREN TOAN BO DU LIEU DA-TRIP
# (dung lai 100% logic model/train/eval/quantize cua core/ai_pipeline.py)
# ============================================================

def run_server_pipeline(raw_data_root, params, output_dir=None, force_preprocess=False, log=print):
    """Ham chinh goi tu CLI. params giong het run_full_training_pipeline()
    cua core/ai_pipeline.py (fs, window_sec, stride_sec, purity_threshold,
    train_ratio, val_ratio, epochs, batch_size, learning_rate)."""

    t0 = time.time()
    timesteps = max(2, int(round(params["fs"] * params["window_sec"])))
    stride_samples = max(1, int(round(params["fs"] * params["stride_sec"])))

    if output_dir is None:
        output_dir = os.path.join(raw_data_root, OUTPUT_DIR_NAME)
    os.makedirs(output_dir, exist_ok=True)

    log("=" * 64)
    log("[1/6] GIAI DOAN GOP TRIPS - PHAT HIEN & TIEN XU LY TUNG TRIP")
    log("=" * 64)
    trip_dirs = discover_trips(raw_data_root, log=log)
    events_dirs = ensure_all_trips_preprocessed(
        trip_dirs, detection_cfg=params.get("detection_cfg"), force=force_preprocess, log=log
    )

    log("\n" + "=" * 64)
    log(f"[2/6] GIAI DOAN TRICH DAC TRUNG DA-TRIP (window={params['window_sec']}s, "
        f"stride={params['stride_sec']}s, purity>={params['purity_threshold']})")
    log("=" * 64)
    X, y, group_ids, multi_trip_stats = build_multi_trip_windows(
        events_dirs, timesteps, stride_samples, params["purity_threshold"], log=log
    )
    num_features = X.shape[2]
    log(f"  Số nhãn thực tế xuất hiện (toàn bộ {len(events_dirs)} trip): "
        f"{multi_trip_stats['num_classes_present']}/{NUM_CLASSES}")

    log("\n" + "=" * 64)
    log("[3/6] CHIA TRAIN / VAL / TEST THEO NHÓM EVENT TOÀN CỤC (chống rò rỉ dữ liệu)")
    log("=" * 64)
    train_mask, val_mask, test_mask = pipe.split_by_event_group(
        group_ids, train_ratio=params["train_ratio"], val_ratio=params["val_ratio"],
        seed=pipe.RANDOM_SEED,
    )
    X_train, y_train = X[train_mask], y[train_mask]
    X_val, y_val = X[val_mask], y[val_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    group_ids_test = group_ids[test_mask]
    log(f"  Train: {len(X_train)} cửa sổ  |  Val: {len(X_val)} cửa sổ  |  Test: {len(X_test)} cửa sổ")
    log(f"  (nhóm theo global_group_id = trip_index × {TRIP_ID_MULTIPLIER:,} + event_id — "
        f"không rò rỉ dữ liệu cả trong 1 trip LẪN giữa các trip khác nhau)")

    log("\n" + "=" * 64)
    log("[4/6] XÂY DỰNG KIẾN TRÚC & HUẤN LUYỆN MÔ HÌNH (toàn bộ dữ liệu đa-trip)")
    log("=" * 64)
    pos_weight = pipe.compute_flag_pos_weights(y_train)
    n_flags_present = int((y_train.sum(axis=0) > 0).sum())
    log(f"  Số cờ (flag) xuất hiện trong Train: {n_flags_present}/{pipe.NUM_FLAGS}")

    model = pipe.build_classifier_model(timesteps, num_features, pipe.NUM_FLAGS)
    log(f"  Tổng số tham số mô hình: {model.count_params():,}")

    pipe.train_classifier(
        model, X_train, y_train, X_val, y_val, pos_weight,
        epochs=params["epochs"], batch_size=params["batch_size"],
        learning_rate=params.get("learning_rate", 1e-3), log=log,
    )

    log("\n" + "=" * 64)
    log("[5/6] ĐÁNH GIÁ NHANH TRÊN TẬP TEST (tóm tắt)")
    log("=" * 64)
    test_report = pipe.evaluate_classifier(model, X_test, y_test, pipe.FLAG_NAMES, log=log)

    log("\n" + "=" * 64)
    log("[6/6] XUẤT 3 PHIÊN BẢN MÔ HÌNH & CHƯƠNG TRÌNH KIỂM ĐỊNH 3 TẦNG (DRIVESAFE AUDIT)")
    log("=" * 64)
    model_paths = model_audit.save_model_versions(
        model, output_dir, X_train, timesteps, num_features,
        quantize_fn=pipe.quantize_to_int8_tflite, base_name=pipe.MODEL_BASE_NAME, log=log,
    )
    tflite_path = model_paths["tflite_int8"]
    orig_kb = os.path.getsize(model_paths["tflite_fp32"]) / 1024.0
    quant_kb = os.path.getsize(tflite_path) / 1024.0

    label_map_path = os.path.join(output_dir, pipe.LABEL_MAP_OUTPUT_NAME)
    export_label_map(label_map_path, extra_meta={
        "feature_columns": pipe.EVENT_CLASSIFIER_FEATURES,
        "timesteps": timesteps,
        "window_sec": params["window_sec"],
        "fs": params["fs"],
        "tflite_model": pipe.TFLITE_OUTPUT_NAME,
        "model_versions": {k: os.path.basename(v) for k, v in model_paths.items()},
        "trained_on": "multi_trip",
        "num_trips": len(events_dirs),
        "trip_names": [os.path.basename(os.path.dirname(d)) for d in events_dirs],
    })
    log(f"  ✅ Đã xuất file định nghĩa nhãn: {label_map_path}")

    audit_report_path = os.path.join(output_dir, pipe.AUDIT_REPORT_NAME)
    audit_result = model_audit.run_three_tier_audit(
        model_paths, X_test, y_test, group_ids_test, pipe.FLAG_NAMES,
        audit_report_path, dataset_info={"Thư mục": output_dir, "Số trip": len(events_dirs)}, log=log,
    )

    elapsed = time.time() - t0
    report = {
        "raw_data_root": raw_data_root,
        "trip_dirs": [os.path.basename(d) for d in trip_dirs],
        "multi_trip_stats": multi_trip_stats,
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
        "num_classes": NUM_CLASSES, "num_flags": pipe.NUM_FLAGS,
        "elapsed_seconds": elapsed,
    }

    report_path = os.path.join(output_dir, MULTI_TRIP_REPORT_NAME)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    report["report_path"] = report_path

    log(f"\n🎉 PIPELINE SERVER ĐA-TRIP HOÀN TẤT trong {elapsed:.1f}s "
        f"({len(events_dirs)} trip, {len(X)} cửa sổ, đa nhãn {pipe.NUM_FLAGS} cờ) → "
        f"Keras FP32 / TFLite FP32 / TFLite INT8 + DriveSafe Audit → {output_dir}")
    return report


# ============================================================
# CLI
# ============================================================

def _build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m core.multi_trip_pipeline",
        description="Quy trinh CHINH tren SERVER: Gop trips -> Trich dac trung -> Train mo hinh (33 nhan, INT8).",
    )
    p.add_argument("raw_data_root", help="Thu muc goc chua cac thu muc trip con (moi trip co RAW_GPS.txt + RAW_ACCELEROMETERS.txt)")
    p.add_argument("--output-dir", default=None, help=f"Thu muc xuat model/.tflite/label_map.json (mac dinh: <raw_data_root>/{OUTPUT_DIR_NAME}/)")
    p.add_argument("--force-preprocess", action="store_true", help="Chay lai Step 8 cho MOI trip, ke ca da co san ket qua")
    p.add_argument("--fs", type=float, default=pipe.FS_IMU, help=f"Tan so IMU (Hz), mac dinh {pipe.FS_IMU}")
    p.add_argument("--window-sec", type=float, default=pipe.WINDOW_SEC, help=f"Do dai cua so (s), mac dinh {pipe.WINDOW_SEC}")
    p.add_argument("--stride-sec", type=float, default=pipe.STRIDE_SEC, help=f"Buoc truot cua so (s), mac dinh {pipe.STRIDE_SEC}")
    p.add_argument("--purity-threshold", type=float, default=pipe.PURITY_THRESHOLD, help=f"Nguong purity gan nhan cua so, mac dinh {pipe.PURITY_THRESHOLD}")
    p.add_argument("--train-ratio", type=float, default=pipe.TRAIN_RATIO, help=f"Ti le Train, mac dinh {pipe.TRAIN_RATIO}")
    p.add_argument("--val-ratio", type=float, default=pipe.VAL_RATIO, help=f"Ti le Val (phan con lai la Test), mac dinh {pipe.VAL_RATIO}")
    p.add_argument("--epochs", type=int, default=30, help="So epoch huan luyen, mac dinh 30")
    p.add_argument("--batch-size", type=int, default=64, help="Batch size, mac dinh 64")
    p.add_argument("--learning-rate", type=float, default=1e-3, help="Learning rate Adam, mac dinh 1e-3")
    return p


def main(argv=None):
    args = _build_arg_parser().parse_args(argv)

    params = {
        "fs": args.fs, "window_sec": args.window_sec, "stride_sec": args.stride_sec,
        "purity_threshold": args.purity_threshold, "train_ratio": args.train_ratio,
        "val_ratio": args.val_ratio, "epochs": args.epochs, "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
    }

    run_server_pipeline(
        args.raw_data_root, params, output_dir=args.output_dir,
        force_preprocess=args.force_preprocess, log=print,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
