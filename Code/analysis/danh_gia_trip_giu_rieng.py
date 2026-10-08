"""Danh gia kha nang tong quat hoa sang CHUYEN DI MOI (held-out trip).

Mo hinh PHU duoc train tren 12 trip voi DUNG giao thuc cua mo hinh trien khai
(cua so 2 s, stride 0.3 s, purity 0.8, chia 70/15/15 theo nhom su kien, cung
kien truc / loss / so epoch), roi kiem dinh 3 tang tren:
  (a) tap Test trong-12-trip  (cung phan phoi voi train - giong bao cao chinh)
  (b) TOAN BO trip giu rieng  (chuyen di hoan toan chua thay)
So sanh (a) vs (b) => muc suy giam khi gap chuyen di moi.

Mo hinh trien khai tren Pi (train 13 trip) KHONG bi thay doi.
Chi ghep cac ham co san cua modular_app/core (khong viet lai cong thuc).

Chay:  python danh_gia_trip_giu_rieng.py [--holdout 01102026_135950]
Ket qua: thongke_output/holdout_<trip>/
"""

import argparse
import json
import os
import sys
import time

PI3_REPO = r"D:\0. UIT\0 - KHOALUAN\Pi3\raw_data_collection"
RAW_ROOT = os.path.join(PI3_REPO, "raw_data", "0-Trips")
sys.path.insert(0, os.path.join(PI3_REPO, "modular_app"))

import numpy as np  # noqa: E402
from core import ai_pipeline as pipe  # noqa: E402
from core import model_audit  # noqa: E402
from core import multi_trip_pipeline as mtp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg=""):
    print(msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", default="01102026_135950")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--reuse", action="store_true", help="dung lai mo hinh da train trong model_dir, bo qua huan luyen")
    args = ap.parse_args()

    out_dir = os.path.join(HERE, "thongke_output", f"holdout_{args.holdout}")
    # TFLite Interpreter tren Windows khong mo duoc duong dan co ky tu Unicode (tieng Viet)
    # => luu/doc mo hinh o thu muc ASCII trong repo Pi3, bao cao van ghi vao out_dir
    model_dir = os.path.join(PI3_REPO, "models_eval", f"holdout_{args.holdout}")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(model_dir, exist_ok=True)
    t0 = time.time()
    timesteps = int(round(pipe.FS_IMU * pipe.WINDOW_SEC))
    stride = int(round(pipe.FS_IMU * pipe.STRIDE_SEC))

    trip_dirs = mtp.discover_trips(RAW_ROOT, log=log)
    hold_dir = [d for d in trip_dirs if os.path.basename(d) == args.holdout]
    train_dirs = [d for d in trip_dirs if os.path.basename(d) != args.holdout]
    assert len(hold_dir) == 1 and len(train_dirs) == len(trip_dirs) - 1, "khong tim thay trip giu rieng"
    log(f"\nTrain tren {len(train_dirs)} trip, giu rieng: {args.holdout}")

    train_events = mtp.ensure_all_trips_preprocessed(train_dirs, log=log)
    hold_events = mtp.ensure_all_trips_preprocessed(hold_dir, log=log)

    X, y, groups, stats_train = mtp.build_multi_trip_windows(train_events, timesteps, stride, pipe.PURITY_THRESHOLD, log=log)
    Xh, yh, gh, stats_hold = mtp.build_multi_trip_windows(hold_events, timesteps, stride, pipe.PURITY_THRESHOLD, log=log)

    tr, va, te = pipe.split_by_event_group(groups, pipe.TRAIN_RATIO, pipe.VAL_RATIO, seed=pipe.RANDOM_SEED)
    log(f"12 trip -> Train {tr.sum()} | Val {va.sum()} | Test {te.sum()} cua so; trip giu rieng: {len(Xh)} cua so")

    if args.reuse:
        paths = {k: os.path.join(model_dir, f"{pipe.MODEL_BASE_NAME}_{s}") for k, s in
                 [("keras_fp32", "fp32.keras"), ("tflite_fp32", "fp32.tflite"), ("tflite_int8", "int8.tflite")]}
        assert all(os.path.isfile(v) for v in paths.values()), f"thieu file mo hinh trong {model_dir}"
        log(f"Dung lai mo hinh da train: {model_dir}")
    else:
        pos_weight = pipe.compute_flag_pos_weights(y[tr])
        model = pipe.build_classifier_model(timesteps, X.shape[2], pipe.NUM_FLAGS)
        pipe.train_classifier(model, X[tr], y[tr], X[va], y[va], pos_weight,
                              epochs=args.epochs, batch_size=64, learning_rate=1e-3, log=log)
        paths = model_audit.save_model_versions(model, model_dir, X[tr], timesteps, X.shape[2],
                                                quantize_fn=pipe.quantize_to_int8_tflite,
                                                base_name=pipe.MODEL_BASE_NAME, log=log)

    res_in = model_audit.run_three_tier_audit(
        paths, X[te], y[te], groups[te], pipe.FLAG_NAMES, os.path.join(out_dir, "audit_test_trong_12_trip.txt"),
        dataset_info={"Tap": "Test trong 12 trip (chia theo su kien)"}, log=log)
    res_out = model_audit.run_three_tier_audit(
        paths, Xh, yh, gh, pipe.FLAG_NAMES, os.path.join(out_dir, f"audit_trip_giu_rieng_{args.holdout}.txt"),
        dataset_info={"Tap": f"Trip giu rieng {args.holdout} (chua tung thay)"}, log=log)

    summary = {
        "holdout_trip": args.holdout,
        "model_dir": model_dir,
        "train_trips": [os.path.basename(d) for d in train_dirs],
        "n_train": int(tr.sum()), "n_val": int(va.sum()), "n_test_in": int(te.sum()), "n_holdout": int(len(Xh)),
        "n_events_test_in": int(len(np.unique(groups[te]))), "n_events_holdout": int(len(np.unique(gh))),
        "window_macro_f1": {"test_trong_12_trip": res_in["f1_results"], "trip_giu_rieng": res_out["f1_results"]},
        "event_macro_f1": {"test_trong_12_trip": res_in["event_f1_results"], "trip_giu_rieng": res_out["event_f1_results"]},
        "verdict": {"test_trong_12_trip": res_in["audit_verdict"], "trip_giu_rieng": res_out["audit_verdict"]},
        "holdout_class_counts": stats_hold["class_counts"],
        "elapsed_s": time.time() - t0,
    }
    with open(os.path.join(out_dir, "tom_tat_holdout.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=float)

    log("\n" + "=" * 70)
    log(f"TOM TAT - mo hinh phu (12 trip), giu rieng {args.holdout}")
    log(f"{'Phien ban':<22}{'Macro-F1 test-12trip':>22}{'Macro-F1 trip moi':>20}")
    for k in res_in["f1_results"]:
        log(f"{k:<22}{100 * res_in['f1_results'][k]:>21.2f}%{100 * res_out['f1_results'][k]:>19.2f}%")
    log(f"Thoi gian: {summary['elapsed_s']:.0f} s  ->  {out_dir}")


if __name__ == "__main__":
    main()
