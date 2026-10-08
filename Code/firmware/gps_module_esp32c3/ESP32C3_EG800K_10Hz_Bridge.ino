/**
 * @file ESP32C3_EG800K_10Hz_Bridge.ino
 * @brief Firmware Arduino (C++) cho vi điều khiển ESP32-C3 kết nối module
 * Quectel EG800K (LTE Cat 1 & GNSS)
 *
 * CHỨC NĂNG CHÍNH:
 * 1. Giao tiếp UART với module Quectel EG800K qua cổng Serial1 (GPIO 20 / GPIO
 * 21).
 * 2. Gửi tập lệnh AT để bật nguồn bộ định vị GNSS (AT+QGPS=1) và cấu hình tần
 * số lấy mẫu lên 10Hz (hoặc 1Hz chuẩn).
 * 3. Đọc và giải mã bản tin NMEA bằng thư viện TinyGPSPlus.
 * 4. Xuất chính xác 12 cột dữ liệu chuẩn theo định dạng dự án ra USB Serial
 * (/dev/ttyACM0 trên Raspberry Pi 3) ở tốc độ 115200 bps.
 *
 * 12 CỘT DỮ LIỆU ĐẦU RA (phân cách bằng khoảng trắng):
 * [0] t_now  [1] UTC  [2] lat  [3] lon  [4] hdop  [5] alt  [6] fix  [7] cog [8]
 * speed_kmh  [9] speed_knots  [10] date  [11] satellites
 *
 * YÊU CẦU THƯ VIỆN ARDUINO IDE:
 * - TinyGPSPlus by Mikal Hart (Cài qua Library Manager trong Arduino IDE)
 */

#include <HardwareSerial.h>
#include <TinyGPSPlus.h>

// ================================================================
// 1. CẤU HÌNH CHÂN GPIO ESP32-C3 & BAUD RATE
// ================================================================
// ================================================================
// 1. CẤU HÌNH CHÂN GPIO ESP32-C3 & BAUD RATE
// ================================================================
#define QUECTEL_RX_PIN 20 // Chân RX của ESP32-C3 nối với chân TX của Quectel EG800K
#define QUECTEL_TX_PIN 21 // Chân TX của ESP32-C3 nối với chân RX của Quectel EG800K
#define EN_PIN 2          // ⚠️ QUAN TRỌNG: Chân kích hoạt nguồn cho module Quectel EG800K
#define BAUDRATE_USB 115200
#define BAUDRATE_GNSS 115200

// Khởi tạo đối tượng Serial1 cho bus UART giao tiếp Quectel EG800K
HardwareSerial QuectelSerial(1);

// Khởi tạo đối tượng phân tích NMEA
TinyGPSPlus gps;

// Biến điều khiển & chốt thời gian
unsigned long last_fix_millis = 0;
unsigned long startTime = 0;
float prevCourse = 0.0;
bool firstFix = true;

// ================================================================
// 2. HÀM CHUYỂN ĐỔI NMEA SANG DECIMAL & FORMAT AT+QGPSLOC
// ================================================================
float nmeatoDecimal(String nmea, char direction) {
  if (nmea.length() == 0)
    return 0.0;
  float raw = nmea.toFloat();
  int degrees = (int)(raw / 100);
  float minutes = raw - (degrees * 100);
  float decimal = degrees + (minutes / 60.0);
  if (direction == 'S' || direction == 'W')
    decimal *= -1;
  return decimal;
}

