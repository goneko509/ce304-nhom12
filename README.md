# DriveSafe — Hệ thống AIoT phát hiện sự kiện lái xe gấp trên thiết bị biên Raspberry Pi 3

Đồ án môn **CE340 — Trí tuệ nhân tạo cho hệ thống nhúng** (UIT, ĐHQG-HCM), GVHD: ThS. Phan Đình Duy — Nhóm 12:
Nguyễn Tấn Viễn (25410333), Huỳnh Văn Tú An (25730005).

Hệ thống hiện thực trọn vẹn **quy trình thiết kế AI cho hệ thống nhúng**: thu thập dữ liệu IMU/GPS trên xe →
tiền xử lý & gán nhãn heuristic → huấn luyện Conv1D đa nhãn (15 cờ) → lượng tử hóa INT8 (PTQ) → suy luận thời
gian thực trên Raspberry Pi 3 → hiển thị cảnh báo trên HUD web.

```
 IMU BNO055 (I2C, 100 Hz) ─┐                         ┌─> realtime_status.json ─> HUD web (cổng 4567)
                           ├─> collector/main.py ────┤
 GPS Quectel EG800K (USB) ─┘   (Raspberry Pi 3)      └─> RAW_*.txt ──(đồng bộ)──> offline_pipeline (PC)
                                  │                                                │
                                  └── realtime_inference.py (tiến trình riêng) <── mô hình .tflite INT8
```

## Cấu trúc thư mục

| Thư mục | Nội dung | Chạy trên |
|---|---|---|
| `pi3_edge/collector/` | Thu thập IMU/GPS, đánh dấu sự kiện bằng tay cầm PS5, web gán nhãn, **suy luận thời gian thực** (`realtime_inference.py`), benchmark (`bench_pi3.py`), giám sát dịch vụ (`watchdog.py`) | Raspberry Pi 3 |
| `pi3_edge/gps_imu_realtime/` | Máy chủ HUD web (bản đồ, tốc độ, cờ AI) | Raspberry Pi 3 |
| `offline_pipeline/modular_app/` | Tiền xử lý, bộ luật gán nhãn heuristic (33 nhãn → 15 cờ), huấn luyện, lượng tử hóa, kiểm định 3 tầng; GUI 9 bước và CLI đa chuyến | Máy tính |
| `models/` | Mô hình đã huấn luyện (Keras FP32, TFLite FP32, **TFLite INT8**), `label_map.json`, báo cáo kiểm định | — |
| `analysis/` | Script sinh số liệu cho báo cáo: thống kê chuyến đi, kiểm chứng nhãn theo mẫu, đánh giá chuyến giữ riêng, phân tích chuyến thực địa, vẽ đồ thị | Máy tính |
| `uah_reference/` | Pipeline thí nghiệm tham chiếu trên bộ dữ liệu UAH-DriveSet (D1 → D2) | Máy tính |
| `firmware/gps_module_esp32c3/` | Firmware của bo GPS (Quectel EG800K tích hợp ESP32-C3) | ESP32-C3 |
| `docs/` | Tài liệu cảm biến (BNO055, Quectel EG800K) và cấu hình dịch vụ systemd trên Raspberry Pi | — |

Dữ liệu thô các chuyến đi, kết quả trung gian và các ghi chép trao đổi với trợ lý AI **không** được đưa lên repo
(xem `.gitignore`).

## Cách chạy

**Raspberry Pi 3** (Raspberry Pi OS 64-bit):
```bash
pip install -r pi3_edge/requirements.txt
# chép models/driversafe_event_classifier_int8.tflite + models/label_map.json vào /home/admin/models/
cd pi3_edge/collector && python3 main.py            # thu thập + suy luận (thường chạy qua systemd, xem docs/)
cd pi3_edge/gps_imu_realtime && python3 server.py   # HUD web tại http://<ip-pi>:4567
python3 pi3_edge/collector/bench_pi3.py --trip <thu_muc_trip>   # benchmark phát lại FP32 vs INT8
```
Đường dẫn dữ liệu/mô hình trên Pi cấu hình trong `pi3_edge/collector/config.py`.

**Máy tính** (Python 3.12):
```bash
pip install -r offline_pipeline/requirements.txt
cd offline_pipeline/modular_app
python main_app.py                                   # GUI phân tích 9 bước
python -m core.multi_trip_pipeline <thu_muc_cac_trip> # huấn luyện đa chuyến + INT8 + kiểm định
```
Các script trong `analysis/` dùng hằng số `PI3_REPO` (đầu `thong_ke_chuyen_di.py`, `danh_gia_trip_giu_rieng.py`)
trỏ tới thư mục gốc chứa `raw_data/` và `modular_app/` — sửa lại theo máy của bạn trước khi chạy.

## Kết quả chính

| Hạng mục | Kết quả |
|---|---|
| Dữ liệu | 13 chuyến tại TP.HCM (427,4 phút), 71.988 cửa sổ 2 s |
| Mô hình | Conv1D đa tỉ lệ + SE + Residual, 90.586 tham số, 15 cờ sigmoid |
| Độ chính xác (Macro-F1, mức cửa sổ) | Keras/TFLite FP32 57,61% — TFLite INT8 49,15% |
| Chuyến đi giữ riêng (FP32) | Macro-F1 53,72% (giảm 2,4 điểm so với tập kiểm thử) |
| Dung lượng | 1.193 KB (Keras) → 362 KB (TFLite FP32) → **121 KB (INT8)** |
| Raspberry Pi 3 (1.000 nhịp) | Một nhịp ≈ 72,9 ms (≈ 24% một nhân CPU ở nhịp 0,3 s); suy luận INT8 2,65 ms; RAM ≈ 47 MB |
| Thực địa 31 phút | IMU giữ 99,98 Hz, nhịp suy luận 0,3 s không gián đoạn; độ phủ phanh/đánh lái/dừng 95–100% |
| Kiểm chứng nhãn heuristic (mẫu 30) | 29/30 đúng (96,7%) |

Chi tiết phương pháp và phân tích: xem báo cáo đồ án.
