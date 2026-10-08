// analytics.js - Client-side Logic for 3D Hub Raw Data Analytics Dashboard
let currentAnalyticsData = null;
let isEventsUnlocked = false;
let isUpdatingRelayout = false;

function showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'fadeOutToast 0.4s ease forwards';
        setTimeout(() => toast.remove(), 400);
    }, 3500);
}
window.showToast = showToast;

function attachPreventBrowserDefaults(chartId) {
    const chartEl = document.getElementById(chartId);
    if (!chartEl || chartEl._preventDefaultsAttached) return;
    chartEl._preventDefaultsAttached = true;

    chartEl.addEventListener('wheel', function(e) {
        if (e.ctrlKey || chartEl.contains(e.target)) {
            e.preventDefault();
        }
    }, { passive: false });

    chartEl.addEventListener('gesturestart', function(e) { e.preventDefault(); }, { passive: false });
    chartEl.addEventListener('gesturechange', function(e) { e.preventDefault(); }, { passive: false });
    chartEl.addEventListener('gestureend', function(e) { e.preventDefault(); }, { passive: false });
}

document.addEventListener("DOMContentLoaded", function() {
    initGlobalFont();
    const urlParams = new URLSearchParams(window.location.search);
    const sourceParam = urlParams.get('source');
    if (sourceParam === 'proc') {
        window.currentDataSource = 'proc';
        const selectSource = document.getElementById("data_source_select");
        if (selectSource) selectSource.value = 'proc';
        const label = document.getElementById("session_select_label");
        if (label) label.textContent = "📂 Phiên Dữ Liệu (`proc_data/`):";
    } else {
        window.currentDataSource = 'raw';
    }
    refreshSessionsList();
    
    window.addEventListener('wheel', function(e) {
        if (e.ctrlKey) {
            e.preventDefault();
        }
    }, { passive: false });
    window.addEventListener('gesturestart', function(e) { e.preventDefault(); }, { passive: false });
    window.addEventListener('gesturechange', function(e) { e.preventDefault(); }, { passive: false });
});

function changeDataSource(source) {
    window.currentDataSource = source;
    const url = new URL(window.location);
    url.searchParams.set('source', source);
    url.searchParams.delete('session');
    window.location.href = url.toString();
}
window.changeDataSource = changeDataSource;

function initGlobalFont() {
    const savedFont = localStorage.getItem('sota_analytics_font') || 'Liberation Serif';
    const selectElem = document.getElementById("font_select");
    if (selectElem) selectElem.value = savedFont;
    changeGlobalFont(savedFont, false);
}

function changeGlobalFont(fontName, updateCharts = true) {
    localStorage.setItem('sota_analytics_font', fontName);
    
    // Cập nhật CSS cho body và các thẻ
    if (fontName === 'Liberation Serif') {
        document.body.style.fontFamily = "'Liberation Serif', 'Times New Roman', serif";
    } else {
        document.body.style.fontFamily = `'${fontName}', sans-serif, monospace`;
    }

    if (updateCharts && currentAnalyticsData) {
        const fontConfig = { font: { family: fontName, color: "#e0e6ed" } };
        ['chart_imu', 'chart_gps', 'chart_traj'].forEach(cid => {
            const el = document.getElementById(cid);
            if (el && el.data && el.data.length > 0) {
                try { 
                    Plotly.relayout(cid, fontConfig);
                    // Cập nhật lại font trong annotations
                    refreshChartOverlays(cid);
                } catch (e) {}
            }
        });
    }
}

function refreshSessionsList() {
    const selectElem = document.getElementById("session_select");
    const source = window.currentDataSource || 'raw';
    selectElem.innerHTML = `<option value="">⏳ Đang quét thư mục ${source === 'proc' ? 'proc_data/' : 'raw_data/'}...</option>`;

    fetch(`/api/analytics/sessions?source=${source}`)
        .then(res => res.json())
        .then(sessions => {
            selectElem.innerHTML = "";
            if (!sessions || sessions.length === 0) {
                selectElem.innerHTML = '<option value="">(Không tìm thấy phiên thu thập nào)</option>';
                return;
            }

            sessions.forEach((s) => {
                const opt = document.createElement("option");
                opt.value = s.name;
                const statusIcon = s.has_all_4 ? "✅" : "⚠️";
                opt.textContent = `${statusIcon} ${s.name} (${s.files_count}/4 files | ${s.size_kb} KB)`;
                selectElem.appendChild(opt);
            });

            const urlParams = new URLSearchParams(window.location.search);
            const initialSession = urlParams.get('session');

            if (initialSession && sessions.some(s => s.name === initialSession)) {
                selectElem.value = initialSession;
                loadSessionAnalytics(initialSession);
            } else if (sessions.length > 0) {
                selectElem.value = sessions[0].name;
                loadSessionAnalytics(sessions[0].name);
            }
        })
        .catch(err => {
            console.error("Lỗi khi tải danh sách phiên:", err);
            selectElem.innerHTML = '<option value="">❌ Lỗi kết nối API</option>';
        });
}

function loadSessionAnalytics(sessionName) {
    if (!sessionName) return;

    const source = window.currentDataSource || 'raw';
    const newUrl = new URL(window.location);
    newUrl.searchParams.set('session', sessionName);
    newUrl.searchParams.set('source', source);
    window.history.replaceState({}, '', newUrl);

    document.getElementById("score_value").textContent = "⏳...";
    document.getElementById("score_desc").textContent = "Đang phân tích & kiểm định SOTA...";

    fetch(`/api/analytics/session/${sessionName}?source=${source}`)
        .then(res => res.json())
        .then(data => {
            if (data.status === "error") {
                showToast("Lỗi tải dữ liệu: " + data.message, "error");
                if (typeof customAlert === 'function') customAlert("LỖI TẢI DỮ LIỆU", data.message, "error");
                return;
            }
            currentAnalyticsData = data;
            if (currentAnalyticsData && currentAnalyticsData.events) {
                currentAnalyticsData.events.forEach(ev => {
                    ev.is_unlocked = Boolean(ev.is_unlocked || false);
                });
            }
            updateScorecard(data);
            renderImuChart(data);
            renderGpsChart(data);
            renderTrajectoryChart(data);
            renderAuditReport(data);
            renderEventLockControls();
        })
        .catch(err => {
            console.error("Lỗi tải phân tích:", err);
            showToast("Đã xảy ra lỗi khi tải dữ liệu phân tích.", "error");
            if (typeof customAlert === 'function') customAlert("LỖI HỆ THỐNG", "Đã xảy ra lỗi khi tải dữ liệu phân tích.", "error");
        });
}

