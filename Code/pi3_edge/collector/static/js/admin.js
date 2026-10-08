let eventsData = [];

function switchAdminObject(objectFile) {
    if (!objectFile) return;
    showToast("🔄 Đang chuyển model object sang [" + objectFile + "]...");
    fetch('/api/set_object', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_file: objectFile })
    }).then(r => r.json()).then(res => {
        setTimeout(() => location.reload(), 300);
    });
}

// --- UI HELPERS (Modals & Toast) ---
function showToast(msg, isError = false) {
    const toast = document.getElementById("toast");
    if (!toast) return;
    toast.innerText = msg;
    toast.className = isError ? "show error" : "show";
    setTimeout(() => toast.className = toast.className.replace("show", ""), 2500);
}

function customConfirm(title, message) {
    return new Promise((resolve) => {
        const overlay = document.getElementById('customModal');
        document.getElementById('modalTitle').innerText = "⚠️ " + title;
        document.getElementById('modalTitle').style.color = "var(--amber)";
        document.getElementById('modalMessage').innerHTML = message;
        
        overlay.style.display = 'flex';
        document.getElementById('modalBtnCancel').style.display = 'inline-block';
        
        document.getElementById('modalBtnOk').onclick = () => { overlay.style.display = 'none'; resolve(true); };
        document.getElementById('modalBtnCancel').onclick = () => { overlay.style.display = 'none'; resolve(false); };
    });
}

function customAlert(title, message, type = "error") {
    const overlay = document.getElementById('customModal');
    const titleEl = document.getElementById('modalTitle');
    titleEl.innerText = (type === "error" ? "🛑 " : "💡 ") + title;
    titleEl.style.color = type === "error" ? "var(--red)" : "var(--cyan)";
    document.getElementById('modalMessage').innerHTML = message;
    
    overlay.style.display = 'flex';
    document.getElementById('modalBtnCancel').style.display = 'none';
    document.getElementById('modalBtnOk').onclick = () => overlay.style.display = 'none';
}

// --- DATA HANDLING ---
function loadData() {
    fetch('/api/admin/events').then(res => res.json()).then(data => {
        // SẮP XẾP SỰ KIỆN THEO EVENT_KEY TĂNG DẦN (A-Z)
        eventsData = data.sort((a, b) => {
            let keyA = (a.EVENT_KEY || "").toUpperCase();
            let keyB = (b.EVENT_KEY || "").toUpperCase();
            return keyA.localeCompare(keyB);
        });
        renderTable();
        updateDynamicLists();
    }).catch(err => customAlert("LỖI", "Không thể tải dữ liệu từ Server. Vui lòng kiểm tra Terminal."));
}

// Phím tắt Search
document.addEventListener('keydown', function(e) {
    if (e.shiftKey && e.code === 'Space') {
        e.preventDefault();
        const searchInput = document.getElementById('searchInput');
        if (searchInput) searchInput.focus();
    }
});

document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById('searchInput');
    if (searchInput) searchInput.addEventListener('input', applySearch);
});

function applySearch() {
    const searchInput = document.getElementById('searchInput');
    const filterGroup = document.getElementById('filterGroup');
    const filterConflict = document.getElementById('filterConflict');
    if (!searchInput || !filterGroup || !filterConflict) return;

    const query = searchInput.value.toLowerCase();
    const fGroup = filterGroup.value;
    const fConflict = filterConflict.value;

    const rows = document.querySelectorAll('#eventTable tbody tr');
    rows.forEach(row => {
        const text = row.getAttribute('data-search') || '';
        const rowGroup = row.getAttribute('data-group') || '';
        const rowConflict = row.getAttribute('data-conflict') || '';

        let isMatch = true;
        if (query && !text.toLowerCase().includes(query)) isMatch = false;
        if (fGroup && rowGroup !== fGroup) isMatch = false;
        if (fConflict && rowConflict !== fConflict) isMatch = false;

        row.style.display = isMatch ? '' : 'none';
    });
}