// Xử lý phản hồi từ lệnh truy vấn AT+QGPSLOC=0 (Dự phòng khi chưa có luồng NMEA)
String formatUAHLine(String resp) {
  if (resp.indexOf("+QGPSLOC:") == -1)
    return "";

  String data = resp.substring(resp.indexOf(":") + 2);
  String fields[12];
  int currentField = 0;
  int lastComma = 0;

  for (int i = 0; i < (int)data.length() && currentField < 12; i++) {
    if (data[i] == ',' || i == (int)data.length() - 1) {
      fields[currentField++] =
          data.substring(lastComma, (i == (int)data.length() - 1) ? i + 1 : i);
      lastComma = i + 1;
    }
  }

  float currentTime = (millis() - startTime) / 1000.0f;
  float speed = fields[7].toFloat();
  char latDir = fields[1].charAt(fields[1].length() - 1);
  float lat =
      nmeatoDecimal(fields[1].substring(0, fields[1].length() - 1), latDir);
  char lonDir = fields[2].charAt(fields[2].length() - 1);
  float lon =
      nmeatoDecimal(fields[2].substring(0, fields[2].length() - 1), lonDir);
  float alt = fields[4].toFloat();
  int nsat = fields[10].toInt();
  int fix = fields[5].toInt();
  float course = fields[6].length() > 0 ? fields[6].toFloat() : 0.0f;

  float difcourse = 0.0f;
  if (!firstFix) {
    difcourse = abs(course - prevCourse);
    if (difcourse > 180.0f)
      difcourse = 360.0f - difcourse;
  }
  prevCourse = course;
  firstFix = false;

  char buffer[150];
  snprintf(buffer, sizeof(buffer),
           "%.3f 083015.000 %.6f %.6f 1.0 %.1f %d %.1f %.1f 0.0 180726 %d",
           currentTime, lat, lon, alt, fix, course, speed, nsat);
  return String(buffer);
}

// ================================================================
// 3. HÀM GỬI LỆNH AT VÀ ĐỢI PHẢN HỒI TỪ QUECTEL EG800K
// ================================================================
void sendATCommand(const char *cmd, unsigned long timeout_ms = 2000) {
  QuectelSerial.println(cmd);
  unsigned long start = millis();
  while (millis() - start < timeout_ms) {
    while (QuectelSerial.available()) {
      char c = QuectelSerial.read();
      Serial.write(c);
    }
  }
}

// ================================================================
// 4. KHỞI TẠO HỆ THỐNG (SETUP)
// ================================================================
void setup() {
  Serial.begin(BAUDRATE_USB); // USB → Pi
  while (!Serial && millis() < 3000)
    ; // Đợi tối đa 3s cho USB Serial kết nối

  // ⚠️ BƯỚC QUAN TRỌNG NHẤT: Bật chân nguồn EN_PIN (GPIO 2) cho module Quectel
  pinMode(EN_PIN, OUTPUT);
  digitalWrite(EN_PIN, HIGH);

  Serial.println("\r\n=============================================");
  Serial.println("[ESP32-C3 Bridge 10Hz] Khởi tạo hệ thống...");
  Serial.println("Đã kích hoạt nguồn chân EN (GPIO 2) = HIGH");
  Serial.println("UART1 RX (GPIO 20) <- TX Quectel EG800K");
  Serial.println("UART1 TX (GPIO 21) -> RX Quectel EG800K");
  Serial.println("=============================================");

  // Khởi tạo UART1 với Quectel EG800K
  QuectelSerial.begin(BAUDRATE_GNSS, SERIAL_8N1, QUECTEL_RX_PIN, QUECTEL_TX_PIN);

  // ⚠️ QUAN TRỌNG: Chờ 2 giây để module Quectel khởi động ổn định sau khi bật chân EN
  Serial.println("[SETUP] Đang chờ 2 giây để module Quectel EG800K ổn định nguồn...");
  delay(2000);

  startTime = millis();

  Serial.println("[SETUP] Gửi lệnh AT kích hoạt GNSS:");
  sendATCommand("AT+QGPS=1", 2000);

  Serial.println("[SETUP] Bật xuất luồng NMEA qua cổng UART:");
  sendATCommand("AT+QGPSCFG=\"nmeasrc\",1", 1000);

  Serial.println("[SETUP] Cấu hình tần số lấy mẫu 10Hz (100ms):");
  sendATCommand("AT+QGPSCFG=\"fixfreq\",10", 1000);

  QuectelSerial.println("$PMTK220,100*2F"); // Lệnh phụ NMEA 10Hz
  Serial.println("[SYSTEM]: TDM2421 READY");
}

