# 🛰️ CẢM BIẾN ĐỊNH VỊ VỆ TINH (QUECTEL EG800K - LTE Cat 1 & GNSS MODULE)

Thư mục này lưu trữ thông số kỹ thuật, cấu hình giao tiếp UART/USB, quy chuẩn định dạng 12 cột dữ liệu thô (`RAW_GPS_*.txt`), mã nguồn firmware vi điều khiển trung gian ESP32-C3 (`10Hz Bridge`), và xử lý sự cố cho module **Quectel EG800K** kết nối với Raspberry Pi 3.

---

## 1. Thông Số Linh Kiện (`Hardware Specifications`)

* **Tên Module / Model:** `Quectel EG800K` (LTE Cat 1 & High-Sensitivity GNSS / GPS Receiver)
* **Điện áp hoạt động (VCC):** `3.3V` / `5.0V` (Tùy mạch chuyển đổi USB/TTL breakout board)
* **Giao thức truyền thông:** Serial / UART (`TX`, `RX` hoặc USB Virtual Serial `/dev/ttyACM0`)
* **Cổng Serial cấu hình (`cfg.GPS_PORT`):** `/dev/ttyACM0` (hoặc `/dev/ttyAMA0` / `/dev/serial0`)
* **Baud rate cấu hình (`cfg.GPS_BAUD`):** `115200` bps
* **Tần số lấy mẫu mục tiêu:** **`1 Hz` (1 bản tin tọa độ/giây - Mặc định)**. Trường hợp cần nâng tần số quét lên **`10 Hz`**, xem hướng dẫn build firmware cho vi điều khiển trung gian **ESP32-C3** tại mục 5 bên dưới.

---

## 2. Quy Chuẩn Định Dạng File Dữ Liệu Thô (`RAW_GPS_*.txt` - Exactly 12 Columns)

Dữ liệu do `collector/main.py` thu thập từ module Quectel EG800K được phân tách và ghi vào tệp log `RAW_GPS_<session_id>.txt` với **chính xác 12 cột** trên mỗi dòng, được phân cách nhau bởi ký tự khoảng trắng (`whitespace`).

Bảng chi tiết ý nghĩa và ánh xạ vào hệ thống 3D HUD Telemetry (`gps_imu_realtime/server.py`):

|   Chỉ số cột   | Tên trường dữ liệu                         | Kiểu dữ liệu | Ý nghĩa & Mô tả vật lý                                                                                          | Sử dụng trong 3D HUD (`server.py`) |
| :----------------: | :---------------------------------------------- | :-------------: | :-------------------------------------------------------------------------------------------------------------------- | :------------------------------------: |
| **`[0]`** | **Thời gian hệ thống (`t_now`)**     |    `float`    | Mốc thời gian tương đối do Raspberry Pi ghi nhận lúc nhận bản tin (giây)                                   |               `"time"`               |
| **`[1]`** | **Giờ UTC (`UTC Time`)**               |   `string`   | Giờ chuẩn quốc tế gốc từ vệ tinh trả về (HHMMSS.sss)                                                         |          *(Lưu trữ log)*          |
| **`[2]`** | **Vĩ độ (`Latitude`)**               |    `float`    | Tọa độ vĩ độ thực tế của phương tiện (đã chuyển sang hệ thập phân Decimal Degrees)                  |               `"lat"`               |
| **`[3]`** | **Kinh độ (`Longitude`)**             |    `float`    | Tọa độ kinh độ thực tế của phương tiện (đã chuyển sang hệ thập phân Decimal Degrees)                 |               `"lon"`               |
| **`[4]`** | **Độ chính xác ngang (`HDOP`)**     |    `float`    | Horizontal Dilution of Precision (Chỉ số suy giảm độ chính xác ngang - số càng nhỏ định vị càng chuẩn) |               `"hdop"`               |
| **`[5]`** | **Độ cao (`Altitude`)**               |    `float`    | Chiều cao so với mực nước biển trung bình (mét)                                                               |               `"alt"`               |
| **`[6]`** | **Trạng thái Fix (`Fix Quality`)**    |     `int`     | Chất lượng chốt tọa độ GNSS (Ví dụ:`0` = No Fix, `1` = 2D Fix, `3` = 3D Fix chuẩn)                    |               `"fix"`               |
| **`[7]`** | **Hướng di chuyển (`COG`)**          |    `float`    | Course Over Ground - Góc hướng đi của xe so với phương Bắc địa lý (độ`0° - 360°`)                   |               `"cog"`               |
| **`[8]`** | **Tốc độ (`km/h`)**                  |    `float`    | Vận tốc di chuyển của phương tiện tính bằng Kilômét trên giờ                                             |              `"speed"`              |
| **`[9]`** | **Tốc độ (`knots`)**                 |    `float`    | Vận tốc di chuyển của phương tiện tính theo Hải lý trên giờ                                               |          *(Lưu trữ log)*          |
| **`[10]`** | **Ngày tháng (`DDMMYY`)**             |   `string`   | Mốc ngày đo theo định dạng Ngày/Tháng/Năm (`DDMMYY`)                                                       |          *(Lưu trữ log)*          |
| **`[11]`** | **Số lượng vệ tinh (`Satellites`)** |     `int`     | Tổng số lượng vệ tinh GPS/GLONASS/Galileo/BeiDou đang kết nối để chốt vị trí                             |            `"satellites"`            |

