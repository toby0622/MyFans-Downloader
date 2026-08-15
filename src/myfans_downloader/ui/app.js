'use strict';

let eventCursor = 0;
let applicationReady = false;
let eventTimer = null;
let statusTimer = null;

const byId = (id) => document.getElementById(id);

function showToast(message, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = String(message);
    byId('toastContainer').appendChild(toast);
    window.setTimeout(() => toast.remove(), 3500);
}

function setRunning(running) {
    const downloadButton = byId('downloadBtn');
    downloadButton.disabled = running || !applicationReady;
    downloadButton.textContent = running ? 'DOWNLOADING...' : 'DOWNLOAD';
    byId('stopBtn').disabled = !running || !applicationReady;
}

function handleApplicationDisconnect() {
    if (!applicationReady) return;
    applicationReady = false;
    document.documentElement.dataset.applicationReady = 'false';
    setRunning(false);
    if (eventTimer) window.clearInterval(eventTimer);
    if (statusTimer) window.clearInterval(statusTimer);
    showToast('The application connection was interrupted.', 'error');
}

function appendLog(message) {
    if (message === 'DONE') {
        setRunning(false);
        return;
    }
    const row = document.createElement('div');
    const timestamp = document.createElement('span');
    timestamp.className = 'log-timestamp';
    timestamp.textContent = `[${new Date().toLocaleTimeString()}] `;
    const text = document.createElement('span');
    text.className = /^error:/i.test(message) ? 'log-error' : 'log-info';
    text.textContent = message;
    row.append(timestamp, text);
    const progress = byId('progress');
    progress.appendChild(row);
    progress.scrollTop = progress.scrollHeight;
}

function standbyCard() {
    const card = document.createElement('div');
    card.className = 'queue-item standby';
    const id = document.createElement('div');
    id.className = 'queue-id';
    id.textContent = 'SYSTEM';
    const status = document.createElement('div');
    status.className = 'queue-status';
    status.textContent = 'STANDBY';
    card.append(id, status);
    return card;
}

function queueCard(postId, info, fetching = false) {
    const card = document.createElement('div');
    card.className = 'queue-item';
    const id = document.createElement('div');
    id.className = 'queue-id';
    id.textContent = fetching ? 'SYSTEM' : `POST #${postId}`;
    const status = document.createElement('div');
    status.className = 'queue-status';

    const downloaded = Number(info.segments_downloaded || 0);
    const total = Number(info.segments_total || 0);
    const percentage = total > 0 ? Math.min(100, Math.round(downloaded / total * 100)) : (info.status === 'completed' ? 100 : 0);
    status.textContent = fetching
        ? `FETCHING POSTS${downloaded > 0 ? ` [PAGE ${downloaded}]` : ''}`
        : `${String(info.status || 'pending').toUpperCase()}${total > 0 ? ` [${downloaded}/${total}] ${percentage}%` : ''}`;

    const bar = document.createElement('div');
    bar.className = 'queue-progress-bar';
    const fill = document.createElement('div');
    fill.className = fetching ? 'queue-progress-fill fetching-animation' : 'queue-progress-fill';
    if (!fetching) fill.style.width = `${percentage}%`;
    bar.appendChild(fill);
    card.append(id, status, bar);
    return card;
}

function renderQueue(statusData) {
    const downloads = statusData && statusData.downloads ? statusData.downloads : {};
    const fetchList = byId('fetchProgressList');
    const queueList = byId('queueList');
    fetchList.replaceChildren();
    queueList.replaceChildren();

    let fetchCount = 0;
    let downloadCount = 0;
    Object.entries(downloads).forEach(([postId, info]) => {
        if (info.status === 'fetching') {
            fetchList.appendChild(queueCard(postId, info, true));
            fetchCount += 1;
        } else {
            queueList.appendChild(queueCard(postId, info));
            downloadCount += 1;
        }
    });
    if (!fetchCount) fetchList.appendChild(standbyCard());
    if (!downloadCount) queueList.appendChild(standbyCard());
}

function fillSettings(settings) {
    byId('filenamePattern').value = settings.filename_pattern || '';
    byId('filenameSeparator').value = settings.filename_separator || '';
    byId('writeMetadata').value = String(settings.write_metadata || 0);
    byId('threadCount').value = String(settings.thread_count || 10);
    byId('outputDir').value = settings.output_dir || '';
    byId('authToken').value = '';
    byId('authToken').placeholder = settings.auth_token_set
        ? 'Token is set (enter a new value to replace it)'
        : 'Enter your MyFans token';
    byId('tokenStatus').textContent = settings.auth_token_set ? 'Token configured' : 'No token configured';
}

