========================================================================
    DỰ ÁN PHẦN MỀM MÃ NGUỒN PHÂN TÍCH DỮ LIỆU TELEMETRY (MODULAR APP)
    Phiên bản 3.0 - Pipeline tuần tự, không còn Tab trùng lặp
========================================================================

Cấu trúc dự án:
- app_config.json         : Tệp cấu hình phân cấp JSON (Part -> Frame -> Tab),
                             từng Tab có kèm mô_tả / cơ_sở_khoa_học / tham_khảo
- main_app.py              : Chương trình chính đọc JSON và nạp động các module .py
- core/
    - app_state.py         : Quản lý trạng thái chia sẻ và Bus sự kiện (Observer)
    - sensor_schema.py      : Tên cột GPS/IMU, công thức Haversine, biến đổi
                             sin/cos góc Euler, danh sách đặc trưng CNN
                             (nguồn chân lý duy nhất, thay cho code trùng lặp cũ)
    - ui_widgets.py         : Combobox instant-trigger + ScienceInfoPanel
                             (khối "Chức năng / Cơ sở khoa học / Tham khảo")
    - ai_pipeline.py        : Trích tensor -> huấn luyện Conv1D -> lượng tử hóa
                             INT8 (dùng chung cho GUI và chạy độc lập qua CLI:
                             python -m core.ai_pipeline)
    - event_detection_engine.py : Logic phát hiện sự kiện lái xe (10/20/40/80
                             km/h + 8 nhãn Residual đặt tên tiếng Anh theo điều kiện), tham số hóa toàn
                             bộ threshold qua dict cfg cho step8 chỉnh từ GUI
    - label_schema.py       : NGUỒN CHÂN LÝ DUY NHẤT cho 33 nhãn (25 Primary
                             + 8 Residual) dùng huấn luyện/suy luận AI - file
                             thuần Python (không phụ thuộc pandas/tensorflow)
                             để import được cả trên runtime Raspberry Pi tối
                             giản; có export_label_map() xuất label_map.json
    - inference_pipeline.py : Logic Step 7 - tái tạo đặc trưng tu RAW theo
                             đúng tần số khai báo, suy luận trượt cửa sổ
                             INT8, gộp sự kiện, so sánh heuristic, xuất
                             báo cáo văn bản (ai_inference_report.txt)
    - multi_trip_pipeline.py : QUY TRÌNH CHÍNH TRÊN SERVER - gộp NHIỀU
                             trip cùng lúc để train 1 model duy nhất
                             (khác ai_pipeline.py chỉ chạy 1 trip/lần qua
                             GUI Step 6). Không phụ thuộc Tkinter, chạy
                             headless qua CLI:
                             python -m core.multi_trip_pipeline <raw_data_root>
                             Xem hướng dẫn đầy đủ ở cuối file này.
- panels/
    - panel_data_loader.py  : Chọn thư mục & nạp file thô (GPS/IMU)
    - panel_system_log.py   : Nhật ký xử lý hệ thống
    - panel_status_bar.py   : Thanh trạng thái dưới cùng
- steps/ (đúng thứ tự pipeline)
    - step1_quality_sync.py    : Kiểm tra chất lượng tín hiệu & đồng bộ tần số
    - step2_statistics_viz.py  : Thống kê & trực quan hóa
    - step3_segment_filter.py  : Lọc đoạn GPS theo vận tốc & gán nhãn IMU
    - step4_route_map.py       : Bản đồ hành trình, highlight & tự động zoom
                                 vào đoạn #ID đang được chọn
    - step5_background_idle.py : Phát hiện đoạn xe đứng yên (Background/Idle)
    - step6_model_training.py  : Huấn luyện mô hình 33 nhãn (25 Primary +
                                 8 Residual, nhãn lấy từ CSV final của
                                 Step 8) — Labeled Windowing → Multi-scale
                                 Conv1D + SE + Residual → INT8 (Raspberry
                                 Pi). Định nghĩa nhãn rõ ràng tại
                                 core/label_schema.py (+ label_map.json
                                 xuất kèm model). Logic dùng chung với CLI:
                                 python -m core.ai_pipeline <folder>
    - step7_model_inference.py : Kiểm thử mô hình Step 6 TRỰC TIẾP trên 2
                                 file RAW (GPS+IMU) — cho nhập tần số quét
                                 thực tế từng tín hiệu (IMU/GPS) để resample
                                 đúng tần số huấn luyện, tái tạo lại đặc
                                 trưng giống Step 8, suy luận trượt cửa sổ,
                                 gộp sự kiện, hiện kết quả tại giao diện +
                                 xuất ai_inference_report.txt (dùng chung
                                 logic với core/inference_pipeline.py)
    - step8_advanced_event_detection.py : Advanced Label Detection — phát
                                 hiện 25 nhãn primary (G10/G20/G40/G80) +
                                 8 nhãn residual đặt tên tiếng Anh theo điều kiện, mỗi nhóm
                                 1 Tab tự chỉnh threshold riêng + nút
                                 Default + xuất CSV tương ứng, nút Run All
                                 combine toàn bộ → detected_events_unified/
                                 (dùng core/event_detection_engine.py)
    - step9_3d_map_visualizer.py : 3D Motion & Map Visualizer — port từ
                                 event_visualizer_3d_pro_gemini.py (giữ
                                 nguyên logic/giao diện gốc), tạo bản đồ
                                 Folium + mô phỏng 3D Three.js khi click
                                 #ID sự kiện. Yêu cầu thư viện "folium"
                                 (pip install folium) — chưa có trong
                                 requirements.txt gốc của dự án Pi.
                                 Khác bản gốc 1 điểm: server local được
                                 tái sử dụng, tab đang mở tự động reload
                                 khi tạo lại bản đồ (không mở tràn tab).

