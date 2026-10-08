const LBL = {
    TOAST_CANCEL_OK: "⚠️ Đã hủy sự kiện GẦN NHẤT!",
    TOAST_CANCEL_ERR: "Không có sự kiện nào để hủy.",
    BTN_PRESSED: window.UI_LBL_BTN_PRESSED || "NÚT NHẤN",
    BTN_RELEASED: window.UI_LBL_BTN_RELEASED || "NHẢ NÚT",
    REC_ACTIVE: window.UI_LBL_REC_ACTIVE || "DỮ LIỆU ĐANG GHI",
    REC_IDLE: window.UI_LBL_REC_IDLE || "SẴN SÀNG"
};

document.addEventListener("DOMContentLoaded", () => {
    if(window.innerWidth <= 768) {
        const mobileTitle = document.getElementById('mobile_title');
        if (mobileTitle) mobileTitle.style.display = "flex";
    }
    initDragAndDrop();
    renderRulePlaylist();
});

let currentActiveEvents = [];
let gpio12State = false; 
let rulePreviousRecordingState = false;
let currentDbVersion = null;
let currentActiveObject = null;

function handleObjectChange(objectFile) {
    if (!objectFile) return;
    showToast("🔄 Đã đổi model object sang [" + objectFile + "]. Đang nạp lại giao diện...");
    fetch('/api/set_object', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_file: objectFile })
    }).then(r => r.json()).then(res => {
        setTimeout(() => location.reload(), 400);
    });
}

function showToast(message) {
    let toast = document.getElementById("toast");
    if (!toast) return;
    toast.innerText = message;
    toast.className = "show";
    setTimeout(function(){ toast.className = toast.className.replace("show", ""); }, 2500);
}

function toggleGPIO12() {
    let newState = !gpio12State;
    let actionStr = newState ? 'ON' : 'OFF';

    fetch('/api/gpio12_control', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ state: actionStr })
    })
    .then(response => {
        if(response.ok) {
            showToast(newState ? "🔌 Đang gửi lệnh BẬT GPIO 12..." : "🔌 Đang gửi lệnh TẮT GPIO 12...");
        } else {
            showToast("❌ Lỗi cấu hình GPIO từ máy chủ!");
        }
    })
    .catch(err => {
        showToast("❌ Mất kết nối tới server!");
    });
}

function handleEventClick(eventName) {
    let isActive = currentActiveEvents.some(ev => ev.name === eventName);
    if (isActive) action('stop', eventName);
    else action('start', eventName);
}

function handleRoadClick(roadName) {
    fetch('/api/road', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({road: roadName})
    });
}

function handleSpeedLimitClick(speedLimit) {
    fetch('/api/speed_limit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed_limit: speedLimit })
    });
}

function handlePresetClick(group, presetKey) {
    fetch('/api/preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group: group, preset_key: presetKey })
    });
}

function action(type, eventName) {
    if (type === 'cancel' && currentActiveEvents.length === 0) {
        showToast(LBL.TOAST_CANCEL_ERR);
        return;
    }
    if (type === 'cancel') showToast(LBL.TOAST_CANCEL_OK);

    fetch('/api/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: type, event: eventName})
    });
}

function reconnectPS5() {
    showToast("🔄 Đang thử kết nối lại PS5...");
    fetch('/api/reconnect_ps5', { method: 'POST' })
    .then(r => r.json())
    .then(data => {
        if(data.success) {
            showToast("✅ Đã kết nối thành công tay cầm PS5!");
        } else {
            showToast("❌ Không tìm thấy PS5! Hãy kiểm tra Bluetooth.");
        }
    })
    .catch(err => {
        showToast("❌ Lỗi kết nối tới máy chủ.");
    });
}

