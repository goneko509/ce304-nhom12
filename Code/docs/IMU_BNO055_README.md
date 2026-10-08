# 🧭 CẢM BIẾN QUÁN TÍNH (BOSCH BNO055 - 9-DOF ABSOLUTE ORIENTATION MODULE)

Thư mục này lưu trữ thông số kỹ thuật chi tiết, sơ đồ quy chuẩn hướng trục trên phương tiện (`Axis Remap / Vehicle Alignment`), ánh xạ góc Euler theo chuẩn Bosch, bảng quy chuẩn định dạng 14 cột dữ liệu thô (`RAW_ACCELEROMETERS_*.txt`), và cấu hình hiệu chuẩn (`Manual Calibration Offsets`) cho cảm biến 9-DOF **Bosch BNO055** trên Raspberry Pi 3.

---

## 1. Thông Số Linh Kiện (`Hardware Specifications`)

* **Tên Module / Model:** `Bosch BNO055` (Absolute Orientation 9-DOF Sensor - Adafruit Breakout / Equivalent)
* **Điện áp hoạt động (VCC):** `3.3V` / `5V` (Tích hợp bộ chuyển đổi mức điện áp trên breakout board)
* **Giao thức kết nối:** Bus `I2C` qua GPIO Raspberry Pi 3 (`board.SCL`, `board.SDA`)
* **Địa chỉ I2C cấu hình (`cfg.BNO_ADDR`):** `0x29` (hoặc `0x28` tùy cấu hình COM3)
* **Tần số lấy mẫu mục tiêu (`cfg.IMU_HZ`):** `100 Hz` (100 chu kỳ đọc/giây)
* **Dữ liệu cung cấp:** Gia tốc tuyến tính 3 trục ($Acc_X, Acc_Y, Acc_Z$), Vận tốc góc Gyroscope ($Gyr_X, Gyr_Y, Gyr_Z$), Góc Euler ($Yaw, Roll, Pitch$) và Quaternion ($Quat_W, Quat_X, Quat_Y, Quat_Z$).

---

## 2. Quy Chuẩn Hướng Trục & Ánh Xạ Góc Euler (`Vehicle Axis Alignment & Bosch Euler Mapping`)

Theo tài liệu kỹ thuật BOSCH và hướng thiết lập thực tế của cảm biến BNO055 cố định trên phương tiện, các trục vật lý được định hướng và ánh xạ chính xác vào góc Euler như sau:

```text
               [ HƯỚNG ĐẦU XE (FORWARD) ]
                          ↑
                          │   +Y (Trục Y chỉa về hướng đầu xe)
                          │
  +X (Trục X chỉa qua bên phải xe) ──→ [ BÊN PHẢI XE (RIGHT) ]
                          │
                          ⊙   +Z (Trục Z chỉa thẳng đứng lên trời - UP)
```

### 🔹 Bảng Ánh Xạ Trục & Bộ Giá Trị Euler (`bno.euler` Tuple trong Python):

Bộ thư viện Adafruit trả về tuple `sensor.euler` gồm 3 phần tử `(Euler[0], Euler[1], Euler[2])`. Ánh xạ chi tiết theo hướng thiết lập trên xe:

|   Trục Vật Lý BNO055   | Hướng Phương Tiện                           | Góc Xoay Tương Ứng           | Chỉ Số Trong`sensor.euler` | Dải Giá Trị Hợp Lệ | Ý Nghĩa Chuyển Động Trên Xe                                                                                      |
| :------------------------: | :----------------------------------------------- | :------------------------------- | :----------------------------: | :---------------------: | :--------------------------------------------------------------------------------------------------------------------- |
| **Trục Z (`+Z`)** | **Hướng thẳng lên trời (`Upward`)** | **Góc `Yaw` (Heading)** |     **`Euler[0]`**     |   `0° đến 360°`   | Góc xoay hướng xe (Rẽ trái / Rẽ phải theo la bàn)                                                              |
| **Trục Y (`+Y`)** | **Hướng đầu xe (`Forward`)**         | **Góc `Roll`**          |     **`Euler[1]`**     |  `-90° đến +90°`  | Góc nghiêng / lắc ngang của xe khi vào cua hoặc đi trên đường nghiêng                                      |
| **Trục X (`+X`)** | **Hướng bên phải xe (`Right`)**      | **Góc `Pitch`**         |     **`Euler[2]`**     | `-180° đến +180°` | Góc chúi/ngửa xe quanh trục X. Dương (+) = xuống dốc (mui chúi xuống); Âm (–) = lên dốc (mui ngóc lên) |

