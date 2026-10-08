import logging
import csv
import os
import socket
from flask import Flask, render_template, jsonify, request
import config as cfg

import event_database
from event_database import EVENTS, EVENT_GROUPS_MAP, ROAD_TYPES, CONFLICT_SETS, SPEED_LIMIT

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

annotator_ref = None
get_t_now_ref = None
get_btn_state_ref = None
get_rec_state_ref = None
toggle_virtual_btn_ref = None  # Thêm biến lưu trữ hàm Công tắc ảo từ main.py
get_ps5_status_ref = None
force_reconnect_ps5_ref = None

# ==========================================
# GIAO DIỆN CHÍNH (HUD)
# ==========================================
@app.route('/')
def index():
    # 1. Đọc trực tiếp CSV để kiểm tra trạng thái VISIBLE
    csv_path = get_csv_path()
    visible_keys = set()
    
    if os.path.exists(csv_path):
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = str(row.get('EVENT_KEY', '')).strip()
                # Chuyển về chữ thường để so sánh chuẩn xác (false, FALSE, 0 đều là ẩn)
                vis = str(row.get('VISIBLE', 'true')).strip().lower()
                
                if vis not in ['false', '0', 'f', 'no']:
                    visible_keys.add(key)

    # 2. Xây dựng lại Map các sự kiện, CHỈ giữ lại các sự kiện có VISIBLE = true
    filtered_groups_map = {}
    preset_groups_map = {}
    dynamic_groups_map = {}
    for group, keys in event_database.EVENT_GROUPS_MAP.items():
        valid_keys = [k for k in keys if k in visible_keys]
        if valid_keys:
            filtered_groups_map[group] = valid_keys
            if any(event_database.EVENT_SCOPES.get(k) == "PRESET" for k in valid_keys):
                preset_groups_map[group] = valid_keys
            else:
                dynamic_groups_map[group] = valid_keys

    # Tạo map ngược cho PS5
    event_ps5_map = {v: k for k, v in event_database.PS5_MAPPING.items()}
    
    # Map event_key -> group
    ev_to_group = {}
    for group, keys in event_database.EVENT_GROUPS_MAP.items():
        for k in keys:
            ev_to_group[k] = group

    # 3. Xuất ra giao diện Web
    return render_template(
        'index.html',
        events=event_database.EVENTS,
        event_groups=list(filtered_groups_map.keys()), # Chỉ gửi ra các Group còn sự kiện
        groups_map=filtered_groups_map,                # Đã được lọc sạch
        preset_groups=list(preset_groups_map.keys()),
        preset_groups_map=preset_groups_map,
        dynamic_groups=list(dynamic_groups_map.keys()),
        dynamic_groups_map=dynamic_groups_map,
        event_scopes=event_database.EVENT_SCOPES,
        roads=event_database.ROAD_TYPES,
        speed_limits=event_database.SPEED_LIMIT,
        ui=cfg.UI,
        conflict_sets=event_database.CONFLICT_SETS,
        active_object=event_database.active_csv_file,
        available_objects=event_database.get_available_objects(),
        event_ps5_map=event_ps5_map,
        ev_to_group=ev_to_group
    )

@app.route('/api/status', methods=['GET'])
def status():
    event_database.check_and_reload_if_modified()
    is_rec = get_rec_state_ref() if get_rec_state_ref else False
    t_now = get_t_now_ref() if is_rec else 0.0
    data = annotator_ref.get_status(t_now)
    data['btn_pressed'] = get_btn_state_ref() if get_btn_state_ref else False
    data['is_recording'] = is_rec
    data['session_time'] = round(t_now, 1)
    data['current_speed_limit'] = getattr(annotator_ref, 'current_speed_limit', 0)
    data['db_version'] = event_database.csv_last_mtime
    data['active_object'] = event_database.active_csv_file
    data['available_objects'] = event_database.get_available_objects()
    data['ps5_connected'] = get_ps5_status_ref() if get_ps5_status_ref else False
    return jsonify(data)

