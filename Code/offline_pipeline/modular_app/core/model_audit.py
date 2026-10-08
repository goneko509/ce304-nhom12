# -*- coding: utf-8 -*-
"""
Module: core/model_audit.py
CHUONG TRINH KIEM DINH MO HINH THEO KHUNG KHOA HOC 3 TANG (DRIVESAFE AUDIT)

Dua tren D:\\0-UAH-DRIVESET-v1\\evaluate_model_audit.py. Mo hinh cua du an
nay la DA NHAN THUC SU (sigmoid DOC LAP tung co, xem core/label_schema.py
FLAG_NAMES - 1 cua so co the mang NHIEU co dong thoi, vd vua BRAKE_HARD
vua STEERING) - CUNG kieu bai toan voi ban tham khao (multi-label, nhieu
co BAT dong thoi), chi khac so luong/ten co va nguon du lieu. Van giu dung
KIEN TRUC 3 TANG kiem dinh:

  TANG 1 (Window-Level)  : Ma tran Precision/Recall/F1/PR-AUC/ROC-AUC
                           TUNG CO DOC LAP (nguong 0.5 tren sigmoid, KHONG
                           argmax) + Macro-F1/Micro-F1/Exact Match Ratio
                           toan cuc, danh gia tren TOAN BO cua so cua tap
                           Test (khong cat bot mau nhu ban tham khao, de
                           cong bang giua Keras/TFLite).

  TANG 2 (Event-Level)   : Quy ve MUC SU KIEN bang MAJORITY VOTE tren
                           tat ca cua so CUNG 1 group_id (= CUNG 1 su
                           kien goc trong detected_driving_events_unified
                           .csv cua Step 8). Chinh xac hon cach "do
                           contiguous-run" cua ban tham khao vi ranh
                           gioi su kien o day la THAT (lay truc tiep tu
                           CSV), khong can doan lai tu chuoi nhi phan.

  TANG 3 (Edge AI Bench) : So sanh 3 PHIEN BAN mo hinh - Keras FP32,
                           TFLite FP32 (TRUOC luong tu hoa), TFLite INT8
                           (SAU luong tu hoa) - ve dung luong file, do
                           tre suy luan TUNG MAU rieng le (mo phong
                           trien khai thuc te tren Raspberry Pi), va
                           muc suy giam Macro F1-Score khi nen INT8.

Dung chung cho core/ai_pipeline.py (1 trip) va core/multi_trip_pipeline.py
(da-trip) - khong phu thuoc GUI/Tkinter, co the chay headless tren server.
"""

import os
import time
import numpy as np
import tensorflow as tf

from sklearn.metrics import (
    f1_score, precision_score, recall_score,
    roc_auc_score, precision_recall_curve, auc,
)

LATENCY_SAMPLE_CAP = 500  # so mau toi da dung de do do tre tung-mau (Tang 3)

MODEL_DISPLAY_NAMES = {
    "keras_fp32": "Keras FP32 Model",
    "tflite_fp32": "TFLite FP32 Model",
    "tflite_int8": "TFLite INT8 Model",
}


# ============================================================
# XUAT 3 PHIEN BAN MO HINH (Keras FP32 / TFLite FP32 / TFLite INT8)
# ============================================================

def convert_tflite_fp32(model, output_path, timesteps, num_features):
    """TFLite FP32 - KHONG luong tu hoa (giu nguyen trong so/kich hoat
    float32), chi chuyen sang dinh dang .tflite de lam MOC SO SANH
    "truoc luong tu hoa" voi ban INT8 (dung luong/do tre/do chinh xac)."""

    input_spec = tf.TensorSpec(shape=[1, timesteps, num_features], dtype=tf.float32)

    @tf.function(input_signature=[input_spec])
    def inference_func(inputs):
        return model(inputs, training=False)

    concrete_func = inference_func.get_concrete_function(input_spec)
    converter = tf.lite.TFLiteConverter.from_concrete_functions([concrete_func])
    # KHONG dat converter.optimizations -> giu nguyen float32, khong nen

    tflite_model = converter.convert()
    with open(output_path, "wb") as f:
        f.write(tflite_model)
    return output_path


