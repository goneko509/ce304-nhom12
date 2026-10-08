# config.py
import os

# ==========================================
# 1. CẤU HÌNH PHẦN CỨNG & CẢM BIẾN (HARDWARE)
# ==========================================
BNO_ADDR = 0x29
IMU_HZ = 100
SWITCH_PIN = 12

GPS_PORT = '/dev/ttyACM0'
GPS_BAUD = 115200
GPS_HZ = 10.0  # tan so lay mau GPS danh nghia (dung de scale cua so lam muot - xem realtime_inference.py)

# ==========================================
# 2. CẤU HÌNH LƯU TRỮ DỮ LIỆU (FILE SYSTEM)
# ==========================================
BASE_DIR = "/home/admin/raw_data_collection/raw_data"
# Đảm bảo thư mục luôn tồn tại khi file config được gọi
os.makedirs(BASE_DIR, exist_ok=True)

# ==========================================
# 1b. CẤU HÌNH SUY LUẬN AI THỜI GIAN THỰC (REALTIME INFERENCE)
# Chay trong 1 multiprocessing.Process RIENG voi imu_worker/gps_worker
# (khong chia GIL, khong anh huong nhip doc I2C 100Hz) - xem
# collector/realtime_inference.py. Mac dinh TAT vi chua co model - nguoi
# dung tu bat sau khi da copy model .tflite + label_map.json tu
# modular_app len Pi.
# ==========================================
ENABLE_REALTIME_AI = True
REALTIME_MODEL_PATH = "/home/admin/models/driversafe_event_classifier_int8.tflite"
REALTIME_LABEL_MAP_PATH = "/home/admin/models/label_map.json"
REALTIME_STRIDE_SEC = 0.3
REALTIME_BUFFER_SEC = 3.0   # du 2.0s cua so model + margin centered-smoothing GPS
REALTIME_LOG_PATH = os.path.join(BASE_DIR, "realtime_events.log")
# File trang thai "hien tai" (ghi de moi tick, khac file log cong don o tren)
# - gps_imu_realtime/server.py doc file nay (cung DATA_DIR tren Pi) trong
# route /data de hien nhan AI tren Telemetry HUD (port 4567), xem
# collector/realtime_inference.py.
REALTIME_STATUS_PATH = os.path.join(BASE_DIR, "realtime_status.json")
REALTIME_STATUS_STALE_SEC = 5.0  # qua ngưỡng nay coi nhu AI khong con chay

# ==========================================
# 3. CẤU HÌNH MẠNG & WEB SERVER (NETWORK)
# ==========================================
WEB_HOST = '0.0.0.0'
WEB_PORT = 5000
TAILSCALE_IP = os.environ.get("TAILSCALE_IP", "")  # dat qua bien moi truong, khong ghi cung IP vao ma nguon

# ==========================================
# 4. GIAO DIỆN WEB & THÔNG BÁO (UI LABELS)
# ==========================================
UI = {
    "PAGE_TITLE": "Driving Data Annotation",
    "HEADER": "🚗 Driving Event Annotator",
    
    # Text cho Nút bấm trên Web
    "BTN_START": "▶ BẮT ĐẦU Sự kiện",
    "BTN_STOP": "⏹ DỪNG Sự kiện",
    "BTN_CANCEL": "✖ HỦY Sự kiện",
    
    # Text trạng thái
    "LBL_BTN_PRESSED": "ON",
    "LBL_BTN_RELEASED": "OFF",
    "LBL_REC_ACTIVE": "RECORDING",
    "LBL_REC_IDLE": "IDLE",
    
    # Popup thông báo (Toast)
    "TOAST_CANCEL_OK": "⚠️ Đã hủy sự kiện đang ghi!",
    "TOAST_CANCEL_ERR": "Không có sự kiện nào đang chạy để hủy.",
    
    # Text hiển thị giao diện
    "TXT_RAW_RECORD": "Trạng thái Raw Data",
    "TXT_SESSION_TIME": "Tổng thời gian",
    "TXT_ACTIVE_EVENT": "Sự kiện hiện tại",
    "TXT_EVENT_TIME": "Thời gian sự kiện",
    "TXT_CURRENT_ROAD": "Mặt đường hiện tại",
    "TXT_GPIO_BTN": "Nút bấm GPIO 12",
    "TXT_EVENT_CONTROL": "Điều khiển Sự kiện",
    "TXT_ROAD_CONDITION": "Điều kiện Mặt đường",
    "TXT_SPEED_LIMIT": "Giới hạn Tốc độ",
}