setInterval(() => {
    fetch('/api/status').then(r => r.json()).then(data => {
        if (currentActiveObject !== null && data.active_object && data.active_object !== currentActiveObject && !data.is_recording) {
            showToast("🔄 Phát hiện chuyển đổi model object. Đang nạp lại giao diện...");
            setTimeout(() => location.reload(), 500);
        }
        if (data.active_object) currentActiveObject = data.active_object;

        let objSel = document.getElementById("object_selector");
        if (objSel && data.active_object && objSel.value !== data.active_object) {
            objSel.value = data.active_object;
        }

        if (currentDbVersion !== null && data.db_version && data.db_version > currentDbVersion && !data.is_recording) {
            showToast("🔄 Phát hiện thay đổi cấu hình sự kiện. Đang tải lại giao diện...");
            setTimeout(() => location.reload(), 600);
        }
        if (data.db_version) currentDbVersion = data.db_version;

        let rawStatus = document.getElementById("raw_status");
        let sessionTime = document.getElementById("session_time");
        let gpioSpan = document.getElementById("gpio_status");
        let evListSpan = document.getElementById("active_events_list");

        let isRec = Boolean(data.is_recording);
        let autoToggleEl = document.getElementById("rule_auto_toggle");
        let isAutoEnabled = autoToggleEl && autoToggleEl.checked;

        // Khi GPIO12 chuyển từ OFF sang ON -> Tự động chạy kịch bản test AI
        if (!rulePreviousRecordingState && isRec && isAutoEnabled) {
            if (typeof rulePlaylist !== 'undefined' && rulePlaylist.length > 0) {
                showToast("⚡ Phát hiện REC (GPIO12)! Bắt đầu chạy kịch bản test AI...");
                startRuleEngine();
            }
        }
        // Khi GPIO12 tắt (hoặc ngắt ghi dữ liệu) -> Dừng kịch bản đang chạy
        if (rulePreviousRecordingState && !isRec && typeof ruleEngineRunning !== 'undefined' && ruleEngineRunning) {
            stopRuleEngine(true, "⏹ Đã dừng kịch bản do ngắt ghi dữ liệu (GPIO12 OFF)");
        }
        rulePreviousRecordingState = isRec;

        if (rawStatus) {
            if (data.is_recording) {
                rawStatus.innerText = LBL.REC_ACTIVE;
                rawStatus.style.color = "#28a745";
            } else {
                rawStatus.innerText = LBL.REC_IDLE;
                rawStatus.style.color = "gray";
            }
        }
        if (sessionTime) sessionTime.innerText = data.session_time;

        if (gpioSpan) {
            if (data.btn_pressed) {
                gpioSpan.innerText = LBL.BTN_PRESSED;
                gpioSpan.style.color = "#dc3545";
            } else {
                gpioSpan.innerText = LBL.BTN_RELEASED;
                gpioSpan.style.color = "gray";
            }
        }
        
        let btnGpio12 = document.getElementById("btn_gpio12");
        if (btnGpio12) {
            gpio12State = data.btn_pressed;
            if (gpio12State) {
                if (btnGpio12.className !== "btn-gpio on") {
                    btnGpio12.className = "btn-gpio on";
                    btnGpio12.innerText = "PIN 12: ON";
                }
            } else {
                if (btnGpio12.className !== "btn-gpio off") {
                    btnGpio12.className = "btn-gpio off";
                    btnGpio12.innerText = "PIN 12: OFF";
                }
            }
        }
        
        let ps5Badge = document.getElementById("ps5_status_badge");
        if (ps5Badge) {
            if (data.ps5_connected) {
                ps5Badge.innerText = "🎮 PS5: ONLINE";
                ps5Badge.style.color = "var(--neon-green)";
                ps5Badge.style.borderColor = "var(--neon-green)";
                ps5Badge.style.background = "rgba(0, 255, 0, 0.1)";
            } else {
                ps5Badge.innerText = "🎮 PS5: OFFLINE";
                ps5Badge.style.color = "#ff3b30";
                ps5Badge.style.borderColor = "#ff3b30";
                ps5Badge.style.background = "rgba(255, 0, 0, 0.1)";
            }
        }

        currentActiveEvents = data.active_events || [];
        document.querySelectorAll('.btn-event').forEach(btn => btn.classList.remove('active-event'));

        if (evListSpan) {
            if (data.active_events && data.active_events.length > 0) {
                let textArray = [];
                data.active_events.forEach(ev => {
                    textArray.push(`► ${ev.name} [${ev.elapsed}s]`);
                    let btn = document.getElementById('btn_ev_' + ev.name);
                    if(btn) btn.classList.add('active-event');
                    let ps5Btn = document.getElementById('btn_ps5_' + ev.name);
                    if(ps5Btn) ps5Btn.classList.add('active-event');
                });
                evListSpan.innerHTML = textArray.join('<br><br>');
                evListSpan.style.color = "#ff3b30";
            } else {
                evListSpan.innerText = "None";
                evListSpan.style.color = "var(--text-dim)";
            }
        }

        document.querySelectorAll('.btn-road').forEach(btn => btn.classList.remove('active-road'));
        if (data.current_road_type) {
            let activeRoadBtn = document.getElementById('btn_road_' + data.current_road_type);
            if (activeRoadBtn) activeRoadBtn.classList.add('active-road');
            let ps5RoadBtn = document.getElementById('btn_ps5_' + data.current_road_type);
            if (ps5RoadBtn) ps5RoadBtn.classList.add('active-road');
        }

        document.querySelectorAll('.btn-speed-limit').forEach(btn => btn.classList.remove('active-speed-limit'));
        if (data.current_speed_limit) {
            let btn = document.getElementById('btn_speed_limit_' + data.current_speed_limit);
            if (btn) btn.classList.add('active-speed-limit');
            let ps5SpeedBtn = document.getElementById('btn_ps5_' + data.current_speed_limit);
            if (ps5SpeedBtn) ps5SpeedBtn.classList.add('active-speed-limit');
        }

        document.querySelectorAll('.btn-preset').forEach(btn => btn.classList.remove('active-preset'));
        if (data.active_presets) {
            for (let [grp, val] of Object.entries(data.active_presets)) {
                if (Array.isArray(val)) {
                    val.forEach(item => {
                        let btn = document.getElementById('btn_preset_' + item);
                        if (btn) btn.classList.add('active-preset');
                        let ps5Btn = document.getElementById('btn_ps5_' + item);
                        if (ps5Btn) ps5Btn.classList.add('active-preset');
                    });
                } else {
                    let btn = document.getElementById('btn_preset_' + val);
                    if (btn) btn.classList.add('active-preset');
                    let ps5Btn = document.getElementById('btn_ps5_' + val);
                    if (ps5Btn) ps5Btn.classList.add('active-preset');
                }
            }
        }
    }).catch(() => {});
}, 200);