def save_model_versions(model, output_dir, X_calibration, timesteps, num_features,
                         quantize_fn, base_name="driversafe_event_classifier", log=print):
    """Xuat CA 3 phien ban mo hinh vao output_dir. quantize_fn la ham
    quantize_to_int8_tflite() CO SAN o core/ai_pipeline.py (KHONG viet
    lai logic luong tu hoa da duoc kiem chung trong du an)."""

    paths = {}

    keras_path = os.path.join(output_dir, f"{base_name}_fp32.keras")
    log(f"  Đang lưu Keras FP32 Model -> {os.path.basename(keras_path)}...")
    model.save(keras_path)
    paths["keras_fp32"] = keras_path

    tflite_fp32_path = os.path.join(output_dir, f"{base_name}_fp32.tflite")
    log(f"  Đang chuyển đổi TFLite FP32 Model (chưa lượng tử hóa) -> {os.path.basename(tflite_fp32_path)}...")
    convert_tflite_fp32(model, tflite_fp32_path, timesteps, num_features)
    paths["tflite_fp32"] = tflite_fp32_path

    tflite_int8_path = os.path.join(output_dir, f"{base_name}_int8.tflite")
    log(f"  Đang lượng tử hóa TFLite INT8 Model -> {os.path.basename(tflite_int8_path)}...")
    quantize_fn(model, X_calibration, tflite_int8_path, timesteps, num_features, log=log)
    paths["tflite_int8"] = tflite_int8_path

    log("  Dung lượng 3 phiên bản mô hình:")
    for key, path in paths.items():
        size_kb = os.path.getsize(path) / 1024.0
        log(f"    {MODEL_DISPLAY_NAMES[key]:<18}: {size_kb:>8.1f} KB  ({os.path.basename(path)})")

    return paths


# ============================================================
# SUY LUAN (dung chung cho ca 3 phien ban - Keras / TFLite FP32 / INT8)
# ============================================================

def _quantize_input_if_needed(window_batch, input_details):
    if input_details["dtype"] == np.int8:
        scale, zero_point = input_details["quantization"]
        if scale == 0:
            return window_batch.astype(np.int8)
        return np.round(window_batch / scale + zero_point).astype(np.int8)
    return window_batch.astype(np.float32)


def _dequantize_output(raw_output, output_details):
    if output_details["dtype"] == np.int8:
        scale, zero_point = output_details["quantization"]
        return (raw_output.astype(np.float32) - zero_point) * scale
    return raw_output.astype(np.float32)


def run_full_inference_probs(model_path, X, model_type, log=print):
    """Suy luan tren TOAN BO X, tra ve ma tran xac suat (N, num_flags) -
    dung cho Tang 1/2 (danh gia do chinh xac tren FULL tap Test, CONG
    BANG giua 3 phien ban mo hinh - khac ban tham khao chi danh gia
    TFLite tren 1 tap con ~500 mau). Da nhan DOC LAP - KHONG ep tong = 1
    (khac softmax): moi co giu nguyen xac suat sigmoid rieng cua no."""

    if model_type == "keras":
        model = tf.keras.models.load_model(model_path, compile=False)
        return model.predict(X, batch_size=64, verbose=0)

    interpreter = tf.lite.Interpreter(model_path=model_path)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]

    probs_list = []
    for i in range(len(X)):
        inp = _quantize_input_if_needed(X[i:i + 1], input_details)
        interpreter.set_tensor(input_details["index"], inp)
        interpreter.invoke()
        raw_out = interpreter.get_tensor(output_details["index"])
        probs = _dequantize_output(raw_out, output_details)[0]
        probs = np.clip(probs, 0.0, 1.0)
        probs_list.append(probs)

    return np.array(probs_list)