File dữ liệu mẫu (giữ nguyên tại gốc, KHÔNG di chuyển vì PanelDataLoader
quét thư mục làm việc hiện tại để tự nhận diện):
- RAW_GPS.txt
- RAW_ACCELEROMETERS.txt

ĐỊNH NGHĨA CỘT DỮ LIỆU THÔ (xem chi tiết đầy đủ tại
sensors_docs/gps/README.md và sensors_docs/imu/README.md):

RAW_GPS.txt (Quectel EG800K, đúng 12 cột, phân cách bằng khoảng trắng):
  [0] t_now        float  - Thời gian tương đối Pi/ESP32 ghi nhận (giây),
                             dùng làm mốc đồng bộ với IMU khi offline
  [1] UTC          string - Giờ UTC từ vệ tinh, định dạng HHMMSS.sss
  [2] lat          float  - Vĩ độ (Decimal Degrees)
  [3] lon          float  - Kinh độ (Decimal Degrees)
  [4] hdop         float  - Horizontal Dilution of Precision (càng nhỏ
                             càng chính xác; 99.9 = chưa fix)
  [5] alt          float  - Độ cao so với mực nước biển (mét)
  [6] fix          int    - Trạng thái fix: 0 = No Fix, 1 = 2D Fix,
                             3 = 3D Fix
  [7] cog          float  - Course Over Ground, hướng di chuyển so với
                             Bắc địa lý (0°-360°)
  [8] speed_kmh    float  - Tốc độ (km/h)
  [9] speed_knots  float  - Tốc độ (hải lý/giờ, knots)
  [10] date        string - Ngày UTC, định dạng DDMMYY
  [11] satellites  int    - Số vệ tinh GPS/GLONASS/Galileo/BeiDou đang
                             kết nối để chốt tọa độ
  Ví dụ: 45.102 083015.100 10.870123 106.803456 1.1 15.4 3 185.5 45.2 24.4 180726 12

RAW_ACCELEROMETERS.txt (Bosch BNO055, đúng 14 cột, phân cách bằng
khoảng trắng, tần số lấy mẫu 100Hz):
  [0] time    float - Thời gian tương đối kể từ lúc bắt đầu ghi phiên (giây)
  [1] acc_x   float - Gia tốc tuyến tính trục X, m/s² (ngang, qua phải xe)
  [2] acc_y   float - Gia tốc tuyến tính trục Y, m/s² (dọc đầu xe/Surge)
  [3] acc_z   float - Gia tốc tuyến tính trục Z, m/s² (thẳng đứng/Heave)
  [4] yaw     float - Góc Yaw/Heading quanh trục Z (0°-360°, Euler[0])
  [5] roll    float - Góc Roll quanh trục Y (-90° đến +90°, Euler[1]);
                       âm = nghiêng phải
  [6] pitch   float - Góc Pitch quanh trục X (-180° đến +180°, Euler[2]);
                       dương = xuống dốc, âm = lên dốc
  [7] gyr_x   float - Vận tốc góc quanh trục X, rad/s (tốc độ góc Pitch)
  [8] gyr_y   float - Vận tốc góc quanh trục Y, rad/s (tốc độ góc Roll)
  [9] gyr_z   float - Vận tốc góc quanh trục Z, rad/s (tốc độ góc Yaw)
  [10] quat_w float - Thành phần W của Quaternion định hướng 3D
  [11] quat_x float - Thành phần X của Quaternion định hướng 3D
  [12] quat_y float - Thành phần Y của Quaternion định hướng 3D
  [13] quat_z float - Thành phần Z của Quaternion định hướng 3D
  Ví dụ: 12.345 0.1234 -0.0512 9.8100 245.1200 1.2500 -2.3100 0.0012 -0.0005 0.0021 0.9981 0.0123 -0.0451 0.0312

  Ghi chú: Tên cột ở trên khớp 1:1 với GPS_COLUMNS / IMU_COLUMNS trong
  core/sensor_schema.py (nguồn chân lý duy nhất của hệ thống).

