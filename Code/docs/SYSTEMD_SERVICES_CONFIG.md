# ⚙️ CẤU HÌNH CÁC DỊCH VỤ HỆ THỐNG (SYSTEMD SERVICES CONFIGURATION)

Tài liệu này lưu trữ chính xác nội dung cấu hình và hướng dẫn vận hành các dịch vụ hệ thống (`systemd services`) đang chạy nền trên **Raspberry Pi 3** liên quan trực tiếp đến dự án `raw_data_collection`.

Hệ thống được vận hành bởi **02 dịch vụ độc lập**, đảm bảo chạy song song và tự động khôi phục (`Restart=always`) ngay cả khi gặp sự cố ngắt quãng hoặc sau khi khởi động lại thiết bị (System Boot).

---

## 1. Dịch vụ Thu thập & Gán nhãn (`raw_data_collection.service`)

* **Nhiệm vụ:** Khởi chạy tiến trình `collector/main.py` (đọc cảm biến GPS/IMU tốc độ cao và lưu file raw log vào `raw_data/`), đồng thời khởi chạy Web Server gán nhãn sự kiện (`Flask`) tại cổng `5000`.
* **Đường dẫn trên Pi:** `/etc/systemd/system/raw_data_collection.service`
* **Trạng thái thực tế:** `Active (running)`
* **Môi trường làm việc:** `/home/admin/raw_data_collection/collector`

### 📄 Nội dung tệp cấu hình `/etc/systemd/system/raw_data_collection.service`:
```ini
[Unit]
Description=Raw Data Collection IMU GPS Server
After=network.target

[Service]
User=admin
WorkingDirectory=/home/admin/raw_data_collection/collector
ExecStart=/usr/bin/python3 /home/admin/raw_data_collection/collector/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 2. Dịch vụ Telemetry 3D HUD Realtime (`gps_imu_realtime.service`)

* **Nhiệm vụ:** Khởi chạy tiến trình `server.py` (REST API Backend đọc stream log mới nhất từ `raw_data/` và phát lại `Replay Mode`) cùng với Giao diện điều khiển 3D HUD tại cổng `4567`.
* **Đường dẫn trên Pi:** `/etc/systemd/system/gps_imu_realtime.service`
* **Trạng thái thực tế:** `Active (running)`
* **Môi trường làm việc:** `/home/admin/raw_data_collection/gps_imu_realtime/web`

### 📄 Nội dung tệp cấu hình `/etc/systemd/system/gps_imu_realtime.service`:
```ini
[Unit]
Description=3D HUD Realtime Server
After=network.target

[Service]
User=admin
WorkingDirectory=/home/admin/raw_data_collection/gps_imu_realtime/web
ExecStart=/usr/bin/python3 /home/admin/raw_data_collection/gps_imu_realtime/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 🛠️ Hướng Dẫn Cài Đặt & Quản Lý Dịch Vụ trên Pi 3

Nếu bạn di chuyển sang một mạch Raspberry Pi mới hoặc cần cài đặt lại từ đầu, hãy thực hiện tuần tự các bước dưới đây qua terminal SSH (`admin@pi3`):

### Bước 1: Tạo tệp dịch vụ Systemd
Sử dụng `sudo nano` để tạo 2 tệp dịch vụ tại `/etc/systemd/system/`:
```bash
sudo nano /etc/systemd/system/raw_data_collection.service
# (Dán nội dung cấu hình số 1 vào, sau đó nhấn Ctrl+O -> Enter để lưu, Ctrl+X để thoát)

sudo nano /etc/systemd/system/gps_imu_realtime.service
# (Dán nội dung cấu hình số 2 vào, sau đó nhấn Ctrl+O -> Enter để lưu, Ctrl+X để thoát)
```

### Bước 2: Tải lại cấu hình Systemd và Kích hoạt tự động khởi chạy cùng hệ thống
```bash
# Nạp lại danh sách dịch vụ mới
sudo systemctl daemon-reload

# Kích hoạt khởi chạy tự động khi bật Pi (Enable)
sudo systemctl enable raw_data_collection.service
sudo systemctl enable gps_imu_realtime.service

# Khởi động ngay 2 dịch vụ (Start)
sudo systemctl start raw_data_collection.service
sudo systemctl start gps_imu_realtime.service
```

### Bước 3: Kiểm tra trạng thái và Xem Log thời gian thực
* **Kiểm tra trạng thái hoạt động (`status`):**
  ```bash
  systemctl status raw_data_collection.service --no-pager
  systemctl status gps_imu_realtime.service --no-pager
  ```
* **Khởi động lại dịch vụ (`restart` - Dùng sau khi cập nhật code mới):**
  ```bash
  sudo systemctl restart raw_data_collection.service && sudo systemctl restart gps_imu_realtime.service
  ```
* **Xem Log trực tiếp (Live Stream Logs - `journalctl`):**
  ```bash
  # Xem log thu thập & gán nhãn
  journalctl -u raw_data_collection.service -f

  # Xem log Telemetry 3D HUD
  journalctl -u gps_imu_realtime.service -f
  ```