def benchmark_latency(model_path, X, model_type, sample_cap=LATENCY_SAMPLE_CAP):
    """TANG 3 (phan do toc do): do do tre suy luan TUNG MAU rieng le (mo
    phong trien khai thuc te tren Raspberry Pi - KHONG dung batch) +
    dung luong file. Chi dung 1 tap mau nho (sample_cap) de do TOC DO,
    KHONG dung de danh gia do chinh xac (xem run_full_inference_probs)."""

    file_size_kb = os.path.getsize(model_path) / 1024.0
    num_samples = min(len(X), sample_cap)
    latencies = []

    if model_type == "keras":
        model = tf.keras.models.load_model(model_path, compile=False)
        X_tensor = tf.convert_to_tensor(X, dtype=tf.float32)
        _ = model(X_tensor[0:1], training=False)  # warm-up
        for i in range(num_samples):
            t0 = time.perf_counter()
            _ = model(X_tensor[i:i + 1], training=False)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)
    else:
        interpreter = tf.lite.Interpreter(model_path=model_path)
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()[0]
        output_details = interpreter.get_output_details()[0]

        warm_inp = _quantize_input_if_needed(X[0:1], input_details)
        interpreter.set_tensor(input_details["index"], warm_inp)
        interpreter.invoke()  # warm-up

        for i in range(num_samples):
            inp = _quantize_input_if_needed(X[i:i + 1], input_details)
            t0 = time.perf_counter()
            interpreter.set_tensor(input_details["index"], inp)
            interpreter.invoke()
            _ = interpreter.get_tensor(output_details["index"])
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0)

    mean_lat = float(np.mean(latencies))
    std_lat = float(np.std(latencies))
    fps = 1000.0 / mean_lat if mean_lat > 0 else 0.0

    return {
        "file_size_kb": file_size_kb,
        "mean_latency_ms": mean_lat,
        "std_latency_ms": std_lat,
        "throughput_fps": fps,
        "num_samples_timed": num_samples,
    }


# ============================================================
# TANG 1: WINDOW-LEVEL METRICS (da nhan - co doc lap, nguong 0.5)
# ============================================================

def compute_multilabel_metrics(y_true, probs, flag_names, threshold=0.5):
    """Ham dung chung (DRY) cho ca Tang 1 (window-level, o file nay) va
    ban tom tat nhanh sau train (core/ai_pipeline.py::evaluate_classifier).

    Tinh P/R/F1/PR-AUC/ROC-AUC DOC LAP cho TUNG CO (nguong co dinh tren
    xac suat sigmoid, KHONG argmax - 1 cua so co the dat >=threshold o
    NHIEU co dong thoi) + 3 chi so toan cuc:
      - Macro-F1   : trung binh F1 qua cac co co mau
      - Micro-F1   : pool TP/FP/FN qua TAT CA co roi moi tinh P/R/F1
      - Exact Match Ratio: ti le cua so du doan DUNG CA NUM_FLAGS co dong
        thoi cung luc - dong vai "Accuracy" o bai toan don nhan truoc day,
        NHUNG khong con bang Micro-F1 nua (dac diem CHI CO o da nhan thuc
        su - 1 cua so dung 14/15 co van gop phan dung vao Micro-F1 nhung
        bi tinh SAI HOAN TOAN trong Exact Match Ratio)."""

    y_true = np.asarray(y_true, dtype=np.int32)
    probs = np.asarray(probs, dtype=np.float32)
    y_pred = (probs >= threshold).astype(np.int32)
    num_flags = len(flag_names)

    per_flag_metrics = {}
    f1_list = []
    tp_total = fp_total = fn_total = 0
    for c in range(num_flags):
        yt = y_true[:, c]
        yp = y_pred[:, c]
        support = int(yt.sum())
        if support == 0 and yp.sum() == 0:
            continue  # co khong xuat hien ca trong GT lan du doan -> bo qua bang

        tp = int(((yt == 1) & (yp == 1)).sum())
        fp = int(((yt == 0) & (yp == 1)).sum())
        fn = int(((yt == 1) & (yp == 0)).sum())
        tp_total += tp
        fp_total += fp
        fn_total += fn

        prec = precision_score(yt, yp, zero_division=0)
        rec = recall_score(yt, yp, zero_division=0)
        f1 = f1_score(yt, yp, zero_division=0)

        try:
            pr_p, pr_r, _ = precision_recall_curve(yt, probs[:, c])
            pr_auc_val = float(auc(pr_r, pr_p))
        except Exception:
            pr_auc_val = 0.0
        try:
            roc_auc_val = float(roc_auc_score(yt, probs[:, c])) if 0 < support < len(yt) else 0.0
        except Exception:
            roc_auc_val = 0.0

        per_flag_metrics[flag_names[c]] = {
            "precision": prec, "recall": rec, "f1": f1,
            "pr_auc": pr_auc_val, "roc_auc": roc_auc_val, "support": support,
        }
        f1_list.append(f1)

    macro_f1 = float(np.mean(f1_list)) if f1_list else 0.0
    micro_prec = tp_total / (tp_total + fp_total) if (tp_total + fp_total) > 0 else 0.0
    micro_rec = tp_total / (tp_total + fn_total) if (tp_total + fn_total) > 0 else 0.0
    micro_f1 = (2 * micro_prec * micro_rec / (micro_prec + micro_rec)
                if (micro_prec + micro_rec) > 0 else 0.0)
    exact_match_ratio = float(np.mean(np.all(y_true == y_pred, axis=1))) if len(y_true) else 0.0

    global_metrics = {
        "macro_f1": macro_f1, "micro_f1": micro_f1, "exact_match_ratio": exact_match_ratio,
    }

    return y_pred, per_flag_metrics, global_metrics