HƯỚNG DẪN CHẠY ỨNG DỤNG:
1. Mở Terminal / Command Prompt tại thư mục này (modular_app/)
2. Chạy lệnh:
   python main_app.py
3. Ở khối "Nạp Dữ Liệu Thô" (thanh trên cùng), hệ thống tự động quét và
   nạp RAW_GPS.txt / RAW_ACCELEROMETERS.txt ngay khi thư mục chỉ chứa đúng
   2 tệp thô (không cần bấm nút); nếu thư mục có nhiều tệp .txt/.csv, hãy
   chọn đúng File GPS/IMU ở Combobox để tự động nạp lại. Sau đó đi qua
   từng Tab theo đúng thứ tự 0 -> 7 trong Notebook.
4. Mỗi Tab có khối "📖 Chức năng & Cơ sở khoa học" ở đầu trang - bấm để mở
   rộng xem mô tả chức năng, nguyên lý/công thức áp dụng và link tham khảo.

========================================================================
    HƯỚNG DẪN HUẤN LUYỆN MÔ HÌNH AI (TRAIN)
========================================================================

Có 3 cách train, tùy quy mô dữ liệu. Điều kiện chung: mỗi trip PHẢI đã
qua Step 8 (tiền xử lý + gán nhãn heuristic) để có đủ 2 file
merged_motion_features.csv + detected_driving_events_unified.csv trong
<trip>/detected_events_unified/ — nếu dùng Cách 3 (CLI đa-trip) thì
KHÔNG cần làm bước này trước, pipeline tự chạy giùm.

------------------------------------------------------------------------
CÁCH 1 — Train 1 trip qua GUI (Step 6, phù hợp thử nghiệm nhanh)
------------------------------------------------------------------------
1. Chạy Step 8 cho trip đó trước (Tab "8. Advanced Label Detection",
   bấm "Run All (Combine)" để có đủ 2 CSV cần thiết).
2. Qua Tab "6. Huấn Luyện Mô Hình AI", thư mục sẽ tự nhận đúng
   <trip>/detected_events_unified/ (đồng bộ theo Panel Nạp Dữ Liệu Thô).
3. Chỉnh tham số nếu cần (Window/Stride/Purity/Epochs...), bấm
   "🚀 Chạy Pipeline (Windowing → Train → INT8)".
4. Xem định nghĩa 33 nhãn qua nút "📄 Xem Định Nghĩa Nhãn" ngay trên GUI.

------------------------------------------------------------------------
CÁCH 2 — Train 1 trip qua dòng lệnh (CLI, không cần mở GUI)
------------------------------------------------------------------------
   cd modular_app
   python -m core.ai_pipeline "<trip>\detected_events_unified"

   (dùng toàn bộ tham số mặc định trong core/ai_pipeline.py: window=2.0s,
   stride=0.3s, purity>=0.8, epochs=30... — muốn đổi thì sửa trực tiếp
   các hằng số FS_IMU/WINDOW_SEC/... đầu file, hoặc dùng Cách 3 vì CLI
   đa-trip có sẵn đầy đủ cờ --option).

------------------------------------------------------------------------
CÁCH 3 — Train TOÀN BỘ nhiều trip cùng lúc (QUY TRÌNH CHÍNH TRÊN SERVER)
------------------------------------------------------------------------
Dùng khi đã thu thập NHIỀU trip và muốn train 1 model DUY NHẤT trên toàn
bộ dữ liệu (không phải từng model riêng cho mỗi trip). Chạy headless,
không cần Tkinter/GUI — phù hợp chạy trên server.

Yêu cầu cấu trúc thư mục: 1 thư mục gốc chứa các thư mục trip con, MỖI
trip có RAW_GPS.txt + RAW_ACCELEROMETERS.txt trực tiếp bên trong, ví dụ:

   raw_data/0-Trips/
   ├── 27092026_011306/
   │   ├── RAW_GPS.txt
   │   └── RAW_ACCELEROMETERS.txt
   ├── 30092026_152135/
   │   ├── RAW_GPS.txt
   │   └── RAW_ACCELEROMETERS.txt
   └── ... (các trip khác)

Lệnh chạy:
   cd modular_app
   python -m core.multi_trip_pipeline "<raw_data_root>" [--options]

