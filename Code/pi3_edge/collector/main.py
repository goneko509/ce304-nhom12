#!/usr/bin/env python3
import time
import struct
import board
import busio
import adafruit_bno055
import threading
import multiprocessing as mp
import queue
import sys
import os
from datetime import datetime
import serial

import config as cfg
from annotation_manager import AnnotationManager
import web_annotation
import ps5_worker
import realtime_inference

imu_queue = queue.Queue()
gps_queue = queue.Queue()

# Suy luan AI thoi gian thuc (process RIENG, khong chia GIL voi
# imu_worker/gps_worker - xem collector/realtime_inference.py). Chi tao
# Queue/process khi ENABLE_REALTIME_AI=True (config.py) de khong ton tai
# nguyen nao khi chua co model.
mp_imu_queue = mp.Queue() if cfg.ENABLE_REALTIME_AI else None
mp_gps_queue = mp.Queue() if cfg.ENABLE_REALTIME_AI else None
_realtime_proc = None
_realtime_stop_event = None

is_program_running = True
is_recording = False
T0 = 0.0

# Dung de tach rieng log AI realtime (collector/realtime_inference.py) cua
# TUNG phien ghi vao chinh thu muc phien do - xem _finalize_realtime_log().
_realtime_log_offset = 0
_current_session_dir = None

# Lock bảo vệ T0 / is_recording
state_lock = threading.Lock()

annotator = AnnotationManager()

# ==========================================
# BIẾN ẢO THAY THẾ CHO NÚT BẤM VẬT LÝ
# ==========================================
virtual_btn_state = False

print("=" * 60)
print("           IMU + GPS + ANNOTATION LOGGER (WEB CONTROL)")
print("=" * 60)
print(f"BNO055 Address      : 0x{cfg.BNO_ADDR:02X}")
print(f"IMU Sample Rate     : {cfg.IMU_HZ} Hz ({1000/cfg.IMU_HZ:.1f} ms/sample)")
print(f"Control Mode        : WEB SOFTWARE BUTTON (No GPIO)")
print(f"GPS Port            : {cfg.GPS_PORT}")
print(f"GPS Baudrate        : {cfg.GPS_BAUD:,} bps")
print(f"Data Directory      : {cfg.BASE_DIR}")
print(f"Python Version      : {sys.version.split()[0]}")
print(f"Platform            : {sys.platform}")
print("=" * 60)

I2C_CLOCK = "Unknown"
try:
    with open("/sys/kernel/debug/clk/clk_summary", "r") as f:
        for line in f:
            if "3f804000.i2c_div" in line:
                parts = line.split()
                if len(parts) >= 5:
                    I2C_CLOCK = f"{int(parts[4]):,} Hz"
                break
except Exception:
    pass
print(f"I2C Clock           : {I2C_CLOCK}")

# ==========================================
# 2. KHỞI TẠO CẢM BIẾN IMU
# ==========================================
def init_sensor():
    while True:
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            sensor = adafruit_bno055.BNO055_I2C(i2c, address=cfg.BNO_ADDR)
            sensor.offsets_gyroscope = (-2, 130, 1)
            sensor.offsets_accelerometer = (-14, -27, -5)
            sensor.offsets_magnetometer = (32417, -32268, 1030)
            print(f"[HỆ THỐNG]: Kết nối BNO055 thành công tại {hex(cfg.BNO_ADDR)}")
            return sensor
        except Exception as e:
            print(f"[LỖI]: Không tìm thấy cảm biến ({e}). Đang thử lại...")
            time.sleep(2)

bno = init_sensor()