### 💡 Ví dụ cấu trúc 1 dòng trong file `RAW_GPS_*.txt`:

```text
1721312345.123 140512.000 10.762622 106.660172 0.8 15.5 3 245.2 42.5 22.9 180726 12
```

---

## 3. Sơ Đồ Kết Nối với Raspberry Pi 3 (`Serial / USB Connection`)

| Chân trên Module Quectel EG800K |                       Cổng/Chân trên Pi 3                       | Mô tả giao tiếp                                       |
| :-------------------------------: | :-----------------------------------------------------------------: | :------------------------------------------------------- |
|       **USB/UART TX**       | `USB Type-A` (`/dev/ttyACM0`) hoặc `Pin 10` (GPIO 15 - RXD0) | Truyền bản tin NMEA/GNSS từ Quectel vào Raspberry Pi |
|       **USB/UART RX**       | `USB Type-A` (`/dev/ttyACM0`) hoặc `Pin 8` (GPIO 14 - TXD0) | Gửi lệnh AT/cấu hình từ Raspberry Pi xuống module  |
|        **VCC & GND**        |          Nguồn USB hoặc`Pin 2` (5V) & `Pin 6` (GND)          | Cấp nguồn và nối đất ổn định                    |

---

## 4. Ghi Chú Kỹ Thuật & Xử Lý Sự Cố (`Troubleshooting`)

* **Kiểm tra luồng dữ liệu thô:** Có thể kiểm tra nhanh bản tin 12 cột phát ra qua lệnh terminal trên Pi: `tail -f /home/admin/raw_data_collection/raw_data/RAW_GPS_*.txt`
* **Đồng bộ hóa nhãn thời gian:** Cột `[0]` (`t_now`) được sử dụng làm mốc thời gian chính để ghép cặp (`Synchronization`) khớp tuyệt đối với cảm biến IMU Bosch BNO055 (100Hz) khi phân tích offline và mô phỏng Replay.

---

## 5. Firmware Vi Điều Khiển Trung Gian ESP32-C3 (`10Hz Bridge & 12-Column Formatter`)

Mặc định module Quectel EG800K trả về chu kỳ **1 Hz** (1 giây / bản tin). Nếu muốn tối ưu hệ thống thu thập với tốc độ quét **10 Hz** (100 ms / bản tin) và tự động phân giải NMEA thành chuỗi **12 cột chuẩn** ngay trên vi điều khiển trước khi gửi qua USB vào Raspberry Pi 3 (`/dev/ttyACM0`), sử dụng mã nguồn Arduino C++ được cung cấp trong kho dự án:

* 📄 **File mã nguồn hoàn chỉnh:** [ESP32C3_EG800K_10Hz_Bridge.ino](<file:///d:/0.%20UIT/0%20-%20KHOALUAN/Pi3/raw_data_collection/sensors_docs/gps/ESP32C3_EG800K_10Hz_Bridge.ino>)
* 🛠️ **Thư viện yêu cầu (Arduino IDE):** `TinyGPSPlus` của Mikal Hart (Cài qua Library Manager).
* ⚡ **Nguyên lý hoạt động:**
  1. ESP32-C3 kết nối với chân UART của Quectel EG800K qua `Serial1` (Chân `GPIO20` RX, `GPIO21` TX ở baudrate `115200`).
  2. Khi khởi động (`setup()`), ESP32-C3 tự động gửi chuỗi lệnh AT (`AT+QGPS=1`, `AT+QGPSCFG="fixfreq",10`) để kích hoạt bộ định vị và cấu hình chu kỳ cập nhật lên 10Hz.
  3. Trong vòng lặp (`loop()`), ESP32-C3 giải mã NMEA liên tục, tính toán và xuất trực tiếp dòng chuỗi 12 cột (`t_now UTC lat lon hdop alt fix cog speed knots date satellites`) qua USB Serial (`Serial.println()`) ở baudrate `115200`.

### A. Cấu Trúc Chính Xác 12 Cột Dữ Liệu Đầu Ra (Từ `[0]` đến `[11]`)

Mỗi chu kỳ ~100ms (10Hz), vi điều khiển xuất qua cổng USB Serial (`/dev/ttyACM0`) đúng 1 dòng ASCII gồm **12 cột phân cách bằng ký tự khoảng trắng**:

|        Cột        | Tên trường             | Kiểu dữ liệu         | Ý nghĩa / Mô tả chi tiết                                                         | Giá trị khi chưa Fix                |
| :----------------: | :------------------------ | :---------------------- | :------------------------------------------------------------------------------------ | :------------------------------------- |
| **`[0]`** | **`t_now`**       | `float` (3 số lẻ)   | Thời gian tương đối của ESP32 kể từ lúc khởi động (giây)                 | Tăng dần (`0.000`, `0.100`, ...) |
| **`[1]`** | **`UTC`**         | `string` (10 ký tự) | Giờ UTC chuẩn theo định dạng`HHMMSS.sss`                                       | `000000.000`                         |
| **`[2]`** | **`lat`**         | `double` (6 số lẻ)  | Vĩ độ hệ thập phân (`Decimal Degrees`)                                        | `0.000000`                           |
| **`[3]`** | **`lon`**         | `double` (6 số lẻ)  | Kinh độ hệ thập phân (`Decimal Degrees`)                                       | `0.000000`                           |
| **`[4]`** | **`hdop`**        | `float` (1 số lẻ)   | Độ suy giảm độ chính xác ngang (`Horizontal Dilution of Precision`)          | `99.9`                               |
| **`[5]`** | **`alt`**         | `float` (1 số lẻ)   | Độ cao so với mực nước biển tính bằng mét (`Altitude`)                    | `0.0`                                |
| **`[6]`** | **`fix`**         | `int`                 | Trạng thái chất lượng định vị:`0` = No Fix, `1` = 2D Fix, `3` = 3D Fix  | `0`                                  |
| **`[7]`** | **`cog`**         | `float` (1 số lẻ)   | Hướng di chuyển so với phương Bắc (`Course Over Ground` `0.0° - 360.0°`) | `0.0`                                |
| **`[8]`** | **`speed_kmh`**   | `float` (1 số lẻ)   | Tốc độ di chuyển tính bằng`km/h`                                              | `0.0`                                |
| **`[9]`** | **`speed_knots`** | `float` (1 số lẻ)   | Tốc độ di chuyển tính bằng hải lý/giờ (`knots`)                            | `0.0`                                |
| **`[10]`** | **`date`**        | `string` (6 ký tự)  | Ngày tháng năm UTC theo định dạng`DDMMYY`                                     | `010126`                             |
| **`[11]`** | **`satellites`**  | `int`                 | Số lượng vệ tinh đang kết nối để chốt tọa độ                             | `0`                                  |

#### 💡 Ví dụ dòng dữ liệu đầu ra thực tế (`Sample Output`):

```text
# Khi mất sóng / chưa chốt vị trí (No Fix - fix=0):
1.125 000000.000 0.000000 0.000000 99.9 0.0 0 0.0 0.0 0.0 010126 0

# Khi đã định vị 3D thành công ở tốc độ 10Hz (3D Fix - fix=3):
45.102 083015.100 10.870123 106.803456 1.1 15.4 3 185.5 45.2 24.4 180726 12
45.203 083015.200 10.870135 106.803451 1.1 15.4 3 185.6 45.4 24.5 180726 12
```

---

### B. Hướng Dẫn Nạp Code (`Flashing Firmware Guide`)

#### 1. Nạp trực tiếp trên máy tính Windows (Khuyên dùng - Arduino IDE)

* **Bước 1:** Mở phần mềm **Arduino IDE** (hoặc Extension Arduino/PlatformIO trên VS Code), tải file [ESP32C3_EG800K_10Hz_Bridge.ino](<file:///d:/0.%20UIT/0%20-%20KHOALUAN/Pi3/raw_data_collection/sensors_docs/gps/ESP32C3_EG800K_10Hz_Bridge.ino>).
* **Bước 2:** Vào `Tools -> Manage Libraries...` (`Ctrl + Shift + I`), tìm và cài đặt thư viện **`TinyGPSPlus`** by Mikal Hart.
* **Bước 3:** Vào `Tools -> Board -> esp32`, chọn **`ESP32C3 Dev Module`**.
* **Bước 4:** Cắm cổng USB ESP32-C3 vào máy tính, chọn cổng `COM` tương ứng trong `Tools -> Port`.
* **Bước 5:** Nhấn nút **Upload** (➡️) để biên dịch và nạp firmware. Sau khi hoàn tất, mở **Serial Monitor** ở baud rate `115200 bps` để kiểm tra luồng 12 cột dữ liệu.

#### 2. Nạp từ xa qua Raspberry Pi 3 (`Remote Flash via SSH`)

Nếu mạch ESP32-C3 đang kết nối qua cổng USB `/dev/ttyACM0` trên Raspberry Pi 3:

* Sử dụng công cụ `arduino-cli` hoặc `esptool.py` được cài đặt trên Raspberry Pi 3 để biên dịch và nạp trực tiếp file `.ino` qua đường dẫn SSH mà không cần tháo mạch khỏi hệ thống.

---

### C. Khắc Phục Cảnh Báo IDE (`clangd Warning Troubleshooting`)

Khi mở file `.ino` trong VS Code / Antigravity IDE, nếu gặp thông báo báo lỗi/cảnh báo ở dòng 1:
`Unable to handle compilation, expected exactly one compiler job in '' @[ESP32C3_EG800K_10Hz_Bridge.ino:L1]`

* **Nguyên nhân:** Đây **không phải lỗi cú pháp C++**. Bộ phân tích ngôn ngữ `clangd` của IDE không tự nhận diện định dạng sketch `.ino` là C++ chuẩn khi thiếu cấu hình `compile_commands.json`.
* **Khắc phục triệt để:**
  * **Cách 1 (Tạo cấu hình `.clangd` ở gốc dự án):** Tạo file `.clangd` với nội dung sau để ép `clangd` xử lý file `.ino` như C++ (`-xc++`):
    ```yaml
    If:
      PathMatch: .*\.ino$
    CompileFlags:
      Add: [-xc++, -std=c++17]
    ```
  * **Cách 2 (Cấu hình VS Code Workspace `.vscode/settings.json`):**
    ```json
    {
      "files.associations": {
        "*.ino": "cpp"
      },
      "clangd.fallbackFlags": [
        "-xc++",
        "-std=c++17"
      ]
    }
    ```

---

## 6. Hướng Dẫn Kiểm Tra SIM & Kết Nối Internet (`SIM & Internet Diagnostics`)

Để kiểm tra trạng thái thẻ SIM, cường độ sóng di động và thiết lập kết nối Internet cho module **Quectel EG800K** trên Raspberry Pi 3:

### A. Yêu Cầu Kết Nối Vật Lý để Chẩn Đoán Lệnh AT

Do firmware 10Hz Bridge mặc định chiếm dụng cổng truyền thông và không chuyển tiếp lệnh AT, bạn phải thực hiện một trong hai cách sau để có thể gửi lệnh cấu hình SIM từ Pi:

1. **Cách 1 (USB Trực Tiếp - Khuyên Dùng để chẩn đoán):** Rút cáp USB từ ESP32 sang Pi, cắm cáp USB trực tiếp từ cổng USB của module Quectel EG800K vào cổng USB của Raspberry Pi 3.
2. **Cách 2 (Sử dụng ESP32 Passthrough):** Nạp firmware nâng cấp [ESP32C3_EG800K_Passthrough_Bridge.ino](<file:///d:/0.%20UIT/0%20-%20KHOALUAN/Pi3/raw_data_collection/sensors_docs/gps/ESP32C3_EG800K_Passthrough_Bridge/ESP32C3_EG800K_Passthrough_Bridge.ino>) vào ESP32-C3. Khi bạn kết nối qua cổng `/dev/ttyACM0` và gõ bất kỳ ký tự nào, mạch sẽ tự động kích hoạt chế độ cầu nối AT tạm thời trong 5 giây trước khi quay lại chế độ đọc GPS.

### B. Các Lệnh AT Chẩn Đoán Nhanh

Mở terminal trên Raspberry Pi 3, cài đặt và mở Minicom kết nối vào cổng điều khiển AT của module (thường là `/dev/ttyUSB2` hoặc cổng ảo `/dev/ttyACM0` của ESP32):

```bash
sudo apt update && sudo apt install minicom -y
sudo minicom -D /dev/ttyUSB2 -b 115200 # Hoặc /dev/ttyACM0
```

Gõ các lệnh sau và nhấn **Enter**:

* **`AT`**: Phản hồi cơ bản. Mong đợi: `OK`.
* **`AT+CPIN?`**: Trạng thái thẻ SIM. Mong đợi: `+CPIN: READY`. Nếu báo `SIM not inserted` cần kiểm tra lại khay SIM.
* **`AT+CSQ`**: Cường độ sóng di động. Mong đợi: `+CSQ: <rssi>,99` (chỉ số `<rssi>` từ 15 đến 31 là sóng tốt, dưới 10 cần kiểm tra ăng-ten LTE).
* **`AT+COPS?`**: Nhà mạng di động đang kết nối (ví dụ: `+COPS: 0,0,"Viettel"`).
* **`AT+CEREG?`**: Đăng ký mạng LTE. Mong đợi: `+CEREG: 0,1` hoặc `+CEREG: 0,5`.

### C. Kích Hoạt Chế Độ Mạng Tự Động (ECM) và Kiểm Tra Ping

Để biến module Quectel thành một card mạng ảo trên Raspberry Pi 3 (tự động kết nối internet di động không cần quay số):

1. **Gửi lệnh kích hoạt ECM:**
   ```text
   AT+QCFG="usbnet",1
   ```
2. **Khởi động lại module để lưu cấu hình:**
   ```text
   AT+CFUN=1,1
   ```
3. **Thoát Minicom** (`Ctrl + A`, sau đó bấm `X` -> `Yes`).
4. **Kiểm tra card mạng ảo:** Gõ lệnh `ifconfig` hoặc `ip a` trên Pi, mong đợi xuất hiện cổng **`usb0`** (hoặc `wwan0`) được cấp IP cục bộ.
5. **Kiểm tra Ping Internet:**
   ```bash
   ping -I usb0 -c 4 google.com
   ```

   Nếu phản hồi thành công và không mất gói tin, mạng di động đã sẵn sàng.

### D. Phân Luồng Định Tuyến (Nếu Sử Dụng WiFi Song Song)

Khi dùng song song WiFi (để kết nối ESP32 hoặc hiển thị Web HUD) và 4G qua cổng USB:

* Cần đặt độ ưu tiên (Metric) của cổng mạng `usb0` thấp hơn WiFi để đảm bảo dữ liệu đi ra internet qua mạng di động, còn WiFi chỉ dùng cho mạng LAN nội bộ.
* Chỉnh sửa trong `/etc/dhcpcd.conf` hoặc trình quản lý NetworkManager trên Pi 3 để đặt Metric cho `usb0` là `100` và `wlan0` là `300`.```