function updateDynamicLists() {
    let groups = new Set();
    let conflicts = new Set();
    
    eventsData.forEach(ev => {
        if(ev.GROUP && ev.GROUP.trim() !== '') groups.add(ev.GROUP.trim());
        if(ev.CONFLICT_SET && ev.CONFLICT_SET.trim() !== '') conflicts.add(ev.CONFLICT_SET.trim());
    });

    const sortedGroups = Array.from(groups).sort();
    const sortedConflicts = Array.from(conflicts).sort();

    const groupList = document.getElementById('groupList');
    const editGroupSelect = document.getElementById('editGroupSelect');
    const filterGroup = document.getElementById('filterGroup');
    
    if (groupList) groupList.innerHTML = ''; 
    if (editGroupSelect) editGroupSelect.innerHTML = '<option value="">-- Chọn Nhóm Cần Sửa --</option>';
    if (filterGroup) filterGroup.innerHTML = '<option value="">-- Tất cả Nhóm UI --</option>';
    
    sortedGroups.forEach(val => {
        if (groupList) groupList.innerHTML += `<option value="${val}">`;
        if (editGroupSelect) editGroupSelect.innerHTML += `<option value="${val}">${val}</option>`;
        if (filterGroup) filterGroup.innerHTML += `<option value="${val}">${val}</option>`;
    });

    const conflictList = document.getElementById('conflictList');
    const editConflictSelect = document.getElementById('editConflictSelect');
    const filterConflict = document.getElementById('filterConflict');
    
    if (conflictList) conflictList.innerHTML = ''; 
    if (editConflictSelect) editConflictSelect.innerHTML = '<option value="">-- Chọn Tập Xung Đột Cần Sửa --</option>';
    if (filterConflict) filterConflict.innerHTML = '<option value="">-- Tất cả Xung Đột --</option>';
    
    sortedConflicts.forEach(val => {
        if (conflictList) conflictList.innerHTML += `<option value="${val}">`;
        if (editConflictSelect) editConflictSelect.innerHTML += `<option value="${val}">${val}</option>`;
        if (filterConflict) filterConflict.innerHTML += `<option value="${val}">${val}</option>`;
    });
}

function renameTarget(targetType, selectId, inputId) {
    const oldName = document.getElementById(selectId).value;
    const newName = document.getElementById(inputId).value.trim();
    
    if(!oldName) return customAlert("LỖI CHỌN", "Vui lòng chọn 1 mục từ danh sách thả xuống để sửa!");
    if(!newName) return customAlert("THIẾU THÔNG TIN", "Vui lòng nhập tên mới vào ô text!");
    if(oldName === newName) return customAlert("TRÙNG LẶP", "Tên mới giống hệt tên cũ, không có gì thay đổi!");
    
    const typeName = targetType === 'GROUP' ? 'Nhóm UI' : 'Tập Xung Đột';
    
    customConfirm("ĐỔI TÊN HÀNG LOẠT", `Bạn có chắc muốn đổi toàn bộ <b>${typeName}</b><br><br>Từ: <span style="color:var(--red)">${oldName}</span><br>Thành: <span style="color:var(--green)">${newName}</span> ?`).then(yes => {
        if(yes) {
            fetch('/api/admin/rename_group', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ target: targetType, old_name: oldName, new_name: newName })
            }).then(res => res.json()).then(res => {
                if(res.status === 'ok') {
                    document.getElementById(inputId).value = '';
                    showToast(`Đã đổi tên thành công!`);
                    loadData();
                }
                else customAlert("LỖI SERVER", res.message);
            });
        }
    });
}