# ==========================================
# 3. KHỞI TẠO GPS
# ==========================================
def init_gps():
    while True:
        try:
            ser = serial.Serial(cfg.GPS_PORT, cfg.GPS_BAUD, timeout=1)
            ser.setDTR(False)
            time.sleep(0.2)
            ser.setDTR(True)
            time.sleep(2)
            ser.reset_input_buffer()

            print("[HỆ THỐNG]: Đang chờ tín hiệu TDM2421 READY từ module GPS...")
            start_wait = time.time()
            ready_found = False
            while time.time() - start_wait < 5.0:
                if ser.in_waiting:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if "TDM2421 READY" in line:
                        ready_found = True
                        break
                    # Nếu phát hiện đã đang phát luồng 12 cột số hợp lệ thì coi như đã sẵn sàng
                    parts = line.split()
                    if len(parts) == 12:
                        try:
                            float(parts[2])
                            float(parts[3])
                            ready_found = True
                            break
                        except ValueError:
                            pass
                else:
                    time.sleep(0.05)

            if ready_found:
                print("[HỆ THỐNG]: Nhận được trạng thái TDM2421 READY (GPS Bridge đã ổn định 10Hz)")
            else:
                print("[HỆ THỐNG]: Hết thời gian chờ READY, tiếp tục kết nối GPS...")

            ser.reset_input_buffer()
            print("[HỆ THỐNG]: GPS đã kết nối")
            return ser
        except Exception as e:
            print(f"[LỖI GPS]: {e} → thử lại...")
            time.sleep(2)

gps_serial = init_gps()

# ==========================================
# 4. XỬ LÝ GHI DỮ LIỆU (ĐIỀU KHIỂN TỪ WEB)
# ==========================================
def start_logging():
    """Hàm bắt đầu ghi dữ liệu (được gọi từ giao diện Web)"""
    global is_recording, T0, _realtime_log_offset, _current_session_dir

    with state_lock:
        if is_recording:
            return  # Chống double-trigger

        while not imu_queue.empty(): imu_queue.get()
        while not gps_queue.empty(): gps_queue.get()

        session_name = datetime.now().strftime("%d%m%Y_%H%M%S")
        session_dir = os.path.join(cfg.BASE_DIR, session_name)
        os.makedirs(session_dir, exist_ok=True)

        annotator.start_session(session_dir, session_name)

        T0 = time.perf_counter()
        is_recording = True
        _current_session_dir = session_dir

        if cfg.ENABLE_REALTIME_AI:
            # Ghi nhan vi tri (byte) hien tai cua file log AI toan cuc - dung
            # de tach rieng dung phan log sinh ra TRONG phien nay luc
            # stop_logging() (xem _finalize_realtime_log()).
            try:
                _realtime_log_offset = os.path.getsize(cfg.REALTIME_LOG_PATH)
            except OSError:
                _realtime_log_offset = 0

        if mp_imu_queue is not None:
            # Bao process suy luan AI XOA buffer cu (session truoc, neu co) -
            # T0 moi reset ve 0 nen timestamp cu con sot se lon hon timestamp
            # moi, pha logic trim theo thoi gian neu khong xoa (xem
            # realtime_inference.py::run_worker).
            mp_imu_queue.put(None)
            mp_gps_queue.put(None)

    print(f"\n\n[SYSTEM]: <<< BẮT ĐẦU PHIÊN GHI: {session_name} | FOLDER: {session_dir} >>>")

def stop_logging():
    """Hàm dừng ghi dữ liệu (được gọi từ giao diện Web)"""
    global is_recording

    with state_lock:
        if not is_recording:
            return
        is_recording = False
        current_t = time.perf_counter() - T0

        if mp_imu_queue is not None:
            # Bao process suy luan AI dung lai NGAY (giong het tin hieu RESET
            # da ghi/dung luc start_logging()) - xoa buffer con dong cung tu
            # phien vua ket thuc, tranh suy luan lap lai vo ich tren du lieu
            # cu VA tranh HUD hien "ket treo" canh bao cua phien truoc (xem
            # realtime_inference.py::run_worker, nhanh xu ly saw_reset).
            mp_imu_queue.put(None)
            mp_gps_queue.put(None)

    print(f"\n[SYSTEM]: <<< DỪNG GHI | ĐANG ĐÓNG FILE... >>>")

    closed = annotator.close_all_active_events(current_t)
    if closed:
        print(f"[ANNOTATION]: Đã tự động chốt {closed} Event do ngắt hệ thống.")

    annotator.end_session(current_t)
    _finalize_realtime_log()

