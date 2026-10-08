import time
import threading
try:
    import evdev
    from evdev import categorize, ecodes
except ImportError:
    evdev = None

import event_database

class PS5Worker:
    def __init__(self, annotator, get_t_now_func, toggle_btn_func, get_rec_state_func):
        self.annotator = annotator
        self.get_t_now = get_t_now_func
        self.toggle_btn = toggle_btn_func
        self.get_rec_state = get_rec_state_func
        self.is_running = True
        self.device = None
        self.is_connected = False
        self.dpad_state = {}
        
        # Nút mặc định dùng để Tắt/Bật ghi log
        self.TOGGLE_RECORD_BTN = "BTN_START"

    def find_ps5_controller(self):
        if evdev is None:
            return None
            
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for dev in devices:
            name = dev.name
            if ("Wireless Controller" in name or "DualSense" in name or "Sony" in name) \
               and "Touchpad" not in name \
               and "Motion Sensors" not in name:
                return dev
        return None
    def normalize_btn(self, keycode):
        if isinstance(keycode, (list, tuple)):
            for k in keycode:
                if 'BTN_SOUTH' in k or 'BTN_A' in k: return 'BTN_SOUTH'
                if 'BTN_EAST' in k or 'BTN_B' in k: return 'BTN_EAST'
                if 'BTN_WEST' in k or 'BTN_Y' in k: return 'BTN_WEST'
                if 'BTN_NORTH' in k or 'BTN_X' in k: return 'BTN_NORTH'
                if 'DPAD_UP' in k: return 'BTN_DPAD_UP'
                if 'DPAD_DOWN' in k: return 'BTN_DPAD_DOWN'
                if 'DPAD_LEFT' in k: return 'BTN_DPAD_LEFT'
                if 'DPAD_RIGHT' in k: return 'BTN_DPAD_RIGHT'
            return keycode[0]
        if keycode == 'BTN_A': return 'BTN_SOUTH'
        if keycode == 'BTN_B': return 'BTN_EAST'
        if keycode == 'BTN_X': return 'BTN_NORTH'
        if keycode == 'BTN_Y': return 'BTN_WEST'
        return keycode

    def handle_ps5_btn_press(self, btn_code, is_pressed, t_now):
        # Xử lý Nút Cancel (Circle / BTN_EAST)
        if btn_code == "BTN_EAST" and is_pressed:
            ok, msg = self.annotator.cancel_event()
            print(f"[PS5_WORKER]: HỦY SỰ KIỆN GẦN NHẤT -> {msg}")
            return
            
        # 1. Nút Bật/Tắt Ghi System
        if btn_code == self.TOGGLE_RECORD_BTN and is_pressed:
            current_rec = self.get_rec_state()
            print(f"[PS5_WORKER]: Nhấn nút {self.TOGGLE_RECORD_BTN} -> Đổi trạng thái ghi")
            self.toggle_btn(not current_rec)
            return

        # 2. Xử lý các nút đã gán sự kiện (Nhấn giữ - Hold to record)
        if is_pressed:
            event_database.check_and_reload_if_modified()
            if btn_code in event_database.PS5_MAPPING:
                ev_key = event_database.PS5_MAPPING[btn_code]
                scope = event_database.EVENT_SCOPES.get(ev_key, "DYNAMIC")
                
                if scope == "PRESET":
                    group = ""
                    if ev_key in event_database.ROAD_TYPES: group = "ROAD"
                    elif ev_key in event_database.SPEED_LIMIT: group = "SPEED_LIMIT"
                    else: group = "ENVIRONMENT"
                    
                    if group == "ROAD":
                        ok, msg = self.annotator.set_road_type(ev_key, t_now)
                    elif group == "SPEED_LIMIT":
                        ok, msg = self.annotator.set_speed_limit(int(ev_key) if ev_key.isdigit() else 0, t_now)
                    else:
                        ok, msg = self.annotator.set_preset(group, ev_key)
                    print(f"[PS5_WORKER]: PRESET [{ev_key}] -> {msg}")
                else:
                    # Bắt đầu sự kiện khi nhấn
                    if ev_key in ["UPHILL", "DOWNHILL", "START_MOVING"]:
                        if ev_key in self.annotator.active_events:
                            ok, msg = self.annotator.stop_event(ev_key, t_now)
                            print(f"[PS5_WORKER]: TẮT (TOGGLE) [{ev_key}] -> {msg}")
                        else:
                            ok, msg = self.annotator.start_event(ev_key, t_now)
                            print(f"[PS5_WORKER]: BẬT (TOGGLE) [{ev_key}] -> {msg}")
                    else:
                        ok, msg = self.annotator.start_event(ev_key, t_now)
                        print(f"[PS5_WORKER]: BẬT [{ev_key}] -> {msg}")
                    
        else:
            if btn_code in event_database.PS5_MAPPING:
                ev_key = event_database.PS5_MAPPING[btn_code]
                scope = event_database.EVENT_SCOPES.get(ev_key, "DYNAMIC")
                if scope == "DYNAMIC":
                    if ev_key in ["UPHILL", "DOWNHILL", "START_MOVING"]:
                        pass # Toggle events không làm gì khi nhả phím
                    else:
                        # Kết thúc sự kiện khi nhả phím
                        ok, msg = self.annotator.stop_event(ev_key, t_now)
                        print(f"[PS5_WORKER]: TẮT [{ev_key}] -> {msg}")

    def run(self):
        if evdev is None:
            print("[PS5_WORKER]: Thư viện 'evdev' chưa được cài đặt. PS5 Controller sẽ không hoạt động (pip install evdev).")
            return
            
        print("[PS5_WORKER]: Đang khởi động tiến trình nền tìm kiếm tay cầm PS5...")
        while self.is_running:
            try:
                if self.device is None:
                    self.is_connected = False
                    self.device = self.find_ps5_controller()
                    if self.device:
                        self.is_connected = True
                        print(f"[PS5_WORKER]: Đã kết nối tay cầm '{self.device.name}' tại {self.device.path}")
                    else:
                        time.sleep(2)
                        continue

                # Đọc sự kiện (blocking)
                for event in self.device.read_loop():
                    if not self.is_running:
                        break
                    
                    t_now = self.get_t_now()
                    
                    if event.type == ecodes.EV_KEY:
                        key_event = categorize(event)
                        btn_code = self.normalize_btn(key_event.keycode)
                        is_pressed = (key_event.keystate == key_event.key_down)
                        if key_event.keystate in (key_event.key_down, key_event.key_up):
                            self.handle_ps5_btn_press(btn_code, is_pressed, t_now)
                            
                    elif event.type == ecodes.EV_ABS:
                        abs_code = ecodes.ABS[event.code] if event.code in ecodes.ABS else event.code
                        if abs_code == 'ABS_HAT0Y' or (isinstance(abs_code, (list, tuple)) and 'ABS_HAT0Y' in abs_code):
                            if event.value < 0:
                                self.dpad_state['ABS_HAT0Y'] = 'BTN_DPAD_UP'
                                self.handle_ps5_btn_press('BTN_DPAD_UP', True, t_now)
                            elif event.value > 0:
                                self.dpad_state['ABS_HAT0Y'] = 'BTN_DPAD_DOWN'
                                self.handle_ps5_btn_press('BTN_DPAD_DOWN', True, t_now)
                            elif event.value == 0:
                                last_btn = self.dpad_state.get('ABS_HAT0Y')
                                if last_btn:
                                    self.handle_ps5_btn_press(last_btn, False, t_now)
                                    self.dpad_state['ABS_HAT0Y'] = None
                        elif abs_code == 'ABS_HAT0X' or (isinstance(abs_code, (list, tuple)) and 'ABS_HAT0X' in abs_code):
                            if event.value < 0:
                                self.dpad_state['ABS_HAT0X'] = 'BTN_DPAD_LEFT'
                                self.handle_ps5_btn_press('BTN_DPAD_LEFT', True, t_now)
                            elif event.value > 0:
                                self.dpad_state['ABS_HAT0X'] = 'BTN_DPAD_RIGHT'
                                self.handle_ps5_btn_press('BTN_DPAD_RIGHT', True, t_now)
                            elif event.value == 0:
                                last_btn = self.dpad_state.get('ABS_HAT0X')
                                if last_btn:
                                    self.handle_ps5_btn_press(last_btn, False, t_now)
                                    self.dpad_state['ABS_HAT0X'] = None
                                
            except (OSError, IOError) as e:
                print(f"[PS5_WORKER]: Mất kết nối tay cầm ({e}). Đang thử kết nối lại...")
                self.device = None
                self.is_connected = False
                time.sleep(2)
            except Exception as e:
                print(f"[PS5_WORKER]: Lỗi: {e}")
                time.sleep(2)
                
    def stop(self):
        self.is_running = False

    def force_reconnect(self):
        print("[PS5_WORKER]: Người dùng yêu cầu kết nối lại PS5...")
        try:
            if self.device:
                self.device.close()
        except Exception:
            pass
        self.device = None
        self.is_connected = False
        return self.find_ps5_controller() is not None

def start_ps5_worker(annotator, get_t_now_func, toggle_btn_func, get_rec_state_func):
    worker = PS5Worker(annotator, get_t_now_func, toggle_btn_func, get_rec_state_func)
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    return worker