/* ============ HỆ THỐNG KÉO THẢ KHỐI VÀ LƯU TRỮ VỊ TRÍ ============ */
function initDragAndDrop() {
    restoreBlocksOrder();

    // Vô hiệu hóa kéo thả trên thiết bị di động để tránh xung đột với thao tác cuộn (scroll)
    const isMobile = window.innerWidth <= 768 || ('ontouchstart' in window) || navigator.maxTouchPoints > 0;
    if (isMobile) return;

    if (typeof Sortable !== 'undefined') {
        const contentEl = document.querySelector('.content');
        const gridEl = document.querySelector('.group-card-grid');
        if (contentEl) {
            Sortable.create(contentEl, {
                group: 'hud-cards',
                handle: 'h3',
                animation: 150,
                ghostClass: 'sortable-ghost',
                onEnd: saveBlocksOrder
            });
        }
        if (gridEl) {
            Sortable.create(gridEl, {
                group: 'hud-cards',
                handle: 'h3',
                animation: 150,
                ghostClass: 'sortable-ghost',
                onEnd: saveBlocksOrder
            });
        }
    } else {
        // Native HTML5 Drag and Drop fallback cho môi trường offline
        let draggedElement = null;
        document.querySelectorAll('.group-card').forEach(card => {
            card.setAttribute('draggable', 'true');
            card.addEventListener('dragstart', (e) => {
                draggedElement = card;
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', card.dataset.group);
                setTimeout(() => card.classList.add('dragging'), 0);
            });
            card.addEventListener('dragend', () => {
                card.classList.remove('dragging');
                document.querySelectorAll('.group-card').forEach(c => c.classList.remove('drag-over'));
                saveBlocksOrder();
            });
            card.addEventListener('dragover', (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (card !== draggedElement) {
                    card.classList.add('drag-over');
                }
            });
            card.addEventListener('dragleave', () => {
                card.classList.remove('drag-over');
            });
            card.addEventListener('drop', (e) => {
                e.preventDefault();
                card.classList.remove('drag-over');
                if (draggedElement && draggedElement !== card) {
                    let parent = card.parentNode;
                    let bounding = card.getBoundingClientRect();
                    let offset = bounding.y + (bounding.height / 2);
                    if (e.clientY - offset > 0) {
                        parent.insertBefore(draggedElement, card.nextSibling);
                    } else {
                        parent.insertBefore(draggedElement, card);
                    }
                    saveBlocksOrder();
                }
            });
        });
    }
}

