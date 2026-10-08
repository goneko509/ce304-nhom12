// ================= 0. HỆ THỐNG TOAST =================
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.4s ease forwards';
        setTimeout(() => toast.remove(), 400);
    }, 3000);
}

// ================= 0.5. NHÃN AI REALTIME (HẰNG SỐ DÙNG CHUNG) =================
// Cùng quy ước mau/lane voi steps/step4_route_map.py::_inject_swimlane_timeline
// (trang 3D "AI Detected") - dung chung cho odometer, mau marker ban do va
// timeline live o day de nhat quan toan bo app.
const LANE_MAP = {
    "BRAKE_HARD": 0, "BRAKE_MODERATE": 0, "ACCEL_HARD": 0, "ACCEL_MODERATE": 0, "STOP": 0,
    "STEERING": 1, "STEERING_ANOMALY": 1,
    "JERK_TRANSIENT": 2, "SPEED_TRANSITION": 2, "DATA_QUALITY_ISSUE": 2
};
const LANE_LABELS = ["Phanh / Ga", "Đánh lái", "Bất thường"];
const FLAG_COLORS = {
    "BRAKE_HARD": "#C0392B", "BRAKE_MODERATE": "#E74C3C",
    "ACCEL_HARD": "#D35400", "ACCEL_MODERATE": "#F1C40F",
    "STOP": "#8E44AD",
    "STEERING": "#3498DB", "STEERING_ANOMALY": "#9B59B6",
    "JERK_TRANSIENT": "#7F8C8D", "SPEED_TRANSITION": "#34495E", "DATA_QUALITY_ISSUE": "#2C3E50"
};
const BG_COLORS = { "SPEED_G10": "#D5F5E3", "SPEED_G20": "#A9DFBF", "SPEED_G40": "#58D68D", "SPEED_G80": "#1E8449" };
// Thu tu uu tien khi chon 1 mau duy nhat cho marker xe tren ban do (Phanh/Ga > Danh lai > Bat thuong).
const FLAG_PRIORITY_ORDER = [
    "BRAKE_HARD", "BRAKE_MODERATE", "ACCEL_HARD", "ACCEL_MODERATE", "STOP",
    "STEERING", "STEERING_ANOMALY",
    "JERK_TRANSIENT", "SPEED_TRANSITION", "DATA_QUALITY_ISSUE"
];
const DEFAULT_MARKER_FILTER = "drop-shadow(0 0 5px cyan)";

let currentAiFlags = [];
let currentAiOnline = false;

function pickMarkerFlagColor(flags) {
    if (!flags || flags.length === 0) return null;
    for (const f of FLAG_PRIORITY_ORDER) { if (flags.includes(f)) return FLAG_COLORS[f]; }
    return null;
}

function updateAiFlagUI(flags, online) {
    const pillsEl = document.getElementById('ai-flag-pills');
    if (!pillsEl) return;
    if (!online) {
        pillsEl.innerHTML = '<span class="ai-pill ai-pill-offline">⚪ OFFLINE</span>';
        return;
    }
    if (!flags || flags.length === 0) {
        pillsEl.innerHTML = '<span class="ai-pill ai-pill-normal">✅ Bình thường</span>';
        return;
    }
    pillsEl.innerHTML = flags.map(f =>
        `<span class="ai-pill" style="background:${FLAG_COLORS[f] || '#555'}">${f}</span>`
    ).join('');
}

// ================= 1. BẢN ĐỒ LEAFLET & TRACKING TÂM XE =================
const map = L.map('map').setView([10.8231, 106.6297], 15);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);

let carMarker = null;
const trajectory = L.polyline([], {color: '#ff003c', weight: 4, opacity: 0.8}).addTo(map);

let autoTrackMap = true;
map.on('dragstart', () => {
    autoTrackMap = false;
    document.getElementById('btn-center-map').classList.add('disabled-tracking');
});

function centerMapOnCar() {
    autoTrackMap = true;
    document.getElementById('btn-center-map').classList.remove('disabled-tracking');
    if (carMarker) {
        map.panTo(carMarker.getLatLng(), { animate: true, duration: 0.5 });
    } else {
        showToast("Chưa có vị trí xe!", "error");
    }
}

// ================= 2. 3D VỚI THREE.JS =================
const canvas3d = document.getElementById('canvas3d');
const scene = new THREE.Scene();

const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
scene.add(ambientLight);
const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
dirLight.position.set(5, 5, 5);
scene.add(dirLight);

const gridHelper = new THREE.GridHelper(50, 50, 0x00f3ff, 0x222222);
gridHelper.rotation.x = Math.PI / 2;
scene.add(gridHelper);

const camera = new THREE.PerspectiveCamera(50, canvas3d.clientWidth / canvas3d.clientHeight, 0.1, 100000);
camera.up.set(0, 0, 1); 
camera.position.set(5, -7, 5); 
camera.lookAt(0, 0, 0);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setSize(canvas3d.clientWidth, canvas3d.clientHeight);
canvas3d.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.05; controls.maxPolarAngle = Math.PI / 2 - 0.05;

const northDir = new THREE.Vector3(0, 1, 0); const northOrigin = new THREE.Vector3(0, 0, 0);
const northArrow = new THREE.ArrowHelper(northDir, northOrigin, 6, 0xff003c, 1.5, 0.8);
scene.add(northArrow);

const carGroup = new THREE.Group();
const bodyGeo = new THREE.BoxGeometry(2.5, 4.5, 0.5); 
const bodyMat = new THREE.MeshPhongMaterial({ color: 0x333333, shininess: 100 }); 
const carBody = new THREE.Mesh(bodyGeo, bodyMat); carGroup.add(carBody);

const edges = new THREE.EdgesGeometry(bodyGeo);
const lineMat = new THREE.LineBasicMaterial({ color: 0x00f3ff });
const carWireframe = new THREE.LineSegments(edges, lineMat); carGroup.add(carWireframe);

