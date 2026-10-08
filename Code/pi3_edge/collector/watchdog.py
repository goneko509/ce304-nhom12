#!/usr/bin/python3
import subprocess
import time
import sys

# Tên dịch vụ cần giám sát
SERVICE_NAME = "raw_data_collection.service"

# Các từ khóa báo lỗi Sensor (Không phân biệt hoa thường)
# - Errno 121: Lỗi rớt dây I2C kinh điển của IMU MPU6050
# - SerialException: Lỗi tuột cáp USB/UART của GPS
# - /dev/ttyUSB: Không tìm thấy cổng GPS
ERROR_KEYWORDS = [
    "errno 121", 
    "remote i/o error", 
    "serialexception", 
    "/dev/ttyusb", 
    "/dev/serial",
    "i2c error"
]

def stop_service():
    print(f"\n[WATCHDOG] 🛑 Đang tự động ngắt {SERVICE_NAME} để bảo vệ hệ thống...")
    subprocess.run(["sudo", "systemctl", "stop", SERVICE_NAME])
    print("[WATCHDOG] Đã ngắt thành công. Dữ liệu rác đã chặn.")

def restart_service():
    print("\n[WATCHDOG] 🔄 Đang khởi động lại dịch vụ...")
    subprocess.run(["sudo", "systemctl", "start", SERVICE_NAME])
    print("[WATCHDOG] ✅ Dịch vụ đã chạy lại. Trở về trạng thái theo dõi...\n")

def monitor():
    print(f"[WATCHDOG] 👁️  Bắt đầu theo dõi lỗi phần cứng từ {SERVICE_NAME}...")
    
    # Mở luồng đọc log realtime (-f) từ dòng mới nhất (-n 0)
    process = subprocess.Popen(
        ["sudo", "journalctl", "-u", SERVICE_NAME, "-f", "-n", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        # Đọc từng dòng log bắn ra từ dịch vụ
        for line in process.stdout:
            line_lower = line.strip().lower()
            
            # Kiểm tra xem dòng log có chứa từ khóa lỗi không
            for kw in ERROR_KEYWORDS:
                if kw in line_lower:
                    print("\n" + "🔥" * 30)
                    print("🚨 PHÁT HIỆN SỰ CỐ MẤT KẾT NỐI SENSOR 🚨")
                    print(f"Chi tiết lỗi: {line.strip()}")
                    print("🔥" * 30)

                    # 1. Dừng tiến trình đọc log hiện tại
                    process.terminate()

                    # 2. Ngắt ngay dịch vụ thu thập dữ liệu
                    stop_service()

                    # 3. Yêu cầu anh Viễn xử lý vật lý
                    print("\n" + "="*60)
                    print(">>> HƯỚNG DẪN XỬ LÝ:")
                    print("  1. Kiểm tra lại jack cắm I2C (IMU) hoặc cổng USB (GPS).")
                    print("  2. Cắm thật chặt lại vào Raspberry Pi.")
                    print("="*60)
                    
                    # 4. Chờ anh xác nhận
                    input(">>> Nhấn phím [ENTER] tại đây sau khi anh đã gắn xong dây... ")

                    # 5. Khởi động lại dịch vụ
                    restart_service()

                    # 6. Trả về True để vòng lặp main() khởi động lại luồng theo dõi
                    return True 
                    
    except KeyboardInterrupt:
        process.terminate()
        print("\n[WATCHDOG] Đã tắt công cụ theo dõi.")
        return False

def main():
    while True:
        # Chạy hàm monitor, nếu nó trả về True (vừa sửa lỗi xong) thì lặp lại tiếp
        should_continue = monitor()
        if not should_continue:
            break
        time.sleep(1)

if __name__ == "__main__":
    main()