# ============================================================
# TANG 2: EVENT-LEVEL METRICS (majority-vote DOC LAP TUNG CO, theo
# group_id THAT)
# ============================================================

def evaluate_event_level_majority_vote(y_true, y_pred, group_ids, flag_names):
    """TANG 2: Quy ve MUC SU KIEN (khong phai tung cua so rieng le), bang
    MAJORITY VOTE DOC LAP TUNG CO giua cac cua so CUNG 1 group_id (= CUNG
    1 su kien goc trong detected_driving_events_unified.csv) - gia tri 1
    co cho 1 event = mode (0/1) qua cac cua so trong group do. Chinh xac
    hon phuong phap "do contiguous-run" cua ban tham khao vi ranh gioi su
    kien o day la THAT (tu CSV goc), khong can doan lai tu chuoi nhi phan
    - dong thoi KHONG bi loi "ghep nham 2 su kien lien ke trong tap Test
    da gop nhieu trip" vi moi group_id la 1 su kien duy nhat, bat ke thu
    tu luu trong mang."""

    unique_groups = np.unique(group_ids)
    num_flags = len(flag_names)
    event_true = np.zeros((len(unique_groups), num_flags), dtype=np.int32)
    event_pred = np.zeros((len(unique_groups), num_flags), dtype=np.int32)

    for gi, g in enumerate(unique_groups):
        mask = group_ids == g
        event_true[gi] = (y_true[mask].mean(axis=0) >= 0.5).astype(np.int32)
        event_pred[gi] = (y_pred[mask].mean(axis=0) >= 0.5).astype(np.int32)

    _, per_flag_metrics, global_metrics = compute_multilabel_metrics(
        event_true, event_pred.astype(np.float32), flag_names, threshold=0.5
    )

    event_per_class = {
        name: {
            "event_precision": m["precision"], "event_recall": m["recall"],
            "event_f1": m["f1"], "support_events": m["support"],
        }
        for name, m in per_flag_metrics.items()
    }
    event_global = {
        "num_events": int(len(unique_groups)),
        "event_exact_match_ratio": global_metrics["exact_match_ratio"],
        "event_macro_f1": global_metrics["macro_f1"],
        "event_micro_f1": global_metrics["micro_f1"],
    }

    return event_per_class, event_global


# ============================================================
# CHUONG TRINH CHINH: CHAY CA 3 TANG TREN 3 PHIEN BAN MO HINH
# ============================================================