const frontGeo = new THREE.BoxGeometry(1.8, 0.5, 0.6);
const frontMat = new THREE.MeshPhongMaterial({ color: 0xff003c, emissive: 0x440000 }); 
const carFront = new THREE.Mesh(frontGeo, frontMat); carFront.position.set(0, 1.8, 0.25); carGroup.add(carFront);

const axesHelper = new THREE.AxesHelper(4); axesHelper.position.set(0, 0, 0.26); carGroup.add(axesHelper); 
carGroup.rotation.order = 'ZXY'; // Chuẩn Bosch Z-Y-X Tait-Bryan theo hệ trục (+Z Up, +X Right, +Y Forward)
scene.add(carGroup);

let refLat = null; let refLon = null; let refAlt = 0; let lastCarPos = null; const EARTH_RADIUS = 6378137;
let camera3DTracking = true; // Camera co tu bam theo xe hay khong (xem set3DView/toggle3DTracking)

// Cac goc nhin camera nhanh (port rut gon tu motion3DView() cua step9_3d_map_visualizer.py
// - chi la UI xem 3D, khong anh huong gi den du lieu/ket qua suy luan AI hien thi).
function set3DView(viewName) {
    const target = lastCarPos ? lastCarPos.clone() : new THREE.Vector3(0, 0, 0);
    if (viewName === 'fit') { camera.position.set(target.x, target.y - 25, target.z + 25); camera.up.set(0, 0, 1); }
    else if (viewName === 'rear') { camera.position.set(target.x, target.y - 10, target.z + 4); camera.up.set(0, 0, 1); }
    else if (viewName === 'top') { camera.position.set(target.x, target.y, target.z + 16); camera.up.set(0, 1, 0); }
    else if (viewName === 'side') { camera.position.set(target.x + 10, target.y, target.z + 4); camera.up.set(0, 0, 1); }
    controls.target.copy(target);
    controls.update();
}

// Bat/tat camera tu bam theo xe (port rut gon tu motion3DToggleTracking() cua step9) - khi
// tat, nguoi dung co the xoay/zoom tu do (OrbitControls) ma khong bi keo lai moi tick.
function toggle3DTracking() {
    camera3DTracking = !camera3DTracking;
    if (camera3DTracking && lastCarPos) {
        const offset = camera.position.clone().sub(controls.target);
        controls.target.copy(lastCarPos);
        camera.position.copy(lastCarPos).add(offset);
    }
    const btn = document.getElementById('btn-toggle-3d-tracking');
    if (btn) btn.innerHTML = camera3DTracking ? '🎯 THEO XE: BẬT' : '🎯 THEO XE: TẮT';
}

function setHomeReference() {
    if (!carMarker) return showToast("⚠️ Chưa có dữ liệu GPS để set Home!", "error");
    const latlng = carMarker.getLatLng();
    refLat = latlng.lat; refLon = latlng.lng;
    const altInput = document.getElementById('ref-alt').value;
    refAlt = altInput ? parseFloat(altInput) : parseFloat(document.getElementById('val-alt').innerText);
    
    document.getElementById('ref-alt').value = refAlt;
    reset3DTrail(false); lastCarPos = null; 
    
    // Ép reset góc nhìn Camera 3D (Auto-Fit)
    camera.position.set(0, -15, 10);
    controls.target.set(0, 0, 0);
    controls.update();

    showToast("📍 Đã thiết lập gốc tọa độ 3D (Home) thành công!", "success");
}

// Lắng nghe sự kiện Enter trên ô nhập tọa độ
const refAltInput = document.getElementById('ref-alt');
if (refAltInput) {
    refAltInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.keyCode === 13) {
            e.preventDefault();
            setHomeReference();
        }
    });
}

function gpsToLocalMap(lat, lon, alt) {
    if (lat === null || lon === null || isNaN(lat) || isNaN(lon) || isNaN(alt) || lat === 0 || lon === 0) return null;
    if (refLat === null) { refLat = lat; refLon = lon; refAlt = alt || 0; }
    const rad = Math.PI / 180;
    const x = EARTH_RADIUS * (lon - refLon) * rad * Math.cos(refLat * rad);
    const y = EARTH_RADIUS * (lat - refLat) * rad;
    return new THREE.Vector3(x, y, (alt || 0) - refAlt);
}

// ====== KHỞI TẠO BÓNG MA QUỸ ĐẠO ======
// (Ban dau co them 1 "ribbon" mau xanh/vang/do chay song song de the hien do
// nghieng (roll) nhung gay roi mat - da go bo theo yeu cau, CHI GIU LAI duong
// quy dao chinh (trailLine, dashed cyan, da bao gom do cao qua truc Z).
const MAX_TRAIL_POINTS = 100000;
const trailGeo = new THREE.BufferGeometry();

const trailPositions = new Float32Array(MAX_TRAIL_POINTS * 3);
trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3));

const trailMat = new THREE.LineDashedMaterial({ color: 0x00ffff, dashSize: 0.5, gapSize: 0.5, linewidth: 2, depthTest: false, transparent: true, opacity: 1.0 });
const trailLine = new THREE.Line(trailGeo, trailMat);
trailLine.frustumCulled = false; trailLine.renderOrder = 999;
scene.add(trailLine);

let trailCount = 0;
window.isTrailVisible = true; // Biến trạng thái Ẩn/Hiện

// Hàm Ẩn/Hiện quỹ đạo
function toggle3DTrail() {
    window.isTrailVisible = !window.isTrailVisible;
    trailLine.visible = window.isTrailVisible;

    const btn = document.getElementById('btn-toggle-trail');
    if (window.isTrailVisible) {
        btn.innerHTML = "👁 ẨN QUỸ ĐẠO";
        btn.style.background = "#0055ff";
        btn.style.boxShadow = "0 0 5px rgba(0,85,255,0.2)";
        showToast("👁 Đã hiển thị quỹ đạo 3D", "info");
    } else {
        btn.innerHTML = "🙈 HIỆN QUỸ ĐẠO";
        btn.style.background = "#550000";
        btn.style.boxShadow = "0 0 5px rgba(255,0,0,0.2)";
        showToast("🙈 Đã ẩn quỹ đạo 3D", "info");
    }
}