---

## 3. Quy Chuẩn Định Dạng File Dữ Liệu Thô (`RAW_ACCELEROMETERS_*.txt` - Exactly 14 Columns)

Dữ liệu do `collector/main.py` đọc với tần số 100Hz được ghi vào tệp log `RAW_ACCELEROMETERS_<session_id>.txt` với **chính xác 14 cột** trên mỗi dòng, phân cách bởi khoảng trắng (`whitespace`).

Bảng cấu trúc 14 cột và ánh xạ sang API Telemetry `server.py`:

|   Chỉ số cột   | Nguồn dữ liệu BNO055        | Tên trường (`server.py`) | Kiểu dữ liệu | Mô tả kỹ thuật                                                                                                                             |
| :----------------: | :----------------------------- | :---------------------------: | :-------------: | :--------------------------------------------------------------------------------------------------------------------------------------------- |
| **`[0]`** | `time.perf_counter() - T0`   |          `"time"`          |    `float`    | Mốc thời gian tương đối kể từ khi bắt đầu ghi phiên (giây, độ phân giải mili giây)                                           |
| **`[1]`** | `bno.linear_acceleration[0]` |     `"acc_x"` (`AX`)     |    `float`    | **Gia tốc AX (Ngang)**: Gia tốc tuyến tính theo trục X (m/s² - Hướng ngang qua bên phải xe)                                    |
| **`[2]`** | `bno.linear_acceleration[1]` |     `"acc_y"` (`AY`)     |    `float`    | **Gia tốc AY (Dọc)**: Gia tốc tuyến tính theo trục Y (m/s² - Hướng dọc đầu xe / Surge)                                       |
| **`[3]`** | `bno.linear_acceleration[2]` |     `"acc_z"` (`AZ`)     |    `float`    | **Gia tốc AZ (Đứng)**: Gia tốc tuyến tính theo trục Z (m/s² - Hướng thẳng đứng / Heave)                                     |
| **`[4]`** | `bno.euler[0]`               |      **`"yaw"`**      |    `float`    | **Góc Yaw / Heading (`Euler[0]`)**: Hướng rẽ của xe quanh trục Z (`0° - 360°`)                                               |
| **`[5]`** | `bno.euler[1]`               |     **`"roll"`**     |    `float`    | **Góc Roll (`Euler[1]`)**: Góc nghiêng lắc ngang quanh trục Y (`-90° đến +90°`). Âm = Nghiêng phải                      |
| **`[6]`** | `bno.euler[2]`               |     **`"pitch"`**     |    `float`    | **Góc Pitch (`Euler[2]`)**: Góc chúi/ngửa quanh trục X (`-180° đến +180°`). Dương (+) = xuống dốc; Âm (–) = lên dốc |
| **`[7]`** | `bno.gyro[0]`                |     `"gyr_x"` (`GX`)     |    `float`    | **Vận tốc góc GX (Pitch)**: Vận tốc xoay quanh trục X (`rad/s` - Tốc độ góc Pitch)                                           |
| **`[8]`** | `bno.gyro[1]`                |     `"gyr_y"` (`GY`)     |    `float`    | **Vận tốc góc GY (Roll)**: Vận tốc xoay quanh trục Y (`rad/s` - Tốc độ góc Roll)                                             |
| **`[9]`** | `bno.gyro[2]`                |     `"gyr_z"` (`GZ`)     |    `float`    | **Vận tốc góc GZ (Yaw)**: Vận tốc xoay quanh trục Z (`rad/s` - Tốc độ rẽ hướng Yaw)                                        |
| **`[10]`** | `bno.quaternion[0]`          |         `"quat_w"`         |    `float`    | Thành phần$W$ của Quaternion định hướng trong không gian 3D                                                                          |
| **`[11]`** | `bno.quaternion[1]`          |         `"quat_x"`         |    `float`    | Thành phần$X$ của Quaternion định hướng trong không gian 3D                                                                          |
| **`[12]`** | `bno.quaternion[2]`          |         `"quat_y"`         |    `float`    | Thành phần$Y$ của Quaternion định hướng trong không gian 3D                                                                          |
| **`[13]`** | `bno.quaternion[3]`          |         `"quat_z"`         |    `float`    | Thành phần$Z$ của Quaternion định hướng trong không gian 3D                                                                          |