function saveBlocksOrder() {
    let order = {
        content: [],
        grid: []
    };
    document.querySelectorAll('.content > .group-card').forEach(el => {
        if (el.dataset.group) order.content.push(el.dataset.group);
    });
    document.querySelectorAll('.group-card-grid > .group-card').forEach(el => {
        if (el.dataset.group) order.grid.push(el.dataset.group);
    });
    localStorage.setItem('hud_blocks_order', JSON.stringify(order));
}

function restoreBlocksOrder() {
    let saved = localStorage.getItem('hud_blocks_order');
    if (!saved) return;
    try {
        let order = JSON.parse(saved);
        let contentEl = document.querySelector('.content');
        let gridEl = document.querySelector('.group-card-grid');

        if (order.content && Array.isArray(order.content)) {
            order.content.forEach(groupName => {
                let card = document.querySelector(`.group-card[data-group="${groupName}"]`);
                if (card && contentEl && gridEl) {
                    contentEl.insertBefore(card, gridEl);
                }
            });
        }
        if (order.grid && Array.isArray(order.grid)) {
            order.grid.forEach(groupName => {
                let card = document.querySelector(`.group-card[data-group="${groupName}"]`);
                if (card && gridEl) {
                    gridEl.appendChild(card);
                }
            });
        }
    } catch (e) {
        console.error("Lỗi khôi phục vị trí HUD:", e);
    }
}

async function resetBlocksOrder() {
    const confirmed = await customConfirm("XÁC NHẬN SẮP XẾP", "Khôi phục lại thứ tự mặc định của các khối sự kiện?");
    if (confirmed) {
        localStorage.removeItem('hud_blocks_order');
        location.reload();
    }
}

/* ============ HỆ THỐNG CẤU HÌNH RULES & KỊCH BẢN TEST AI (RULE ENGINE) ============ */
let rulePlaylist = [];
let ruleEngineRunning = false;
let ruleCurrentStep = 0;
let ruleTimer = null;

function toggleRulePanel() {
    let bodyEl = document.getElementById('rule_panel_body');
    let iconEl = document.getElementById('rule_panel_icon');
    if (!bodyEl) return;
    if (bodyEl.style.display === 'none') {
        bodyEl.style.display = 'flex';
        if (iconEl) iconEl.innerText = '▼';
    } else {
        bodyEl.style.display = 'none';
        if (iconEl) iconEl.innerText = '►';
    }
}

function renderRulePlaylist() {
    try {
        let saved = localStorage.getItem('ai_test_playlist');
        if (saved) rulePlaylist = JSON.parse(saved);
    } catch (e) {
        rulePlaylist = [];
    }

    let box = document.getElementById('rule_playlist_box');
    let countSpan = document.getElementById('rule_step_count');
    if (!box) return;

    if (countSpan) countSpan.innerText = rulePlaylist.length;

    if (rulePlaylist.length === 0) {
        box.innerHTML = '<div style="color: var(--text-dim); font-size: 11px; text-align: center; padding: 12px 4px;">Chưa có sự kiện nào. Hãy thêm ở trên!</div>';
        updateSelectAllCheckboxState();
        return;
    }

    let html = '';
    rulePlaylist.forEach((step, idx) => {
        let activeCls = '';
        if (ruleEngineRunning && idx === ruleCurrentStep) activeCls = 'active-step';
        else if (ruleEngineRunning && idx < ruleCurrentStep) activeCls = 'done-step';

        let isChecked = step.selected ? 'checked' : '';
        html += `
        <div class="rule-item ${activeCls}">
            <div style="display: flex; align-items: center; gap: 6px; min-width: 0; flex: 1;">
                <input type="checkbox" class="rule-step-chk" data-idx="${idx}" ${isChecked} onchange="toggleRuleStepSelect(${idx}, this.checked)" style="accent-color: var(--neon-cyan); cursor: pointer; width: 14px; height: 14px; flex-shrink: 0;">
                <div class="rule-item-info">
                    <span class="rule-item-name">#${idx+1}. ${step.evLabel}</span>
                    <span class="rule-item-time">⏳ Chuẩn bị: ${step.prepTime}s | 🔴 Ghi: ${step.duration}s</span>
                </div>
            </div>
            <div class="rule-item-actions">
                ${idx > 0 ? `<button class="rule-btn-mini" onclick="moveRuleStep(${idx}, -1)" title="Lên">▲</button>` : ''}
                ${idx < rulePlaylist.length - 1 ? `<button class="rule-btn-mini" onclick="moveRuleStep(${idx}, 1)" title="Xuống">▼</button>` : ''}
                <button class="rule-btn-mini" onclick="removeRuleStep(${idx})" title="Xóa" style="color: #ff3b30;">✕</button>
            </div>
        </div>`;
    });
    box.innerHTML = html;
    updateSelectAllCheckboxState();
}

