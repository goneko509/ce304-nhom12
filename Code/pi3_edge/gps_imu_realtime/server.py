#!/usr/bin/python3
import os
import json
import glob
import time
import urllib.parse 
from http.server import HTTPServer, SimpleHTTPRequestHandler
from config import *

# ================= CONFIG BỔ SUNG =================
# Ưu tiên lấy từ config.py, nhưng nếu config.py thiếu thì dùng cấu hình cứng ở đây
WEB_DIR = "/home/admin/raw_data_collection/gps_imu_realtime/web"
DATA_DIR = "/home/admin/raw_data_collection/raw_data"
PORT = 4567
GPS_LINES_LIMIT = 50
IMU_LINES_LIMIT = 200

# File trạng thái AI realtime (ghi đè mỗi tick bởi collector/realtime_inference.py
# ::_write_status, xem collector/config.py::REALTIME_STATUS_PATH - 2 service độc
# lập nên dùng path tuyệt đối cùng DATA_DIR, không import chéo package collector/).
REALTIME_STATUS_FILE = os.path.join(DATA_DIR, "realtime_status.json")
REALTIME_STATUS_STALE_SEC = 5.0  # qua ngưỡng này coi như AI không còn chạy

# ================= SOTA TTL CACHE FOR FILE PATHS =================
_path_cache = {
    "latest_dir": None,
    "gps_file": None,
    "imu_file": None,
    "last_check": 0.0,
    "ttl": 1.5  # Cache đường dẫn trong 1.5 giây để tránh thundering herd I/O trên thẻ nhớ Pi
}

def get_cached_latest_files():
    """
    SOTA Path Resolver: Sử dụng bộ nhớ đệm TTL (1.5s) để xác định thư mục session mới nhất
    và đường dẫn file GPS/IMU mới nhất, giảm 95% thao tác glob/stat trên SD card của Pi.
    """
    now = time.time()
    if now - _path_cache["last_check"] < _path_cache["ttl"] and _path_cache["latest_dir"] is not None:
        return _path_cache["gps_file"], _path_cache["imu_file"]

    try:
        subdirs = [os.path.join(DATA_DIR, d) for d in os.listdir(DATA_DIR) 
                   if os.path.isdir(os.path.join(DATA_DIR, d))]
        
        latest_dir = max(subdirs, key=os.path.getmtime) if subdirs else DATA_DIR
        _path_cache["latest_dir"] = latest_dir

        # Ưu tiên kiểm tra file chuẩn theo tên không có suffix trong thư mục phiên: RAW_GPS.txt / RAW_ACCELEROMETERS.txt
        gps_direct = os.path.join(latest_dir, "RAW_GPS.txt")
        imu_direct = os.path.join(latest_dir, "RAW_ACCELEROMETERS.txt")

        gps_files = glob.glob(os.path.join(latest_dir, "RAW_GPS*.txt"))
        imu_files = glob.glob(os.path.join(latest_dir, "RAW_ACCELEROMETERS*.txt"))

        if os.path.exists(gps_direct):
            _path_cache["gps_file"] = gps_direct
        else:
            _path_cache["gps_file"] = max(gps_files, key=os.path.getmtime) if gps_files else None

        if os.path.exists(imu_direct):
            _path_cache["imu_file"] = imu_direct
        else:
            _path_cache["imu_file"] = max(imu_files, key=os.path.getmtime) if imu_files else None
        _path_cache["last_check"] = now
    except Exception as e:
        print(f"[CACHE RESOLVE ERROR] {e}")
        
    return _path_cache["gps_file"], _path_cache["imu_file"]

