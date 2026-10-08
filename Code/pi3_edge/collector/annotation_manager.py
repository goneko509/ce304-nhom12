import os
import threading
import event_database


class AnnotationManager:
    def __init__(self):
        self.lock = threading.Lock()

        # KHÔNG tạo file/folder gì ở đây. Chỉ start_session() mới tạo,
        # và start_session() chỉ được gọi khi GPIO12 kích hoạt.
        self.base_dir = None
        self.session_timestamp = None
        self.session_active = False

        self.events_file = None
        self.roads_file = None
        self.metadata_file = None

        self.active_events = {}       # { "EVENT_NAME": start_time }
        self.current_road_type = "UNKNOWN"
        self.road_start_time = 0.0
        self.metadata = {}
        self.current_speed_limit = 0 # Khởi tạo biến này
        self.active_presets = {}     # { "ENVIRONMENT": "INDOOR_ROOM_TEMP" }
        self.auto_resume_events = {} # { "CONFLICT_SET": "EVENT_NAME" }

    def start_session(self, base_dir, session_name):
        """Khởi tạo phiên ghi mới trong `base_dir` (đã được caller tạo sẵn
        khi GPIO kích hoạt). Mutate object hiện có — KHÔNG tạo instance mới —
        để mọi tham chiếu (annotator_ref bên web server) luôn hợp lệ."""
        with self.lock:
            self.base_dir = base_dir
            self.session_timestamp = session_name
            self.events_file = os.path.join(base_dir, "events.csv")
            self.roads_file = os.path.join(base_dir, "roads.csv")
            self.metadata_file = os.path.join(base_dir, "metadata.json")

            with open(self.events_file, "w") as f:
                f.write("start_timestamp,end_timestamp,event_name,event_group,road_type\n")
            with open(self.roads_file, "w") as f:
                f.write("start_timestamp,end_timestamp,road_type\n")

            self.active_events = {}
            self.auto_resume_events = {}
            if self.current_road_type not in event_database.ROAD_TYPES:
                self.current_road_type = "UNKNOWN"
            self.road_start_time = 0.0
            self.session_active = True

            # Ghi nhận ngay các thông số PRESET đã cấu hình trước đó vào metadata
            self.metadata = {
                "session_name": session_name,
                "created_at": session_name,
                "presets": self.active_presets,
                "initial_road_type": self.current_road_type,
                "initial_speed_limit": self.current_speed_limit
            }
            try:
                import json
                with open(self.metadata_file, "w", encoding="utf-8") as mf:
                    json.dump(self.metadata, mf, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"Error saving metadata: {e}")

            print(f"[{session_name}] Session initialized at {base_dir}")

    def stop_session(self, t_now):
        with self.lock:
            if not self.session_active:
                return

            # Dừng các event đang chạy dở
            for ev in list(self.active_events.keys()):
                self._stop_event_internal(ev, t_now)

            # Đóng nốt đoạn đường đang đi dở
            self._flush_road_segment(t_now)

            self.session_active = False
            self.base_dir = None
            self.events_file = None
            self.roads_file = None
            self.metadata_file = None
            print(f"[{self.session_timestamp}] Session stopped.")

    def _flush_road_segment(self, t_now):
        # Dùng nội bộ, gọi khi đã giữ lock
        if self.current_road_type != "UNKNOWN" and self.roads_file:
            with open(self.roads_file, "a") as f:
                f.write(f"{self.road_start_time:.3f},{t_now:.3f},{self.current_road_type}\n")

    def end_session(self, t_now=None):
        with self.lock:
            if t_now is not None:
                self._flush_road_segment(t_now)
            self.session_active = False

    def start_event(self, event_name, t_now):
        event_database.check_and_reload_if_modified()
        with self.lock:
            if not self.session_active:
                return False, "Vui lòng bật công tắc GHI RAW DATA trước!"

            if event_name in self.active_events:
                return False, f"{event_name} đang chạy."
            if event_name not in event_database.EVENTS:
                return False, "Sự kiện không hợp lệ."

            conflict_set = event_database.EVENT_TO_SET.get(event_name)
            stopped_msgs = []

            # 1. Auto-Swap: ngắt event cùng nhóm xung đột
            if conflict_set:
                for active_ev in list(self.active_events.keys()):
                    if active_ev == "STRAIGHT_CRUISING":
                        continue # KHÔNG tự ngắt "Đi thẳng" bởi các sự kiện cùng nhóm (ví dụ: rẽ trái/phải)
                        
                    if event_database.EVENT_TO_SET.get(active_ev) == conflict_set:
                        self._stop_event_internal(active_ev, t_now)
                        stopped_msgs.append(active_ev)
                        if "STRAIGHT" in active_ev:
                            self.auto_resume_events[conflict_set] = active_ev

            # Khi có sự kiện Tăng tốc, tắt Khởi hành
            if "ACCELERATION" in event_name or event_name in ["NORMAL_ACCELERATION", "HARSH_ACCELERATION"]:
                if "START_MOVING" in self.active_events:
                    self._stop_event_internal("START_MOVING", t_now)
                    stopped_msgs.append("START_MOVING")

            # 2. Override: Xe dừng -> tắt di chuyển
            if event_name == "STOP" or conflict_set == "STATIC_STATE":
                for active_ev in list(self.active_events.keys()):
                    act_set = event_database.EVENT_TO_SET.get(active_ev)
                    if act_set in ["LONGITUDINAL", "LATERAL", "LONGITUDINAL_CONTROL", "LATERAL_CONTROL"] or active_ev in ["LOW_SPEED", "MEDIUM_SPEED", "HIGH_SPEED", "OVERSPEED", "STRAIGHT_CRUISING", "START_MOVING"]:
                        self._stop_event_internal(active_ev, t_now)
                        stopped_msgs.append(active_ev)

            # 3. Override ngược: bắt đầu di chuyển -> tắt trạng thái đứng yên
            if conflict_set in ["LONGITUDINAL", "LATERAL", "SPEED", "LONGITUDINAL_CONTROL", "LATERAL_CONTROL"]:
                for active_ev in list(self.active_events.keys()):
                    if active_ev == "STOP" or event_database.EVENT_TO_SET.get(active_ev) == "STATIC_STATE":
                        self._stop_event_internal(active_ev, t_now)
                        stopped_msgs.append(active_ev)

            self.active_events[event_name] = t_now
            msg = f"Started {event_name}"
            if stopped_msgs:
                msg += f" (Auto-stopped: {', '.join(stopped_msgs)})"
                
            # Auto-start "Đi thẳng" khi "Khởi hành"
            if event_name == "START_MOVING":
                if "STRAIGHT_CRUISING" not in self.active_events:
                    self.active_events["STRAIGHT_CRUISING"] = t_now
                    msg += " & Auto-started STRAIGHT_CRUISING"
                    
            return True, msg

    def _stop_event_internal(self, event_name, t_now):
        # Nội bộ, gọi khi đã giữ lock rồi
        if event_name in self.active_events:
            start_time = self.active_events.pop(event_name)
            group = event_database.EVENTS.get(event_name, "CUSTOM")
            with open(self.events_file, 'a') as f:
                f.write(f"{start_time:.3f},{t_now:.3f},{event_name},{group},{self.current_road_type}\n")

    def stop_event(self, event_name, t_now):
        with self.lock:
            if event_name not in self.active_events:
                return False, "Sự kiện không tồn tại."
            self._stop_event_internal(event_name, t_now)
            msg = f"Stopped {event_name}"
            
            # Auto-resume logic cho "Đi thẳng"
            conflict_set = event_database.EVENT_TO_SET.get(event_name)
            if conflict_set and conflict_set in self.auto_resume_events:
                if self.auto_resume_events[conflict_set] == event_name:
                    # Nếu người dùng chủ động tắt "Đi thẳng", xóa bỏ auto-resume
                    del self.auto_resume_events[conflict_set]
                else:
                    resume_ev = self.auto_resume_events[conflict_set]
                    # Chỉ resume nếu chưa có event nào khác trong cùng conflict_set đang chạy
                    has_active = any(event_database.EVENT_TO_SET.get(e) == conflict_set for e in self.active_events.keys())
                    if not has_active:
                        self.active_events[resume_ev] = t_now
                        msg += f" (Auto-resumed: {resume_ev})"
                        
            return True, msg

    def close_all_active_events(self, t_now):
        """Chốt toàn bộ event đang chạy dở và phân đoạn đường hiện tại (dùng khi dừng ghi / thoát chương trình)."""
        with self.lock:
            active_list = list(self.active_events.keys())
            for ev_name in active_list:
                self._stop_event_internal(ev_name, t_now)
            self._flush_road_segment(t_now)
            return len(active_list)

    def cancel_event(self):
        with self.lock:
            if not self.active_events:
                return False, "Không có sự kiện để hủy."
            last_event = list(self.active_events.keys())[-1]
            del self.active_events[last_event]
            return True, f"Đã hủy {last_event}"

    def set_road_type(self, road_type, t_now):
        event_database.check_and_reload_if_modified()
        with self.lock:
            if road_type not in event_database.ROAD_TYPES:
                return False, "Invalid road type."

            if not self.session_active:
                self.current_road_type = road_type
                return True, f"Road set to {road_type} (Preset)"

            if self.current_road_type != road_type:
                self._flush_road_segment(t_now)
                self.current_road_type = road_type
                self.road_start_time = t_now

            return True, f"Road changed to {road_type}"
    
    def set_speed_limit(self, speed_limit, t_now):
        event_database.check_and_reload_if_modified()
        with self.lock:
            if not self.session_active:
                self.current_speed_limit = speed_limit
                return True, f"Speed limit set to {speed_limit} (Preset)"
            
            if self.current_speed_limit != speed_limit:
                self._flush_road_segment(t_now)
                self.current_speed_limit = speed_limit
                self.road_start_time = t_now

            return True, f"Speed limit changed to {speed_limit}"

    def set_preset(self, group, preset_key):
        with self.lock:
            conflict = event_database.EVENT_TO_SET.get(preset_key, "") if event_database.EVENT_TO_SET.get(preset_key) else ""
            current_val = self.active_presets.get(group, [])
            if isinstance(current_val, str):
                current_list = [current_val] if current_val else []
            elif isinstance(current_val, list):
                current_list = list(current_val)
            else:
                current_list = []

            if preset_key in current_list:
                current_list.remove(preset_key)
            else:
                if not conflict:
                    current_list.append(preset_key)
                else:
                    current_list = [k for k in current_list if event_database.EVENT_TO_SET.get(k, "") != conflict]
                    current_list.append(preset_key)

            self.active_presets[group] = current_list
            if self.session_active and self.metadata_file and os.path.exists(self.metadata_file):
                import json
                try:
                    with open(self.metadata_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception:
                    data = {"session_name": self.session_timestamp}
                data["presets"] = self.active_presets
                try:
                    with open(self.metadata_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    print(f"Error updating presets in metadata: {e}")
            return True, f"Preset {group} set to {preset_key}"

    def get_status(self, t_now):
        event_database.check_and_reload_if_modified()
        with self.lock:
            active_list = []
            for ev, st in self.active_events.items():
                active_list.append({"name": ev, "elapsed": round(t_now - st, 1)})

            return {
                "active_events": active_list,
                "current_road_type": self.current_road_type,
                "current_speed_limit": self.current_speed_limit,
                "active_presets": self.active_presets
            }