function reset3DTrail(showNotif = false) {
    trailCount = 0; trailGeo.setDrawRange(0, 0);
    if (showNotif && fullImu.length > 0) showToast("🗑 Đã xóa quỹ đạo 3D", "info");
}

function update3DTrail(position) {
    if (!position || trailCount >= MAX_TRAIL_POINTS) return;
    if (isNaN(position.x) || isNaN(position.y) || isNaN(position.z)) return;

    trailPositions[trailCount * 3] = position.x; trailPositions[trailCount * 3 + 1] = position.y; trailPositions[trailCount * 3 + 2] = position.z;

    trailCount++;
    trailGeo.setDrawRange(0, trailCount); trailGeo.attributes.position.needsUpdate = true;
    trailGeo.computeBoundingSphere(); trailGeo.computeBoundingBox(); trailLine.computeLineDistances();

    trailLine.visible = window.isTrailVisible;
}

function rebuild3DTrailTo(maxGpsIndex) {
    trailCount = 0;
    const limit = Math.min(maxGpsIndex, MAX_TRAIL_POINTS - 1);
    let lastValidPos = null;

    for (let i = 0; i <= limit; i++) {
        const point = fullGps[i];
        if (!point) continue;
        const pos = gpsToLocalMap(point.lat, point.lon, point.alt);
        if (!pos) continue;

        if (lastValidPos && pos.distanceTo(lastValidPos) > 1000) continue;
        lastValidPos = pos.clone();

        if (isNaN(pos.x) || isNaN(pos.y) || isNaN(pos.z)) continue;

        trailPositions[trailCount * 3] = pos.x; trailPositions[trailCount * 3 + 1] = pos.y; trailPositions[trailCount * 3 + 2] = pos.z;
        trailCount++;
    }

    trailGeo.setDrawRange(0, trailCount); trailGeo.attributes.position.needsUpdate = true;
    trailGeo.computeBoundingSphere(); trailGeo.computeBoundingBox(); trailLine.computeLineDistances();

    trailLine.visible = window.isTrailVisible;
}

function animate3D() {
    requestAnimationFrame(animate3D);
    if (controls) controls.update(); 
    renderer.render(scene, camera);
}
animate3D();

// ================= 3. CHART.JS CẤU HÌNH LEGEND =================
const maxDataPoints = 50; 

function createLineChart(ctx, datasetsConfig, isDualAxis = false) {
    const scalesConfig = {
        x: { display: false },
        y: { type: 'linear', display: true, position: 'left', ticks: { color: datasetsConfig[0].borderColor, font: {family: 'Segoe UI', size: 10} }, grid: { color: 'rgba(255,255,255,0.05)' } }
    };

    if (isDualAxis && datasetsConfig.length >= 2) {
        scalesConfig.y1 = { type: 'linear', display: true, position: 'right', ticks: { color: datasetsConfig[1].borderColor, font: {family: 'Segoe UI', size: 10} }, grid: { drawOnChartArea: false } };
    }

    return new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: datasetsConfig },
        options: {
            animation: false, responsive: true, maintainAspectRatio: false,
            plugins: { 
                legend: { 
                    display: true, position: 'top', align: 'end', 
                    labels: {
                        font: {family: 'Segoe UI', size: 11, weight: 'bold'},
                        generateLabels: function(chart) {
                            const datasets = chart.data.datasets;
                            return datasets.map((dataset, i) => {
                                const isVisible = chart.isDatasetVisible(i);
                                return {
                                    text: dataset.label,
                                    fillStyle: isVisible ? (dataset.backgroundColor || dataset.borderColor) : 'rgba(50, 50, 50, 0.4)',
                                    strokeStyle: isVisible ? dataset.borderColor : 'rgba(100, 100, 100, 0.4)',
                                    lineWidth: 2,
                                    hidden: false,
                                    fontColor: isVisible ? '#fff' : '#666',
                                    datasetIndex: i
                                };
                            });
                        }
                    },
                    onClick: function(e, legendItem, legend) {
                        const index = legendItem.datasetIndex;
                        const ci = legend.chart;
                        ci.data.datasets.forEach(function(dataset, i) {
                            ci.setDatasetVisibility(i, i === index);
                        });
                        ci.update();
                    }
                } 
            },
            scales: scalesConfig
        }
    });
}

const accChart = createLineChart(document.getElementById('accChart').getContext('2d'), [
    { label: 'X', borderColor: '#ff003c', backgroundColor: 'rgba(255,0,60,0.1)', data: [], tension: 0.1, borderWidth: 2, pointRadius: 0, fill: true },
    { label: 'Y', borderColor: '#00ff66', backgroundColor: 'rgba(0,255,102,0.1)', data: [], tension: 0.1, borderWidth: 2, pointRadius: 0, fill: true },
    { label: 'Z', borderColor: '#00f3ff', backgroundColor: 'rgba(0,243,255,0.1)', data: [], tension: 0.1, borderWidth: 2, pointRadius: 0, fill: true }
]);

const gyrChart = createLineChart(document.getElementById('gyrChart').getContext('2d'), [
    { label: 'X', borderColor: '#ff003c', backgroundColor: 'rgba(255,0,60,0.1)', data: [], tension: 0.1, borderWidth: 2, pointRadius: 0, fill: true },
    { label: 'Y', borderColor: '#00ff66', backgroundColor: 'rgba(0,255,102,0.1)', data: [], tension: 0.1, borderWidth: 2, pointRadius: 0, fill: true },
    { label: 'Z', borderColor: '#00f3ff', backgroundColor: 'rgba(0,243,255,0.1)', data: [], tension: 0.1, borderWidth: 2, pointRadius: 0, fill: true }
]);