@app.route('/api/set_object', methods=['POST'])
def api_set_object():
    data = request.get_json()
    object_file = data.get('object_file')
    ok, msg = event_database.set_active_object(object_file)
    return jsonify(success=ok, message=msg)

@app.route('/api/action', methods=['POST'])
def handle_action():
    data = request.json
    act = data.get('action')
    t_now = get_t_now_ref()
    if act == 'start': ok, msg = annotator_ref.start_event(data.get('event'), t_now)
    elif act == 'stop': ok, msg = annotator_ref.stop_event(data.get('event'), t_now)
    elif act == 'cancel': ok, msg = annotator_ref.cancel_event()
    else: ok, msg = False, "Hành động không hợp lệ."
    return jsonify({"status": "ok" if ok else "error", "message": msg})

@app.route('/api/road', methods=['POST'])
def handle_road():
    ok, msg = annotator_ref.set_road_type(request.json.get('road'), get_t_now_ref())
    return jsonify({"status": "ok" if ok else "error", "message": msg})

@app.route("/api/speed_limit", methods=["POST"])
def api_speed_limit():
    data = request.get_json()
    t_now = get_t_now_ref() 
    
    ok, msg = annotator_ref.set_speed_limit(data["speed_limit"], t_now)
    
    return jsonify(success=ok, message=msg)

@app.route("/api/preset", methods=["POST"])
def api_preset():
    data = request.get_json()
    group = data.get("group")
    preset_key = data.get("preset_key")
    ok, msg = annotator_ref.set_preset(group, preset_key)
    return jsonify(success=ok, message=msg)
        
# ==========================================
# GIAO DIỆN & API QUẢN TRỊ (ADMIN)
# ==========================================
@app.route('/api/reconnect_ps5', methods=['POST'])
def api_reconnect_ps5():
    if force_reconnect_ps5_ref:
        force_reconnect_ps5_ref()
        # Đợi một chút để worker kịp kết nối lại nếu có thể
        import time
        time.sleep(0.5)
        success = get_ps5_status_ref() if get_ps5_status_ref else False
        return jsonify({"success": success})
    return jsonify({"success": False, "error": "Not supported"})

@app.route('/admin')
def admin_page():
    return render_template('admin.html',
        active_object=event_database.active_csv_file,
        available_objects=event_database.get_available_objects()
    )

# Nút bấm Ảo trên web (thay thế nút vật lý)
@app.route('/api/gpio12_control', methods=['POST'])
def gpio12_control():
    data = request.json
    state = data.get('state')
    
    if state == 'ON':
        if toggle_virtual_btn_ref:
            toggle_virtual_btn_ref(True)  # Gửi lệnh True để Bật Ghi
        print("[WEB]: Đã nhận lệnh BẮT ĐẦU từ giao diện web")
    elif state == 'OFF':
        if toggle_virtual_btn_ref:
            toggle_virtual_btn_ref(False) # Gửi lệnh False để Tắt Ghi
        print("[WEB]: Đã nhận lệnh DỪNG từ giao diện web")
        
    return jsonify({"status": "success"})

def get_csv_path():
    return event_database.get_csv_path()

def get_dynamic_headers(rows):
    base_headers = ['EVENT_KEY', 'LABEL', 'GROUP', 'CONFLICT_SET', 'VISIBLE', 'SCOPE']
    if rows:
        for r in rows:
            for k in r.keys():
                if k and k not in base_headers:
                    base_headers.append(k)
    return base_headers

@app.route('/api/admin/events', methods=['GET'])
def get_all_events():
    events = []
    csv_path = get_csv_path()
    if os.path.exists(csv_path):
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = row.get('EVENT_KEY', '')
                if key and str(key).strip():
                    events.append(row)
    return jsonify(events)

