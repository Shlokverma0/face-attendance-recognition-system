// exit.js
// This page tells the Flask server to start/stop reading a phone's RTSP stream
// in a background thread, polls /exit-camera/status to update detection status
// and displays live preview snapshots with HUD badge.

document.addEventListener('DOMContentLoaded', function () {
    const rtspInput = document.getElementById('rtspUrl');
    const startBtn = document.getElementById('startExitCam');
    const stopBtn = document.getElementById('stopExitCam');
    const statusDiv = document.getElementById('exitCamStatus');
    const detailsDiv = document.getElementById('exitDetails');
    const resultDiv = document.getElementById('attendanceResult');
    const previewContainer = document.getElementById('rtspPreviewContainer');
    const previewImg = document.getElementById('rtspPreview');
    const rtspHudPill = document.getElementById('rtspHudPill');
    const rtspLaserScan = document.getElementById('rtspLaserScan');

    if (!startBtn) return; // not on the exit page

    let pollTimer = null;
    const lastMessageByRoll = {};

    function updateHudPill(text, icon = '📡', stateClass = 'scanning') {
        if (!rtspHudPill) return;
        rtspHudPill.className = `hud-status-pill ${stateClass}`;
        rtspHudPill.innerHTML = `<span class="pill-icon">${icon}</span> <span class="pill-text">${text}</span>`;
    }

    async function pollStatus() {
        try {
            const res = await fetch('/exit-camera/status');
            const data = await res.json();
            handleStatus(data);
        } catch (err) {
            console.error('Status poll error:', err);
        }
    }

    function startPolling() {
        if (pollTimer) return;
        pollTimer = setInterval(pollStatus, 1000);
        pollStatus(); // fetch immediately instead of waiting a full second
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        if (previewContainer) {
            previewContainer.style.display = 'none';
        }
        if (rtspLaserScan) rtspLaserScan.classList.add('paused');
        updateHudPill('RTSP Stream Stopped', '⏹', 'scanning');
    }

    function handleStatus(data) {
        if (data.frame && previewContainer && previewImg) {
            previewImg.src = data.frame;
            previewContainer.style.display = 'block';
        }

        if (data.fire_alert) {
            if (typeof triggerFireAlertUI === 'function') {
                triggerFireAlertUI();
            }
        }

        if (data.error) {
            statusDiv.className = 'error';
            statusDiv.textContent = '❌ ' + data.error;
            if (rtspLaserScan) rtspLaserScan.classList.add('paused');
            updateHudPill('Stream Error', '❌', 'error');
            return;
        }

        if (!data.faces || data.faces.length === 0) {
            statusDiv.className = 'info';
            statusDiv.textContent = data.message || 'Watching for a face...';
            if (rtspLaserScan) rtspLaserScan.classList.remove('paused'); // resume laser scanning
            updateHudPill('RTSP Active — Watching for face', '📡', 'scanning');
            return;
        }

        // Face detected -> pause laser scan animation to avoid distraction during verification
        if (rtspLaserScan) rtspLaserScan.classList.add('paused');

        statusDiv.className = 'info';
        statusDiv.textContent = 'Camera active — watching for faces.';

        const primaryFace = data.faces[0];
        if (primaryFace.status === 'liveness_pending') {
            updateHudPill('Verifying Liveness (Please Blink)', '👁️', 'liveness');
        } else if (primaryFace.status === 'marked') {
            updateHudPill(`Exit Marked: ${primaryFace.name}`, '🚪', 'success');
        } else if (primaryFace.status === 'already_exited') {
            updateHudPill(`Already Exited Today: ${primaryFace.name}`, '⏳', 'warning');
        } else if (primaryFace.status === 'no_entry') {
            updateHudPill(`No Entry Found: ${primaryFace.name}`, '⚠️', 'warning');
        } else if (primaryFace.status === 'unknown') {
            updateHudPill('Unknown Face Detected', '⚠️', 'error');
        }

        let messagesHtml = '';

        data.faces.forEach(face => {
            const key = face.roll_no || face.name || 'unknown';

            if (face.status === 'liveness_pending' && typeof face.ear !== 'undefined') {
                console.log('[liveness] ' + face.name + ' EAR = ' + face.ear);
            }

            if (lastMessageByRoll[key] === face.message) return;
            lastMessageByRoll[key] = face.message;

            let cssClass = 'info';
            if (face.status === 'marked') cssClass = 'success';
            else if (face.status === 'already_exited') cssClass = 'warning';
            else if (face.status === 'no_entry') cssClass = 'warning';
            else if (face.status === 'liveness_pending') cssClass = 'info';
            else if (face.status === 'unknown' || face.status === 'error') cssClass = 'error';

            messagesHtml += '<p class="' + cssClass + '">' + face.message + '</p>';

            if (face.roll_no) {
                detailsDiv.innerHTML =
                    '<div class="exit-detail-card">' +
                    '<p><strong>Employee Name:</strong> ' + face.name + '</p>' +
                    '<p><strong>Employee ID:</strong> ' + face.roll_no + '</p>' +
                    '<p><strong>Entry Time:</strong> ' + (face.entry_time || '-') + '</p>' +
                    '<p><strong>Exit Time:</strong> ' + (face.exit_time || '-') + '</p>' +
                    '<p><strong>Status:</strong> ' + (face.emp_status || '-') + '</p>' +
                    '</div>';
            }
        });

        if (messagesHtml) {
            resultDiv.innerHTML = messagesHtml + resultDiv.innerHTML;
        }
    }

    startBtn.addEventListener('click', async function () {
        const rtspUrl = (rtspInput.value || '').trim();
        if (!rtspUrl) {
            showToast("Please enter the RTSP URL shown in your phone's camera app first.", 'warning');
            return;
        }

        statusDiv.className = 'info';
        statusDiv.textContent = 'Connecting to camera...';
        updateHudPill('Connecting to RTSP Stream...', '⏳', 'scanning');

        try {
            const res = await fetch('/exit-camera/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rtsp_url: rtspUrl })
            });
            const data = await res.json();

            if (!data.success) {
                statusDiv.className = 'error';
                statusDiv.textContent = '❌ ' + data.message;
                updateHudPill('Connection Failed', '❌', 'error');
                if (typeof showToast === 'function') {
                    showToast(data.message, 'error');
                }
                return;
            }

            if (typeof showToast === 'function') {
                showToast('RTSP Exit Camera connected!', 'success');
            }
            if (rtspLaserScan) rtspLaserScan.classList.remove('paused');
            startPolling();
        } catch (err) {
            console.error('Start exit camera error:', err);
            statusDiv.className = 'error';
            statusDiv.textContent = '❌ Could not reach the server.';
            updateHudPill('Server Unreachable', '❌', 'error');
            if (typeof showToast === 'function') {
                showToast('Could not reach the server.', 'error');
            }
        }
    });

    stopBtn.addEventListener('click', async function () {
        stopPolling();
        try {
            const res = await fetch('/exit-camera/stop', { method: 'POST' });
            const data = await res.json();
            statusDiv.className = 'info';
            statusDiv.textContent = data.message || 'Camera stopped.';
            if (typeof showToast === 'function') {
                showToast('Exit camera stopped', 'info');
            }
        } catch (err) {
            console.error('Stop exit camera error:', err);
        }
    });

    window.addEventListener('beforeunload', stopPolling);
});