function updateScorecard(data) {
    const scoreVal = document.getElementById("score_value");
    const scoreDesc = document.getElementById("score_desc");
    const cardScore = document.getElementById("card_score");
    scoreVal.textContent = `${data.score}%`;
    if (data.score >= 90) {
        scoreVal.style.color = "#00ffcc";
        scoreDesc.textContent = "🌟 Dữ liệu Sạch chuẩn SOTA - Sẵn sàng Train AI";
        cardScore.style.borderColor = "rgba(0, 255, 204, 0.5)";
    } else if (data.score >= 70) {
        scoreVal.style.color = "#ffff00";
        scoreDesc.textContent = "⚠️ Dữ liệu Khá - Cần lưu ý một số ngắt quãng/nhiễu";
        cardScore.style.borderColor = "rgba(255, 255, 0, 0.5)";
    } else {
        scoreVal.style.color = "#ff007f";
        scoreDesc.textContent = "❌ Dữ liệu Cần tiền xử lý & Lọc nhiễu trước khi dùng";
        cardScore.style.borderColor = "rgba(255, 0, 127, 0.5)";
    }

    document.getElementById("freq_imu").textContent = data.imu_stats.hz;
    document.getElementById("freq_gps").textContent = data.gps_stats.hz;
    document.getElementById("freq_desc").textContent = `IMU (${data.imu_stats.count} mẫu) | GPS (${data.gps_stats.count} mẫu)`;

    const totalGaps = data.imu_stats.gaps + data.gps_stats.gaps;
    document.getElementById("gaps_count").textContent = totalGaps;
    document.getElementById("outliers_count").textContent = data.imu_stats.outliers;
    const gapsDesc = document.getElementById("gaps_desc");
    if (totalGaps === 0 && data.imu_stats.outliers === 0) {
        gapsDesc.textContent = "✅ Hoàn hảo: 0 Gaps & 0 Spikes";
        gapsDesc.style.color = "#00ffcc";
    } else {
        gapsDesc.textContent = `IMU ${data.imu_stats.gaps} gaps | GPS ${data.gps_stats.gaps} gaps`;
        gapsDesc.style.color = "#ffff00";
    }

    document.getElementById("events_total").textContent = data.events_count;
    document.getElementById("roads_total").textContent = data.roads_count;
    const maxDur = Math.max(data.imu_stats.duration, data.gps_stats.duration);
    document.getElementById("total_dur").textContent = `${maxDur}s`;

    const badge = document.getElementById("session_meta_badge");
    badge.style.display = "flex";
    document.getElementById("meta_files").textContent = `${data.checks[0].status === 'pass' ? '4/4' : 'Thiếu'} Files (${data.session_name})`;
}

// BẬT / TẮT TRỤC IMU TRONG PANEL-TOOLS
function toggleImuTrace(axis, isChecked) {
    const traceMap = { 
        'ax': 0, 'ay': 1, 'az': 2, 
        'gx': 3, 'gy': 4, 'gz': 5,
        'ex': 6, 'ey': 7, 'ez': 8,
        'sin_yaw': 9, 'cos_yaw': 10, 'sin_roll': 11, 'cos_roll': 12, 'sin_pitch': 13, 'cos_pitch': 14
    };
    const idx = traceMap[axis];
    if (idx !== undefined) {
        Plotly.restyle("chart_imu", { visible: isChecked }, [idx]);
    }
}

function toggleAllImuTraces(enable) {
    document.querySelectorAll(".chk-imu").forEach(chk => {
        chk.checked = enable;
    });
    const allIdxs = Array.from({length: 15}, (_, i) => i);
    Plotly.restyle("chart_imu", { visible: enable }, allIdxs);
}

function toggleImuGroup(group) {
    let idxs = [];
    if (group === 'acc') idxs = [0, 1, 2];
    else if (group === 'gyr') idxs = [3, 4, 5];
    else if (group === 'eul') idxs = [6, 7, 8];
    else if (group === 'sincos') idxs = [9, 10, 11, 12, 13, 14];

    const inputs = document.querySelectorAll(".chk-imu");
    let newState = true;
    idxs.forEach(i => { if (inputs[i] && inputs[i].checked) newState = false; });
    idxs.forEach(i => {
        if (inputs[i]) inputs[i].checked = newState;
    });
    Plotly.restyle("chart_imu", { visible: newState }, idxs);
}

function toggleGpsTrace(idx, isChecked) {
    Plotly.restyle("chart_gps", { visible: isChecked ? true : "legendonly" }, [idx]);
}

// CHUYỂN ĐỔI CHẾ ĐỘ ZOOM VÙNG VS DI CHUYỂN (PAN) NGĂN CHROME SELECT TEXT
function setChartDragMode(chartId, mode, btnElem) {
    Plotly.relayout(chartId, { dragmode: mode });
    const parent = btnElem.parentElement;
    if (parent) {
        parent.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
    }
    btnElem.classList.add("active");
}

function getActiveFontFamily() {
    return localStorage.getItem('sota_analytics_font') || 'Liberation Serif';
}

