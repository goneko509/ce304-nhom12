# -*- coding: utf-8 -*-
"""
Module: collector/bench_pi3.py
Chuc nang: BENCHMARK PHAT LAI (replay) tren Raspberry Pi 3 - so sanh TFLite FP32
           vs TFLite INT8 (va tuy chon Keras FP32) tren CUNG MOT chuoi cua so du
           lieu that, dung DUNG cac ham trien khai cua realtime_inference.py
           (build_features_from_buffer + decode_window), khong viet lai cong thuc.

Moi mo hinh chay trong 1 TIEN TRINH CON RIENG (subprocess) de so do RAM khong
bi lan giua cac mo hinh. Cac tick chay lien tiep (khong sleep) de do do tre;
tai CPU o nhip that duoc suy ra = thoi gian 1 tick / stride.

Cach chay tren Pi (tu thu muc collector/):
    python3 bench_pi3.py --trip /home/admin/raw_data_collection/raw_data/01102026_135950
    # them Keras (chi khi Pi co tensorflow day du):
    python3 bench_pi3.py --trip ... --keras /home/admin/models/driversafe_event_classifier_fp32.keras

Ket qua: collector/bench_results/<YYYYmmdd_HHMMSS>/bench_pi3_report.{json,txt}
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time

import numpy as np

import realtime_inference as rt

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL_DIR = "/home/admin/models"
DEFAULT_MODELS = ["driversafe_event_classifier_fp32.tflite", "driversafe_event_classifier_int8.tflite"]
IMU_HZ, GPS_HZ = 100.0, 10.0


# ------------------------------------------------------------------ tien ich he thong
def rss_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return float("nan")


def peak_rss_mb():
    try:
        import resource  # chi co tren Linux/Unix
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3  # KB -> MB
    except Exception:
        return float("nan")


def cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return float("nan")


def throttled_flag():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3)
        return out.stdout.strip()
    except Exception:
        return "n/a"


def system_info():
    model = "n/a"
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read().strip("\x00\n ")
    except Exception:
        pass
    mem_total = float("nan")
    try:
        import psutil
        mem_total = psutil.virtual_memory().total / 1e6
    except Exception:
        pass
    return {"board": model, "machine": platform.machine(), "python": platform.python_version(),
            "os": platform.platform(), "mem_total_mb": mem_total, "cpu_count": os.cpu_count()}


def runtime_name():
    for mod in ("tflite_runtime", "ai_edge_litert", "tensorflow"):
        try:
            m = __import__(mod)
            return f"{mod} {getattr(m, '__version__', '')}".strip()
        except ImportError:
            continue
    return "none"


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def stats_ms(arr_s):
    a = np.asarray(arr_s) * 1000.0
    return {"mean": float(a.mean()), "std": float(a.std()), "p50": pct(a, 50), "p95": pct(a, 95),
            "p99": pct(a, 99), "max": float(a.max())} if len(a) else {}


# ------------------------------------------------------------------ du lieu phat lai
def load_trip(trip_dir, t_start, duration):
    """Doc RAW_ACCELEROMETERS/RAW_GPS dung dinh dang collector/main.py ghi ra,
    chi lay doan [t_start - 5s, t_start + duration + 5s] de doc nhanh tren Pi."""
    t_lo, t_hi = t_start - 5.0, t_start + duration + 5.0
    imu_rows, gps_rows = [], []
    with open(os.path.join(trip_dir, "RAW_ACCELEROMETERS.txt")) as f:
        for line in f:
            p = line.split()
            if len(p) < 10:
                continue
            try:
                t = float(p[0])
            except ValueError:
                continue
            if t < t_lo:
                continue
            if t > t_hi:
                break
            imu_rows.append(tuple(float(x) for x in p[:10]))  # t, acc xyz, yaw roll pitch, gyr xyz
    with open(os.path.join(trip_dir, "RAW_GPS.txt")) as f:
        for line in f:
            p = line.split()
            if len(p) < 9:
                continue
            try:
                t, speed = float(p[0]), float(p[8])
            except ValueError:
                continue
            if t < t_lo:
                continue
            if t > t_hi:
                break
            gps_rows.append((t, speed))
    return np.asarray(imu_rows), np.asarray(gps_rows)


def iter_windows(imu, gps, t_start, n_ticks, stride, buffer_sec):
    """Mo phong dung bo dem truot cua run_worker: tai moi tick, giu buffer_sec
    du lieu gan nhat. Tra ve (t_end, imu_rows, gps_rows) - list nhu deque that."""
    ti, tg = imu[:, 0], gps[:, 0] if len(gps) else np.empty(0)
    for i in range(n_ticks):
        t_end = t_start + buffer_sec + i * stride
        a, b = np.searchsorted(ti, t_end - buffer_sec, "left"), np.searchsorted(ti, t_end, "right")
        c, d = np.searchsorted(tg, t_end - buffer_sec, "left"), np.searchsorted(tg, t_end, "right")
        yield t_end, imu[a:b].tolist(), gps[c:d].tolist()


# ------------------------------------------------------------------ tien trinh con
def worker(args):
    out = {"model": os.path.basename(args.worker_model), "file_kb": os.path.getsize(args.worker_model) / 1024.0}
    out["rss_start_mb"] = rss_mb()
    imu, gps = load_trip(args.trip, args.t_start, args.n_ticks * args.stride + args.buffer)
    out["rss_after_data_mb"] = rss_mb()
    is_keras = args.worker_model.endswith(".keras")

    t0 = time.perf_counter()
    if is_keras:
        import tensorflow as tf
        out["runtime"] = f"tensorflow {tf.__version__}"
        t_imp = time.perf_counter()
        model = tf.keras.models.load_model(args.worker_model, compile=False)
        with open(args.label_map, encoding="utf-8") as f:
            lm = json.load(f)
        timesteps = int(lm["meta"]["timesteps"])
        model_fs = float(lm["meta"]["fs"])
        flag_names = lm["flag_names"]
        out["import_s"] = t_imp - t0
    else:
        out["runtime"] = runtime_name()
        bundle = rt.load_model_bundle(args.worker_model, args.label_map)
        timesteps, model_fs, flag_names = bundle["timesteps"], bundle["model_fs"], bundle["flag_names"]
        interp = bundle["interpreter"]
        out["input_dtype"] = str(interp.get_input_details()[0]["dtype"].__name__)
    out["load_s"] = time.perf_counter() - t0
    out["rss_after_load_mb"] = rss_mb()

    feat_t, infer_t, tick_t, flags_log = [], [], [], []
    temp_before = cpu_temp_c()
    cpu0, wall0 = time.process_time(), time.perf_counter()
    for i, (t_end, imu_rows, gps_rows) in enumerate(
            iter_windows(imu, gps, args.t_start, args.n_ticks + args.warmup, args.stride, args.buffer)):
        s0 = time.perf_counter()
        t_arr, feats = rt.build_features_from_buffer(imu_rows, gps_rows, model_fs, IMU_HZ, GPS_HZ)
        s1 = time.perf_counter()
        if len(t_arr) < timesteps:
            continue
        window = feats[-timesteps:]
        if is_keras:
            probs = model(np.expand_dims(window.astype(np.float32), 0), training=False).numpy()[0]
            active = [flag_names[k] for k, p in enumerate(probs) if p >= 0.5]
        else:
            active, _ = rt.decode_window(bundle, window)
        s2 = time.perf_counter()
        if i < args.warmup:
            continue
        feat_t.append(s1 - s0)
        infer_t.append(s2 - s1)
        tick_t.append(s2 - s0)
        flags_log.append(sorted(active))
    cpu_s, wall_s = time.process_time() - cpu0, time.perf_counter() - wall0

    tick_mean = float(np.mean(tick_t)) if tick_t else float("nan")
    out.update({
        "n_ticks": len(tick_t),
        "feature_ms": stats_ms(feat_t),
        "inference_ms": stats_ms(infer_t),
        "tick_ms": stats_ms(tick_t),
        "cpu_util_backtoback_pct": 100.0 * cpu_s / wall_s if wall_s > 0 else float("nan"),
        "duty_cycle_at_stride_pct": 100.0 * tick_mean / args.stride,
        "rss_end_mb": rss_mb(),
        "peak_rss_mb": peak_rss_mb(),
        "cpu_temp_before_c": temp_before,
        "cpu_temp_after_c": cpu_temp_c(),
        "throttled": throttled_flag(),
        "flags": flags_log,
    })
    print(json.dumps(out))


def run_child(model_path, args):
    cmd = [sys.executable, os.path.abspath(__file__), "--worker-model", model_path,
           "--trip", args.trip, "--label-map", args.label_map, "--t-start", str(args.t_start),
           "--n-ticks", str(args.n_ticks), "--warmup", str(args.warmup),
           "--stride", str(args.stride), "--buffer", str(args.buffer)]
    print(f"\n>>> Dang do: {os.path.basename(model_path)}", flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        return {"model": os.path.basename(model_path), "error": res.stderr.strip().splitlines()[-1:]}
    return json.loads(res.stdout.strip().splitlines()[-1])


def fmt_report(results, meta):
    L = ["=" * 78, "BENCHMARK PHAT LAI TREN THIET BI BIEN - DriveSafe", "=" * 78]
    for k, v in meta.items():
        L.append(f"{k:<22}: {v}")
    ok = [r for r in results if "error" not in r]
    L.append("")
    hdr = f"{'Chi so':<34}" + "".join(f"{r['model'].replace('driversafe_event_classifier_', ''):>18}" for r in ok)
    L.append(hdr)
    L.append("-" * len(hdr))

    def row(name, fn, fmt="{:.2f}"):
        vals = []
        for r in ok:
            try:
                vals.append(fmt.format(fn(r)))
            except Exception:
                vals.append("-")
        L.append(f"{name:<34}" + "".join(f"{v:>18}" for v in vals))
    row("Runtime", lambda r: r["runtime"], "{}")
    row("Dung luong file (KB)", lambda r: r["file_kb"], "{:.1f}")
    row("Thoi gian nap mo hinh (s)", lambda r: r["load_s"], "{:.3f}")
    row("So tick do", lambda r: r["n_ticks"], "{}")
    row("Suy luan TB (ms)", lambda r: r["inference_ms"]["mean"])
    row("Suy luan p50 / p95 (ms)", lambda r: f"{r['inference_ms']['p50']:.2f}/{r['inference_ms']['p95']:.2f}", "{}")
    row("Suy luan p99 / max (ms)", lambda r: f"{r['inference_ms']['p99']:.2f}/{r['inference_ms']['max']:.2f}", "{}")
    row("Tinh dac trung TB (ms)", lambda r: r["feature_ms"]["mean"])
    row("Ca tick (dac trung+suy luan) TB", lambda r: r["tick_ms"]["mean"])
    row("Ca tick p95 (ms)", lambda r: r["tick_ms"]["p95"])
    row("Tai CPU o nhip 0.3 s (%)", lambda r: r["duty_cycle_at_stride_pct"], "{:.1f}")
    row("RAM sau nap mo hinh (MB)", lambda r: r["rss_after_load_mb"], "{:.1f}")
    row("RAM tang do nap mo hinh (MB)", lambda r: r["rss_after_load_mb"] - r["rss_after_data_mb"], "{:.1f}")
    row("RAM dinh tien trinh (MB)", lambda r: r["peak_rss_mb"], "{:.1f}")
    row("Nhiet do CPU truoc/sau (C)", lambda r: f"{r['cpu_temp_before_c']:.1f}/{r['cpu_temp_after_c']:.1f}", "{}")
    row("vcgencmd get_throttled", lambda r: r["throttled"], "{}")
    for r in results:
        if "error" in r:
            L.append(f"[LOI] {r['model']}: {r['error']}")

    by = {r["model"]: r for r in ok}
    f32 = by.get("driversafe_event_classifier_fp32.tflite")
    i8 = by.get("driversafe_event_classifier_int8.tflite")
    if f32 and i8 and len(f32["flags"]) == len(i8["flags"]):
        n = len(f32["flags"])
        same = sum(a == b for a, b in zip(f32["flags"], i8["flags"]))
        L.append("")
        L.append(f"Do khop dau ra FP32 vs INT8 tren cung {n} cua so: {100.0 * same / n:.1f}% tick trung khop hoan toan bo co")
        names = sorted({f for fl in f32["flags"] + i8["flags"] for f in fl})
        for nm in names:
            a = np.array([nm in fl for fl in f32["flags"]])
            b = np.array([nm in fl for fl in i8["flags"]])
            L.append(f"   {nm:<20} FP32 bat {a.mean() * 100:5.1f}% | INT8 bat {b.mean() * 100:5.1f}% | khop {100 * (a == b).mean():5.1f}%")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Benchmark phat lai TFLite FP32/INT8 tren Pi 3")
    ap.add_argument("--trip", required=True, help="thu muc trip co RAW_ACCELEROMETERS.txt + RAW_GPS.txt")
    ap.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    ap.add_argument("--label-map", default=None)
    ap.add_argument("--keras", default=None, help="duong dan .keras (tuy chon, can tensorflow day du)")
    ap.add_argument("--t-start", type=float, default=60.0, help="giay bat dau phat lai trong trip")
    ap.add_argument("--n-ticks", type=int, default=1000)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--stride", type=float, default=0.3)
    ap.add_argument("--buffer", type=float, default=3.0)
    ap.add_argument("--worker-model", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.label_map = args.label_map or os.path.join(args.model_dir, "label_map.json")

    if args.worker_model:
        worker(args)
        return

    models = [os.path.join(args.model_dir, m) for m in DEFAULT_MODELS]
    if args.keras:
        models.append(args.keras)
    meta = {"thoi_diem": time.strftime("%Y-%m-%d %H:%M:%S"), "trip": os.path.basename(args.trip.rstrip("/")),
            "t_start_s": args.t_start, "n_ticks": args.n_ticks, "stride_s": args.stride, "buffer_s": args.buffer,
            **system_info()}
    results = [run_child(m, args) for m in models]

    out_dir = os.path.join(HERE, "bench_results", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    report = fmt_report(results, meta)
    with open(os.path.join(out_dir, "bench_pi3_report.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n")
    slim = [{k: v for k, v in r.items() if k != "flags"} for r in results]
    with open(os.path.join(out_dir, "bench_pi3_report.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "results": slim}, f, ensure_ascii=False, indent=2)
    print("\n" + report)
    print(f"\nDa ghi: {out_dir}")


if __name__ == "__main__":
    main()