Các cờ tùy chọn (đều có giá trị mặc định hợp lý, không bắt buộc truyền):
   --output-dir PATH       Thư mục xuất model/.tflite/label_map.json
                           (mặc định: <raw_data_root>/trained_model_multi_trip/)
   --force-preprocess      Chạy lại Step 8 cho MỌI trip, kể cả đã có kết
                           quả sẵn. BẮT BUỘC dùng cờ này nếu trip đã được
                           xử lý bằng 1 PHIÊN BẢN CODE CŨ (vd còn nhãn
                           "Unknown0x" từ trước khi đổi tên sang tiếng
                           Anh) — nếu không, các event cũ không khớp tên
                           nhãn hiện tại sẽ bị BỎ QUA (có log cảnh báo,
                           không crash, nhưng mất dữ liệu).
   --fs HZ                 Tần số IMU, mặc định 100.0
   --window-sec S          Độ dài cửa sổ (giây), mặc định 2.0
   --stride-sec S          Bước trượt cửa sổ (giây), mặc định 0.3
   --purity-threshold P    Ngưỡng độ phủ nhãn/cửa sổ, mặc định 0.8
   --train-ratio R         Tỉ lệ Train, mặc định 0.70
   --val-ratio R           Tỉ lệ Val (phần còn lại là Test), mặc định 0.15
   --epochs N              Số epoch, mặc định 30
   --batch-size N          Batch size, mặc định 64
   --learning-rate LR      Learning rate Adam, mặc định 1e-3

Quy trình khuyến nghị (đã kiểm chứng thực tế trên 13 trip, ~2.5 triệu
mẫu IMU — xem số liệu thật ở ví dụ dưới):

1. CHẠY THỬ NHANH (sanity check) trước khi train thật, để chắc mọi trip
   xử lý đúng, không lệch nhãn:
      python -m core.multi_trip_pipeline "<raw_data_root>" --force-preprocess --epochs 3

2. Sau khi xác nhận ổn, CHẠY BẢN ĐẦY ĐỦ — lần này KHÔNG cần
   --force-preprocess nữa (dữ liệu đã được tái tạo mới ở bước 1), nên
   sẽ bỏ qua Step 8 (phần chậm nhất) và nhanh hơn nhiều:
      python -m core.multi_trip_pipeline "<raw_data_root>" --epochs 30

Ví dụ thực tế (13 trip, ~2.5 triệu mẫu IMU gộp lại):
   - Bước 1 (force-preprocess, epochs=3):  ~18 phút, chủ yếu do Step 8
     chạy lại cho toàn bộ 13 trip.
   - Bước 2 (epochs=30, không force):      ~9.4 phút (Step 8 được bỏ qua
     vì đã có sẵn kết quả mới từ bước 1).
   - Gộp được 70.626 cửa sổ từ 13 trip -> Train/Val/Test =
     51.002/10.535/9.089 (chia theo NHÓM event_id TOÀN CỤC — mỗi sự
     kiện + mọi cửa sổ overlap của nó trong CÙNG 1 trip LUÔN nằm trong
     CÙNG 1 tập, và event của trip khác nhau không bao giờ bị trộn lẫn
     group, chống rò rỉ dữ liệu).
   - EarlyStopping dừng ở epoch 20/30 (khôi phục trọng số tốt nhất).
   - Kết quả: Test Accuracy 76.84%, Macro-F1 40.35%, model INT8 giảm từ
     353.6 KB xuống 122.6 KB.

Output được lưu tại <raw_data_root>/trained_model_multi_trip/ (KHÔNG
ghi vào thư mục detected_events_unified/ của bất kỳ trip riêng lẻ nào,
tránh nhầm lẫn model đa-trip với model 1-trip):
   - driversafe_event_classifier_int8.tflite  (model INT8, nạp vào
     Raspberry Pi hoặc Step 7 để suy luận)
   - label_map.json   (định nghĩa 33 nhãn + metadata: feature_columns,
     timesteps, window_sec, fs, số trip đã dùng, tên từng trip)
   - multi_trip_training_report.json   (thống kê đầy đủ: số cửa sổ mỗi
     trip, phân bố nhãn, Precision/Recall/F1 từng lớp trên tập Test)

SAU KHI TRAIN XONG: đưa file .tflite + label_map.json (từ thư mục output
ở trên) vào Tab "7. Suy Luận Mô Hình AI" để kiểm thử trên 1 trip mới chưa
từng train, hoặc copy sang Raspberry Pi để chạy suy luận thực tế.

Cơ sở lý thuyết chi tiết của toàn bộ pipeline (cấu trúc CSV đặc trưng,
có cần .npz không, công thức windowing/split/class-weight/kiến trúc
model...): xem docs/ai_training_pipeline_multitrip.md.