function resetChartVisibility(chart) {
    chart.data.datasets.forEach((dataset, i) => { chart.setDatasetVisibility(i, true); });
    chart.update();
}

function updateGenericChart(chart, timeLabel, valuesArray) {
    chart.data.labels.push(timeLabel);
    for (let i = 0; i < valuesArray.length; i++) { chart.data.datasets[i].data.push(valuesArray[i]); }
    if (chart.data.labels.length > maxDataPoints) { chart.data.labels.shift(); chart.data.datasets.forEach(ds => ds.data.shift()); }
    chart.update();
}

// ================= 4. LOGIC DATA, REPLAY & SCRUBBING TIMELINE =================
let realtimeTimer = null; let replayTimer = null; let isRealtime = true;
let fullGps = []; let fullImu = []; let gpsIdx = 0; let imuIdx = 0; let baseTime = 0; let totalDuration = 0; let wasPlayingBeforeScrub = false;

let lastOnlineGpsTime = 0;
let lastOnlineImuTime = 0;
let lastOnlineChangeTime = Date.now();
let offlineCheckTimer = null;

async function initOnlineStatus() {
    try {
        const response = await fetch('/data');
        const data = await response.json();
        let latestGps = data.gps && data.gps.length > 0 ? data.gps[data.gps.length - 1] : null;
        let latestImu = data.imu && data.imu.length > 0 ? data.imu[data.imu.length - 1] : null;
        if (latestGps) lastOnlineGpsTime = latestGps.time;
        if (latestImu) lastOnlineImuTime = latestImu.time;
        lastOnlineChangeTime = Date.now();
    } catch (err) {
        console.error("Init online status error:", err);
    }
}

function setAppMode(modeName) {
    const radios = document.querySelectorAll('input[name="mode"]');
    radios.forEach(r => {
        r.checked = (r.value === modeName);
    });
    toggleMode();
}

function startOfflineCheck() {
    if (offlineCheckTimer) clearInterval(offlineCheckTimer);
    offlineCheckTimer = setInterval(async () => {
        if (isRealtime) return;
        try {
            const response = await fetch('/data');
            const data = await response.json();
            let latestGps = data.gps && data.gps.length > 0 ? data.gps[data.gps.length - 1] : null;
            let latestImu = data.imu && data.imu.length > 0 ? data.imu[data.imu.length - 1] : null;
            
            let hasNewData = false;
            if (latestGps && latestGps.time > lastOnlineGpsTime) {
                lastOnlineGpsTime = latestGps.time;
                hasNewData = true;
            }
            if (latestImu && latestImu.time > lastOnlineImuTime) {
                lastOnlineImuTime = latestImu.time;
                hasNewData = true;
            }
            
            if (hasNewData) {
                lastOnlineChangeTime = Date.now();
                showToast("📡 Phát hiện dữ liệu Live online! Tự động chuyển sang chế độ Realtime.", "success");
                setAppMode('realtime');
            }
        } catch (err) {
            console.error("Offline passive check error:", err);
        }
    }, 2000);
}

const ODO_MAX_SPEED = 140;
function updateOdometer(speedKmh) {
    const needle = document.getElementById('odo-needle');
    if (!needle) return;
    const clamped = Math.max(0, Math.min(ODO_MAX_SPEED, speedKmh || 0));
    const angle = -120 + (clamped / ODO_MAX_SPEED) * 240;
    needle.style.transform = `rotate(${angle}deg)`;
}

function updateGpsUI(latestGps) {
    document.getElementById('val-speed').innerText = latestGps.speed.toFixed(1); document.getElementById('val-sat').innerText = latestGps.satellites;
    document.getElementById('val-alt').innerText = latestGps.alt.toFixed(1); document.getElementById('val-hdop').innerText = latestGps.hdop.toFixed(1);
    updateOdometer(latestGps.speed);

    const markerFlagColor = pickMarkerFlagColor(currentAiFlags);
    const markerFilter = markerFlagColor ? `drop-shadow(0 0 6px ${markerFlagColor})` : DEFAULT_MARKER_FILTER;

    const latlng = [latestGps.lat, latestGps.lon];
    if (!carMarker) {
        const iconHtml = `<img src="car.png" style="width: 24px; height: 48px; transform: rotate(${latestGps.cog}deg); transform-origin: center center; filter: ${markerFilter};">`;
        const carIcon = L.divIcon({ html: iconHtml, className: '', iconSize: [24, 48], iconAnchor: [12, 24] });
        carMarker = L.marker(latlng, {icon: carIcon}).addTo(map);
        map.setView(latlng, 18);
    } else {
        carMarker.setLatLng(latlng);
        const img = carMarker._icon.querySelector('img');
        if (img) { img.style.transform = `rotate(${latestGps.cog}deg)`; img.style.filter = markerFilter; }
        if (autoTrackMap) { map.panTo(latlng, { animate: true, duration: 0.25 }); }
    }
}

const PEDAL_MAX_ACCEL = 6.0; // m/s^2 ung voi 100% thanh ga/phanh
function updatePedalBars(accY) {
    const a = accY || 0;
    const throttlePct = Math.max(0, Math.min(1, a / PEDAL_MAX_ACCEL)) * 100;
    const brakePct = Math.max(0, Math.min(1, -a / PEDAL_MAX_ACCEL)) * 100;
    const tEl = document.getElementById('bar-throttle'); if (tEl) tEl.style.height = throttlePct + '%';
    const bEl = document.getElementById('bar-brake'); if (bEl) bEl.style.height = brakePct + '%';
}