// ================= SINH HÌNH KHỐI (SHAPES) & NHÃN CHÚ THÍCH (ANNOTATIONS) SỰ KIỆN / ĐƯỜNG =================
function buildShapesAndAnnotations(data, showEvents = true, showRoads = true) {
    const shapes = [];
    const annotations = [];
    const fontFam = getActiveFontFamily();
    if (showEvents && data.events && data.events.length > 0) {
        data.events.forEach((ev, idx) => {
            const isEvUnlocked = Boolean(ev.is_unlocked);
            // Khối nền sự kiện
            shapes.push({
                type: 'rect', xref: 'x', yref: 'paper',
                x0: ev.start, x1: ev.end, y0: 0, y1: 1,
                fillcolor: '#ff007f', opacity: isEvUnlocked ? 0.22 : 0.14,
                line: { width: isEvUnlocked ? 2 : 1.5, color: '#ff007f', dash: 'dash' },
                editable: isEvUnlocked,
                _eventIndex: idx,
                _isEventShape: true
            });

            if (isEvUnlocked) {
                // Thanh dọc kéo mép trái (Start - màu xanh lơ sáng)
                shapes.push({
                    type: 'line', xref: 'x', yref: 'paper',
                    x0: ev.start, x1: ev.start, y0: 0, y1: 1,
                    line: { width: 4.5, color: '#00ffcc' },
                    editable: true,
                    _eventIndex: idx,
                    _isEventStartLine: true
                });
                // Thanh dọc kéo mép phải (End - màu hồng sáng)
                shapes.push({
                    type: 'line', xref: 'x', yref: 'paper',
                    x0: ev.end, x1: ev.end, y0: 0, y1: 1,
                    line: { width: 4.5, color: '#ff007f' },
                    editable: true,
                    _eventIndex: idx,
                    _isEventEndLine: true
                });
            }

            // Nhãn chữ hiển thị trực tiếp trên biểu đồ
            annotations.push({
                x: (ev.start + ev.end) / 2,
                y: 0.96 - (idx % 2) * 0.08,
                xref: 'x', yref: 'paper',
                text: isEvUnlocked ? 
                    `<b>🚩 ${ev.label || ev.event}</b><br><span style="font-size:10px; color:#00ffcc">◄ ${ev.start}s</span> - <span style="font-size:10px; color:#ffb3d9">${ev.end}s ►</span><br><span style="font-size:9.5px; color:#ffff00">🔓 Đang mở sửa</span>` :
                    `<b>🚩 ${ev.label || ev.event}</b><br><span style="font-size:10px">${ev.start}s - ${ev.end}s</span><br><span style="font-size:9px; color:#8899a6">🔒 Đã khóa</span>`,
                showarrow: true,
                arrowhead: 2,
                arrowcolor: '#ff007f',
                ax: 0, ay: -22,
                bgcolor: 'rgba(255, 0, 127, 0.88)',
                bordercolor: isEvUnlocked ? '#00ffcc' : '#ffffff', borderwidth: isEvUnlocked ? 2 : 1, borderpad: 4,
                font: { color: '#ffffff', size: 11.5, family: fontFam }
            });
        });
    }

    if (showRoads && data.roads && data.roads.length > 0) {
        data.roads.forEach((rd) => {
            let color = '#00aaff';
            let roadLabel = rd.road_type;
            if (rd.road_type === 'SMOOTH_ASPHALT') { color = '#00ffcc'; roadLabel = 'Đường nhựa phẳng (Smooth)'; }
            else if (rd.road_type === 'DIRT_ROAD' || rd.road_type === 'GRAVEL_ROAD') { color = '#ff8800'; roadLabel = 'Đường đất / Sỏi (Dirt/Gravel)'; }
            else if (rd.road_type === 'ROUGH_ASPHALT') { color = '#ffff00'; roadLabel = 'Đường nhựa gồ ghề (Rough)'; }

            // Dải màu đáy biểu diễn bề mặt đường
            shapes.push({
                type: 'rect', xref: 'x', yref: 'paper',
                x0: rd.start, x1: rd.end, y0: 0, y1: 0.07,
                fillcolor: color, opacity: 0.45,
                line: { width: 1, color: color }
            });

            // Nhãn chữ loại đường ở đáy
            annotations.push({
                x: (rd.start + rd.end) / 2,
                y: 0.035,
                xref: 'x', yref: 'paper',
                text: `<b>🛣️ ${roadLabel}</b>${rd.speed_limit > 0 ? ` [Giới hạn: ${rd.speed_limit}km/h]` : ''}`,
                showarrow: false,
                bgcolor: 'rgba(7, 9, 14, 0.85)',
                bordercolor: color, borderwidth: 1.5, borderpad: 3,
                font: { color: color, size: 11, family: fontFam }
            });
        });
    }

    return { shapes, annotations };
}

// ĐIỀU KHIỂN LỚP PHỦ TRÊN CÁC TAB
function toggleOverlay(chartId, type, isChecked) {
    if (!currentAnalyticsData) return;

    if (chartId === 'chart_imu' || chartId === 'chart_gps') {
        refreshChartOverlays(chartId);
    } else if (chartId === 'chart_traj') {
        const showEv = document.getElementById("chk_traj_ev") ? document.getElementById("chk_traj_ev").checked : true;
        const showRd = document.getElementById("chk_traj_rd") ? document.getElementById("chk_traj_rd").checked : true;
        renderTrajectoryChart(currentAnalyticsData, showEv, showRd);
    }
}

function refreshChartOverlays(chartId) {
    if (!currentAnalyticsData) return;
    const prefix = chartId === 'chart_imu' ? 'chk_imu_' : 'chk_gps_';
    const showEv = document.getElementById(prefix + 'ev') ? document.getElementById(prefix + 'ev').checked : true;
    const showRd = document.getElementById(prefix + 'rd') ? document.getElementById(prefix + 'rd').checked : true;

    const overlays = buildShapesAndAnnotations(currentAnalyticsData, showEv, showRd);
    const chartEl = document.getElementById(chartId);
    if (!chartEl) return;

    try {
        isUpdatingRelayout = true;
        if (typeof Plotly.react === 'function' && chartEl.data && chartEl.layout) {
            chartEl.layout.shapes = overlays.shapes;
            chartEl.layout.annotations = overlays.annotations;
            if (!chartEl.layout.font) chartEl.layout.font = {};
            chartEl.layout.font.family = getActiveFontFamily();
            Plotly.react(chartId, chartEl.data, chartEl.layout, chartEl.config || {
                responsive: true,
                displaylogo: false,
                scrollZoom: true,
                doubleClick: 'reset+autosize',
                modeBarButtonsToRemove: ['lasso2d', 'select2d'],
                displayModeBar: true,
                edits: { shapePosition: true }
            });
        } else {
            Plotly.relayout(chartId, {
                shapes: overlays.shapes,
                annotations: overlays.annotations,
                'font.family': getActiveFontFamily()
            });
        }
    } catch (e) {
        console.error("Lỗi refreshChartOverlays:", e);
    } finally {
        isUpdatingRelayout = false;
    }
}

