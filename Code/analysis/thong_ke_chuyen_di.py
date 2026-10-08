"""Thong ke su kien chinh + danh gia chat luong du lieu cho tung chuyen di (trip).

Dau vao (moi trip trong RAW_ROOT):
    <trip>/detected_events_unified/detected_driving_events_unified.csv  (nhan heuristic)
    <trip>/RAW_ACCELEROMETERS.txt, <trip>/RAW_GPS.txt                    (du lieu tho)
    <trip>/events.csv                                                    (nhan PS5, neu con)

Dau ra:
    <trip>/<trip>_main_events.csv     su kien chinh BRAKE / ACCEL / STEER (va to hop)
    <trip>/<trip>_data_quality.txt    tan so IMU/GPS, cac doan gian doan, thong ke su kien
    Final_Project/thongke_output/tong_hop_chuyen_di.csv   bang tong hop cho bao cao

Gom nhom su kien dung bang anh xa 33 nhan -> 15 co cua chinh mo hinh
(modular_app/core/label_schema.py::LABEL_TO_FLAGS), khong tu dinh nghia lai.

Chay:  python thong_ke_chuyen_di.py [--root <0-Trips>]
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

PI3_REPO = r"D:\0. UIT\0 - KHOALUAN\Pi3\raw_data_collection"
RAW_ROOT = os.path.join(PI3_REPO, "raw_data", "0-Trips")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thongke_output")

sys.path.insert(0, os.path.join(PI3_REPO, "modular_app"))
from core.label_schema import LABEL_TO_FLAGS  # noqa: E402

# Nguon nhan theo ngay chuyen di (theo quy trinh thuc te cua nhom, xem DeXuat_TaiCauTruc_DeTai.md 4.2)
LABEL_SOURCE_BY_DATE = {
    "26092026": "THU_CONG+HEURISTIC",
    "27092026": "THU_CONG+HEURISTIC",
    "28092026": "THU_CONG+HEURISTIC",
    "29092026": "THU_CONG+HEURISTIC",
    "30092026": "THU_CONG+HEURISTIC",
    "01102026": "HEURISTIC+KIEM_CHUNG_THU_CONG",
}

GAP_SECONDS = 1.0        # giong ai_pipeline.GAP_SECONDS: dut tin hieu > 1s => cat doan
SHORT_DROPOUT_SEC = 0.05  # > 5 chu ky IMU (100Hz) nhung <= 1s: mat mau ngan
IMU_NOMINAL_HZ = 100.0
GPS_NOMINAL_HZ = 10.0

IMU_COLS = ["time", "acc_x", "acc_y", "acc_z", "yaw", "roll", "pitch",
            "gyr_x", "gyr_y", "gyr_z", "quat_w", "quat_x", "quat_y", "quat_z"]
GPS_COLS = ["time", "utc", "lat", "lon", "hdop", "alt", "fix", "cog",
            "speed_kmh", "speed_knots", "date", "satellites"]

GROUP_FLAGS = {
    "BRAKE": ("BRAKE_HARD", "BRAKE_MODERATE"),
    "ACCEL": ("ACCEL_HARD", "ACCEL_MODERATE"),
    "STEER": ("STEERING", "STEERING_ANOMALY"),
}
SPEED_FLAGS = ("SPEED_G10", "SPEED_G20", "SPEED_G40", "SPEED_G80")
MAIN_COLUMNS = ["timestamp", "event_type", "start_time", "end_time", "label_source",
                "heuristic_label", "severity", "speed_group", "duration_s",
                "speed_start_kmh", "speed_end_kmh", "min_accel", "max_accel", "max_gyr_z",
                "lat", "lon"]


def trip_start_time(trip_name):
    return datetime.strptime(trip_name, "%d%m%Y_%H%M%S")


def classify_label(label):
    """Tra ve (event_type, severity, speed_group) hoac None neu khong phai su kien chinh."""
    flags = set(LABEL_TO_FLAGS.get(label, ()))
    groups = [g for g, fl in GROUP_FLAGS.items() if flags & set(fl)]
    if not groups:
        return None
    if {"BRAKE_HARD", "ACCEL_HARD"} & flags:
        severity = "HARD"
    elif {"BRAKE_MODERATE", "ACCEL_MODERATE"} & flags:
        severity = "MODERATE"
    elif "STEERING_ANOMALY" in flags:
        severity = "ANOMALY"
    else:
        severity = "NORMAL"
    speed = next((f.replace("SPEED_", "") for f in SPEED_FLAGS if f in flags), "-")
    return "+".join(groups), severity, speed


def build_main_events(trip, events, label_source):
    t0 = trip_start_time(trip)
    rows = []
    for _, ev in events.iterrows():
        cls = classify_label(ev["event_type"])
        if cls is None:
            continue
        event_type, severity, speed = cls
        rows.append({
            "timestamp": (t0 + timedelta(seconds=float(ev["start_time"]))).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "event_type": event_type,
            "start_time": round(float(ev["start_time"]), 3),
            "end_time": round(float(ev["end_time"]), 3),
            "label_source": label_source,
            "heuristic_label": ev["event_type"],
            "severity": severity,
            "speed_group": speed,
            "duration_s": round(float(ev["duration"]), 3),
            "speed_start_kmh": ev["speed_start"],
            "speed_end_kmh": ev["speed_end"],
            "min_accel": ev["min_accel"],
            "max_accel": ev["max_accel"],
            "max_gyr_z": ev["max_gyr_z"],
            "lat": ev["lat"],
            "lon": ev["lon"],
        })
    return pd.DataFrame(rows, columns=MAIN_COLUMNS)


def read_raw(path, cols):
    df = pd.read_csv(path, sep=r"\s+", header=None, names=cols, usecols=range(len(cols)),
                     dtype={"utc": str, "date": str}, on_bad_lines="skip", engine="c")
    df["time"] = pd.to_numeric(df["time"], errors="coerce")
    return df.dropna(subset=["time"]).reset_index(drop=True)


def find_gaps(t, gap_sec):
    dt = np.diff(t)
    idx = np.where(dt > gap_sec)[0]
    return [(float(t[i]), float(t[i + 1]), float(dt[i])) for i in idx], dt


def segments_from_gaps(t, gaps):
    bounds = [float(t[0])] + [g for gap in gaps for g in (gap[0], gap[1])] + [float(t[-1])]
    return [(bounds[i], bounds[i + 1]) for i in range(0, len(bounds), 2)]


def imu_quality(t):
    span = float(t[-1] - t[0])
    gaps, dt = find_gaps(t, GAP_SECONDS)
    return {
        "rows": len(t),
        "span_s": span,
        "mean_hz": (len(t) - 1) / span if span > 0 else float("nan"),
        "median_hz": 1.0 / float(np.median(dt)) if len(dt) else float("nan"),
        "non_increasing": int((dt <= 0).sum()),
        "short_dropouts": int(((dt > SHORT_DROPOUT_SEC) & (dt <= GAP_SECONDS)).sum()),
        "gaps": gaps,
        "segments": segments_from_gaps(t, gaps),
    }


def gps_quality(gps):
    t = gps["time"].to_numpy()
    span = float(t[-1] - t[0])
    gaps, dt = find_gaps(t, GAP_SECONDS)
    # cot UTC co the dung yen (khong cap nhat) => do tan so cap nhat hieu dung bang
    # so dong ma vi tri/toc do thuc su thay doi so voi dong truoc
    changed = (gps[["lat", "lon", "speed_kmh"]].diff().abs().sum(axis=1) > 0).sum()
    fix = pd.to_numeric(gps["fix"], errors="coerce")
    return {
        "rows": len(t),
        "span_s": span,
        "mean_hz": (len(t) - 1) / span if span > 0 else float("nan"),
        "median_hz": 1.0 / float(np.median(dt)) if len(dt) else float("nan"),
        "effective_update_hz": changed / span if span > 0 else float("nan"),
        "utc_unique": int(gps["utc"].nunique()),
        "fix_ok_pct": 100.0 * float((fix > 0).mean()),
        "hdop_median": float(pd.to_numeric(gps["hdop"], errors="coerce").median()),
        "gaps": gaps,
    }


def count_ps5_events(trip_dir):
    path = os.path.join(trip_dir, "events.csv")
    if not os.path.isfile(path):
        return 0
    return max(0, len(pd.read_csv(path)))


def fmt_hms(sec):
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def write_quality_report(path, trip, label_source, imu, gps, main_df, all_events, ps5_count):
    t0 = trip_start_time(trip)
    L = []
    L.append("=" * 72)
    L.append(f"BAO CAO CHAT LUONG DU LIEU & THONG KE SU KIEN - TRIP {trip}")
    L.append("=" * 72)
    L.append(f"Thoi diem bat dau phien ghi : {t0:%Y-%m-%d %H:%M:%S}")
    L.append(f"Thoi luong (theo IMU)       : {imu['span_s']:.1f} s ({fmt_hms(imu['span_s'])})")
    L.append(f"Nguon nhan                  : {label_source}")
    L.append(f"So su kien PS5 (events.csv) : {ps5_count}")
    L.append("")
    L.append("--- 1. IMU (BNO055, danh nghia 100 Hz) ---")
    L.append(f"So mau                      : {imu['rows']:,}")
    L.append(f"Tan so trung binh           : {imu['mean_hz']:.2f} Hz (tinh ca khoang gian doan)")
    L.append(f"Tan so trung vi (1/median dt): {imu['median_hz']:.2f} Hz")
    L.append(f"Moc thoi gian khong tang    : {imu['non_increasing']}")
    L.append(f"Mat mau ngan (0.05-1.0 s)   : {imu['short_dropouts']}")
    L.append(f"So doan gian doan (> {GAP_SECONDS:.1f} s) : {len(imu['gaps'])}")
    for i, (a, b, d) in enumerate(imu["gaps"], 1):
        L.append(f"   Gian doan {i:>2}: {a:9.3f} s -> {b:9.3f} s  (mat {d:.3f} s)")
    L.append(f"So phan doan lien tuc       : {len(imu['segments'])}")
    for i, (a, b) in enumerate(imu["segments"], 1):
        L.append(f"   Doan {i:>2}: {a:9.3f} s -> {b:9.3f} s  ({b - a:.1f} s)")
    L.append("")
    L.append(f"--- 2. GPS (Quectel EG800K, danh nghia {GPS_NOMINAL_HZ:.0f} Hz) ---")
    L.append(f"So dong                     : {gps['rows']:,}")
    L.append(f"Tan so ghi trung binh       : {gps['mean_hz']:.2f} Hz")
    L.append(f"Tan so ghi trung vi         : {gps['median_hz']:.2f} Hz")
    L.append(f"Tan so cap nhat hieu dung   : {gps['effective_update_hz']:.2f} Hz (dong co lat/lon/toc do thay doi)")
    L.append(f"So gia tri UTC khac nhau    : {gps['utc_unique']}")
    L.append(f"Ty le co fix (fix > 0)      : {gps['fix_ok_pct']:.1f} %")
    L.append(f"HDOP trung vi               : {gps['hdop_median']:.2f}")
    L.append(f"So doan gian doan (> {GAP_SECONDS:.1f} s) : {len(gps['gaps'])}")
    for i, (a, b, d) in enumerate(gps["gaps"], 1):
        L.append(f"   Gian doan {i:>2}: {a:9.3f} s -> {b:9.3f} s  (mat {d:.3f} s)")
    L.append("")
    L.append("--- 3. Su kien heuristic (toan bo 33 nhan) ---")
    L.append(f"Tong so su kien             : {len(all_events)}")
    for lab, n in all_events["event_type"].value_counts().items():
        L.append(f"   {lab:<24} {n:>5}")
    L.append("")
    L.append("--- 4. Su kien chinh (BRAKE / ACCEL / STEER) ---")
    L.append(f"Tong so su kien chinh       : {len(main_df)}")
    if len(main_df):
        pivot = pd.crosstab(main_df["event_type"], main_df["severity"], margins=True, margins_name="TONG")
        L.extend("   " + line for line in pivot.to_string().split("\n"))
        L.append("")
        L.append("Theo dai toc do:")
        sp = pd.crosstab(main_df["event_type"], main_df["speed_group"])
        L.extend("   " + line for line in sp.to_string().split("\n"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def process_trip(root, trip):
    trip_dir = os.path.join(root, trip)
    ev_path = os.path.join(trip_dir, "detected_events_unified", "detected_driving_events_unified.csv")
    if not os.path.isfile(ev_path):
        print(f"[BO QUA] {trip}: khong co detected_driving_events_unified.csv")
        return None
    label_source = LABEL_SOURCE_BY_DATE.get(trip[:8], "HEURISTIC")
    events = pd.read_csv(ev_path, encoding="utf-8-sig")
    main_df = build_main_events(trip, events, label_source)
    main_df.to_csv(os.path.join(trip_dir, f"{trip}_main_events.csv"), index=False, encoding="utf-8-sig")

    imu = imu_quality(read_raw(os.path.join(trip_dir, "RAW_ACCELEROMETERS.txt"), IMU_COLS)["time"].to_numpy())
    gps = gps_quality(read_raw(os.path.join(trip_dir, "RAW_GPS.txt"), GPS_COLS))
    ps5 = count_ps5_events(trip_dir)
    write_quality_report(os.path.join(trip_dir, f"{trip}_data_quality.txt"),
                         trip, label_source, imu, gps, main_df, events, ps5)

    def n(cond):
        return int(cond.sum())
    et, sv = main_df["event_type"], main_df["severity"]
    return {
        "trip": trip,
        "start": f"{trip_start_time(trip):%Y-%m-%d %H:%M}",
        "duration_min": round(imu["span_s"] / 60.0, 1),
        "label_source": label_source,
        "ps5_events": ps5,
        "imu_mean_hz": round(imu["mean_hz"], 2),
        "imu_median_hz": round(imu["median_hz"], 2),
        "imu_gaps": len(imu["gaps"]),
        "imu_segments": len(imu["segments"]),
        "gps_mean_hz": round(gps["mean_hz"], 2),
        "gps_effective_hz": round(gps["effective_update_hz"], 2),
        "gps_gaps": len(gps["gaps"]),
        "heuristic_events": len(events),
        "main_events": len(main_df),
        "brake_hard": n(et.str.contains("BRAKE") & (sv == "HARD")),
        "brake_moderate": n(et.str.contains("BRAKE") & (sv == "MODERATE")),
        "accel_hard": n(et.str.contains("ACCEL") & (sv == "HARD")),
        "accel_moderate": n(et.str.contains("ACCEL") & (sv == "MODERATE")),
        "steer_total": n(et.str.contains("STEER")),
        "steer_only": n(et == "STEER"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=RAW_ROOT, help="thu muc chua cac trip (mac dinh: 0-Trips)")
    args = ap.parse_args()

    trips = sorted((d for d in os.listdir(args.root) if re.fullmatch(r"\d{8}_\d{6}", d)),
                   key=trip_start_time)
    rows = [r for r in (process_trip(args.root, t) for t in trips) if r]
    summary = pd.DataFrame(rows)
    total = summary.select_dtypes("number").sum(numeric_only=True)
    for col in ("imu_mean_hz", "imu_median_hz", "gps_mean_hz", "gps_effective_hz"):
        total[col] = round(summary[col].mean(), 2)  # trung binh, khong cong don
    total_row = {"trip": "TONG/TB", **total.to_dict()}
    int_cols = [c for c in summary.columns if pd.api.types.is_integer_dtype(summary[c])]
    summary = pd.concat([summary, pd.DataFrame([total_row])], ignore_index=True)
    summary[int_cols] = summary[int_cols].astype("Int64")

    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, "tong_hop_chuyen_di.csv")
    summary.to_csv(out_csv, index=False, encoding="utf-8-sig")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(summary.to_string(index=False))
    print(f"\nDa ghi {len(rows)} trip. Tong hop: {out_csv}")


if __name__ == "__main__":
    main()