function renderTable() {
    const tbody = document.querySelector('#eventTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    eventsData.forEach(row => {
        let isVisible = (row.VISIBLE !== 'False' && row.VISIBLE !== 'false' && row.VISIBLE !== '0');
        let tr = document.createElement('tr');
        
        tr.setAttribute('data-search', `${row.EVENT_KEY} ${row.LABEL} ${row.GROUP} ${row.CONFLICT_SET}`);
        tr.setAttribute('data-group', row.GROUP || '');
        tr.setAttribute('data-conflict', row.CONFLICT_SET || '');

        let toggleHtml = `
            <div style="display:flex; align-items:center; gap:10px;">
                <label class="switch">
                    <input type="checkbox" ${isVisible ? 'checked' : ''} onchange="toggleInline('${row.EVENT_KEY}', this.checked)">
                    <span class="slider"></span>
                </label>
                <span style="font-size: 11px; font-weight: bold; color: ${isVisible ? 'var(--green)' : 'var(--text-dim)'}">
                    ${isVisible ? 'TRÊN HUD' : 'ĐÃ TẮT'}
                </span>
            </div>
        `;

        tr.innerHTML = `
            <td>${toggleHtml}</td>
            <td style="color: var(--amber); font-weight: 600;">${row.EVENT_KEY}</td>
            <td>${row.LABEL}</td>
            <td style="color: var(--cyan);">${row.GROUP}</td>
            <td style="color: var(--red);">${row.CONFLICT_SET || '-'}</td>
            <td style="color: #4facfe; font-weight: bold;">${row.PS5_BUTTON || ''}</td>
            <td class="action-cell">
                <button class="btn" style="padding: 4px 8px; font-size: 11px;" onclick="editEvent('${row.EVENT_KEY}')">Sửa</button>
                <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="deleteEvent('${row.EVENT_KEY}')">Xóa</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    applySearch(); 
}

function toggleInline(key, isChecked) {
    const ev = eventsData.find(e => e.EVENT_KEY === key);
    ev.VISIBLE = isChecked ? 'True' : 'False';
    
    const payload = { old_key: key, is_edit: true, data: ev };
    fetch('/api/admin/events', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(res => res.json()).then(res => {
        if(res.status === 'ok') renderTable();
        else { customAlert("LỖI", res.message); loadData(); }
    });
}

async function toggleAll(state) {
    let actionStr = state ? 'BẬT' : 'TẮT';
    let yes = await customConfirm(`XÁC NHẬN ${actionStr} TẤT CẢ`, `Bạn có chắc muốn <b>${actionStr}</b> toàn bộ sự kiện trên giao diện HUD chính không?`);
    if(!yes) return;
    
    const val = state ? 'True' : 'False';
    document.body.style.cursor = 'wait';
    showToast(`Đang ${actionStr.toLowerCase()} dữ liệu, vui lòng chờ...`);
    
    for (let ev of eventsData) {
        let currentVisible = (ev.VISIBLE !== 'False' && ev.VISIBLE !== 'false' && ev.VISIBLE !== '0');
        if (currentVisible === state) continue; 
        
        ev.VISIBLE = val;
        let payload = { old_key: ev.EVENT_KEY, is_edit: true, data: ev };
        await fetch('/api/admin/events', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
    }
    
    document.body.style.cursor = 'default';
    showToast(`Đã ${actionStr} tất cả thành công!`);
    loadData(); 
}

function editEvent(key) {
    const ev = eventsData.find(e => e.EVENT_KEY === key);
    if(!ev) return;
    document.getElementById('evKey').value = ev.EVENT_KEY;
    document.getElementById('evLabel').value = ev.LABEL;
    document.getElementById('evGroup').value = ev.GROUP || '';
    document.getElementById('evConflict').value = ev.CONFLICT_SET || '';
    document.getElementById('evPs5').value = ev.PS5_BUTTON || '';
    
    document.getElementById('editMode').value = "true";
    document.getElementById('oldKey').value = ev.EVENT_KEY;
    document.getElementById('formTitle').innerHTML = "📝 Sửa Sự Kiện";
    document.getElementById('formTitle').style.color = "var(--amber)";
    window.scrollTo({ top: 0, behavior: 'smooth' }); 
}

function resetForm() {
    document.getElementById('evKey').value = '';
    document.getElementById('evLabel').value = '';
    document.getElementById('evGroup').value = '';
    document.getElementById('evConflict').value = '';
    document.getElementById('evPs5').value = '';
    
    document.getElementById('editMode').value = "false";
    document.getElementById('oldKey').value = '';
    document.getElementById('formTitle').innerHTML = "➕ Thêm Sự Kiện Mới";
    document.getElementById('formTitle').style.color = "var(--cyan)";
}

function saveEvent() {
    const isEdit = document.getElementById('editMode').value === "true";
    const oldKey = document.getElementById('oldKey').value;
    const evKey = document.getElementById('evKey').value.trim().toUpperCase();
    
    let visibleStatus = 'True';
    if (isEdit) {
        const ev = eventsData.find(e => e.EVENT_KEY === oldKey);
        if (ev) visibleStatus = ev.VISIBLE || 'True';
    }

    const payload = {
        old_key: oldKey,
        is_edit: isEdit,
        data: {
            EVENT_KEY: evKey,
            LABEL: document.getElementById('evLabel').value.trim(),
            GROUP: document.getElementById('evGroup').value.trim(),
            CONFLICT_SET: document.getElementById('evConflict').value.trim(),
            PS5_BUTTON: document.getElementById('evPs5').value.trim(),
            VISIBLE: visibleStatus
        }
    };

    if(!payload.data.EVENT_KEY || !payload.data.LABEL) {
        return customAlert("THIẾU THÔNG TIN", "Vui lòng nhập đầy đủ Mã sự kiện (KEY) và Tên hiển thị (LABEL)!");
    }

    fetch('/api/admin/events', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    }).then(res => res.json()).then(res => {
        if(res.status === 'ok') {
            showToast("Đã lưu sự kiện thành công!");
            resetForm();
            loadData();
        } else { customAlert("LỖI LƯU DỮ LIỆU", res.message); }
    });
}

function deleteEvent(key) {
    customConfirm("XÓA SỰ KIỆN", `Bạn có chắc chắn muốn xóa vĩnh viễn sự kiện <b style="color:var(--red)">${key}</b> không? Dữ liệu này sẽ không thể khôi phục.`).then(yes => {
        if(yes) {
            fetch('/api/admin/events/' + key, { method: 'DELETE' }).then(res => res.json()).then(res => {
                if(res.status === 'ok') {
                    showToast(`Đã xóa ${key}`);
                    loadData();
                }
            });
        }
    });
}

window.onload = loadData;