function snapToNearestImuTime(rawTime) {
    if (!currentAnalyticsData || !currentAnalyticsData.imu_data || currentAnalyticsData.imu_data.length === 0) {
        return Number(rawTime.toFixed(3));
    }
    const imu = currentAnalyticsData.imu_data;
    let closestT = imu[0].t;
    let minDiff = Math.abs(rawTime - closestT);
    for (let i = 1; i < imu.length; i++) {
        const diff = Math.abs(rawTime - imu[i].t);
        if (diff < minDiff) {
            minDiff = diff;
            closestT = imu[i].t;
        } else if (diff > minDiff) {
            break;
        }
    }
    return Number(closestT.toFixed(3));
}

function renderEventLockControls() {
    if (!currentAnalyticsData || !currentAnalyticsData.events) return;
    const events = currentAnalyticsData.events;
    const grid = document.getElementById("imu_events_cards_grid");
    if (grid) {
        grid.innerHTML = "";
        if (events.length === 0) {
            grid.innerHTML = '<div style="color: #8899a6; padding: 10px;">(Không có sự kiện động nào trong phiên)</div>';
        } else {
            const groupsMap = new Map();
            events.forEach((ev, idx) => {
                const gName = ev.event || ev.group || 'Khác';
                if (!groupsMap.has(gName)) groupsMap.set(gName, []);
                groupsMap.get(gName).push({ ev, idx });
            });

            window.eventGroupsCollapsedState = window.eventGroupsCollapsedState || {};

            groupsMap.forEach((items, gName) => {
                const safeId = 'grp_' + String(gName).replace(/[^a-zA-Z0-9]/g, '_');
                const isCollapsed = Boolean(window.eventGroupsCollapsedState[safeId]);
                const unlockedCount = items.filter(item => Boolean(item.ev.is_unlocked)).length;
                const allUnlocked = items.length > 0 && unlockedCount === items.length;

                const section = document.createElement("div");
                section.className = "event-group-section";
                section.style.cssText = "background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; overflow: hidden; margin-bottom: 6px; transition: border-color 0.2s;";
                
                let cardsHtml = "";
                items.forEach(({ ev, idx }) => {
                    const isUn = Boolean(ev.is_unlocked);
                    const safeIdx = idx;
                    cardsHtml += `
                        <div id="event_card_${safeIdx}" class="event-card-item" onclick="zoomToEvent(${safeIdx})" style="background: rgba(255,255,255,0.035); border: 1px solid ${isUn ? '#00ffcc' : 'rgba(255,0,127,0.3)'}; border-radius: 6px; padding: 10px; display: flex; flex-direction: column; gap: 6px; cursor: pointer; transition: all 0.2s;">
                            <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                                <span style="color: #ff007f; font-weight: bold; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: flex; align-items: center; gap: 6px;">
                                    <span>🚩 ${ev.label || ev.event}</span>
                                    <span style="font-size: 10.5px; background: rgba(0,255,204,0.15); border: 1px solid #00ffcc; color: #00ffcc; padding: 1px 7px; border-radius: 3px;">🔍 Click Zoom</span>
                                </span>
                                <button onclick="event.stopPropagation(); toggleSingleEventLock(${safeIdx})" style="background: ${isUn ? 'rgba(0, 255, 204, 0.2)' : 'rgba(255, 0, 127, 0.15)'}; border: 1px solid ${isUn ? '#00ffcc' : '#ff007f'}; color: ${isUn ? '#00ffcc' : '#ffb3d9'}; border-radius: 4px; padding: 4px 10px; font-size: 11px; cursor: pointer; font-weight: bold; flex-shrink: 0;">
                                    ${isUn ? '🔓 Đang mở sửa' : '🔒 Đã Khóa'}
                                </button>
                            </div>
                            <div style="font-size: 11.5px; color: #cbd5e1; display: flex; justify-content: space-between; align-items: center;">
                                <span>Start: <b style="color: #00ffcc;">${ev.start}s</b></span>
                                <span>End: <b style="color: #ffb3d9;">${ev.end}s</b></span>
                                <span>Nhóm: <i>${ev.group || ev.event || '--'}</i></span>
                            </div>
                        </div>
                    `;
                });

                const escapedGName = String(gName).replace(/'/g, "\\'");
                section.innerHTML = `
                    <div class="event-group-header" onclick="toggleEventGroupCollapse('${safeId}')" style="background: rgba(255,255,255,0.06); padding: 12px 16px; display: flex; justify-content: space-between; align-items: center; cursor: pointer; user-select: none; transition: background 0.2s;">
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <span id="icon_${safeId}" style="font-size: 14px; color: #00ffcc; width: 16px; display: inline-block;">${isCollapsed ? '▶' : '▼'}</span>
                            <span style="color: #00ffcc; font-weight: bold; font-size: 14px;">📁 ${gName}</span>
                            <span style="background: rgba(255,0,127,0.2); border: 1px solid #ff007f; color: #ff007f; border-radius: 12px; padding: 2px 8px; font-size: 11px; font-weight: bold;">${items.length} sự kiện</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 12px;">
                            <span style="font-size: 11.5px; color: #cbd5e1;">(${unlockedCount}/${items.length} đang mở sửa)</span>
                            <button onclick="event.stopPropagation(); toggleLockAllInGroup('${escapedGName}')" style="background: ${allUnlocked ? 'rgba(0, 255, 204, 0.2)' : 'rgba(255, 0, 127, 0.15)'}; border: 1px solid ${allUnlocked ? '#00ffcc' : '#ff007f'}; color: ${allUnlocked ? '#00ffcc' : '#ffb3d9'}; padding: 4px 10px; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: bold;">
                                ${allUnlocked ? '🔒 Khóa nhóm' : '🔓 Mở sửa nhóm'}
                            </button>
                        </div>
                    </div>
                    <div id="content_${safeId}" class="event-group-content" style="display: ${isCollapsed ? 'none' : 'grid'}; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 10px; padding: 12px; background: rgba(0,0,0,0.25);">
                        ${cardsHtml}
                    </div>
                `;
                grid.appendChild(section);
            });
        }
    }

    const tbodyEv = document.querySelector("#table_events tbody");
    if (tbodyEv) {
        tbodyEv.innerHTML = "";
        if (events.length === 0) {
            tbodyEv.innerHTML = '<tr><td colspan="7" class="text-center" style="color: #8899a6;">(Không có sự kiện động nào trong phiên)</td></tr>';
        } else {
            events.forEach((ev, idx) => {
                const isUn = Boolean(ev.is_unlocked);
                const tr = document.createElement("tr");
                tr.style.cursor = "pointer";
                tr.onclick = (e) => { if (!e.target.closest('button')) zoomToEvent(idx); };
                tr.innerHTML = `
                    <td>${ev.start.toFixed(3)}</td>
                    <td>${ev.end.toFixed(3)}</td>
                    <td style="color: #ff007f; font-weight: bold;">${ev.event}</td>
                    <td>${ev.label || ev.event}</td>
                    <td>${ev.group || ''}</td>
                    <td style="color: #00ffcc;">${ev.road || ''}</td>
                    <td>
                        <button onclick="event.stopPropagation(); toggleSingleEventLock(${idx})" style="background: ${isUn ? 'rgba(0, 255, 204, 0.2)' : 'rgba(255, 0, 127, 0.15)'}; border: 1px solid ${isUn ? '#00ffcc' : '#ff007f'}; color: ${isUn ? '#00ffcc' : '#ffb3d9'}; border-radius: 4px; padding: 4px 10px; font-size: 11px; cursor: pointer; font-weight: bold;">
                            ${isUn ? '🔓 Đang mở sửa' : '🔒 Đã Khóa'}
                        </button>
                    </td>
                `;
                tbodyEv.appendChild(tr);
            });
        }
    }
}

function toggleEventGroupCollapse(safeId) {
    const content = document.getElementById(`content_${safeId}`);
    const icon = document.getElementById(`icon_${safeId}`);
    if (!content) return;
    window.eventGroupsCollapsedState = window.eventGroupsCollapsedState || {};
    if (content.style.display === "none") {
        content.style.display = "grid";
        if (icon) icon.innerText = "▼";
        window.eventGroupsCollapsedState[safeId] = false;
    } else {
        content.style.display = "none";
        if (icon) icon.innerText = "▶";
        window.eventGroupsCollapsedState[safeId] = true;
    }
}

function toggleLockAllInGroup(groupName) {
    if (!currentAnalyticsData || !currentAnalyticsData.events) return;
    const events = currentAnalyticsData.events;
    const groupEvs = events.filter(e => (e.event || e.group || 'Khác') === groupName);
    const allUnlocked = groupEvs.length > 0 && groupEvs.every(e => Boolean(e.is_unlocked));
    
    events.forEach(ev => {
        if ((ev.event || ev.group || 'Khác') === groupName) {
            ev.is_unlocked = !allUnlocked;
        }
    });
    
    renderEventLockControls();
    refreshChartOverlays("chart_imu");
    showToast(`${allUnlocked ? '🔒 Đã khóa' : '🔓 Đã mở sửa'} toàn bộ sự kiện nhóm "${groupName}"`, "info");
}

function zoomToEvent(idx) {
    if (!currentAnalyticsData || !currentAnalyticsData.events || !currentAnalyticsData.events[idx]) return;
    const ev = currentAnalyticsData.events[idx];
    const chartEl = document.getElementById("chart_imu");
    if (!chartEl || !chartEl.layout) return;

    const duration = ev.end - ev.start;
    const margin = Math.max(duration * 0.6, 1.5);
    const zoomStart = ev.start - margin;
    const zoomEnd = ev.end + margin;

    const tabBtn = document.querySelector('.tab-btn[onclick*="tab_imu"]');
    if (tabBtn) switchTab('tab_imu', tabBtn);

    Plotly.relayout("chart_imu", {
        'xaxis.range': [zoomStart, zoomEnd],
        'xaxis.autorange': false
    }).then(() => {
        showToast(`🔍 Đã zoom vào sự kiện "${ev.label || ev.event}" (${ev.start.toFixed(2)}s - ${ev.end.toFixed(2)}s)`, "info");
        document.querySelectorAll('.event-card-item').forEach(c => c.style.borderColor = '');
        const targetCard = document.getElementById(`event_card_${idx}`);
        if (targetCard) targetCard.style.borderColor = '#ffff00';
    });
}

function toggleSingleEventLock(idx) {
    if (!currentAnalyticsData || !currentAnalyticsData.events || !currentAnalyticsData.events[idx]) return;
    const ev = currentAnalyticsData.events[idx];
    ev.is_unlocked = !ev.is_unlocked;
    
    renderEventLockControls();
    refreshChartOverlays("chart_imu");
    
    if (ev.is_unlocked) {
        showToast(`🔓 Đã mở khóa sửa cho sự kiện "${ev.label || ev.event}" (${ev.start}s - ${ev.end}s). Rê chuột vào 2 đường dọc xanh/hồng để kéo sửa!`, "info");
    } else {
        showToast(`🔒 Đã khóa sửa sự kiện "${ev.label || ev.event}".`, "info");
    }
}

function saveAllEventsToServer() {
    if (!currentAnalyticsData || !currentAnalyticsData.events || currentAnalyticsData.events.length === 0) {
        showToast("⚠️ Chưa có dữ liệu sự kiện nào trong phiên để lưu", "warning");
        return;
    }
    saveUpdatedEventsToServer(currentAnalyticsData.events);
}

function renderImuChart(data) {
    const imu = data.imu_data;
    if (!imu || imu.length === 0) {
        document.getElementById("chart_imu").innerHTML = '<div style="padding: 40px; text-align: center; color: #8899a6;">(Chưa có dữ liệu IMU trong phiên này)</div>';
        return;
    }

    const t = imu.map(d => d.t);
    const ax = imu.map(d => d.ax);
    const ay = imu.map(d => d.ay);
    const az = imu.map(d => d.az);
    const gx = imu.map(d => d.gx);
    const gy = imu.map(d => d.gy);
    const gz = imu.map(d => d.gz);
    const ex = imu.map(d => d.ex || 0);
    const ey = imu.map(d => d.ey || 0);
    const ez = imu.map(d => d.ez || 0);
    const sin_yaw = imu.map(d => d.sin_yaw || 0);
    const cos_yaw = imu.map(d => d.cos_yaw || 0);
    const sin_roll = imu.map(d => d.sin_roll || 0);
    const cos_roll = imu.map(d => d.cos_roll || 0);
    const sin_pitch = imu.map(d => d.sin_pitch || 0);
    const cos_pitch = imu.map(d => d.cos_pitch || 0);

    const traces = [
        { x: t, y: ax, name: "AX (Ngang - m/s²)", type: "scatter", mode: "lines", line: { color: "#00ffcc", width: 1.5 } },
        { x: t, y: ay, name: "AY (Dọc - m/s²)", type: "scatter", mode: "lines", line: { color: "#ff007f", width: 1.5 } },
        { x: t, y: az, name: "AZ (Đứng - m/s²)", type: "scatter", mode: "lines", line: { color: "#ffff00", width: 1.5 } },
        { x: t, y: gx, name: "GX (Pitch - rad/s)", type: "scatter", mode: "lines", line: { color: "#00aaff", width: 1.2 }, visible: false },
        { x: t, y: gy, name: "GY (Roll - rad/s)", type: "scatter", mode: "lines", line: { color: "#ff8800", width: 1.2 }, visible: false },
        { x: t, y: gz, name: "GZ (Yaw - rad/s)", type: "scatter", mode: "lines", line: { color: "#bb00ff", width: 1.2 }, visible: false },
        { x: t, y: ex, name: "EX (Pitch - °)", type: "scatter", mode: "lines", line: { color: "#33ff00", width: 1.3 }, visible: false },
        { x: t, y: ey, name: "EY (Roll - °)", type: "scatter", mode: "lines", line: { color: "#ff3300", width: 1.3 }, visible: false },
        { x: t, y: ez, name: "EZ (Yaw - °)", type: "scatter", mode: "lines", line: { color: "#00ffff", width: 1.3 }, visible: false },
        { x: t, y: sin_yaw, name: "Sin(Yaw)", type: "scatter", mode: "lines", line: { color: "#ff66cc", width: 1.1 }, visible: false },
        { x: t, y: cos_yaw, name: "Cos(Yaw)", type: "scatter", mode: "lines", line: { color: "#cc66ff", width: 1.1 }, visible: false },
        { x: t, y: sin_roll, name: "Sin(Roll)", type: "scatter", mode: "lines", line: { color: "#66ccff", width: 1.1 }, visible: false },
        { x: t, y: cos_roll, name: "Cos(Roll)", type: "scatter", mode: "lines", line: { color: "#66ffcc", width: 1.1 }, visible: false },
        { x: t, y: sin_pitch, name: "Sin(Pitch)", type: "scatter", mode: "lines", line: { color: "#ffff66", width: 1.1 }, visible: false },
        { x: t, y: cos_pitch, name: "Cos(Pitch)", type: "scatter", mode: "lines", line: { color: "#ff9966", width: 1.1 }, visible: false }
    ];

    const traceKeys = ['ax', 'ay', 'az', 'gx', 'gy', 'gz', 'ex', 'ey', 'ez', 'sin_yaw', 'cos_yaw', 'sin_roll', 'cos_roll', 'sin_pitch', 'cos_pitch'];
    const chkInputs = document.querySelectorAll(".chk-imu");
    const chkMap = {};
    chkInputs.forEach(chk => {
        if (chk && chk.value) chkMap[chk.value] = chk.checked;
    });
    traces.forEach((tr, idx) => {
        const key = traceKeys[idx];
        if (chkMap[key] !== undefined) {
            tr.visible = chkMap[key];
        }
    });

    const showEv = document.getElementById("chk_imu_ev") ? document.getElementById("chk_imu_ev").checked : true;
    const showRd = document.getElementById("chk_imu_rd") ? document.getElementById("chk_imu_rd").checked : true;
    const overlays = buildShapesAndAnnotations(data, showEv, showRd);

    const layout = {
        paper_bgcolor: "#05080e",
        plot_bgcolor: "#05080e",
        font: { color: "#e0e6ed", family: getActiveFontFamily() },
        margin: { t: 40, r: 30, l: 50, b: 40 },
        xaxis: { 
            title: "Thời gian tuyệt đối Pi T0 (giây)", 
            gridcolor: "rgba(255,255,255,0.08)", 
            zerolinecolor: "rgba(255,255,255,0.2)",
            showspikes: true,
            spikemode: "toaxis+across",
            spikesnap: "data",
            spikedash: "dot",
            spikethickness: 1.5,
            spikecolor: "#00ffcc"
        },
        yaxis: { title: "Gia tốc (m/s²) / Vận tốc góc", gridcolor: "rgba(255,255,255,0.08)", zerolinecolor: "rgba(255,255,255,0.2)" },
        shapes: overlays.shapes,
        annotations: overlays.annotations,
        hovermode: "x unified",
        dragmode: "zoom",
        legend: { orientation: "h", y: 1.15 }
    };

    const config = {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
        doubleClick: 'reset+autosize',
        modeBarButtonsToRemove: ['lasso2d', 'select2d'],
        displayModeBar: true,
        edits: { shapePosition: true }
    };

    Plotly.newPlot("chart_imu", traces, layout, config).then(() => {
        attachPreventBrowserDefaults("chart_imu");
        const chartEl = document.getElementById("chart_imu");
        if (chartEl && !chartEl._hasBoundRelayout) {
            chartEl._hasBoundRelayout = true;
            chartEl.on('plotly_relayout', handleShapeRelayout);
        }
    });
}

function handleShapeRelayout(eventData) {
    if (isUpdatingRelayout) return;
    if (!currentAnalyticsData || !currentAnalyticsData.events || currentAnalyticsData.events.length === 0) return;
    if (!eventData) return;

    const shapeKeys = Object.keys(eventData).filter(k => k.startsWith('shapes'));
    if (shapeKeys.length === 0 && !eventData.shapes) return;

    const chartEl = document.getElementById("chart_imu");
    if (!chartEl || !chartEl.layout || !chartEl.layout.shapes) return;

    const modifiedIdxs = new Set();
    if (eventData.shapes && Array.isArray(eventData.shapes)) {
        eventData.shapes.forEach((s, idx) => {
            modifiedIdxs.add(idx);
        });
    } else {
        shapeKeys.forEach(k => {
            const match = k.match(/^shapes\[(\d+)\]/);
            if (match) {
                modifiedIdxs.add(parseInt(match[1]));
            }
        });
    }

    if (modifiedIdxs.size === 0) return;

    let hasChanges = false;
    modifiedIdxs.forEach(shapeIdx => {
        const shape = chartEl.layout.shapes[shapeIdx];
        if (!shape) return;
        
        let evIdx = shape._eventIndex;
        if (evIdx === undefined) {
            if (shape._isEventShape || shape.fillcolor === '#ff007f' || shape.line?.color === '#ff007f') {
                if (shapeIdx < currentAnalyticsData.events.length) {
                    evIdx = shapeIdx;
                }
            }
        }
        if (evIdx === undefined || evIdx < 0 || evIdx >= currentAnalyticsData.events.length) return;

        const ev = currentAnalyticsData.events[evIdx];
        if (!ev || !ev.is_unlocked) return;

        const x0 = Number(shape.x0);
        const x1 = Number(shape.x1);
        if (isNaN(x0) || isNaN(x1)) return;

        if (shape._isEventStartLine || shape.line?.color === '#00ffcc') {
            const newStart = snapToNearestImuTime(x0);
            if (Math.abs(newStart - ev.start) > 0.001) {
                ev.start = newStart;
                if (ev.start > ev.end) { const tmp = ev.start; ev.start = ev.end; ev.end = tmp; }
                hasChanges = true;
            }
        } else if (shape._isEventEndLine) {
            const newEnd = snapToNearestImuTime(x0);
            if (Math.abs(newEnd - ev.end) > 0.001) {
                ev.end = newEnd;
                if (ev.start > ev.end) { const tmp = ev.start; ev.start = ev.end; ev.end = tmp; }
                hasChanges = true;
            }
        } else {
            const newStart = snapToNearestImuTime(Math.min(x0, x1));
            const newEnd = snapToNearestImuTime(Math.max(x0, x1));
            if (Math.abs(newStart - ev.start) > 0.001 || Math.abs(newEnd - ev.end) > 0.001) {
                ev.start = newStart;
                ev.end = newEnd;
                hasChanges = true;
            }
        }
    });

    if (hasChanges) {
        refreshChartOverlays("chart_imu");
        renderEventLockControls();
    }
}

function saveUpdatedEventsToServer(eventsList) {
    if (!currentAnalyticsData || !currentAnalyticsData.session_name) return;
    const sessionName = currentAnalyticsData.session_name;
    const source = window.currentDataSource || 'raw';

    fetch('/api/analytics/update_events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            session: sessionName,
            source: source,
            events: eventsList
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            const dirLabel = source === 'proc' ? 'proc_data' : 'raw_data';
            showToast(`✅ Đã lưu ${data.count} sự kiện vào events.csv (${dirLabel}/${sessionName})!`, "success");
        } else {
            showToast(`⚠️ Lỗi lưu file events.csv: ${data.message || 'Unknown error'}`, "error");
        }
    })
    .catch(err => {
        console.error("Lỗi POST /api/analytics/update_events:", err);
        showToast("❌ Không thể kết nối đến server để cập nhật events.csv", "error");
    });
}