function toggleRuleStepSelect(idx, checked) {
    if (rulePlaylist[idx]) {
        rulePlaylist[idx].selected = checked;
        localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
    }
    updateSelectAllCheckboxState();
}

function toggleSelectAllRules(checked) {
    rulePlaylist.forEach(step => step.selected = checked);
    localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
    renderRulePlaylist();
}

function updateSelectAllCheckboxState() {
    let chkAll = document.getElementById("chk_rule_select_all");
    if (!chkAll) return;
    if (rulePlaylist.length === 0) {
        chkAll.checked = false;
    } else {
        chkAll.checked = rulePlaylist.every(step => step.selected);
    }
}

function cloneSelectedRules() {
    let selectedSteps = rulePlaylist.filter(step => step.selected);
    if (selectedSteps.length === 0) {
        showToast("⚠️ Vui lòng tick chọn ít nhất 1 sự kiện (hoặc Chọn tất cả) trước khi nhân bản!");
        return;
    }
    let multEl = document.getElementById("rule_clone_multiplier");
    let times = parseInt(multEl ? multEl.value : '1', 10) || 1;
    if (times < 1) times = 1;
    if (times > 50) times = 50;

    let newClones = [];
    for (let i = 0; i < times; i++) {
        selectedSteps.forEach(s => {
            newClones.push({
                eventKey: s.eventKey,
                evLabel: s.evLabel,
                prepTime: s.prepTime,
                duration: s.duration,
                selected: true
            });
        });
    }

    let lastSelIdx = -1;
    for (let i = rulePlaylist.length - 1; i >= 0; i--) {
        if (rulePlaylist[i].selected) {
            lastSelIdx = i;
            break;
        }
    }

    if (lastSelIdx >= 0) {
        rulePlaylist.splice(lastSelIdx + 1, 0, ...newClones);
    } else {
        rulePlaylist.push(...newClones);
    }

    localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
    renderRulePlaylist();
    showToast(`📑 Đã nhân bản nhóm ${selectedSteps.length} sự kiện lên ${times} lần (+${newClones.length} bước)!`);
}

async function deleteSelectedRules() {
    let selCount = rulePlaylist.filter(step => step.selected).length;
    if (selCount === 0) {
        showToast("⚠️ Vui lòng tick chọn ít nhất 1 sự kiện để xóa!");
        return;
    }
    const confirmed = await customConfirm("XÁC NHẬN XÓA SỰ KIỆN", `Bạn có chắc muốn xóa ${selCount} sự kiện đang được chọn không?`);
    if (confirmed) {
        rulePlaylist = rulePlaylist.filter(step => !step.selected);
        localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
        renderRulePlaylist();
        showToast(`🗑️ Đã xóa ${selCount} sự kiện đã chọn.`);
    }
}

