"""Phan tich chuyen chay thuc dia: mo hinh INT8 suy luan TRUC TIEP tren Pi 3 khi xe chay.

Dau vao (thu muc trip):  RAW_ACCELEROMETERS.txt, RAW_GPS.txt, realtime_events.log
Cac buoc:
  1. Chat luong du lieu khi BAT suy luan (tan so IMU, gian doan) - tai su dung thong_ke_chuyen_di.
  2. Nhip suy luan thuc te tu realtime_events.log (t = moc thoi gian tuong doi cua IMU).
  3. Chay lai bo luat heuristic (Step 8) tren chinh du lieu tho cua chuyen nay -> nhan doi chieu.
  4. Doi chieu muc su kien theo 3 nhom BRAKE / ACCEL / STEER (+ STOP):
       - Do phu  = ti le su kien heuristic co it nhat 1 nhip mo hinh bat co cung nhom trong [start, end + 2 s]
       - Do dung = ti le dot canh bao cua mo hinh (cac nhip lien tiep) trung voi 1 su kien heuristic cung nhom
  5. Ve do thi dong thoi gian.
Luu y: realtime_events.log chi ghi nhip co it nhat 1 co khac NORMAL (xem realtime_inference._log_result).

Chay:  python phan_tich_thuc_dia.py --trip <thu muc trip>
"""
import argparse
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from thong_ke_chuyen_di import GPS_COLS, IMU_COLS, PI3_REPO, gps_quality, imu_quality, read_raw  # noqa: E402

sys.path.insert(0, os.path.join(PI3_REPO, "modular_app"))
from core import multi_trip_pipeline as mtp  # noqa: E402
from core.label_schema import LABEL_TO_FLAGS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WINDOW_SEC = 2.0
EPISODE_GAP = 0.65  # 2 nhip 0.3 s lien tiep (co dung sai) => cung 1 dot canh bao
GROUPS = {
    "BRAKE": {"BRAKE_HARD", "BRAKE_MODERATE"},
    "ACCEL": {"ACCEL_HARD", "ACCEL_MODERATE"},
    "STEER": {"STEERING", "STEERING_ANOMALY"},
    "STOP": {"STOP"},
}
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#ffffff"
SERIES = {"BRAKE": "#2a78d6", "ACCEL": "#eb6834", "STEER": "#1baf7a", "STOP": "#4a3aa7"}


def parse_log(path):
    rows = []
    pat = re.compile(r"\[(\d\d):(\d\d):(\d\d)\] t=\s*([\d.]+)s\s+(\S+)")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pat.match(line)
            if m:
                rows.append((float(m.group(4)), set(m.group(5).split("+"))))
    return rows


def episodes(times):
    """Gop cac nhip lien tiep thanh dot [t_first - WINDOW_SEC, t_last]."""
    if len(times) == 0:
        return []
    out, s, p = [], times[0], times[0]
    for t in times[1:]:
        if t - p > EPISODE_GAP:
            out.append((s - WINDOW_SEC, p))
            s = t
        p = t
    out.append((s - WINDOW_SEC, p))
    return out


def heuristic_events_by_group(events):
    res = {g: [] for g in GROUPS}
    for _, ev in events.iterrows():
        flags = set(LABEL_TO_FLAGS.get(ev["event_type"], ()))
        for g, fl in GROUPS.items():
            if flags & fl:
                res[g].append((float(ev["start_time"]), float(ev["end_time"]), ev["event_type"]))
    return res