def _finalize_realtime_log():
    """Trích đúng phần log AI (collector/realtime_inference.py) phát sinh
    TRONG phiên vừa kết thúc - dựa vào byte offset ghi nhận lúc
    start_logging() (chính xác tuyệt đối, KHÔNG parse timestamp "t=...s" vì
    giá trị đó reset về 0 mỗi phiên nên không dùng để lọc được giữa các
    phiên) - ghi bản sao vào chính thư mục phiên để người dùng xem lại AI
    detect riêng cho từng chuyến đi, trong khi file tổng (REALTIME_LOG_PATH)
    vẫn giữ nguyên làm lịch sử cộng dồn toàn thời gian."""
    if not cfg.ENABLE_REALTIME_AI or not _current_session_dir:
        return

    # Process suy luận AI ghi log bất đồng bộ mỗi REALTIME_STRIDE_SEC giây
    # (xem realtime_inference.py::run_worker) - chờ 1 nhịp để tick cuối cùng
    # của phiên kịp flush trước khi cắt file, tránh mất vài trăm ms cuối.
    time.sleep(cfg.REALTIME_STRIDE_SEC + 0.1)

    try:
        with open(cfg.REALTIME_LOG_PATH, "rb") as f:
            f.seek(_realtime_log_offset)
            session_log_bytes = f.read()
    except FileNotFoundError:
        session_log_bytes = b""
    except Exception as e:
        print(f"[REALTIME-AI] Lỗi đọc log toàn cục để tách phiên: {e}")
        return

    try:
        out_path = os.path.join(_current_session_dir, "realtime_events.log")
        with open(out_path, "wb") as f:
            f.write(session_log_bytes)
        print(f"[REALTIME-AI]: Đã lưu log AI của phiên vào {out_path}")
    except Exception as e:
        print(f"[REALTIME-AI] Lỗi ghi log AI riêng cho phiên: {e}")

def toggle_virtual_button(state_bool):
    """Công tắc ảo nhận tín hiệu từ file web_annotation.py"""
    global virtual_btn_state
    virtual_btn_state = state_bool
    if virtual_btn_state:
        start_logging()
    else:
        stop_logging()

# ==========================================
# 5. IMU WORKER
# ==========================================
def read_imu_burst_safe(bno):
    """
    Kỹ thuật I2C Burst Read: Đọc toàn bộ 26 bytes dữ liệu IMU (từ 0x14 đến 0x2D)
    trong 1 giao dịch I2C duy nhất. Khắc phục triệt để lỗi thắt cổ chai I2C Clock Stretching.
    Bằng cách đọc nguyên khối (Burst), thanh ghi của BNO055 bị khóa trong suốt quá trình truyền,
    giúp triệt tiêu hoàn toàn lỗi "xé byte" (tearing) gây ra các gai (spike) ảo.
    """
    buf = bytearray(26)
    
    try:
        with bno.i2c_device as i2c:
            i2c.write_then_readinto(bytearray([0x14]), buf)
        
        gyr_x, gyr_y, gyr_z = struct.unpack_from('<hhh', buf, 0)
        eul_x, eul_y, eul_z = struct.unpack_from('<hhh', buf, 6)
        quat_w, quat_x, quat_y, quat_z = struct.unpack_from('<hhhh', buf, 12)
        lia_x, lia_y, lia_z = struct.unpack_from('<hhh', buf, 20)
        
        gyr = (gyr_x / 900.0, gyr_y / 900.0, gyr_z / 900.0)
        eul = (eul_x / 16.0, eul_y / 16.0, eul_z / 16.0)
        quat = (quat_w / 16384.0, quat_x / 16384.0, quat_y / 16384.0, quat_z / 16384.0)
        acc = (lia_x / 100.0, lia_y / 100.0, lia_z / 100.0)
        
        return acc, eul, gyr, quat
    except Exception:
        return (None,)*3, (None,)*4

