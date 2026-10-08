# config.py
import os
import json
import time
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler

# ================= CONFIG =================
# (Hiện tại nó sẽ tự hiểu là: /home/admin/raw_data_collection/gps_imu_realtime)
CURRENT_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Đường dẫn đến dữ liệu lịch sử chuyến đi
BASE_DIR = "/home/admin/raw_data_collection/raw_data"   

# Đường dẫn đến thư mục web
# Cấu trúc: gps_imu_realtime/ -> server.py
#                             -> web/ (chứa index.html)
WEB_DIR = os.path.abspath(os.path.join(CURRENT_SCRIPT_DIR, "web"))

PORT = 4567
MAX_POINTS = 500
# ==========================================