# event_database.py
import csv
import os

import json

# KHAI BÁO CÁC BIẾN (Giữ nguyên tham chiếu bộ nhớ)
EVENTS = {}
EVENT_GROUPS_MAP = {}
ROAD_TYPES = {}
CONFLICT_SETS = {}
EVENT_TO_SET = {}
SPEED_LIMIT = {}
PRESET_EVENTS = {}
DYNAMIC_EVENTS = {}
EVENT_SCOPES = {}
PS5_MAPPING = {}
csv_last_mtime = 0.0
active_csv_file = None

def get_objects_list_dir():
    base_dir = os.path.dirname(__file__) if '__file__' in globals() else '.'
    return os.path.join(base_dir, 'objects_list')

def get_active_object_config_path():
    return os.path.join(get_objects_list_dir(), '.active_object.json')

def get_available_objects():
    obj_dir = get_objects_list_dir()
    if not os.path.exists(obj_dir):
        return []
    files = [f for f in os.listdir(obj_dir) if f.endswith('_event_db.csv') or f.endswith('.csv')]
    return sorted(files)

def init_active_csv_file():
    global active_csv_file
    cfg_path = get_active_object_config_path()
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                fname = data.get('active_object', '')
                if fname and os.path.exists(os.path.join(get_objects_list_dir(), fname)):
                    active_csv_file = fname
                    return
        except Exception:
            pass

    # Fallback to test_event_db.csv or car_event_db.csv or first csv
    obj_dir = get_objects_list_dir()
    if os.path.exists(obj_dir):
        if os.path.exists(os.path.join(obj_dir, 'test_event_db.csv')):
            active_csv_file = 'test_event_db.csv'
            return
        elif os.path.exists(os.path.join(obj_dir, 'car_event_db.csv')):
            active_csv_file = 'car_event_db.csv'
            return
        files = get_available_objects()
        if files:
            active_csv_file = files[0]
            return

    active_csv_file = 'event_db.csv'

def get_csv_path():
    global active_csv_file
    if not active_csv_file:
        init_active_csv_file()
    
    obj_path = os.path.join(get_objects_list_dir(), active_csv_file)
    if os.path.exists(obj_path):
        return obj_path
    
    return os.path.join(os.path.dirname(__file__), active_csv_file) if '__file__' in globals() else active_csv_file

def set_active_object(filename):
    global active_csv_file, csv_last_mtime
    obj_path = os.path.join(get_objects_list_dir(), filename)
    if not os.path.exists(obj_path):
        return False, f"File {filename} không tồn tại trong objects_list."
    
    active_csv_file = filename
    cfg_path = get_active_object_config_path()
    try:
        os.makedirs(get_objects_list_dir(), exist_ok=True)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump({"active_object": filename}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving active object config: {e}")
        
    csv_last_mtime = 0.0 # reset để nạp mới và kích hoạt auto-reload client
    load_events_from_csv()
    return True, f"Đã chuyển model sang {filename}"

def check_and_reload_if_modified():
    """Kiểm tra mtime của file CSV, nếu có thay đổi thì nạp lại tức thì vào RAM"""
    global csv_last_mtime
    csv_path = get_csv_path()
    if os.path.exists(csv_path):
        try:
            mtime = os.path.getmtime(csv_path)
            if mtime > csv_last_mtime:
                load_events_from_csv()
                return True
        except Exception:
            pass
    return False

def load_events_from_csv():
    """Hàm này đọc file CSV và cập nhật lại toàn bộ dữ liệu vào RAM"""
    global csv_last_mtime
    csv_path = get_csv_path()
    
    if not os.path.exists(csv_path):
        print(f"⚠️ Không tìm thấy file {csv_path}. Vui lòng tạo file CSV.")
        return

    try:
        csv_last_mtime = os.path.getmtime(csv_path)
    except Exception:
        pass

    # Xóa sạch dữ liệu cũ trong RAM (Bắt buộc dùng .clear())
    EVENTS.clear()
    EVENT_GROUPS_MAP.clear()
    ROAD_TYPES.clear()
    CONFLICT_SETS.clear()
    EVENT_TO_SET.clear()
    SPEED_LIMIT.clear()
    PRESET_EVENTS.clear()
    DYNAMIC_EVENTS.clear()
    EVENT_SCOPES.clear()
    PS5_MAPPING.clear()

    # Đọc dữ liệu mới từ ổ cứng
    with open(csv_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = row.get('EVENT_KEY', '').strip()
            if not key:
                continue
            
            label = row.get('LABEL', '').strip()
            group = row.get('GROUP', '').strip()
            conflict = row.get('CONFLICT_SET', '').strip()
            if conflict.lower() in ['null', 'none', 'n/a', '0', 'false']:
                conflict = ''
            scope = row.get('SCOPE', '').strip().upper()
            if not scope:
                if group in ["ROAD", "SPEED_LIMIT", "ENVIRONMENT"]:
                    scope = "PRESET"
                else:
                    scope = "DYNAMIC"

            # 1. Thêm vào danh sách Tên sự kiện
            EVENTS[key] = label
            EVENT_SCOPES[key] = scope
            if scope == "PRESET":
                PRESET_EVENTS[key] = label
            else:
                DYNAMIC_EVENTS[key] = label

            # 2. Phân loại theo Group UI
            if group == "ROAD":
                ROAD_TYPES[key] = label

            elif group == "SPEED_LIMIT":
                SPEED_LIMIT[key] = label

            else:
                if group:
                    EVENT_GROUPS_MAP.setdefault(group, []).append(key)

            # 3. Phân loại theo Xung đột (Conflict)
            if conflict:
                if conflict not in CONFLICT_SETS:
                    CONFLICT_SETS[conflict] = []
                CONFLICT_SETS[conflict].append(key)
                EVENT_TO_SET[key] = conflict

            # 4. Lưu Ánh xạ tay cầm PS5
            ps5_button = row.get('PS5_BUTTON', '').strip()
            if ps5_button:
                PS5_MAPPING[ps5_button] = key

# Chạy lần đầu tiên khi app khởi động
load_events_from_csv()