// ================================================================
// 5. VÒNG LẶP CHÍNH (LOOP) - XỬ LÝ NMEA & POLLING 10Hz
// ================================================================
void loop() {
  static unsigned long lastReq = 0;

  // Polling truy vấn AT+QGPSLOC=0 với tần số 10Hz (100ms/lần)
  if (millis() - lastReq > 100) {
    QuectelSerial.println("AT+QGPSLOC=0");
    lastReq = millis();
  }

  // Đọc liên tục dữ liệu từ module Quectel EG800K
  while (QuectelSerial.available() > 0) {
    // Để có thể đọc cả dòng phản hồi AT+QGPSLOC lẫn luồng ký tự NMEA
    String line = QuectelSerial.readStringUntil('\n');
    line.trim();

    // 1. Nếu module trả về chuỗi vị trí AT+QGPSLOC
    if (line.indexOf("+QGPSLOC:") != -1) {
      String uahLine = formatUAHLine(line);
      if (uahLine != "") {
        Serial.println(uahLine); // Xuất 12 cột chuẩn ra USB Serial
      }
    }
    // 2. Nếu module đang chờ sóng GNSS (Lỗi 516 của Quectel)
    else if (line.indexOf("516") != -1) {
      static unsigned long lastLog516 = 0;
      if (millis() - lastLog516 > 1000) {
        lastLog516 = millis();
        Serial.println("[LOG]: WAIT GPS FIX... (Module đang tìm vệ tinh)");
      }
    }
    // 3. Nếu là bản tin NMEA ($GPRMC, $GPGGA...) từ chế độ nmeasrc
    else if (line.startsWith("$")) {
      for (int i = 0; i < (int)line.length(); i++) {
        if (gps.encode(line[i])) {
          if (gps.location.isUpdated() || (millis() - last_fix_millis >= 95)) {
            last_fix_millis = millis();

            float t_now = (millis() - startTime) / 1000.0f;
            char utc_str[16] = "000000.000";
            if (gps.time.isValid()) {
              snprintf(utc_str, sizeof(utc_str), "%02d%02d%02d.%03d",
                       gps.time.hour(), gps.time.minute(), gps.time.second(),
                       gps.time.centisecond() * 10);
            }

            double lat = gps.location.isValid() ? gps.location.lat() : 0.0;
            double lon = gps.location.isValid() ? gps.location.lng() : 0.0;
            float hdop = gps.hdop.isValid() ? gps.hdop.hdop() : 99.9f;
            float alt = gps.altitude.isValid() ? gps.altitude.meters() : 0.0f;
            int fix = gps.location.isValid() ? (gps.altitude.isValid() ? 3 : 1) : 0;
            float cog = gps.course.isValid() ? gps.course.deg() : 0.0f;
            float speed_kmh = gps.speed.isValid() ? gps.speed.kmph() : 0.0f;
            float speed_knots = gps.speed.isValid() ? gps.speed.knots() : 0.0f;

            char date_str[10] = "010126";
            if (gps.date.isValid()) {
              snprintf(date_str, sizeof(date_str), "%02d%02d%02d", gps.date.day(),
                       gps.date.month(), gps.date.year() % 100);
            }
            int satellites = gps.satellites.isValid() ? gps.satellites.value() : 0;

            Serial.printf("%.3f %s %.6f %.6f %.1f %.1f %d %.1f %.1f %.1f %s %d\n",
                          t_now, utc_str, lat, lon, hdop, alt, fix, cog,
                          speed_kmh, speed_knots, date_str, satellites);
          }
        }
      }
    }
  }
}