### 💡 Ví dụ cấu trúc 1 dòng trong file `RAW_ACCELEROMETERS_*.txt`:

```text
12.345 0.1234 -0.0512 9.8100 245.1200 1.2500 -2.3100 0.0012 -0.0005 0.0021 0.9981 0.0123 -0.0451 0.0312
```

---

## 4. Bảng Hiệu Chuẩn Thủ Công (`Manual Calibration Offsets`)

Cảm biến BNO055 tích hợp bộ vi xử lý ARM Cortex-M0 chạy thuật toán dung hợp cảm biến (`Sensor Fusion`). Sau quá trình thực hiện hiệu chuẩn thủ công ban đầu cho 3 cụm cảm biến con (Gyroscope, Accelerometer, Magnetometer), các giá trị bù sai lệch (`Offsets`) thu được đã được nạp cố định vào mã nguồn `collector/main.py`:

```python
# Cấu hình khởi tạo và nạp offset tĩnh cho BNO055 trong hàm init_sensor() của collector/main.py
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_bno055.BNO055_I2C(i2c, address=cfg.BNO_ADDR)

# Các bộ giá trị bù độ lệch tĩnh sau khi hiệu chuẩn thủ công:
sensor.offsets_gyroscope = (-2, 130, 1)            # Bù trôi điểm không góc xoay (Gyro Bias)
sensor.offsets_accelerometer = (-14, -27, -5)       # Bù sai lệch gia tốc tuyến tính (Acc Bias)
sensor.offsets_magnetometer = (32417, -32268, 1030) # Bù từ trường cứng/mềm (Mag Hard/Soft Iron)
```

### 💡 Ý nghĩa của việc nạp tĩnh Offsets:

* Khi dịch vụ `raw_data_collection.service` khởi chạy trên Raspberry Pi 3, bộ `offsets_*` này được ghi thẳng vào các thanh ghi offset (`Offset Registers`) của BNO055. Nhờ đó, cảm biến lập tức xuất dữ liệu góc `Euler` và `Quaternion` với độ chính xác cao ngay từ giây đầu tiên mà không yêu cầu người lái xe phải di chuyển xe theo hình số 8 để hiệu chuẩn lại từ đầu.

---

## 5. Sơ Đồ Đấu Nối Thực Tế (`I2C Wiring Pinout`)

| Chân trên BNO055 Breakout |    Chân GPIO Raspberry Pi 3    | Mô tả chức năng                                 |
| :-------------------------: | :------------------------------: | :-------------------------------------------------- |
|     **VIN / VCC**     |         `Pin 1` (3.3V)         | Nguồn điện cấp cho cảm biến                   |
|        **GND**        |         `Pin 6` (GND)         | Nguồn đất chung                                  |
|                            |                                  |                                                     |
|                            |                                  |                                                     |
|        **SCL**        |    `Pin 5` (GPIO 3 - SCL1)    | Xung nhịp bus I2C (`board.SCL`)                  |
|        **SDA**        |    `Pin 3` (GPIO 2 - SDA1)    | Đường dữ liệu bus I2C (`board.SDA`)          |
|    **ADR / COM3**    | *(Để trống hoặc nối VCC)* | Đặt địa chỉ I2C là`0x29` (`cfg.BNO_ADDR`) |