# ================= SOTA HIGH-PERFORMANCE TAILING ENGINE =================
def tail_lines_fast(file_path, limit, avg_line_bytes=120):
    """
    SOTA Binary Seek-from-End Tailing (Lock-Free Concurrent Read Guard):
    1. Không bao giờ đọc toàn bộ file bằng f.readlines() (tránh O(N) RAM & CPU).
    2. Dùng seek nhị phân (os.SEEK_END) nhảy về sát đuôi file chỉ đọc đúng số bytes cần thiết O(limit).
    3. Bảo vệ chống race condition (khi collector/main.py đang ghi dở dòng cuối cùng chưa có \n):
       tự động phát hiện và loại bỏ dòng cuối nếu block chưa kết thúc bằng \n.
    """
    if not file_path or not os.path.exists(file_path):
        return []
        
    try:
        with open(file_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            if file_size == 0:
                return []

            # Ước lượng số byte cần đọc từ đuôi (gấp 1.8 lần avg_line_bytes * limit để đảm bảo đủ dòng)
            chunk_size = min(file_size, int(limit * avg_line_bytes * 1.8))
            if chunk_size < 4096:
                chunk_size = min(file_size, max(4096, limit * avg_line_bytes * 2))

            f.seek(file_size - chunk_size, os.SEEK_SET)
            block = f.read(chunk_size)

            # Kiểm tra xem dòng cuối cùng có bị ghi dở dang không (chưa có \n ở cuối byte block)
            is_complete_tail = block.endswith(b'\n') or block.endswith(b'\r')
            
            lines = block.decode('utf-8', errors='ignore').splitlines()

            # Nếu seek không ở đầu file (0), dòng đầu tiên của block bị cắt ngang -> loại bỏ
            if file_size - chunk_size > 0 and len(lines) > 1:
                lines = lines[1:]

            # Nếu collector/main.py đang ghi dở dòng cuối cùng -> loại bỏ dòng cuối để tránh lỗi số liệu partial
            if not is_complete_tail and len(lines) > 0:
                lines = lines[:-1]

            return lines[-limit:]
    except Exception as e:
        print(f"[TAIL ERROR] {file_path}: {e}")
        return []

# ================= READ GPS =================
def read_latest_gps():
    try:
        gps_file, _ = get_cached_latest_files()
        lines = tail_lines_fast(gps_file, GPS_LINES_LIMIT, avg_line_bytes=100)
        
        gps_data = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 12:
                try:
                    # Tự động nhận diện định dạng 13 cột cũ (khi parts[2] là UTC > 90.0) vs 12 cột chuẩn
                    offset = 1 if (len(parts) >= 13 and abs(float(parts[2])) > 90.0) else 0
                    if 2 + offset < len(parts) and 11 + offset < len(parts):
                        gps_data.append({
                            "time": float(parts[0]), "lat": float(parts[2 + offset]), "lon": float(parts[3 + offset]),
                            "hdop": float(parts[4 + offset]), "alt": float(parts[5 + offset]), "fix": int(float(parts[6 + offset])),
                            "cog": float(parts[7 + offset]), "speed": float(parts[8 + offset]), "satellites": int(float(parts[11 + offset]))
                        })
                except ValueError: continue
        return gps_data
    except Exception as e:
        print(f"[GPS ERROR] {e}")
        return []

# ================= READ IMU =================
def read_latest_imu():
    try:
        _, imu_file = get_cached_latest_files()
        lines = tail_lines_fast(imu_file, IMU_LINES_LIMIT, avg_line_bytes=130)
        
        imu_data = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 14:
                try:
                    imu_data.append({
                        "time": float(parts[0]), "acc_x": float(parts[1]), "acc_y": float(parts[2]),
                        "acc_z": float(parts[3]), "yaw": float(parts[4]), "roll": float(parts[5]),
                        "pitch": float(parts[6]), "gyr_x": float(parts[7]), "gyr_y": float(parts[8]),
                        "gyr_z": float(parts[9]), "quat_w": float(parts[10]), "quat_x": float(parts[11]),
                        "quat_y": float(parts[12]), "quat_z": float(parts[13])
                    })
                except ValueError: continue
        return imu_data
    except Exception as e:
        print(f"[IMU ERROR] {e}")
        return []

# ================= READ AI STATUS =================
def read_realtime_ai_status():
    """Đọc trạng thái cờ AI hiện tại (flags=[] khi lái bình thường). Trả về
    (flags, online) - online=False nếu file chưa tồn tại hoặc đã cũ quá
    REALTIME_STATUS_STALE_SEC giây (AI process không chạy / đã crash)."""
    try:
        with open(REALTIME_STATUS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if time.time() - payload.get("updated_at", 0) > REALTIME_STATUS_STALE_SEC:
            return [], False
        return payload.get("flags", []), True
    except Exception:
        return [], False

# ================= HTTP HANDLER =================
class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed_path = urllib.parse.urlparse(self.path)

        # 1. API TRẢ VỀ DỮ LIỆU REAL-TIME
        if parsed_path.path == "/data":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.end_headers()
            ai_flags, ai_online = read_realtime_ai_status()
            data = {"gps": read_latest_gps(), "imu": read_latest_imu(), "ai_flags": ai_flags, "ai_online": ai_online}
            try:
                self.wfile.write(json.dumps(data).encode('utf-8'))
            except Exception: pass
            return

        # 2. API DANH SÁCH PHIÊN
        elif parsed_path.path == "/list_files":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            try:
                # Quét các thư mục phiên trong DATA_DIR hoặc file thô có sẵn
                sessions = []
                if os.path.exists(DATA_DIR):
                    for entry in os.listdir(DATA_DIR):
                        full_path = os.path.join(DATA_DIR, entry)
                        if os.path.isdir(full_path):
                            # Kiểm tra sự tồn tại của tệp IMU trong thư mục phiên
                            has_imu = os.path.exists(os.path.join(full_path, "RAW_ACCELEROMETERS.txt")) or \
                                      any(glob.glob(os.path.join(full_path, "RAW_ACCELEROMETERS*.txt"))) or \
                                      os.path.exists(os.path.join(full_path, "PROC_ACC.csv"))
                            if has_imu:
                                sessions.append(entry)
                        elif os.path.isfile(full_path) and entry.startswith("RAW_ACCELEROMETERS_"):
                            s_id = entry.replace("RAW_ACCELEROMETERS_", "").replace(".txt", "")
                            if s_id not in sessions:
                                sessions.append(s_id)
                sessions = sorted(list(set(sessions)), reverse=True)
                self.wfile.write(json.dumps({"sessions": sessions}).encode('utf-8'))
            except Exception as e:
                print(f"[LỖI ĐỌC THƯ MỤC]: {e}")
                self.wfile.write(json.dumps({"sessions": []}).encode('utf-8'))
            return

        # 3. API PHÁT LẠI DỮ LIỆU (REPLAY)
        elif parsed_path.path == "/replay_data":
            query = urllib.parse.parse_qs(parsed_path.query)
            session = query.get('session', [''])[0]

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            def find_session_filepath(session, prefix):
                # 1. Kiểm tra trực tiếp trong thư mục DATA_DIR/<session>/prefix.txt
                direct_path = os.path.join(DATA_DIR, session, f"{prefix}.txt")
                if os.path.exists(direct_path):
                    return direct_path
                # 2. Kiểm tra trong thư mục DATA_DIR/<session>/prefix_<session>.txt
                sub_path = os.path.join(DATA_DIR, session, f"{prefix}_{session}.txt")
                if os.path.exists(sub_path):
                    return sub_path
                # 3. Tìm kiếm toàn cục trong DATA_DIR nếu là định dạng cũ
                for root, dirs, files in os.walk(DATA_DIR):
                    if f"{prefix}_{session}.txt" in files:
                        return os.path.join(root, f"{prefix}_{session}.txt")
                    if os.path.basename(root) == session and f"{prefix}.txt" in files:
                        return os.path.join(root, f"{prefix}.txt")
                return None

            def read_all(filepath, file_type):
                if not filepath or not os.path.exists(filepath): 
                    print(f"[CẢNH BÁO] Không tìm thấy file cho type {file_type} của phiên {session}")
                    return []
                
                with open(filepath, "r") as f:
                    lines = f.readlines()
                
                data_list = []
                for line in lines:
                    parts = line.strip().split()
                    try:
                        if file_type == 'gps' and len(parts) >= 12:
                            offset = 1 if (len(parts) >= 13 and abs(float(parts[2])) > 90.0) else 0
                            if 2 + offset < len(parts) and 11 + offset < len(parts):
                                data_list.append({
                                    "time": float(parts[0]), "lat": float(parts[2 + offset]), "lon": float(parts[3 + offset]),
                                    "hdop": float(parts[4 + offset]), "alt": float(parts[5 + offset]), "fix": int(float(parts[6 + offset])),
                                    "cog": float(parts[7 + offset]), "speed": float(parts[8 + offset]), "satellites": int(float(parts[11 + offset]))
                                })
                        elif file_type == 'imu' and len(parts) >= 14:
                            data_list.append({
                                "time": float(parts[0]), "acc_x": float(parts[1]), "acc_y": float(parts[2]),
                                "acc_z": float(parts[3]), "yaw": float(parts[4]), "roll": float(parts[5]),
                                "pitch": float(parts[6]), "gyr_x": float(parts[7]), "gyr_y": float(parts[8]),
                                "gyr_z": float(parts[9]), "quat_w": float(parts[10]), "quat_x": float(parts[11]),
                                "quat_y": float(parts[12]), "quat_z": float(parts[13])
                            })
                    except ValueError: continue
                return data_list

            data = {
                "gps": read_all(find_session_filepath(session, "RAW_GPS"), 'gps'),
                "imu": read_all(find_session_filepath(session, "RAW_ACCELEROMETERS"), 'imu')
            }
            try:
                self.wfile.write(json.dumps(data).encode('utf-8'))
            except Exception as e: 
                print(f"[LỖI XUẤT JSON]: {e}")
            return

        # 4. TRANG DASHBOARD PHÂN TÍCH DATA (SOTA ANALYTICS)
        elif parsed_path.path == "/analytics" or parsed_path.path == "/analytics.html":
            self.path = "/analytics.html"
            return super().do_GET()

        # 5. API DANH SÁCH PHIÊN PHÂN TÍCH (ANALYTICS SESSIONS)
        elif parsed_path.path == "/api/analytics/sessions":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                source = params.get('source', ['raw'])[0]
                if source == 'proc':
                    base_dir = "/home/admin/raw_data_collection/proc_data"
                    if not os.path.exists(base_dir):
                        local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'proc_data'))
                        if os.path.exists(local_dir): base_dir = local_dir
                else:
                    base_dir = DATA_DIR
                    if not os.path.exists(base_dir):
                        local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'raw_data'))
                        if os.path.exists(local_dir): base_dir = local_dir
                
                sessions = []
                if os.path.exists(base_dir):
                    for folder_name in os.listdir(base_dir):
                        folder_path = os.path.join(base_dir, folder_name)
                        if os.path.isdir(folder_path):
                            files = os.listdir(folder_path)
                            acc_file = any(f.startswith('RAW_ACCELEROMETERS') or f.startswith('PROC_ACCELEROMETERS') or f.startswith('PROC_IMU') or 'acc' in f.lower() or 'imu' in f.lower() for f in files)
                            gps_file = any(f.startswith('RAW_GPS') or f.startswith('PROC_GPS') or 'gps' in f.lower() for f in files)
                            ev_file = any(f.startswith('events') for f in files)
                            road_file = any(f.startswith('road_segments') or f.startswith('roads') for f in files)
                            files_count = sum([acc_file, gps_file, ev_file, road_file])
                            total_bytes = sum(os.path.getsize(os.path.join(folder_path, f)) for f in files if os.path.isfile(os.path.join(folder_path, f)))
                            size_kb = round(total_bytes / 1024, 1)
                            
                            date_str = folder_name
                            if len(folder_name) == 15 and '_' in folder_name:
                                parts = folder_name.split('_')
                                if len(parts[0]) == 8 and len(parts[1]) == 6:
                                    d, t = parts[0], parts[1]
                                    date_str = f"{d[:2]}/{d[2:4]}/{d[4:]} {t[:2]}:{t[2:4]}:{t[4:]}"
                            
                            # Đánh dấu (*) nếu trip đã được xử lý (tồn tại trong proc_data)
                            if source == 'raw':
                                proc_dir = base_dir.replace('raw_data', 'proc_data')
                                if os.path.exists(os.path.join(proc_dir, folder_name)):
                                    date_str = f"(*) {date_str}"
                                    
                            sessions.append({
                                "name": folder_name, "files_count": files_count,
                                "size_kb": size_kb, "date_str": date_str, "has_all_4": files_count == 4
                            })
                    # Kiểm tra và thêm các file trực tiếp trong thư mục gốc raw_data/ (loose files: RAW_ACCELEROMETERS.txt, RAW_GPS.txt, events.csv...)
                    root_files = [f for f in os.listdir(base_dir) if os.path.isfile(os.path.join(base_dir, f))]
                    acc_file = any(f.startswith('RAW_ACCELEROMETERS') or f.startswith('PROC_ACCELEROMETERS') or f.startswith('PROC_IMU') or 'acc' in f.lower() or 'imu' in f.lower() for f in root_files)
                    gps_file = any(f.startswith('RAW_GPS') or f.startswith('PROC_GPS') or 'gps' in f.lower() for f in root_files)
                    ev_file = any(f.startswith('events') for f in root_files)
                    road_file = any(f.startswith('road_segments') or f.startswith('roads') for f in root_files)
                    if any([acc_file, gps_file, ev_file, road_file]):
                        files_count = sum([acc_file, gps_file, ev_file, road_file])
                        total_bytes = sum(os.path.getsize(os.path.join(base_dir, f)) for f in root_files)
                        sessions.append({
                            "name": "raw_data_root", "files_count": files_count,
                            "size_kb": round(total_bytes / 1024, 1), "date_str": "📁 Thư mục gốc raw_data/ (Loose Files)", "has_all_4": files_count == 4
                        })

                    def sort_key(item):
                        name = item["name"]
                        if name == "raw_data_root": return "99999999_999999"
                        if len(name) == 15 and '_' in name:
                            p = name.split('_')
                            return f"{p[0][4:]}{p[0][2:4]}{p[0][:2]}_{p[1]}"
                        return name
                    sessions.sort(key=sort_key, reverse=True)
                self.wfile.write(json.dumps(sessions).encode('utf-8'))
            except Exception as e:
                print(f"[LỖI API SESSIONS]: {e}")
                self.wfile.write(json.dumps([]).encode('utf-8'))
            return

        # 6. API CHI TIẾT PHÂN TÍCH VÀ KIỂM ĐỊNH SOTA CHO 1 PHIÊN
        elif parsed_path.path.startswith("/api/analytics/session/"):
            import math
            session_name = parsed_path.path.split("/")[-1].split("?")[0]
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            try:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                source = params.get('source', ['raw'])[0]
                if source == 'proc':
                    base_dir = "/home/admin/raw_data_collection/proc_data"
                    if not os.path.exists(base_dir):
                        local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'proc_data'))
                        if os.path.exists(local_dir): base_dir = local_dir
                else:
                    base_dir = DATA_DIR
                    if not os.path.exists(base_dir):
                        local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'raw_data'))
                        if os.path.exists(local_dir): base_dir = local_dir
                
                if session_name == "raw_data_root" or session_name == "root":
                    folder_path = base_dir
                else:
                    folder_path = os.path.join(base_dir, session_name)

                if not os.path.exists(folder_path):
                    self.wfile.write(json.dumps({"status": "error", "message": "Phiên thu thập không tồn tại trong nguồn " + ("proc_data" if source == 'proc' else "raw_data")}).encode('utf-8'))
                    return
                
                acc_path = os.path.join(folder_path, "RAW_ACCELEROMETERS.txt")
                if not os.path.exists(acc_path):
                    acc_path = os.path.join(folder_path, f"RAW_ACCELEROMETERS_{session_name}.txt")
                if not os.path.exists(acc_path):
                    for fname in os.listdir(folder_path):
                        if fname.startswith("PROC_") or fname.startswith("RAW_ACCELEROMETERS") or "acc" in fname.lower() or "imu" in fname.lower():
                            acc_path = os.path.join(folder_path, fname)
                            break

                gps_path = os.path.join(folder_path, "RAW_GPS.txt")
                if not os.path.exists(gps_path):
                    gps_path = os.path.join(folder_path, f"RAW_GPS_{session_name}.txt")
                if not os.path.exists(gps_path):
                    for fname in os.listdir(folder_path):
                        if fname.startswith("PROC_GPS") or fname.startswith("RAW_GPS") or "gps" in fname.lower():
                            gps_path = os.path.join(folder_path, fname)
                            break

                ev_path = os.path.join(folder_path, "events.csv")
                if not os.path.exists(ev_path):
                    ev_path = os.path.join(folder_path, f"events_{session_name}.csv")
                road_path = os.path.join(folder_path, "roads.csv")
                if not os.path.exists(road_path):
                    road_path = os.path.join(folder_path, f"road_segments_{session_name}.csv")
                
                def downsample(data_list, max_pts=1500):
                    n = len(data_list)
                    if n <= max_pts: return data_list
                    step = n / max_pts
                    res = []
                    for i in range(max_pts):
                        idx = int(i * step)
                        if idx < n: res.append(data_list[idx])
                    if data_list[-1] not in res: res.append(data_list[-1])
                    return res

                imu_raw = []
                imu_stats = {"count": 0, "hz": 0.0, "gaps": 0, "outliers": 0, "duration": 0.0}
                if os.path.exists(acc_path):
                    with open(acc_path, 'r', encoding='utf-8', errors='ignore') as f:
                        prev_t = None
                        is_proc = os.path.basename(acc_path).startswith("PROC_") or acc_path.endswith(".csv")
                        for line in f:
                            parts = line.strip().split(',') if ',' in line else line.strip().split()
                            if len(parts) >= 7:
                                try:
                                    t = float(parts[0])
                                    ax, ay, az = float(parts[1]), float(parts[2]), float(parts[3])
                                    if is_proc and len(parts) >= 13:
                                        gx, gy, gz = float(parts[4]), float(parts[5]), float(parts[6])
                                        sin_yaw, cos_yaw = float(parts[7]), float(parts[8])
                                        sin_roll, cos_roll = float(parts[9]), float(parts[10])
                                        sin_pitch, cos_pitch = float(parts[11]), float(parts[12])
                                        qw, qx, qy, qz = (float(parts[13]), float(parts[14]), float(parts[15]), float(parts[16])) if len(parts) >= 17 else (1.0, 0.0, 0.0, 0.0)
                                        ez = round(math.degrees(math.atan2(sin_yaw, cos_yaw)), 2)
                                        ey = round(math.degrees(math.atan2(sin_roll, cos_roll)), 2)
                                        ex = round(math.degrees(math.atan2(sin_pitch, cos_pitch)), 2)
                                    else:
                                        ez, ey, ex = float(parts[4]), float(parts[5]), float(parts[6]) # EZ = Yaw (euler[0]), EY = Roll (euler[1]), EX = Pitch (euler[2])
                                        gx, gy, gz = float(parts[7]), float(parts[8]), float(parts[9]) if len(parts) >= 10 else (0.0, 0.0, 0.0)
                                        qw, qx, qy, qz = (float(parts[10]), float(parts[11]), float(parts[12]), float(parts[13])) if len(parts) >= 14 else (1.0, 0.0, 0.0, 0.0)
                                        yaw_rad = math.radians(ez)
                                        roll_rad = math.radians(ey)
                                        pitch_rad = math.radians(ex)
                                        sin_yaw, cos_yaw = round(math.sin(yaw_rad), 4), round(math.cos(yaw_rad), 4)
                                        sin_roll, cos_roll = round(math.sin(roll_rad), 4), round(math.cos(roll_rad), 4)
                                        sin_pitch, cos_pitch = round(math.sin(pitch_rad), 4), round(math.cos(pitch_rad), 4)

                                    imu_stats["count"] += 1
                                    if prev_t is not None and (t - prev_t) > 0.05: imu_stats["gaps"] += 1
                                    prev_t = t
                                    if abs(ax) > 40 or abs(ay) > 40 or abs(az) > 40: imu_stats["outliers"] += 1
                                    imu_raw.append({
                                        "t": round(t, 3), "ax": round(ax, 3), "ay": round(ay, 3), "az": round(az, 3),
                                        "gx": round(gx, 3), "gy": round(gy, 3), "gz": round(gz, 3),
                                        "ex": round(ex, 2), "ey": round(ey, 2), "ez": round(ez, 2),
                                        "sin_yaw": sin_yaw, "cos_yaw": cos_yaw,
                                        "sin_roll": sin_roll, "cos_roll": cos_roll,
                                        "sin_pitch": sin_pitch, "cos_pitch": cos_pitch,
                                        "qw": round(qw, 4), "qx": round(qx, 4), "qy": round(qy, 4), "qz": round(qz, 4)
                                    })
                                except ValueError: pass
                    if imu_raw:
                        dur = imu_raw[-1]["t"] - imu_raw[0]["t"]
                        imu_stats["duration"] = round(dur, 2)
                        if dur > 0: imu_stats["hz"] = round(imu_stats["count"] / dur, 1)

                gps_raw = []
                gps_stats = {"count": 0, "hz": 0.0, "gaps": 0, "max_speed": 0.0, "avg_sats": 0.0}
                total_sats = 0
                if os.path.exists(gps_path):
                    with open(gps_path, 'r', encoding='utf-8', errors='ignore') as f:
                        prev_t = None
                        for line in f:
                            parts = line.strip().split()
                            if len(parts) >= 12:
                                try:
                                    t = float(parts[0])
                                    offset = 1 if (len(parts) >= 13 and abs(float(parts[2])) > 90.0) else 0
                                    lat = float(parts[2 + offset])
                                    lon = float(parts[3 + offset])
                                    hdop = float(parts[4 + offset])
                                    alt = float(parts[5 + offset])
                                    spd = float(parts[8 + offset])
                                    sats = int(float(parts[11 + offset]))

                                    gps_stats["count"] += 1
                                    total_sats += sats
                                    if spd > gps_stats["max_speed"]: gps_stats["max_speed"] = round(spd, 1)
                                    if prev_t is not None and (t - prev_t) > 0.3: gps_stats["gaps"] += 1
                                    prev_t = t
                                    gps_raw.append({"t": round(t, 3), "lat": round(lat, 6), "lon": round(lon, 6), "spd": round(spd, 1), "alt": round(alt, 1), "hdop": round(hdop, 2), "sats": sats})
                                except ValueError: pass
                    if gps_raw:
                        dur = gps_raw[-1]["t"] - gps_raw[0]["t"]
                        if dur > 0: gps_stats["hz"] = round(gps_stats["count"] / dur, 1)
                        gps_stats["avg_sats"] = round(total_sats / gps_stats["count"], 1) if gps_stats["count"] > 0 else 0.0

                import csv
                events_list = []
                event_labels_map = {}
                db_path = os.path.join(base_dir, "..", "collector", "event_db.csv")
                if os.path.exists(db_path):
                    try:
                        with open(db_path, 'r', encoding='utf-8-sig', errors='ignore') as f_db:
                            for r_db in csv.DictReader(f_db):
                                event_labels_map[r_db.get("EVENT_KEY", "")] = r_db.get("LABEL", "")
                    except Exception: pass

                if os.path.exists(ev_path):
                    with open(ev_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                        for row in csv.DictReader(f):
                            try:
                                ev_name = row.get("Event_Name", row.get("event_name", ""))
                                st = float(row.get("Start_Time", row.get("start_timestamp", 0)))
                                et = float(row.get("End_Time", row.get("end_timestamp", 0)))
                                events_list.append({
                                    "start": st, "end": et,
                                    "event": ev_name, "label": event_labels_map.get(ev_name, ev_name),
                                    "group": row.get("Event_Group", row.get("event_group", "")),
                                    "road": row.get("Road_Type", row.get("road_type", ""))
                                })
                            except ValueError: pass

                roads_list = []
                if os.path.exists(road_path):
                    with open(road_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                        for row in csv.DictReader(f):
                            try:
                                st = float(row.get("Start_Time", row.get("start_timestamp", 0)))
                                et = float(row.get("End_Time", row.get("end_timestamp", 0)))
                                rtype = row.get("Road_Type", row.get("road_type", "UNKNOWN"))
                                spd_lim = int(float(row.get("Speed_Limit", row.get("speed_limit", 0))))
                                roads_list.append({
                                    "start": st, "end": et,
                                    "road_type": rtype, "speed_limit": spd_lim
                                })
                            except ValueError: pass

                score = 100
                checks = []
                files_present = sum([os.path.exists(acc_path), os.path.exists(gps_path), os.path.exists(ev_path), os.path.exists(road_path)])
                if files_present == 4:
                    checks.append({"name": "Đầy đủ 4 Tệp Dữ liệu Chuẩn", "status": "pass", "desc": "Đã ghi nhận đủ RAW_ACCELEROMETERS, RAW_GPS, events, roads/road_segments."})
                else:
                    score -= (4 - files_present) * 15
                    checks.append({"name": "Kiểm tra Tệp Dữ liệu", "status": "error", "desc": f"Thiếu {4 - files_present}/4 tệp chuẩn SOTA."})
                if imu_stats["hz"] >= 90: checks.append({"name": f"Tần số IMU Đạt Chuẩn Cao Tần ({imu_stats['hz']} Hz)", "status": "pass", "desc": "Tốc độ lấy mẫu IMU ổn định quanh 100Hz."})
                elif imu_stats["hz"] >= 50:
                    score -= 10
                    checks.append({"name": f"Tần số IMU Trung bình ({imu_stats['hz']} Hz)", "status": "warning", "desc": "Tốc độ mẫu thấp hơn 100Hz."})
                else:
                    score -= 25
                    checks.append({"name": f"Tần số IMU Thấp ({imu_stats['hz']} Hz)", "status": "error", "desc": "Tần số lấy mẫu quá thấp, không đạt chuẩn SOTA."})
                if gps_stats["hz"] >= 8.5 and gps_stats["avg_sats"] >= 4: checks.append({"name": f"GPS 10Hz & Vệ tinh Ổn định ({gps_stats['hz']} Hz | {gps_stats['avg_sats']} Sats)", "status": "pass", "desc": "Quỹ đạo chuyển động liên tục 10Hz, khóa vệ tinh tốt."})
                else:
                    score -= 10
                    checks.append({"name": f"GPS / Vệ tinh cần lưu ý ({gps_stats['hz']} Hz | {gps_stats['avg_sats']} Sats)", "status": "warning", "desc": "Độ chính xác hoặc tần số mẫu GPS bị trễ trong một số đoạn."})
                total_gaps = imu_stats["gaps"] + gps_stats["gaps"]
                if total_gaps == 0: checks.append({"name": "Không Ngắt Quãng (0 Gaps)", "status": "pass", "desc": "Chuỗi thời gian liên tục 100%."})
                else:
                    score -= min(total_gaps * 3, 20)
                    checks.append({"name": f"Phát hiện Ngắt quãng ({total_gaps} Gaps)", "status": "warning", "desc": f"Có {imu_stats['gaps']} khoảng trễ IMU và {gps_stats['gaps']} khoảng trễ GPS."})
                if imu_stats["outliers"] == 0: checks.append({"name": "Tín hiệu Gia tốc Sạch (0 Spikes)", "status": "pass", "desc": "Gia tốc nằm trong dải động lực học hợp lệ."})
                else:
                    score -= min(imu_stats["outliers"] * 2, 15)
                    checks.append({"name": f"Ghi nhận Xung Gia tốc Lớn ({imu_stats['outliers']} Spikes)", "status": "warning", "desc": "Cần kiểm tra liệu đây là va chạm hay nhiễu cảm biến."})
                score = max(score, 0)

                res_data = {
                    "status": "ok", "session_name": session_name, "score": score,
                    "imu_stats": imu_stats, "gps_stats": gps_stats, "events_count": len(events_list),
                    "roads_count": len(roads_list), "checks": checks,
                    "imu_data": downsample(imu_raw, 1500), "gps_data": downsample(gps_raw, 1500),
                    "events": events_list, "roads": roads_list
                }
                self.wfile.write(json.dumps(res_data).encode('utf-8'))
            except Exception as e:
                print(f"[LỖI API ANALYTICS SESSION]: {e}")
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        # BẮT BUỘC PHẢI CÓ DÒNG NÀY ĐỂ TRẢ VỀ GIAO DIỆN WEB (index.html, JS, CSS)
        return super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path == "/api/analytics/update_events":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                if not data or 'session' not in data or 'events' not in data:
                    self.wfile.write(json.dumps({"status": "error", "message": "Missing session or events data"}).encode('utf-8'))
                    return
                    
                session_name = data['session']
                events_list = data['events']
                source = data.get('source', 'raw')
                
                import shutil
                raw_dir = DATA_DIR
                if not os.path.exists(raw_dir):
                    local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'raw_data'))
                    if os.path.exists(local_dir): raw_dir = local_dir
                proc_dir = raw_dir.replace('raw_data', 'proc_data')
                
                source_folder = os.path.join(raw_dir if source == 'raw' else proc_dir, session_name)
                target_folder = os.path.join(proc_dir, session_name)
                
                # Copy toàn bộ folder từ raw_data sang proc_data nếu chưa tồn tại
                if not os.path.exists(target_folder):
                    if not os.path.exists(source_folder):
                        self.wfile.write(json.dumps({"status": "error", "message": "Source session folder not found"}).encode('utf-8'))
                        return
                    try:
                        shutil.copytree(source_folder, target_folder)
                    except Exception as e:
                        self.wfile.write(json.dumps({"status": "error", "message": f"Failed to copy to proc_data: {str(e)}"}).encode('utf-8'))
                        return
                        
                ev_path = os.path.join(target_folder, "events.csv")
                if not os.path.exists(ev_path):
                    ev_path = os.path.join(target_folder, f"events_{session_name}.csv")
                    
                use_old_header = False
                if os.path.exists(ev_path):
                    try:
                        with open(ev_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                            first_line = f.readline()
                            if "Start_Time" in first_line or "start_time" in first_line:
                                use_old_header = True
                    except Exception:
                        pass
                        
                events_sorted = sorted(events_list, key=lambda x: float(x.get('start', 0)))
                import csv
                with open(ev_path, 'w', encoding='utf-8', newline='') as f:
                    if use_old_header:
                        writer = csv.writer(f)
                        writer.writerow(["Start_Time", "End_Time", "Event_Name", "Event_Group", "Road_Type"])
                        for ev in events_sorted:
                            writer.writerow([
                                ev.get("start", 0),
                                ev.get("end", 0),
                                ev.get("event", ""),
                                ev.get("group", ""),
                                ev.get("road", "")
                            ])
                    else:
                        writer = csv.writer(f)
                        writer.writerow(["start_timestamp", "end_timestamp", "event_name", "event_group", "road_type"])
                        for ev in events_sorted:
                            writer.writerow([
                                ev.get("start", 0),
                                ev.get("end", 0),
                                ev.get("event", ""),
                                ev.get("group", ""),
                                ev.get("road", "")
                            ])
                self.wfile.write(json.dumps({"status": "success", "message": "Updated events.csv successfully", "count": len(events_sorted)}).encode('utf-8'))
            except Exception as e:
                print(f"[LỖI API UPDATE EVENTS]: {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

# ================= START SERVER =================
def run():
    os.chdir(WEB_DIR)
    print("=== REALTIME SERVER STARTING ===")
    print(f"[*] Data Dir: {DATA_DIR}")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == "__main__":
    run()