function updateFormOptions() {
    const isVideo = byId('type').value === 'videos';
    const downloadType = byId('downloadType');
    downloadType.options[0].textContent = isVideo ? 'Download All Videos' : 'Download All Images';
    downloadType.options[1].textContent = isVideo ? 'Download Single Video' : 'Download Single Image';
    byId('resolutionGroup').classList.toggle('hidden', !isVideo);
}

function toggleInputs() {
    const single = byId('downloadType').value === 'single';
    byId('bulkDownload').classList.toggle('hidden', single);
    byId('singleDownload').classList.toggle('hidden', !single);
    byId('username').required = !single;
    byId('postId').required = single;
}

async function pollEvents() {
    if (!applicationReady) return;
    try {
        const result = await window.pywebview.api.get_events(eventCursor);
        (result.events || []).forEach((event) => {
            eventCursor = Math.max(eventCursor, Number(event.id));
            appendLog(String(event.message));
        });
        setRunning(Boolean(result.running));
    } catch (error) {
        handleApplicationDisconnect();
    }
}

async function pollStatus() {
    if (!applicationReady) return;
    try {
        const result = await window.pywebview.api.get_status();
        renderQueue(result.state);
        setRunning(Boolean(result.running));
    } catch (error) {
        handleApplicationDisconnect();
    }
}

async function initializeApplication() {
    try {
        const data = await window.pywebview.api.bootstrap();
        applicationReady = true;
        document.documentElement.dataset.applicationReady = 'true';
        fillSettings(data.settings);
        renderQueue(data.status);
        byId('dataDir').textContent = data.data_dir;
        setRunning(Boolean(data.running));
        eventTimer = window.setInterval(pollEvents, 300);
        statusTimer = window.setInterval(pollStatus, 1000);
        await pollEvents();
    } catch (error) {
        applicationReady = false;
        document.documentElement.dataset.applicationReady = 'false';
        setRunning(false);
        showToast(`Application startup failed: ${error.message || error}`, 'error');
    }
}

document.querySelectorAll('.nav-link').forEach((button) => {
    button.addEventListener('click', async () => {
        document.querySelectorAll('.nav-link').forEach((item) => item.classList.remove('active'));
        document.querySelectorAll('.page').forEach((page) => page.classList.remove('active'));
        button.classList.add('active');
        byId(button.dataset.page).classList.add('active');
        if (button.dataset.page === 'settingsPage' && applicationReady) {
            fillSettings(await window.pywebview.api.get_settings());
        }
    });
});

byId('type').addEventListener('change', updateFormOptions);
byId('downloadType').addEventListener('change', toggleInputs);

byId('downloadForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!applicationReady) return;
    const single = byId('downloadType').value === 'single';
    const payload = {
        username: single ? null : byId('username').value,
        post_id: single ? byId('postId').value : null,
        type: byId('type').value,
        download_type: single ? 'single' : byId('downloadFilter').value,
        resolution: byId('resolution').value,
    };
    byId('progress').replaceChildren();
    setRunning(true);
    const result = await window.pywebview.api.start_download(payload);
    if (!result.ok) {
        setRunning(false);
        appendLog(`Error: ${result.error}`);
        showToast(result.error, 'error');
    }
});

byId('stopBtn').addEventListener('click', async () => {
    if (applicationReady) await window.pywebview.api.stop_download();
});

byId('openDownloadsBtn').addEventListener('click', async () => {
    if (!applicationReady) return;
    const result = await window.pywebview.api.open_output_directory();
    if (!result.ok) showToast(result.error, 'error');
});

byId('browseOutputBtn').addEventListener('click', async () => {
    if (!applicationReady) return;
    const result = await window.pywebview.api.choose_output_directory();
    if (result.ok && !result.cancelled) byId('outputDir').value = result.path;
    if (!result.ok) showToast(result.error, 'error');
});

byId('settingsForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!applicationReady) return;
    const payload = {
        filename_pattern: byId('filenamePattern').value,
        filename_separator: byId('filenameSeparator').value,
        write_metadata: byId('writeMetadata').value,
        thread_count: Number.parseInt(byId('threadCount').value, 10),
        output_dir: byId('outputDir').value,
        auth_token: byId('authToken').value,
    };
    const result = await window.pywebview.api.save_settings(payload);
    if (result.ok) {
        fillSettings(result.settings);
        showToast('Settings saved successfully.');
    } else {
        showToast(result.error, 'error');
    }
});

window.addEventListener('pywebviewready', initializeApplication, {once: true});
window.addEventListener('beforeunload', () => {
    if (eventTimer) window.clearInterval(eventTimer);
    if (statusTimer) window.clearInterval(statusTimer);
});

updateFormOptions();
toggleInputs();
renderQueue({downloads: {}});
setRunning(false);