function renderGpsChart(data) {
    const gps = data.gps_data;
    if (!gps || gps.length === 0) {
        document.getElementById("chart_gps").innerHTML = '<div style="padding: 40px; text-align: center; color: #8899a6;">(Chưa có dữ liệu GPS trong phiên này)</div>';
        return;
    }

    const t = gps.map(d => d.t);
    const spd = gps.map(d => d.spd);
    const alt = gps.map(d => d.alt);
    const sats = gps.map(d => d.sats);

    const traces = [
        { x: t, y: spd, name: "Vận tốc (km/h)", type: "scatter", mode: "lines", line: { color: "#00ffcc", width: 2.5 } },
        { x: t, y: alt, name: "Độ cao (m)", type: "scatter", mode: "lines", yaxis: "y2", line: { color: "#00aaff", width: 1.5, dash: "dot" } },
        { x: t, y: sats, name: "Số lượng Vệ tinh (Sats)", type: "scatter", mode: "lines", yaxis: "y2", line: { color: "#ffff00", width: 1 }, visible: "legendonly" }
    ];

    const showEv = document.getElementById("chk_gps_ev") ? document.getElementById("chk_gps_ev").checked : true;
    const showRd = document.getElementById("chk_gps_rd") ? document.getElementById("chk_gps_rd").checked : true;
    const overlays = buildShapesAndAnnotations(data, showEv, showRd);

    const layout = {
        paper_bgcolor: "#05080e",
        plot_bgcolor: "#05080e",
        font: { color: "#e0e6ed", family: getActiveFontFamily() },
        margin: { t: 40, r: 50, l: 50, b: 40 },
        xaxis: { title: "Thời gian tuyệt đối Pi T0 (giây)", gridcolor: "rgba(255,255,255,0.08)" },
        yaxis: { title: "Vận tốc di chuyển (km/h)", gridcolor: "rgba(255,255,255,0.08)" },
        yaxis2: { title: "Độ cao (m) / Vệ tinh", overlaying: "y", side: "right", gridcolor: "rgba(0,0,0,0)" },
        shapes: overlays.shapes,
        annotations: overlays.annotations,
        hovermode: "x unified",
        dragmode: "zoom",
        legend: { orientation: "h", y: 1.15 }
    };

    const config = {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
        doubleClick: 'reset+autosize',
        modeBarButtonsToRemove: ['lasso2d', 'select2d'],
        displayModeBar: true
    };

    Plotly.newPlot("chart_gps", traces, layout, config).then(() => {
        attachPreventBrowserDefaults("chart_gps");
    });
}