def overlap(a0, a1, b0, b1, tol=0.0):
    return a0 <= b1 + tol and b0 <= a1 + tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trip", required=True)
    args = ap.parse_args()
    trip_dir = os.path.abspath(args.trip)
    trip = os.path.basename(trip_dir.rstrip("\\/"))
    out_dir = os.path.join(HERE, "thongke_output", f"thuc_dia_{trip}")
    os.makedirs(out_dir, exist_ok=True)

    imu = imu_quality(read_raw(os.path.join(trip_dir, "RAW_ACCELEROMETERS.txt"), IMU_COLS)["time"].to_numpy())
    gps_df = read_raw(os.path.join(trip_dir, "RAW_GPS.txt"), GPS_COLS)
    gps = gps_quality(gps_df)
    speed = pd.to_numeric(gps_df["speed_kmh"], errors="coerce")

    log = parse_log(os.path.join(trip_dir, "realtime_events.log"))
    t_log = np.array([r[0] for r in log])
    dt = np.diff(t_log)
    expected = (t_log[-1] - t_log[0]) / 0.3 + 1

    print("Chay bo luat heuristic tren du lieu chuyen thuc dia...")
    events_dir = mtp.ensure_trip_preprocessed(trip_dir, log=lambda m: None)
    events = pd.read_csv(os.path.join(events_dir, "detected_driving_events_unified.csv"), encoding="utf-8-sig")
    heur = heuristic_events_by_group(events)

    rows, model_eps = [], {}
    for g, fl in GROUPS.items():
        times = np.array([t for t, f in log if f & fl])
        eps = episodes(times)
        model_eps[g] = eps
        hev = heur[g]
        covered = sum(any(s <= t <= e + WINDOW_SEC for t in times[(times >= s) & (times <= e + WINDOW_SEC)])
                      for s, e, _ in hev) if len(times) else 0
        correct = sum(any(overlap(a, b, s, e, tol=1.0) for s, e, _ in hev) for a, b in eps)
        rows.append({
            "nhom": g, "su_kien_heuristic": len(hev), "dot_canh_bao_mo_hinh": len(eps),
            "nhip_mo_hinh_bat": int(len(times)),
            "do_phu_pct": 100.0 * covered / len(hev) if hev else float("nan"),
            "do_dung_pct": 100.0 * correct / len(eps) if eps else float("nan"),
        })
    cmp_df = pd.DataFrame(rows)

    flag_counts = pd.Series([f for _, fs in log for f in fs]).value_counts()
    summary = {
        "trip": trip,
        "thoi_luong_s": imu["span_s"],
        "imu_mean_hz": imu["mean_hz"], "imu_median_hz": imu["median_hz"], "imu_gaps_gt1s": len(imu["gaps"]),
        "imu_short_dropouts": imu["short_dropouts"],
        "gps_mean_hz": gps["mean_hz"], "gps_effective_hz": gps["effective_update_hz"],
        "toc_do_tb_kmh": float(speed.mean()), "toc_do_max_kmh": float(speed.max()),
        "pct_thoi_gian_tren_5kmh": float((speed > 5).mean() * 100),
        "so_nhip_log": len(t_log), "so_nhip_ky_vong_0_3s": float(expected),
        "nhip_trung_vi_s": float(np.median(dt)), "nhip_max_s": float(dt.max()), "so_khoang_lon_hon_0_5s": int((dt > 0.5).sum()),
        "su_kien_heuristic_tong": int(len(events)),
        "dem_co_mo_hinh": flag_counts.to_dict(),
        "doi_chieu": rows,
    }
    with open(os.path.join(out_dir, "tom_tat_thuc_dia.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=float)

    L = [f"PHAN TICH CHUYEN THUC DIA {trip} (INT8 suy luan truc tiep tren Raspberry Pi 3)", "=" * 72,
         f"Thoi luong: {imu['span_s']:.1f} s ({imu['span_s'] / 60:.1f} phut) | toc do TB {speed.mean():.1f} km/h, "
         f"max {speed.max():.1f} km/h, {summary['pct_thoi_gian_tren_5kmh']:.1f}% thoi gian > 5 km/h",
         f"IMU khi bat AI: TB {imu['mean_hz']:.2f} Hz, trung vi {imu['median_hz']:.2f} Hz, gian doan >1 s: {len(imu['gaps'])}, "
         f"mat mau ngan: {imu['short_dropouts']}",
         f"GPS: ghi {gps['mean_hz']:.2f} dong/s, cap nhat hieu dung {gps['effective_update_hz']:.2f} Hz",
         f"Nhip suy luan: {len(t_log)} nhip ghi log / ~{expected:.0f} nhip ky vong; trung vi {np.median(dt):.3f} s, "
         f"max {dt.max():.3f} s, so khoang > 0.5 s: {(dt > 0.5).sum()}",
         f"Su kien heuristic (sinh lai tu du lieu tho): {len(events)}", "",
         "Doi chieu muc su kien (mo hinh tren xe vs nhan heuristic):", cmp_df.to_string(index=False), "",
         "So nhip mo hinh bat tung co:", flag_counts.to_string()]
    text = "\n".join(L)
    with open(os.path.join(out_dir, "phan_tich_thuc_dia.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)

    # ---- do thi dong thoi gian
    t_gps = gps_df["time"].to_numpy() / 60.0
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(11, 5.2), sharex=True, facecolor=SURFACE,
                                   gridspec_kw={"height_ratios": [1.1, 1.6]})
    ax0.plot(t_gps, speed, color=INK2, lw=0.9)
    ax0.set_ylabel("Tốc độ (km/h)", color=INK2, fontsize=9)
    lanes = list(GROUPS)
    for i, g in enumerate(lanes):
        y_h, y_m = 2 * i + 1.0, 2 * i + 0.4
        for s, e, _ in heur[g]:
            ax1.add_patch(plt.Rectangle((s / 60, y_h - 0.22), max((e - s) / 60, 0.03), 0.44, color=SERIES[g], alpha=0.45, lw=0))
        for a, b in model_eps[g]:
            ax1.add_patch(plt.Rectangle((a / 60, y_m - 0.22), max((b - a) / 60, 0.03), 0.44, color=SERIES[g], lw=0))
    ax1.set_yticks([2 * i + y for i in range(len(lanes)) for y in (0.4, 1.0)])
    ax1.set_yticklabels([f"{g} · {s}" for g in lanes for s in ("mô hình (Pi)", "heuristic")], fontsize=8, color=INK2)
    ax1.set_ylim(0, 2 * len(lanes))
    ax1.set_xlim(0, imu["span_s"] / 60)
    ax1.set_xlabel("Thời gian trong chuyến (phút)", color=INK2, fontsize=9)
    for ax in (ax0, ax1):
        ax.set_facecolor(SURFACE)
        ax.grid(True, axis="x", color=GRID, lw=0.6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        for s in ("left", "bottom"):
            ax.spines[s].set_color("#c9c8c3")
        ax.tick_params(colors=INK2, labelsize=8)
    fig.suptitle(f"Chuyến thực địa {trip}: cờ do mô hình INT8 phát hiện trên Pi 3 (đậm) và nhãn heuristic (nhạt)",
                 color=INK, fontsize=10, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(out_dir, f"dong_thoi_gian_thuc_dia.{ext}"), dpi=150, facecolor=SURFACE)
    print(f"\nDa ghi: {out_dir}")


if __name__ == "__main__":
    main()