def imu_worker():
    interval = 1.0 / cfg.IMU_HZ
    next_time = time.perf_counter()

    while is_program_running:
        try:
            acc, eul, gyr, quat = read_imu_burst_safe(bno)

            if (is_recording and isinstance(acc, tuple) and isinstance(eul, tuple)
                and isinstance(gyr, tuple) and isinstance(quat, tuple)):
                if all(v is not None for v in acc + eul + gyr + quat):
                    if max(abs(v) for v in acc) < 40 and 0 <= eul[0] < 360 and -180 <= eul[1] <= 180 and -90 <= eul[2] <= 90:
                        t_rel = time.perf_counter() - T0
                        line = (
                            f"{t_rel:.3f} "
                            f"{acc[0]:.4f} {acc[1]:.4f} {acc[2]:.4f} "
                            f"{eul[0]:.4f} {eul[1]:.4f} {eul[2]:.4f} "
                            f"{gyr[0]:.4f} {gyr[1]:.4f} {gyr[2]:.4f} "
                            f"{quat[0]:.4f} {quat[1]:.4f} {quat[2]:.4f} {quat[3]:.4f}\n"
                        )
                        imu_queue.put(line)
                        if mp_imu_queue is not None:
                            mp_imu_queue.put((
                                t_rel, acc[0], acc[1], acc[2],
                                eul[0], eul[1], eul[2], gyr[0], gyr[1], gyr[2],
                            ))
                    else:
                        print("!", end="", flush=True)
        except Exception as e:
            print(f"\n[LỖI IMU I2C]: {e}")

        next_time += interval
        sleep_time = next_time - time.perf_counter()
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            next_time = time.perf_counter()

# ==========================================
# 6. GPS WORKER
# ==========================================
def gps_worker():
    while is_program_running:
        try:
            raw_line = gps_serial.readline().decode('utf-8', errors='ignore')
            raw_line = raw_line.encode('ascii', 'ignore').decode().strip()

            if raw_line and is_recording:
                # Bỏ qua các dòng log cấu hình, hệ thống, lỗi AT từ ESP32/Quectel
                if any(tag in raw_line for tag in ["[SYSTEM]", "[SETUP]", "===", "UART", "AT+", "CME ERROR", "RDY", "OK"]):
                    continue

                parts = raw_line.split()
                # Kiểm tra đúng định dạng 12 cột từ ESP32-C3 Bridge
                if len(parts) == 12:
                    try:
                        # Kiểm tra hợp lệ dữ liệu số tọa độ lat/lon
                        float(parts[2])
                        float(parts[3])

                        # Đồng bộ nhịp thời gian T0 (Pi) thay cho t_now tương đối của ESP32 tại cột 0
                        t_now = time.perf_counter() - T0
                        parts[0] = f"{t_now:.3f}"

                        clean_line = ' '.join(parts) + '\n'
                        gps_queue.put(clean_line)

                        if mp_gps_queue is not None:
                            try:
                                speed_kmh = float(parts[8])
                                mp_gps_queue.put((t_now, speed_kmh))
                            except (IndexError, ValueError):
                                pass
                    except ValueError:
                        continue

        except Exception as e:
            print(f"[LỖI ĐỌC GPS]: {e}")
        time.sleep(0.01)

# ==========================================
# 7. WRITER WORKER
# ==========================================
def writer_worker():
    f_imu = None
    f_gps = None

    while is_program_running or not imu_queue.empty() or not gps_queue.empty():
        if is_recording and f_imu is None:
            session_dir = annotator.base_dir
            session_name = annotator.session_timestamp
            if session_dir:
                try:
                    imu_path = os.path.join(session_dir, "RAW_ACCELEROMETERS.txt")
                    gps_path = os.path.join(session_dir, "RAW_GPS.txt")

                    f_imu = open(imu_path, "w", buffering=1)
                    f_gps = open(gps_path, "w", buffering=1)

                    print(f"[FILE]: IMU → {imu_path}")
                    print(f"[FILE]: GPS → {gps_path}")
                except Exception as e:
                    print(f"[LỖI FILE]: {e}")

        while not imu_queue.empty() and f_imu:
            f_imu.write(imu_queue.get())

        while not gps_queue.empty() and f_gps:
            f_gps.write(gps_queue.get())

        if not is_recording and f_imu:
            try:
                f_imu.flush()
                os.fsync(f_imu.fileno())
                f_imu.close()

                f_gps.flush()
                os.fsync(f_gps.fileno())
                f_gps.close()

                f_imu = None
                f_gps = None
                print("\n[OK]: FILE RAW ĐÃ ĐƯỢC LƯU AN TOÀN")
            except Exception as e:
                print(f"[LỖI KHI ĐÓNG FILE]: {e}")

        time.sleep(0.05)