function renderTrajectoryChart(data, showEvents = true, showRoads = true) {
    const gps = data.gps_data;
    if (!gps || gps.length === 0) {
        document.getElementById("chart_traj").innerHTML = '<div style="padding: 40px; text-align: center; color: #8899a6;">(Chưa có tọa độ GPS hợp lệ để vẽ quỹ đạo)</div>';
        return;
    }

    if (showEvents === undefined && document.getElementById("chk_traj_ev")) showEvents = document.getElementById("chk_traj_ev").checked;
    if (showRoads === undefined && document.getElementById("chk_traj_rd")) showRoads = document.getElementById("chk_traj_rd").checked;

    const lat = gps.map(d => d.lat);
    const lon = gps.map(d => d.lon);
    const spd = gps.map(d => d.spd);
    const hoverText = gps.map(d => `t: ${d.t}s<br>Vận tốc: ${d.spd} km/h<br>Độ cao: ${d.alt}m<br>Sats: ${d.sats}`);

    const traces = [{
        x: lon,
        y: lat,
        name: "Quỹ đạo chuyển động (Speed Heatmap)",
        text: hoverText,
        hoverinfo: "text",
        mode: "lines+markers",
        marker: {
            size: 6.5,
            color: spd,
            colorscale: "Jet",
            colorbar: { title: "Speed (km/h)", thickness: 15, x: 1.02 },
            showscale: true
        },
        line: { width: 2, color: "rgba(0, 255, 204, 0.4)" }
    }];

    // Overlay Phân đoạn đường trên quỹ đạo 2D
    if (showRoads && data.roads && data.roads.length > 0) {
        data.roads.forEach((rd) => {
            const pts = gps.filter(d => d.t >= rd.start && d.t <= rd.end);
            if (pts.length > 1) {
                let color = '#00aaff';
                if (rd.road_type === 'SMOOTH_ASPHALT') color = '#00ffcc';
                else if (rd.road_type === 'DIRT_ROAD' || rd.road_type === 'GRAVEL_ROAD') color = '#ff8800';
                else if (rd.road_type === 'ROUGH_ASPHALT') color = '#ffff00';

                traces.push({
                    x: pts.map(p => p.lon),
                    y: pts.map(p => p.lat),
                    name: `🛣️ ${rd.road_type}`,
                    type: "scatter", mode: "lines",
                    line: { width: 6, color: color },
                    hoverinfo: "text",
                    text: pts.map(() => `<b>🛣️ PHÂN ĐOẠN ĐƯỜNG: ${rd.road_type}</b><br>Giới hạn tốc độ: ${rd.speed_limit} km/h<br>Thời gian: ${rd.start}s - ${rd.end}s`)
                });
            }
        });
    }

    // Overlay Sự kiện trên quỹ đạo 2D
    if (showEvents && data.events && data.events.length > 0) {
        data.events.forEach((ev) => {
            const pts = gps.filter(d => d.t >= ev.start && d.t <= ev.end);
            if (pts.length > 0) {
                traces.push({
                    x: pts.map(p => p.lon),
                    y: pts.map(p => p.lat),
                    name: `🚩 ${ev.label || ev.event}`,
                    type: "scatter", mode: "lines+markers",
                    marker: { symbol: 'star', size: 12, color: '#ff007f', line: { width: 1.5, color: '#ffffff' } },
                    line: { width: 4.5, color: '#ff007f' },
                    hoverinfo: "text",
                    text: pts.map(() => `<b>🚩 SỰ KIỆN: ${ev.label || ev.event}</b><br>Nhóm: ${ev.group || 'N/A'}<br>Thời gian: ${ev.start}s - ${ev.end}s`)
                });
            }
        });
    }

    const layout = {
        paper_bgcolor: "#05080e",
        plot_bgcolor: "#05080e",
        font: { color: "#e0e6ed", family: getActiveFontFamily() },
        margin: { t: 40, r: 80, l: 60, b: 50 },
        xaxis: { title: "Longitude (Kinh độ)", gridcolor: "rgba(255,255,255,0.08)", scaleanchor: "y", scaleratio: 1 },
        yaxis: { title: "Latitude (Vĩ độ)", gridcolor: "rgba(255,255,255,0.08)" },
        hovermode: "closest",
        dragmode: "zoom",
        legend: { orientation: "h", y: 1.15 }
    };

    const config = {
        responsive: true,
        displaylogo: false,
        scrollZoom: true,
        doubleClick: 'reset+autosize',
        modeBarButtonsToRemove: ['lasso2d', 'select2d'],
        displayModeBar: true
    };

    Plotly.newPlot("chart_traj", traces, layout, config).then(() => {
        attachPreventBrowserDefaults("chart_traj");
    });
}