def run_three_tier_audit(model_paths, X_test, y_test, group_ids_test, flag_names,
                          output_path, dataset_info=None, log=print):
    """model_paths: dict {'keras_fp32'/'tflite_fp32'/'tflite_int8' -> duong
    dan file}, thuong la ket qua tra ve tu save_model_versions(). Ghi bao
    cao van ban day du ra output_path, tra ve dict tom tat de GUI/CLI
    hien thi them (khong bat buoc doc lai file .txt)."""

    report_lines = []

    def rlog(msg=""):
        log(msg)
        report_lines.append(str(msg))

    rlog("=" * 80)
    rlog("📌 CHƯƠNG TRÌNH KIỂM ĐỊNH MÔ HÌNH THEO KHUNG KHOA HỌC 3 TẦNG (DRIVESAFE AUDIT)")
    rlog(f"📍 Thời điểm kiểm thử: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    rlog(f"📍 Tổng số cửa sổ trượt trong tập Test: {len(X_test):,}")
    rlog(f"📍 Tổng số sự kiện (group) trong tập Test: {len(np.unique(group_ids_test)):,}")
    if dataset_info:
        for k, v in dataset_info.items():
            rlog(f"📍 {k}: {v}")
    rlog("-" * 80)
    rlog("Lưu ý phương pháp: bài toán ở đây là ĐA NHÃN THỰC SỰ (15 cờ độc lập,")
    rlog("sigmoid, ngưỡng 0.5 — 1 cửa sổ có thể mang NHIỀU cờ đồng thời, vd vừa")
    rlog("BRAKE_HARD vừa STEERING). Tầng 2 dùng majority-vote ĐỘC LẬP TỪNG CỜ theo")
    rlog("group_id THẬT (từ CSV Step 8) thay vì dò contiguous-run, chính xác hơn khi")
    rlog("dữ liệu Test được gộp từ nhiều trip. Vì là đa nhãn thực sự, Micro-F1 KHÔNG")
    rlog("còn bằng Exact Match Ratio (vai trò tương tự 'Accuracy' cũ): 1 cửa sổ đúng")
    rlog("14/15 cờ vẫn góp phần đúng vào Micro-F1 nhưng bị tính SAI HOÀN TOÀN trong")
    rlog("Exact Match Ratio — đây là khác biệt CHỦ ĐÍCH so với bài toán đơn nhãn trước")
    rlog("đây, không phải lỗi. Chi tiết công thức: xem core/model_audit.py.")
    rlog("=" * 80 + "\n")

    benchmark_results = {}
    f1_results = {}
    event_f1_results = {}

    for key in ("keras_fp32", "tflite_fp32", "tflite_int8"):
        path = model_paths.get(key)
        name = MODEL_DISPLAY_NAMES[key]
        if not path or not os.path.isfile(path):
            rlog(f"⚠️ Bỏ qua {name}: không tìm thấy file.\n")
            continue

        model_type = "keras" if key == "keras_fp32" else "tflite"
        rlog(f"🔍 Đang đánh giá Mô hình: {name} ({os.path.basename(path)})...")

        probs = run_full_inference_probs(path, X_test, model_type, log=rlog)
        y_pred, per_flag, global_m = compute_multilabel_metrics(y_test, probs, flag_names)
        f1_results[name] = global_m["macro_f1"]

        rlog("\n  📊 TẦNG 1: BẢNG CHỈ SỐ WINDOW-LEVEL (chỉ hiện cờ có mẫu trong Test)")
        rlog(f"  {'Cờ':<22} | {'Prec':<7} | {'Recall':<7} | {'F1':<7} | {'PR-AUC':<7} | {'ROC-AUC':<7} | {'Support'}")
        rlog("  " + "-" * 84)
        for cname, m in sorted(per_flag.items(), key=lambda kv: -kv[1]["support"]):
            rlog(f"  {cname:<22} | {m['precision']*100:>6.1f}% | {m['recall']*100:>6.1f}% | "
                 f"{m['f1']*100:>6.1f}% | {m['pr_auc']:>6.3f} | {m['roc_auc']:>6.3f} | {m['support']:>7}")
        rlog("  " + "-" * 84)
        rlog(f"  • Exact Match Ratio (khớp cả {len(flag_names)} cờ): {global_m['exact_match_ratio']*100:.2f}%")
        rlog(f"  • Macro F1-Score    : {global_m['macro_f1']*100:.2f}%")
        rlog(f"  • Micro F1-Score    : {global_m['micro_f1']*100:.2f}%  "
             f"(KHÔNG bằng Exact Match Ratio — xem lưu ý phương pháp ở trên)\n")

        event_per_class, event_global = evaluate_event_level_majority_vote(
            y_test, y_pred, group_ids_test, flag_names
        )
        event_f1_results[name] = event_global["event_macro_f1"]

        rlog("  ⏱️ TẦNG 2: BẢNG CHỈ SỐ MỨC SỰ KIỆN (Majority-Vote độc lập từng cờ theo group_id thật)")
        rlog(f"  {'Cờ':<22} | {'Ev.Prec':<8} | {'Ev.Recall':<9} | {'Ev.F1':<7} | {'Support (events)'}")
        rlog("  " + "-" * 72)
        for cname, m in sorted(event_per_class.items(), key=lambda kv: -kv[1]["support_events"]):
            rlog(f"  {cname:<22} | {m['event_precision']*100:>7.1f}% | {m['event_recall']*100:>8.1f}% | "
                 f"{m['event_f1']*100:>6.1f}% | {m['support_events']:>7}")
        rlog("  " + "-" * 72)
        rlog(f"  • Số sự kiện đánh giá    : {event_global['num_events']:,}")
        rlog(f"  • Event Exact Match Ratio: {event_global['event_exact_match_ratio']*100:.2f}%")
        rlog(f"  • Event Macro F1-Score   : {event_global['event_macro_f1']*100:.2f}%\n")

        bench = benchmark_latency(path, X_test, model_type)
        benchmark_results[name] = bench
        rlog(f"  ⚡ Benchmark tốc độ (suy luận từng cửa sổ riêng lẻ, n={bench['num_samples_timed']} mẫu):")
        rlog(f"    Dung lượng: {bench['file_size_kb']:.1f} KB | "
             f"Độ trễ TB: {bench['mean_latency_ms']:.3f} ms (±{bench['std_latency_ms']:.3f}) | "
             f"Throughput: {bench['throughput_fps']:.1f} FPS\n")
        rlog("=" * 80 + "\n")

    rlog("⚡ TẦNG 3: BẢNG SO SÁNH TỔNG HỢP EDGE AI & PHẦN CỨNG "
         "(KERAS FP32 vs TFLITE FP32 vs TFLITE INT8)")
    rlog(f"  {'Tên mô hình':<20} | {'Dung lượng':<12} | {'Độ trễ':<14} | {'Throughput':<12} | "
         f"{'Macro F1 (Win)':<16} | {'Macro F1 (Event)'}")
    rlog("  " + "-" * 100)
    for name, res in benchmark_results.items():
        size_str = f"{res['file_size_kb']:.1f} KB" if res['file_size_kb'] < 1024 else f"{res['file_size_kb']/1024:.2f} MB"
        lat_str = f"{res['mean_latency_ms']:.3f} ms"
        fps_str = f"{res['throughput_fps']:.1f} FPS"
        f1_str = f"{f1_results.get(name, 0.0)*100:.2f}%"
        ef1_str = f"{event_f1_results.get(name, 0.0)*100:.2f}%"
        rlog(f"  {name:<20} | {size_str:<12} | {lat_str:<14} | {fps_str:<12} | {f1_str:<16} | {ef1_str}")

    audit_verdict = None
    base_name = "TFLite FP32 Model" if "TFLite FP32 Model" in f1_results else "Keras FP32 Model"
    if base_name in f1_results and "TFLite INT8 Model" in f1_results:
        f1_base = f1_results[base_name]
        f1_int8 = f1_results["TFLite INT8 Model"]
        delta_f1 = abs(f1_base - f1_int8) * 100.0

        size_base = benchmark_results[base_name]["file_size_kb"]
        size_int8 = benchmark_results["TFLite INT8 Model"]["file_size_kb"]
        comp_ratio = size_base / size_int8 if size_int8 > 0 else 0.0

        rlog("\n  🎯 ĐÁNH GIÁ KẾT QUẢ TỐI ƯU HÓA LƯỢNG TỬ HÓA (INT8 vs FP32):")
        rlog(f"    • Mô hình gốc so sánh                      : {base_name}")
        rlog(f"    • Tỉ lệ nén dung lượng (Compression Ratio) : {comp_ratio:.2f}x "
             f"(giảm {(1 - size_int8/size_base)*100:.1f}% kích thước)")
        rlog(f"    • Mức suy giảm Macro F1-Score (ΔF1, Window): {delta_f1:.2f}%")
        if delta_f1 <= 1.0:
            rlog("    ✅ ĐẠT CHUẨN KHOA HỌC: Mức suy giảm F1 < 1.0% — mô hình INT8 bảo toàn")
            rlog("       hoàn hảo độ chính xác gốc, phù hợp triển khai trên Raspberry Pi.")
            audit_verdict = "PASS"
        else:
            rlog("    ⚠️ CẢNH BÁO: Mức suy giảm F1 > 1.0% — cần tinh chỉnh lại tập đại diện")
            rlog("       (representative dataset) khi lượng tử hóa, hoặc xem xét kiến trúc")
            rlog("       mô hình để tăng độ bền với lượng tử hóa (quantization-robustness).")
            audit_verdict = "WARNING"

        rlog(f"\n    • Compression Ratio : {comp_ratio:.2f}x")
        rlog(f"    • Delta Macro-F1    : {delta_f1:.2f}%")

    rlog("\n" + "=" * 80)
    rlog("🎉 ĐÃ HOÀN THÀNH QUY TRÌNH KIỂM ĐỊNH MÔ HÌNH DRIVESAFE AUDIT!")
    rlog("=" * 80)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    return {
        "report_path": output_path,
        "report_text": "\n".join(report_lines),
        "f1_results": f1_results,
        "event_f1_results": event_f1_results,
        "benchmark_results": benchmark_results,
        "audit_verdict": audit_verdict,
    }