@app.route('/api/admin/events', methods=['POST'])
def save_event():
    req = request.json
    is_edit = req.get('is_edit', False)
    old_key = req.get('old_key', '')
    new_data = req.get('data', {})
    
    csv_path = get_csv_path()
    rows = []
    if os.path.exists(csv_path):
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))

    if is_edit:
        for r in rows:
            if r.get('EVENT_KEY') == old_key:
                r.update(new_data)
                break
    else:
        if any(r.get('EVENT_KEY') == new_data.get('EVENT_KEY') for r in rows):
            return jsonify({"status": "error", "message": "EVENT_KEY đã tồn tại!"})
        rows.append(new_data)

    headers = get_dynamic_headers(rows)
    with open(csv_path, mode='w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    event_database.load_events_from_csv()
    return jsonify({"status": "ok"})

@app.route('/api/admin/events/<key>', methods=['DELETE'])
def delete_event(key):
    csv_path = get_csv_path()
    if not os.path.exists(csv_path): return jsonify({"status": "error"})

    with open(csv_path, mode='r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    new_rows = [r for r in rows if r.get('EVENT_KEY') != key]
    headers = get_dynamic_headers(rows)

    with open(csv_path, mode='w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(new_rows)

    event_database.load_events_from_csv()
    return jsonify({"status": "ok"})

@app.route('/api/admin/rename_group', methods=['POST'])
def rename_group():
    req = request.json
    target = req.get('target')
    old_name = req.get('old_name')
    new_name = req.get('new_name')

    csv_path = get_csv_path()
    if not os.path.exists(csv_path): return jsonify({"status": "error"})

    with open(csv_path, mode='r', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))

    for r in rows:
        if r.get(target) == old_name:
            r[target] = new_name

    headers = get_dynamic_headers(rows)
    with open(csv_path, mode='w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    event_database.load_events_from_csv()
    return jsonify({"status": "ok"})  

# ==========================================
# PHÂN HỆ PHÂN TÍCH & KIỂM ĐỊNH DATA (ANALYTICS)
# ==========================================
def get_raw_data_dir():
    # 1. Thử đường dẫn trong config (thường là /home/admin/raw_data_collection/raw_data trên Pi)
    if os.path.exists(cfg.BASE_DIR) and os.path.isdir(cfg.BASE_DIR):
        return cfg.BASE_DIR
    # 2. Thử đường dẫn tương đối so với file web_annotation.py (dùng khi chạy dev local trên Windows)
    local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'raw_data'))
    if os.path.exists(local_dir) and os.path.isdir(local_dir):
        return local_dir
    return cfg.BASE_DIR

def get_proc_data_dir():
    parent_dir = os.path.dirname(cfg.BASE_DIR) if (os.path.exists(cfg.BASE_DIR) and os.path.isdir(cfg.BASE_DIR)) else os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    proc_dir = os.path.join(parent_dir, 'proc_data')
    if not os.path.exists(proc_dir):
        try:
            os.makedirs(proc_dir, exist_ok=True)
        except Exception:
            pass
    return proc_dir

def downsample_timeseries(data_list, max_points=1500):
    """Hạ mẫu thông minh để giữ nhẹ tải cho trình duyệt trong khi giữ nguyên hình dạng sóng."""
    n = len(data_list)
    if n <= max_points:
        return data_list
    step = n / max_points
    result = []
    for i in range(max_points):
        idx = int(i * step)
        if idx < n:
            result.append(data_list[idx])
    if data_list[-1] not in result:
        result.append(data_list[-1])
    return result

@app.route('/analytics')
def analytics_page():
    return render_template('analytics.html', ui=cfg.UI)

@app.route('/api/analytics/sessions', methods=['GET'])
def get_analytics_sessions():
    source = request.args.get('source', 'raw')
    base_dir = get_proc_data_dir() if source == 'proc' else get_raw_data_dir()
    sessions = []
    if not os.path.exists(base_dir):
        return jsonify(sessions)
    
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
            if source == 'raw' and os.path.exists(os.path.join(get_proc_data_dir(), folder_name)):
                date_str = f"(*) {date_str}"

            sessions.append({
                "name": folder_name,
                "files_count": files_count,
                "size_kb": size_kb,
                "date_str": date_str,
                "has_all_4": files_count == 4
            })
            
    def sort_key(item):
        name = item["name"]
        if len(name) == 15 and '_' in name:
            p = name.split('_')
            d, t = p[0], p[1]
            return f"{d[4:]}{d[2:4]}{d[:2]}_{t}"
        return name

    sessions.sort(key=sort_key, reverse=True)
    return jsonify(sessions)

@app.route('/api/analytics/session/<session_name>', methods=['GET'])
def get_session_analytics(session_name):
    import math
    source = request.args.get('source', 'raw')
    base_dir = get_proc_data_dir() if source == 'proc' else get_raw_data_dir()
    folder_path = os.path.join(base_dir, session_name)
    if not os.path.exists(folder_path):
        return jsonify({"status": "error", "message": "Phiên thu thập không tồn tại trong nguồn " + ("proc_data" if source == 'proc' else "raw_data")}), 404
        
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
                            gx, gy, gz = float(parts[7]), float(parts[8]), float(parts[9]) if len(parts) >= 10 else (0.0, 0.0, 0.0) # Gyro rad/s
                            qw, qx, qy, qz = (float(parts[10]), float(parts[11]), float(parts[12]), float(parts[13])) if len(parts) >= 14 else (1.0, 0.0, 0.0, 0.0)
                            yaw_rad = math.radians(ez)
                            roll_rad = math.radians(ey)
                            pitch_rad = math.radians(ex)
                            sin_yaw, cos_yaw = round(math.sin(yaw_rad), 4), round(math.cos(yaw_rad), 4)
                            sin_roll, cos_roll = round(math.sin(roll_rad), 4), round(math.cos(roll_rad), 4)
                            sin_pitch, cos_pitch = round(math.sin(pitch_rad), 4), round(math.cos(pitch_rad), 4)

                        imu_stats["count"] += 1
                        if prev_t is not None and (t - prev_t) > 0.05:
                            imu_stats["gaps"] += 1
                        prev_t = t
                        if abs(ax) > 40 or abs(ay) > 40 or abs(az) > 40:
                            imu_stats["outliers"] += 1

                        imu_raw.append({
                            "t": round(t, 3), "ax": round(ax, 3), "ay": round(ay, 3), "az": round(az, 3),
                            "gx": round(gx, 3), "gy": round(gy, 3), "gz": round(gz, 3),
                            "ex": round(ex, 2), "ey": round(ey, 2), "ez": round(ez, 2),
                            "sin_yaw": sin_yaw, "cos_yaw": cos_yaw,
                            "sin_roll": sin_roll, "cos_roll": cos_roll,
                            "sin_pitch": sin_pitch, "cos_pitch": cos_pitch,
                            "qw": round(qw, 4), "qx": round(qx, 4), "qy": round(qy, 4), "qz": round(qz, 4)
                        })
                    except ValueError:
                        pass
        if imu_raw:
            dur = imu_raw[-1]["t"] - imu_raw[0]["t"]
            imu_stats["duration"] = round(dur, 2)
            if dur > 0:
                imu_stats["hz"] = round(imu_stats["count"] / dur, 1)

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
                        lat, lon = float(parts[2]), float(parts[3])
                        spd, alt = float(parts[5]), float(parts[7])
                        hdop, sats = float(parts[8]), int(parts[11])
                        
                        gps_stats["count"] += 1
                        total_sats += sats
                        if spd > gps_stats["max_speed"]:
                            gps_stats["max_speed"] = round(spd, 1)
                            
                        if prev_t is not None:
                            dt = t - prev_t
                            if dt > 0.3: # > 300ms gap cho 10Hz GPS
                                gps_stats["gaps"] += 1
                        prev_t = t
                        
                        gps_raw.append({
                            "t": round(t, 3), "lat": round(lat, 6), "lon": round(lon, 6),
                            "spd": round(spd, 1), "alt": round(alt, 1), "hdop": round(hdop, 2), "sats": sats
                        })
                    except ValueError:
                        pass
        if gps_raw:
            dur = gps_raw[-1]["t"] - gps_raw[0]["t"]
            if dur > 0:
                gps_stats["hz"] = round(gps_stats["count"] / dur, 1)
            gps_stats["avg_sats"] = round(total_sats / gps_stats["count"], 1) if gps_stats["count"] > 0 else 0.0

    events_list = []
    if os.path.exists(ev_path):
        with open(ev_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    st = float(row.get("Start_Time", row.get("start_timestamp", 0)))
                    et = float(row.get("End_Time", row.get("end_timestamp", 0)))
                    ev_name = row.get("Event_Name", row.get("event_name", ""))
                    label = event_database.EVENTS.get(ev_name, ev_name) if ev_name else "Sự kiện"
                    events_list.append({
                        "start": st, "end": et, "event": ev_name, "label": label,
                        "group": row.get("Event_Group", row.get("event_group", "")),
                        "road": row.get("Road_Type", row.get("road_type", ""))
                    })
                except ValueError:
                    pass

    roads_list = []
    if os.path.exists(road_path):
        with open(road_path, 'r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    st = float(row.get("Start_Time", row.get("start_timestamp", 0)))
                    et = float(row.get("End_Time", row.get("end_timestamp", 0)))
                    rtype = row.get("Road_Type", row.get("road_type", "UNKNOWN"))
                    spd_lim = int(float(row.get("Speed_Limit", row.get("speed_limit", 0))))
                    roads_list.append({
                        "start": st, "end": et, "road_type": rtype, "speed_limit": spd_lim
                    })
                except ValueError:
                    pass

    score = 100
    checks = []
    
    files_present = sum([os.path.exists(acc_path), os.path.exists(gps_path), os.path.exists(ev_path), os.path.exists(road_path)])
    if files_present == 4:
        checks.append({"name": "Đầy đủ 4 Tệp Dữ liệu Chuẩn", "status": "pass", "desc": "Đã ghi nhận đủ RAW_ACCELEROMETERS, RAW_GPS, events, roads/road_segments."})
    else:
        score -= (4 - files_present) * 15
        checks.append({"name": "Kiểm tra Tệp Dữ liệu", "status": "error", "desc": f"Thiếu {4 - files_present}/4 tệp chuẩn SOTA."})
        
    if imu_stats["hz"] >= 90:
        checks.append({"name": f"Tần số IMU Đạt Chuẩn Cao Tần ({imu_stats['hz']} Hz)", "status": "pass", "desc": "Tốc độ lấy mẫu IMU ổn định quanh 100Hz, cực kỳ phù hợp cho Deep Learning time-series."})
    elif imu_stats["hz"] >= 50:
        score -= 10
        checks.append({"name": f"Tần số IMU Trung bình ({imu_stats['hz']} Hz)", "status": "warning", "desc": "Tốc độ mẫu thấp hơn 100Hz (đạt mức 50-90Hz)."})
    else:
        score -= 25
        checks.append({"name": f"Tần số IMU Thấp ({imu_stats['hz']} Hz)", "status": "error", "desc": "Tần số lấy mẫu quá thấp, không đạt chuẩn SOTA 100Hz."})
        
    if gps_stats["hz"] >= 8.5 and gps_stats["avg_sats"] >= 4:
        checks.append({"name": f"GPS 10Hz & Vệ tinh Ổn định ({gps_stats['hz']} Hz | {gps_stats['avg_sats']} Sats)", "status": "pass", "desc": "Quỹ đạo chuyển động liên tục 10Hz, số lượng vệ tinh khóa tốt."})
    else:
        score -= 10
        checks.append({"name": f"GPS / Vệ tinh cần lưu ý ({gps_stats['hz']} Hz | {gps_stats['avg_sats']} Sats)", "status": "warning", "desc": "Độ chính xác hoặc tần số mẫu GPS bị trễ trong một số đoạn."})
        
    total_gaps = imu_stats["gaps"] + gps_stats["gaps"]
    if total_gaps == 0:
        checks.append({"name": "Không Ngắt Quãng / Mất Gói (0 Gaps)", "status": "pass", "desc": "Chuỗi thời gian liên tục 100%, không bị rớt mẫu hay trễ buffer I/O."})
    else:
        score -= min(total_gaps * 3, 20)
        checks.append({"name": f"Phát hiện Ngắt quãng ({total_gaps} Gaps)", "status": "warning", "desc": f"Có {imu_stats['gaps']} khoảng trễ IMU và {gps_stats['gaps']} khoảng trễ GPS vượt ngưỡng."})

    if imu_stats["outliers"] == 0:
        checks.append({"name": "Tín hiệu Gia tốc Sạch (0 Outliers)", "status": "pass", "desc": "Gia tốc nằm trong dải động lực học vật lý hợp lệ của phương tiện."})
    else:
        score -= min(imu_stats["outliers"] * 2, 15)
        checks.append({"name": f"Ghi nhận Xung Gia tốc Lớn ({imu_stats['outliers']} Spikes)", "status": "warning", "desc": "Cần kiểm tra liệu đây là va chạm thực tế hay nhiễu cảm biến."})

    score = max(score, 0)

    return jsonify({
        "status": "ok",
        "session_name": session_name,
        "score": score,
        "imu_stats": imu_stats,
        "gps_stats": gps_stats,
        "events_count": len(events_list),
        "roads_count": len(roads_list),
        "checks": checks,
        "imu_data": downsample_timeseries(imu_raw, 1500),
        "gps_data": downsample_timeseries(gps_raw, 1500),
        "events": events_list,
        "roads": roads_list
    })

@app.route('/api/analytics/update_events', methods=['POST'])
def update_analytics_events():
    data = request.json
    if not data or 'session' not in data or 'events' not in data:
        return jsonify({"status": "error", "message": "Missing session or events data"}), 400
    session_name = data['session']
    events_list = data['events']
    source = data.get('source', 'raw')  # 'raw' hoặc 'proc'

    import shutil
    raw_dir = get_raw_data_dir()
    proc_dir = get_proc_data_dir()
    
    source_folder = os.path.join(raw_dir if source == 'raw' else proc_dir, session_name)
    target_folder = os.path.join(proc_dir, session_name)
    
    # 1. Bất kể nguồn là gì, mục tiêu lưu trữ luôn là proc_data
    # 2. Nếu thư mục đích chưa tồn tại trong proc_data, copy toàn bộ từ raw_data sang
    if not os.path.exists(target_folder):
        if not os.path.exists(source_folder):
            return jsonify({"status": "error", "message": "Source session folder not found"}), 404
        try:
            shutil.copytree(source_folder, target_folder)
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to copy to proc_data: {str(e)}"}), 500
    
    # 3. Path của file events sẽ nằm trong target_folder (tức proc_data)
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
    try:
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
        return jsonify({"status": "success", "message": "Updated events.csv successfully", "count": len(events_sorted)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================================
# KHỞI CHẠY SERVER
# ==========================================
def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        lan_ip = s.getsockname()[0]
    except Exception: lan_ip = "127.0.0.1"
    finally: s.close()
    return lan_ip

# Thêm tham số toggle_btn_func vào hàm chạy
def run_server(annotator, time_func, btn_func, rec_func, toggle_btn_func, ps5_status_func, force_reconnect_func=None):
    global annotator_ref, get_t_now_ref, get_btn_state_ref, get_rec_state_ref, toggle_virtual_btn_ref, get_ps5_status_ref, force_reconnect_ps5_ref
    annotator_ref = annotator
    get_t_now_ref = time_func
    get_btn_state_ref = btn_func
    get_rec_state_ref = rec_func
    toggle_virtual_btn_ref = toggle_btn_func  # Gán hàm kích hoạt vào biến toàn cục
    get_ps5_status_ref = ps5_status_func
    force_reconnect_ps5_ref = force_reconnect_func

    print("\n" + "=" * 60)
    print(f"\n[WEB]: Server STARTING at http://{get_lan_ip()}:{cfg.WEB_PORT}")
    print("=" * 60 + "\n")
    app.run(host=cfg.WEB_HOST, port=cfg.WEB_PORT, debug=False, use_reloader=False)