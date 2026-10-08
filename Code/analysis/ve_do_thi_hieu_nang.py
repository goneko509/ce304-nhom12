"""Ve Hinh so sanh hieu nang nhung (Chuong 4, fig:edge_metrics_chart).

Nguon so lieu (khong go tay):
  - 20261003_032130/bench_pi3_report.json   (do tren Raspberry Pi 3, 1000 nhip, lan 3: tan nhiet, throttled=0x0)
  - Pi3 repo models/multi_trip_training_report.json (dung luong, Macro-F1)
Xuat: Latex/imgs/edge_metrics.pdf
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(HERE, "20261003_032130", "bench_pi3_report.json")
TRAIN = r"D:\0. UIT\0 - KHOALUAN\Pi3\raw_data_collection\models\multi_trip_training_report.json"
OUT = os.path.join(HERE, "Latex", "imgs", "edge_metrics.pdf")

INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#ffffff"
S1, S2 = "#2a78d6", "#eb6834"  # slot 1 (tinh dac trung), slot 2 (suy luan)

bench = {r["model"]: r for r in json.load(open(BENCH, encoding="utf-8"))["results"]}
train = json.load(open(TRAIN, encoding="utf-8"))
bm = train["audit_benchmark_results"]
f1 = train["audit_f1_results"]

names = ["Keras FP32", "TFLite FP32", "TFLite INT8"]
size_kb = [bm["Keras FP32 Model"]["file_size_kb"], bm["TFLite FP32 Model"]["file_size_kb"],
           bm["TFLite INT8 Model"]["file_size_kb"]]
macro = [100 * f1["Keras FP32 Model"], 100 * f1["TFLite FP32 Model"], 100 * f1["TFLite INT8 Model"]]
pi = [bench["driversafe_event_classifier_fp32.tflite"], bench["driversafe_event_classifier_int8.tflite"]]
feat = [r["feature_ms"]["mean"] for r in pi]
infer = [r["inference_ms"]["mean"] for r in pi]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4), facecolor=SURFACE)


def style(ax, title):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=10, loc="left")
    ax.grid(True, axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c9c8c3")
    ax.tick_params(colors=INK2)


ax = axes[0]
bars = ax.bar(names, size_kb, color=S1, width=0.6)
ax.bar_label(bars, labels=[f"{v:,.0f}".replace(",", ".") for v in size_kb], padding=2, color=INK2, fontsize=8.5)
style(ax, "(a) Dung lượng tệp mô hình (KB)")

ax = axes[1]
x = ["TFLite FP32", "TFLite INT8"]
ax.bar(x, feat, color=S1, width=0.55, label="Tính đặc trưng (NumPy/SciPy)", edgecolor=SURFACE, linewidth=2)
top = ax.bar(x, infer, bottom=feat, color=S2, width=0.55, label="Suy luận TFLite", edgecolor=SURFACE, linewidth=2)
for b, f_, i_ in zip(top, feat, infer):
    ax.text(b.get_x() + b.get_width() / 2, f_ + i_ + 1.5, f"{f_ + i_:.1f} ms\n(suy luận {i_:.2f})".replace(".", ","),
            ha="center", va="bottom", color=INK2, fontsize=8.5)
ax.set_ylim(0, max(f + i for f, i in zip(feat, infer)) * 1.8)
ax.legend(frameon=False, fontsize=8, loc="upper center", labelcolor=INK2)
style(ax, "(b) Thời gian 1 nhịp trên Raspberry Pi 3 (ms)")

ax = axes[2]
bars = ax.bar(names, macro, color=S1, width=0.6)
ax.bar_label(bars, labels=[f"{v:.2f}%".replace(".", ",") for v in macro], padding=2, color=INK2, fontsize=8.5)
ax.set_ylim(0, 70)
style(ax, "(c) Macro-F1 trên tập kiểm thử (%)")

for ax in axes:
    for lab in ax.get_xticklabels():
        lab.set_fontsize(8.5)
fig.tight_layout()
fig.savefig(OUT, facecolor=SURFACE)
fig.savefig(OUT.replace(".pdf", ".png"), dpi=150, facecolor=SURFACE)
print("Da ghi:", OUT)