function updateImuUI(latestImu) {
    document.getElementById('val-yaw').innerText = latestImu.yaw.toFixed(1) + '°'; document.getElementById('val-roll').innerText = latestImu.roll.toFixed(4) + '°'; document.getElementById('val-pitch').innerText = latestImu.pitch.toFixed(4) + '°';
    updatePedalBars(latestImu.acc_y);
    updateGenericChart(accChart, latestImu.time, [latestImu.acc_x, latestImu.acc_y, latestImu.acc_z]); updateGenericChart(gyrChart, latestImu.time, [latestImu.gyr_x, latestImu.gyr_y, latestImu.gyr_z]);
}

function update3DWorld(latestGps, latestImu) {
    if (latestImu && latestImu.quat_w !== undefined && (latestImu.quat_w !== 0 || latestImu.quat_x !== 0 || latestImu.quat_y !== 0 || latestImu.quat_z !== 0)) {
        carGroup.quaternion.set(latestImu.quat_x, latestImu.quat_y, latestImu.quat_z, latestImu.quat_w);
    } else if (latestImu) {
        carGroup.rotation.order = 'ZXY';
        carGroup.rotation.z = -THREE.MathUtils.degToRad(latestImu.yaw || 0);
        carGroup.rotation.x = THREE.MathUtils.degToRad(latestImu.pitch || 0);
        carGroup.rotation.y = THREE.MathUtils.degToRad(latestImu.roll || 0);
    }

    if (latestGps && latestGps.lat !== undefined && latestGps.lon !== undefined && latestGps.lat !== 0 && latestGps.lon !== 0) {
        const localPos = gpsToLocalMap(latestGps.lat, latestGps.lon, latestGps.alt || 0);
        if (localPos) {
            if (!lastCarPos) {
                lastCarPos = localPos.clone();
                camera.position.copy(localPos).add(new THREE.Vector3(0, -15, 10));
                controls.target.copy(localPos);
            } else if (camera3DTracking) {
                if (localPos.distanceTo(lastCarPos) > 1000) {
                    lastCarPos.copy(localPos); // Jump, don't move camera smoothly
                } else {
                    const deltaPos = localPos.clone().sub(lastCarPos);
                    camera.position.add(deltaPos);
                    lastCarPos.copy(localPos);
                }
                controls.target.copy(localPos);
            } else {
                lastCarPos.copy(localPos); // Camera dung yen (tu do xoay), chi cap nhat vi tri xe tham chieu
            }

            carGroup.position.copy(localPos); northArrow.position.copy(localPos);
        }
    }
}

// ================= 3.5. TIMELINE SỰ KIỆN AI (LIVE, CUỘN TỰ ĐỘNG) =================
// Ban rut gon cua swimlane trong steps/step4_route_map.py::_inject_swimlane_timeline
// nhung cho du lieu LIVE: khong co Play/Pause/keo tay - luon tu cuon sang phai.
const LIVE_SWIMLANE_WINDOW_SEC = 30;
const PX_PER_SEC_LIVE = 20;
const LIVE_SWIMLANE_BG_H = 14, LIVE_SWIMLANE_LANE_H = 26;
let liveSwimlaneBuffer = []; // {t, flags, speedKmh}
let liveSwimlaneLabelsBuilt = false;

function buildLiveSwimlaneLabels() {
    const panel = document.getElementById('live-swimlane-labels');
    if (!panel || liveSwimlaneLabelsBuilt) return;
    panel.innerHTML = '';
    LANE_LABELS.forEach((label, i) => {
        const lbl = document.createElement('div');
        lbl.textContent = label;
        lbl.className = 'live-swimlane-lane-label';
        lbl.style.top = (LIVE_SWIMLANE_BG_H + i * LIVE_SWIMLANE_LANE_H + 5) + 'px';
        panel.appendChild(lbl);
    });
    const bgLbl = document.createElement('div');
    bgLbl.textContent = 'Tốc độ';
    bgLbl.className = 'live-swimlane-bg-label';
    panel.appendChild(bgLbl);
    liveSwimlaneLabelsBuilt = true;
}

function speedToBgFlag(speedKmh) {
    if (speedKmh >= 80) return 'SPEED_G80';
    if (speedKmh >= 40) return 'SPEED_G40';
    if (speedKmh >= 20) return 'SPEED_G20';
    if (speedKmh >= 10) return 'SPEED_G10';
    return null;
}

function appendBgSeg(track, t0, t1, flag, minT) {
    const div = document.createElement('div');
    div.className = 'live-swimlane-bg-seg';
    div.style.left = ((t0 - minT) * PX_PER_SEC_LIVE) + 'px';
    div.style.width = Math.max(2, (t1 - t0) * PX_PER_SEC_LIVE) + 'px';
    div.style.background = BG_COLORS[flag];
    track.appendChild(div);
}

function appendLaneSeg(track, t0, t1, flag, minT) {
    const lane = LANE_MAP[flag];
    const div = document.createElement('div');
    div.className = 'live-swimlane-lane-seg';
    div.style.top = (LIVE_SWIMLANE_BG_H + lane * LIVE_SWIMLANE_LANE_H + 2) + 'px';
    div.style.left = ((t0 - minT) * PX_PER_SEC_LIVE) + 'px';
    div.style.width = Math.max(3, (t1 - t0) * PX_PER_SEC_LIVE) + 'px';
    div.style.background = FLAG_COLORS[flag];
    div.title = flag;
    track.appendChild(div);
}