function renderAuditReport(data) {
    const auditContainer = document.getElementById("audit_list");
    auditContainer.innerHTML = "";
    data.checks.forEach(chk => {
        const item = document.createElement("div");
        item.className = `audit-item ${chk.status}`;
        const icon = chk.status === 'pass' ? '✅' : (chk.status === 'warning' ? '⚠️' : '❌');
        item.innerHTML = `
            <div class="audit-icon">${icon}</div>
            <div class="audit-details">
                <h4>${chk.name}</h4>
                <p>${chk.desc}</p>
            </div>
        `;
        auditContainer.appendChild(item);
    });

    const tbodyEv = document.querySelector("#table_events tbody");
    tbodyEv.innerHTML = "";
    if (data.events.length === 0) {
        tbodyEv.innerHTML = '<tr><td colspan="6" class="text-center" style="color: #8899a6;">(Không có sự kiện động nào trong phiên)</td></tr>';
    } else {
        data.events.forEach(ev => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${ev.start.toFixed(3)}</td>
                <td>${ev.end.toFixed(3)}</td>
                <td style="color: #ff007f; font-weight: bold;">${ev.event}</td>
                <td>${ev.label}</td>
                <td>${ev.group}</td>
                <td style="color: #00ffcc;">${ev.road}</td>
            `;
            tbodyEv.appendChild(tr);
        });
    }

    const tbodyRd = document.querySelector("#table_roads tbody");
    tbodyRd.innerHTML = "";
    if (data.roads.length === 0) {
        tbodyRd.innerHTML = '<tr><td colspan="4" class="text-center" style="color: #8899a6;">(Không có ghi nhận phân đoạn đường)</td></tr>';
    } else {
        data.roads.forEach(rd => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td>${rd.start.toFixed(3)}</td>
                <td>${rd.end.toFixed(3)}</td>
                <td style="color: #00ffcc; font-weight: bold;">${rd.road_type}</td>
                <td style="color: #ffff00;">${rd.speed_limit} km/h</td>
            `;
            tbodyRd.appendChild(tr);
        });
    }
}

function switchTab(tabId, btnElem) {
    document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));
    document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
    
    const targetPane = document.getElementById(tabId);
    if (targetPane) targetPane.classList.add("active");
    if (btnElem) btnElem.classList.add("active");

    setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
    }, 100);
}

window.saveAllEventsToServer = saveAllEventsToServer;
window.toggleSingleEventLock = toggleSingleEventLock;
window.toggleEventGroupCollapse = toggleEventGroupCollapse;
window.toggleLockAllInGroup = toggleLockAllInGroup;
window.zoomToEvent = zoomToEvent;