function addRuleStep() {
    let selectEl = document.getElementById('rule_event_select');
    let prepEl = document.getElementById('rule_prep_time');
    let durEl = document.getElementById('rule_duration');
    let multAddEl = document.getElementById('rule_add_multiplier');
    if (!selectEl) return;

    let evKey = selectEl.value;
    let option = selectEl.options[selectEl.selectedIndex];
    let evLabel = option ? (option.getAttribute('data-label') || option.text.split(' [')[0]) : evKey;
    let prepTime = parseFloat(prepEl ? prepEl.value : '3') || 3;
    let duration = parseFloat(durEl ? durEl.value : '5') || 5;
    let addCount = parseInt(multAddEl ? multAddEl.value : '1', 10) || 1;
    if (addCount < 1) addCount = 1;
    if (addCount > 50) addCount = 50;

    for (let i = 0; i < addCount; i++) {
        rulePlaylist.push({
            eventKey: evKey,
            evLabel: evLabel,
            prepTime: prepTime,
            duration: duration,
            selected: false
        });
    }

    localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
    renderRulePlaylist();
    showToast(`➕ Đã thêm ${addCount > 1 ? addCount + ' x ' : ''}${evLabel} (${prepTime}s prep / ${duration}s rec)`);
}

function removeRuleStep(index) {
    if (index >= 0 && index < rulePlaylist.length) {
        rulePlaylist.splice(index, 1);
        localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
        renderRulePlaylist();
    }
}

function moveRuleStep(index, direction) {
    let newIndex = index + direction;
    if (newIndex >= 0 && newIndex < rulePlaylist.length) {
        let temp = rulePlaylist[index];
        rulePlaylist[index] = rulePlaylist[newIndex];
        rulePlaylist[newIndex] = temp;
        localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
        renderRulePlaylist();
    }
}

async function clearRulePlaylist() {
    if (rulePlaylist.length === 0) return;
    const confirmed = await customConfirm("XÁC NHẬN XÓA KỊCH BẢN", "Xóa toàn bộ kịch bản test trong danh sách?");
    if (confirmed) {
        rulePlaylist = [];
        localStorage.setItem('ai_test_playlist', JSON.stringify(rulePlaylist));
        renderRulePlaylist();
        showToast("🗑️ Đã xóa sạch danh sách kịch bản.");
    }
}

function manualStartRuleEngine() {
    if (rulePlaylist.length === 0) {
        showToast("⚠️ Danh sách kịch bản đang trống! Hãy thêm sự kiện trước.");
        return;
    }
    showToast("▶ Bắt đầu chạy thử nghiệm kịch bản AI...");
    startRuleEngine();
}

function startRuleEngine() {
    if (rulePlaylist.length === 0 || ruleEngineRunning) return;
    ruleEngineRunning = true;
    ruleCurrentStep = 0;

    let startBtn = document.getElementById("btn_rule_start");
    let stopBtn = document.getElementById("btn_rule_stop");
    if (startBtn) { startBtn.disabled = true; startBtn.style.opacity = "0.5"; }
    if (stopBtn) { stopBtn.disabled = false; stopBtn.style.opacity = "1"; }

    renderRulePlaylist();
    runNextStep();
}