function renderLiveSwimlane(nowT) {
    const track = document.getElementById('live-swimlane-track');
    const scrollDiv = document.getElementById('live-swimlane-scroll');
    if (!track || liveSwimlaneBuffer.length === 0) return;
    buildLiveSwimlaneLabels();

    const minT = liveSwimlaneBuffer[0].t;
    const widthPx = Math.max(400, (nowT - minT) * PX_PER_SEC_LIVE + 20);
    track.style.width = widthPx + 'px';
    track.innerHTML = '';

    let segStart = liveSwimlaneBuffer[0].t, segFlag = speedToBgFlag(liveSwimlaneBuffer[0].speedKmh);
    for (let i = 1; i < liveSwimlaneBuffer.length; i++) {
        const bgFlag = speedToBgFlag(liveSwimlaneBuffer[i].speedKmh);
        if (bgFlag !== segFlag) {
            if (segFlag) appendBgSeg(track, segStart, liveSwimlaneBuffer[i].t, segFlag, minT);
            segStart = liveSwimlaneBuffer[i].t; segFlag = bgFlag;
        }
    }
    if (segFlag) appendBgSeg(track, segStart, nowT, segFlag, minT);

    for (let i = 0; i < liveSwimlaneBuffer.length; i++) {
        const sample = liveSwimlaneBuffer[i];
        const nextT = (i + 1 < liveSwimlaneBuffer.length) ? liveSwimlaneBuffer[i + 1].t : nowT;
        sample.flags.forEach(f => { if (f in LANE_MAP) appendLaneSeg(track, sample.t, nextT, f, minT); });
    }

    const cursor = document.createElement('div');
    cursor.className = 'live-swimlane-cursor';
    cursor.style.left = ((nowT - minT) * PX_PER_SEC_LIVE) + 'px';
    track.appendChild(cursor);

    const clock = document.getElementById('live-swimlane-clock');
    if (clock) clock.textContent = (nowT - minT).toFixed(1) + 's';

    if (scrollDiv) scrollDiv.scrollLeft = widthPx; // luon cuon ve mep phai (moi nhat), khong can keo tay
}

function pushLiveSwimlaneSample(t, flags, speedKmh) {
    liveSwimlaneBuffer.push({ t, flags: flags || [], speedKmh: speedKmh || 0 });
    const cutoff = t - LIVE_SWIMLANE_WINDOW_SEC;
    while (liveSwimlaneBuffer.length > 0 && liveSwimlaneBuffer[0].t < cutoff) liveSwimlaneBuffer.shift();
    renderLiveSwimlane(t);
}

function resetLiveSwimlane() {
    liveSwimlaneBuffer = [];
    const track = document.getElementById('live-swimlane-track');
    if (track) track.innerHTML = '';
    const clock = document.getElementById('live-swimlane-clock');
    if (clock) clock.textContent = '0.0s';
}

function formatTime(seconds) {
    if (isNaN(seconds) || seconds < 0) return "00:00";
    const m = Math.floor(seconds / 60).toString().padStart(2, '0'); const s = Math.floor(seconds % 60).toString().padStart(2, '0'); return `${m}:${s}`;
}

async function fetchRealtime() {
    if (!isRealtime) return;
    try {
        const response = await fetch('/data'); const data = await response.json();
        let latestGps = data.gps && data.gps.length > 0 ? data.gps[data.gps.length - 1] : null;
        let latestImu = data.imu && data.imu.length > 0 ? data.imu[data.imu.length - 1] : null;
        
        let hasNewData = false;
        if (latestGps && latestGps.time > lastOnlineGpsTime) {
            lastOnlineGpsTime = latestGps.time;
            hasNewData = true;
        }
        if (latestImu && latestImu.time > lastOnlineImuTime) {
            lastOnlineImuTime = latestImu.time;
            hasNewData = true;
        }
        
        if (hasNewData) {
            lastOnlineChangeTime = Date.now();
        } else {
            // Nếu không nhận được dữ liệu mới trong 7 giây, tự động chuyển về chế độ offline/replay
            if (Date.now() - lastOnlineChangeTime > 7000) {
                showToast("📡 Không nhận thêm dữ liệu live trong 7s. Tự động chuyển sang chế độ Replay.", "info");
                setAppMode('replay');
                return;
            }
        }

        currentAiFlags = data.ai_flags || [];
        currentAiOnline = !!data.ai_online;
        updateAiFlagUI(currentAiFlags, currentAiOnline);

        if (latestGps && hasNewData) {
            updateGpsUI(latestGps);
            trajectory.addLatLng([latestGps.lat, latestGps.lon]);
            const pos = gpsToLocalMap(latestGps.lat, latestGps.lon, latestGps.alt);
            if (pos) update3DTrail(pos);
            pushLiveSwimlaneSample(latestGps.time, currentAiFlags, latestGps.speed);
        }
        if (latestImu && hasNewData) { updateImuUI(latestImu); }
        if (latestImu && hasNewData) update3DWorld(latestGps, latestImu);
    } catch (err) { console.error("Realtime Error:", err); }
}

let currentLoadedSession = "";

async function loadFileList() {
    const sessionSelect = document.getElementById('session-select'); sessionSelect.innerHTML = '<option>Đang tải...</option>';
    try {
        const res = await fetch('/list_files'); if (!res.ok) throw new Error(`Mã lỗi Server: ${res.status}`);
        const data = await res.json();
        if (data.sessions && data.sessions.length > 0) sessionSelect.innerHTML = data.sessions.map(s => `<option value="${s}">${s}</option>`).join('');
        else sessionSelect.innerHTML = `<option value="">Thư mục trống!</option>`;
        handleSessionChange();
    } catch (err) { sessionSelect.innerHTML = `<option value="">Lỗi kết nối Backend!</option>`; showToast("Lỗi kết nối lấy danh sách files", "error"); }
}

function handleSessionChange() { 
    stopReplay(); 
    currentLoadedSession = ""; 
    const btn = document.getElementById('btn-play-pause'); 
    btn.innerHTML = "▶ INIT PLAY"; 
    btn.style.background = "var(--neon-green)"; 
    
}

async function togglePlayPause() {
    const session = document.getElementById('session-select').value;
    if (!session) return showToast("Vui lòng chọn một phiên dữ liệu để phát!", "error");
    if (session !== currentLoadedSession) return await loadAndPlaySession(session);
    if (replayTimer) stopReplay(); else { if (imuIdx >= fullImu.length) restartReplay(); else playLoop(); }
}