# ==========================================
# 8. MAIN THREAD & WEB SERVER
# ==========================================
if __name__ == '__main__':
    def get_relative_time():
        return time.perf_counter() - T0 if is_recording else 0.0

    def get_btn_state():
        return virtual_btn_state

    def get_recording_state():
        return is_recording

    # Bắt đầu PS5 worker trước để lấy instance
    worker_instance = None
    try:
        worker_instance = ps5_worker.start_ps5_worker(annotator, get_relative_time, toggle_virtual_button, get_recording_state)
    except Exception as e:
        print(f"[MAIN] Lỗi khởi tạo tay cầm PS5: {e}")

    # THÊM `toggle_virtual_button` VÀO ARGS ĐỂ WEB CÓ QUYỀN ĐIỀU KHIỂN
    if worker_instance:
        def get_ps5_status(): return worker_instance.is_connected
        def force_reconnect_ps5(): return worker_instance.force_reconnect()
    else:
        def get_ps5_status(): return False
        def force_reconnect_ps5(): return False

    threading.Thread(
        target=web_annotation.run_server,
        args=(annotator, get_relative_time, get_btn_state, get_recording_state, toggle_virtual_button, get_ps5_status, force_reconnect_ps5),
        daemon=True
    ).start()

    threading.Thread(target=imu_worker, daemon=True).start()
    threading.Thread(target=gps_worker, daemon=True).start()
    threading.Thread(target=writer_worker, daemon=True).start()

    if cfg.ENABLE_REALTIME_AI:
        _realtime_proc, _realtime_stop_event = realtime_inference.start_process(
            mp_imu_queue, mp_gps_queue, cfg.REALTIME_MODEL_PATH, cfg.REALTIME_LABEL_MAP_PATH,
            stride_sec=cfg.REALTIME_STRIDE_SEC, buffer_sec=cfg.REALTIME_BUFFER_SEC,
            declared_imu_hz=cfg.IMU_HZ, declared_gps_hz=cfg.GPS_HZ,
            log_path=cfg.REALTIME_LOG_PATH, status_path=cfg.REALTIME_STATUS_PATH,
        )
        print(f"[REALTIME-AI] Đã khởi động tiến trình suy luận (pid={_realtime_proc.pid}).")

    try:
        while True:
            t_now = time.perf_counter() - T0 if is_recording else 0.0
            btn_state = "ĐÓNG (GHI)" if virtual_btn_state else "MỞ (DỪNG)"

            if is_recording:
                status = annotator.get_status(t_now)
                active_list = status.get("active_events", [])
                if active_list:
                    ev_strs = [f"{ev['name']}({ev['elapsed']}s)" for ev in active_list]
                else:
                    sys.stdout.write(f"\r[REC RAW: {t_now:.1f}s] ĐANG GHI DỮ LIỆU... | CHỜ EVENT TRÊN WEB | BTN: {btn_state}       ")
            else:
                sys.stdout.write(f"\r[IDLE] HỆ THỐNG ĐANG CHỜ | NHẤN NÚT TRÊN WEB ĐỂ BẮT ĐẦU | BTN: {btn_state}       ")

            sys.stdout.flush()
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\n[THOÁT]: Đang dừng hệ thống và lưu file...")

        if is_recording:
            current_t = time.perf_counter() - T0
            closed = annotator.close_all_active_events(current_t)
            if closed:
                print(f"[ANNOTATION]: Đã chốt {closed} sự kiện đang chạy.")
            annotator.end_session(current_t)

        is_program_running = False
        with state_lock:
            is_recording = False

        if _realtime_stop_event is not None:
            print("[REALTIME-AI]: Đang dừng tiến trình suy luận...")
            _realtime_stop_event.set()
            _realtime_proc.join(timeout=5.0)
            if _realtime_proc.is_alive():
                _realtime_proc.terminate()

        time.sleep(2.0)
        print("[OK]: Hệ thống tắt an toàn.")