function runNextStep() {
    if (!ruleEngineRunning) return;

    if (ruleCurrentStep >= rulePlaylist.length) {
        showBanner("state-done", "✅ HOÀN TẤT KỊCH BẢN TEST AI", `Đã thực hiện xong toàn bộ ${rulePlaylist.length} sự kiện!`, "Hoàn thành 100%", 100, null);
        playRuleBeep('done');
        setTimeout(() => {
            stopRuleEngine(false);
            hideBanner();
        }, 3500);
        return;
    }

    let step = rulePlaylist[ruleCurrentStep];
    let totalPrep = parseFloat(step.prepTime) || 3;
    let prepElapsed = 0;
    let stepNumberStr = `Bước ${ruleCurrentStep + 1} / ${rulePlaylist.length}`;

    let nextStepInfo = null;
    if (ruleCurrentStep + 1 < rulePlaylist.length) {
        let nStep = rulePlaylist[ruleCurrentStep + 1];
        nextStepInfo = `#${ruleCurrentStep + 2}. ${nStep.evLabel} (${nStep.duration}s)`;
    }

    let formatRem = (r, total) => (total % 1 !== 0) ? r.toFixed(1) : Math.ceil(r);

    // Giai đoạn 1: CHUẨN BỊ (ĐẾM NGƯỢC)
    showBanner("state-prep", `⏳ CHUẨN BỊ SAU ${formatRem(totalPrep, totalPrep)}s:`, `${step.evLabel}`, stepNumberStr, 0, nextStepInfo);
    playRuleBeep('prep');
    renderRulePlaylist();

    clearInterval(ruleTimer);
    let lastBeepSec = Math.ceil(totalPrep);

    ruleTimer = setInterval(() => {
        prepElapsed += 0.1;
        let rem = totalPrep - prepElapsed;
        let pct = Math.min(100, Math.max(0, (prepElapsed / totalPrep) * 100));

        let currentSec = Math.ceil(rem);
        if (currentSec !== lastBeepSec && currentSec > 0) {
            lastBeepSec = currentSec;
            playRuleBeep('prep');
        }

        if (rem > 0) {
            showBanner("state-prep", `⏳ CHUẨN BỊ SAU ${formatRem(rem, totalPrep)}s:`, `${step.evLabel}`, stepNumberStr, pct, nextStepInfo);
        } else {
            clearInterval(ruleTimer);

            // Giai đoạn 2: THU THẬP SỰ KIỆN (RECORDING)
            action('start', step.eventKey);
            playRuleBeep('start');

            let totalRec = parseFloat(step.duration) || 5;
            let recElapsed = 0;
            showBanner("state-recording", `🔴 ĐANG THU THẬP (${formatRem(totalRec, totalRec)}s):`, `${step.evLabel}`, stepNumberStr, 0, nextStepInfo);

            ruleTimer = setInterval(() => {
                recElapsed += 0.1;
                let remRec = totalRec - recElapsed;
                let pctRec = Math.min(100, Math.max(0, (recElapsed / totalRec) * 100));

                if (remRec > 0) {
                    showBanner("state-recording", `🔴 ĐANG THU THẬP (${formatRem(remRec, totalRec)}s):`, `${step.evLabel}`, stepNumberStr, pctRec, nextStepInfo);
                } else {
                    clearInterval(ruleTimer);
                    action('stop', step.eventKey);

                    ruleCurrentStep++;
                    runNextStep();
                }
            }, 100);
        }
    }, 100);
}

function stopRuleEngine(isAborted, abortMsg) {
    clearInterval(ruleTimer);
    ruleTimer = null;
    ruleEngineRunning = false;

    let startBtn = document.getElementById("btn_rule_start");
    let stopBtn = document.getElementById("btn_rule_stop");
    if (startBtn) { startBtn.disabled = false; startBtn.style.opacity = "1"; }
    if (stopBtn) { stopBtn.disabled = true; stopBtn.style.opacity = "0.5"; }

    if (isAborted) {
        if (rulePlaylist[ruleCurrentStep]) {
            action('stop', rulePlaylist[ruleCurrentStep].eventKey);
        }
        if (abortMsg) showToast(abortMsg);
        hideBanner();
    }
    renderRulePlaylist();
}

let lastBannerStateKey = "";

function showBanner(stateClass, statusText, eventText, progressText, pct = 100, nextStepInfo = null) {
    let banner = document.getElementById("rule_hud_banner");
    if (!banner) return;
    banner.style.display = "flex";
    banner.className = `rule-vertical-banner ${stateClass}`;

    // Cập nhật nội dung Dòng Trên (Active Row)
    let stEl = document.getElementById("rule_banner_status");
    let evEl = document.getElementById("rule_banner_event");
    let prEl = document.getElementById("rule_banner_step_badge");
    if (stEl) stEl.innerText = statusText;
    if (evEl) evEl.innerText = eventText;
    if (prEl && progressText) prEl.innerText = `[${progressText}]`;

    // Cập nhật độ rộng thanh tiến trình ngay dưới Dòng Trên
    let progBar = document.getElementById("rule_row_progress_bar");
    if (progBar) progBar.style.width = `${pct}%`;

    // Cập nhật Dòng Dưới (Anticipation / Next Row)
    let nextRowEl = document.getElementById("rule_row_next");
    let nextTextEl = document.getElementById("rule_next_step_text");
    if (nextRowEl && nextTextEl) {
        if (nextStepInfo) {
            nextTextEl.innerText = nextStepInfo;
            nextRowEl.style.display = "flex";
        } else if (stateClass === "state-done") {
            nextRowEl.style.display = "none";
        } else {
            nextTextEl.innerText = "🏁 Đây là bước cuối cùng trong kịch bản test.";
            nextRowEl.style.display = "flex";
        }
    }

    // Kích hoạt animation trượt dòng từ dưới lên (Roll Up Animation) khi chuyển giai đoạn hoặc chuyển bước
    let stateKey = `${stateClass}_${progressText}_${eventText}`;
    let stackEl = document.getElementById("rule_vertical_stack");
    if (stackEl && stateKey !== lastBannerStateKey) {
        lastBannerStateKey = stateKey;
        stackEl.classList.remove("roll-animate");
        void stackEl.offsetWidth; // Trigger reflow
        stackEl.classList.add("roll-animate");
    }
}