async function loadAndPlaySession(session) {
    stopReplay(); trajectory.setLatLngs([]); reset3DTrail(false); lastCarPos = null;
    document.getElementById('btn-play-pause').innerText = "⏳ LOADING...";

    try {
        const response = await fetch(`/replay_data?session=${session}`); const data = await response.json();
        fullGps = data.gps || []; fullImu = data.imu || [];
        if (fullImu.length === 0) { document.getElementById('btn-play-pause').innerText = "▶ INIT PLAY"; return showToast("Phiên dữ liệu này không có gói tin IMU!", "error"); }

        // --- THÊM ĐOẠN NÀY ĐỂ TÍNH TOÁN TỐC ĐỘ GỐC ---
        // Tính chênh lệch thời gian giữa 2 bản ghi đầu tiên
        // Giả định dữ liệu time tính bằng giây (ví dụ: 0.02s)
        let timeDiff = (fullImu.length > 1) ? (fullImu[1].time - fullImu[0].time) : 0.02;
        // Quy đổi ra mili giây (ms)
        let intervalMs = Math.round(timeDiff * 1000); 

        // Nếu interval quá nhỏ (dưới 10ms) hoặc không hợp lệ, ép về 20ms (50Hz) để tránh làm treo trình duyệt
        if (intervalMs < 10) intervalMs = 20; 
        // ---------------------------------------------

        currentLoadedSession = session; gpsIdx = 0; imuIdx = 0; baseTime = fullImu[0].time; totalDuration = fullImu[fullImu.length - 1].time - baseTime;
        const slider = document.getElementById('timeline-slider'); slider.max = fullImu.length - 1; slider.value = 0;
        
        showToast("✅ Tải dữ liệu thành công! Bắt đầu phát...", "success"); playLoop();
        
    } catch (err) { console.error("Lỗi Replay:", err); document.getElementById('btn-play-pause').innerText = "▶ INIT PLAY"; showToast("❌ Lỗi tải dữ liệu file!", "error"); }
}

function playLoop() {
    if (replayTimer) clearInterval(replayTimer);
    const btn = document.getElementById('btn-play-pause'); btn.innerHTML = "⏸ PAUSED"; btn.style.background = "var(--neon-yellow)";

    replayTimer = setInterval(() => {
        if (imuIdx < fullImu.length) { renderFrameAt(imuIdx); imuIdx++; } 
        else { stopReplay(); btn.innerHTML = "🔄 FINISHED"; btn.style.background = "var(--neon-blue)"; }
    }, 10);
}

function stopReplay() {
    if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
    if (currentLoadedSession && imuIdx < fullImu.length) { const btn = document.getElementById('btn-play-pause'); btn.innerHTML = "▶ RESUME"; btn.style.background = "var(--neon-green)"; }
}

function restartReplay() {
    if (!currentLoadedSession) return;
    stopReplay(); trajectory.setLatLngs([]); reset3DTrail(false); lastCarPos = null; gpsIdx = 0; imuIdx = 0; playLoop();
}

function openSelectedSessionAnalytics() {
    const selectElem = document.getElementById("session-select");
    const sessionName = selectElem ? selectElem.value : "";
    if (!sessionName || sessionName === "Đang tải...") {
        alert("Vui lòng chọn một phiên dữ liệu hợp lệ trong danh sách để phân tích.");
        return;
    }
    window.open(`analytics.html?session=${encodeURIComponent(sessionName)}`, '_blank');
}

function renderFrameAt(index) {
    const currentImu = fullImu[index]; updateImuUI(currentImu);
    
    while (gpsIdx < fullGps.length - 1 && fullGps[gpsIdx + 1].time <= currentImu.time) {
        gpsIdx++; 
        trajectory.addLatLng([fullGps[gpsIdx].lat, fullGps[gpsIdx].lon]);
        
        const pos = gpsToLocalMap(fullGps[gpsIdx].lat, fullGps[gpsIdx].lon, fullGps[gpsIdx].alt);
        if (pos) update3DTrail(pos);
    }
    
    let currentGps = null; if (fullGps.length > 0) { currentGps = fullGps[gpsIdx]; updateGpsUI(currentGps); }
    if (currentImu) update3DWorld(currentGps, currentImu);
    
    const currentDuration = currentImu.time - baseTime;
    document.getElementById('time-display').innerText = `${formatTime(currentDuration)} / ${formatTime(totalDuration)}`; document.getElementById('timeline-slider').value = index;
}

function pauseForScrub() { wasPlayingBeforeScrub = (replayTimer !== null); stopReplay(); }

function scrubTimeline() {
    if (fullImu.length === 0) return;
    const slider = document.getElementById('timeline-slider'); imuIdx = parseInt(slider.value);
    
    gpsIdx = 0; const currentImuTime = fullImu[imuIdx].time;
    while (gpsIdx < fullGps.length - 1 && fullGps[gpsIdx + 1].time <= currentImuTime) { gpsIdx++; }
    
    const currentImu = fullImu[imuIdx];
    const currentGps = fullGps.length > 0 ? fullGps[gpsIdx] : null;

    [accChart, gyrChart].forEach(c => {
        c.data.labels = []; c.data.datasets.forEach(ds => ds.data = []);
    });

    const startIdx = Math.max(0, imuIdx - maxDataPoints + 1);
    for (let i = startIdx; i <= imuIdx; i++) {
        const tImu = fullImu[i];
        accChart.data.labels.push(tImu.time); accChart.data.datasets[0].data.push(tImu.acc_x); accChart.data.datasets[1].data.push(tImu.acc_y); accChart.data.datasets[2].data.push(tImu.acc_z);
        gyrChart.data.labels.push(tImu.time); gyrChart.data.datasets[0].data.push(tImu.gyr_x); gyrChart.data.datasets[1].data.push(tImu.gyr_y); gyrChart.data.datasets[2].data.push(tImu.gyr_z);
    }
    accChart.update(); gyrChart.update();

    if (currentImu) {
        document.getElementById('val-yaw').innerText = currentImu.yaw.toFixed(1) + '°'; document.getElementById('val-roll').innerText = currentImu.roll.toFixed(4) + '°'; document.getElementById('val-pitch').innerText = currentImu.pitch.toFixed(4) + '°';
        update3DWorld(currentGps, currentImu);
    }
    if (currentGps) {
        document.getElementById('val-speed').innerText = currentGps.speed.toFixed(1); document.getElementById('val-sat').innerText = currentGps.satellites;
        document.getElementById('val-alt').innerText = currentGps.alt.toFixed(1); document.getElementById('val-hdop').innerText = currentGps.hdop.toFixed(1);
        
        const latlng = [currentGps.lat, currentGps.lon];
        if (carMarker) {
            carMarker.setLatLng(latlng); const img = carMarker._icon.querySelector('img'); 
            if (img) img.style.transform = `rotate(${currentGps.cog}deg)`;
            if (autoTrackMap) map.panTo(latlng, { animate: false }); 
        }
    }

    const pts = []; for (let i = 0; i <= gpsIdx; i++) { pts.push([fullGps[i].lat, fullGps[i].lon]); }
    trajectory.setLatLngs(pts);

    if (currentGps && currentImu) {
        carGroup.quaternion.set(currentImu.quat_x, currentImu.quat_y, currentImu.quat_z, currentImu.quat_w);
        const localPos = gpsToLocalMap(currentGps.lat, currentGps.lon, currentGps.alt);
        if (localPos) {
            if (!lastCarPos) {
                lastCarPos = localPos.clone(); camera.position.copy(localPos).add(new THREE.Vector3(0, -15, 10));
                controls.target.copy(localPos);
            } else if (camera3DTracking) {
                if (localPos.distanceTo(lastCarPos) > 1000) {
                    lastCarPos.copy(localPos);
                } else {
                    const deltaPos = localPos.clone().sub(lastCarPos); camera.position.add(deltaPos); lastCarPos.copy(localPos);
                }
                controls.target.copy(localPos);
            } else {
                lastCarPos.copy(localPos);
            }
            carGroup.position.copy(localPos); northArrow.position.copy(localPos);
        }
        rebuild3DTrailTo(gpsIdx);
    }

    const currentDuration = currentImuTime - baseTime;
    document.getElementById('time-display').innerText = `${formatTime(currentDuration)} / ${formatTime(totalDuration)}`;
}

function updateTimelinePlayButton(isPlaying) {
    const btnMain = document.getElementById('btn-play-pause');
    const btnTimeline = document.getElementById('btn-timeline-play');
    
    if (isPlaying) {
        btnMain.innerHTML = "⏸ PAUSED";
        btnMain.style.background = "var(--neon-yellow)";
        btnTimeline.innerHTML = "⏸";
    } else {
        btnMain.innerHTML = "▶ RESUME";
        btnMain.style.background = "var(--neon-green)";
        btnTimeline.innerHTML = "▶";
    }
}

function playLoop() {
    if (replayTimer) clearInterval(replayTimer);
    updateTimelinePlayButton(true); // Gọi hàm mới

    replayTimer = setInterval(() => {
        if (imuIdx < fullImu.length) {
            renderFrameAt(imuIdx);
            imuIdx++;
        } else {
            stopReplay(); 
            // Cập nhật trạng thái hoàn thành
            document.getElementById('btn-timeline-play').innerHTML = "🔄";
        }
    }, 10);
}

function stopReplay() {
    if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
    if (currentLoadedSession && imuIdx < fullImu.length) {
        updateTimelinePlayButton(false); // Gọi hàm mới
    }
}

function resumeAfterScrub() { if (wasPlayingBeforeScrub && imuIdx < fullImu.length) playLoop(); }

function toggleMode() {
    const mode = document.querySelector('input[name="mode"]:checked').value;
    const replayPanel = document.getElementById('replay-panel'); const timelineContainer = document.getElementById('timeline-container');
    const liveDot = document.getElementById('live-dot'); const mainPanel = document.getElementById('main-control-panel');
    const panel = document.getElementById('main-control-panel');
    
    if (mode === 'realtime') {
        isRealtime = true; replayPanel.style.display = 'none'; timelineContainer.style.display = 'none';
        liveDot.style.background = 'var(--neon-red)'; liveDot.style.boxShadow = '0 0 10px var(--neon-red)'; mainPanel.style.borderLeftColor = 'var(--neon-red)';
        
        stopReplay(); trajectory.setLatLngs([]); reset3DTrail(false); lastCarPos = null;
        resetLiveSwimlane();
        showToast("🔴 Đã chuyển sang chế độ Thời gian thực", "info");

        lastOnlineChangeTime = Date.now();
        if (realtimeTimer) clearInterval(realtimeTimer);
        realtimeTimer = setInterval(fetchRealtime, 200);
        if (offlineCheckTimer) { clearInterval(offlineCheckTimer); offlineCheckTimer = null; }
    } else {
        isRealtime = false; replayPanel.style.display = 'flex'; timelineContainer.style.display = 'flex';
        liveDot.style.background = 'var(--neon-blue)'; liveDot.style.boxShadow = '0 0 10px var(--neon-blue)'; mainPanel.style.borderLeftColor = 'var(--neon-blue)';
        if (realtimeTimer) { clearInterval(realtimeTimer); realtimeTimer = null; }
        currentAiFlags = []; currentAiOnline = false; updateAiFlagUI([], false);
        loadFileList();
        startOfflineCheck();
    }
}

initOnlineStatus().then(() => {
    toggleMode();
}); 
window.addEventListener('resize', () => { camera.aspect = canvas3d.clientWidth / canvas3d.clientHeight; camera.updateProjectionMatrix(); renderer.setSize(canvas3d.clientWidth, canvas3d.clientHeight); });