function hideBanner() {
    let banner = document.getElementById("rule_hud_banner");
    if (banner) banner.style.display = "none";
}

function playRuleBeep(type) {
    try {
        let ctx = new (window.AudioContext || window.webkitAudioContext)();
        let osc = ctx.createOscillator();
        let gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);

        if (type === 'prep') {
            osc.frequency.value = 600;
            gain.gain.setValueAtTime(0.08, ctx.currentTime);
            osc.start();
            osc.stop(ctx.currentTime + 0.08);
        } else if (type === 'start') {
            osc.frequency.value = 1200;
            gain.gain.setValueAtTime(0.12, ctx.currentTime);
            osc.start();
            osc.stop(ctx.currentTime + 0.25);
        } else if (type === 'done') {
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.12, ctx.currentTime);
            osc.start();
            setTimeout(() => {
                let osc2 = ctx.createOscillator();
                osc2.connect(gain);
                osc2.frequency.value = 1320;
                osc2.start();
                osc2.stop(ctx.currentTime + 0.3);
            }, 150);
            osc.stop(ctx.currentTime + 0.15);
        }
    } catch (e) {}
}

/* ============ MODAL GÁN NÚT PS5 ============ */
function openPs5AssignModal(evKey, evLabel, currentBtn) {
    document.getElementById("ps5AssignEvKey").value = evKey;
    document.getElementById("ps5AssignEventName").innerText = evLabel;
    
    // Đặt select đúng nút hiện tại hoặc mặc định ""
    let selectEl = document.getElementById("ps5AssignSelect");
    if (selectEl) {
        selectEl.value = currentBtn;
    }
    
    let modal = document.getElementById("ps5AssignModal");
    if (modal) {
        modal.style.display = "flex";
    }
}

function closePs5AssignModal() {
    let modal = document.getElementById("ps5AssignModal");
    if (modal) {
        modal.style.display = "none";
    }
}

function savePs5Assign() {
    let evKey = document.getElementById("ps5AssignEvKey").value;
    let btnCode = document.getElementById("ps5AssignSelect").value;
    
    fetch('/api/admin/events', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            is_edit: true,
            old_key: evKey,
            data: { EVENT_KEY: evKey, PS5_BUTTON: btnCode }
        })
    })
    .then(r => r.json())
    .then(res => {
        if(res.status === 'ok') {
            showToast("✅ Đã gán phím PS5 thành công!");
            closePs5AssignModal();
            // Reload để cập nhật lại giao diện và badge
            setTimeout(() => location.reload(), 500);
        } else {
            showToast("❌ Lỗi: " + (res.message || 'Không thể lưu.'));
        }
    })
    .catch(err => {
        showToast("❌ Lỗi kết nối máy chủ.");
    });
}

/* ================= TOGGLE SIDEBAR ================= */
function toggleMainSidebar() {
    let isCollapsed = document.body.classList.toggle('sidebar-collapsed');
    let tabIcon = document.getElementById('sidebar_tab');
    if (tabIcon) {
        if (window.innerWidth <= 768) {
            tabIcon.innerText = isCollapsed ? '▲' : '▼';
        } else {
            tabIcon.innerText = isCollapsed ? '◀' : '▶';
        }
    }
}

