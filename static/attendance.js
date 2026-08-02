// attendance.js
// Single camera page — automatically detects entry vs exit based on
// each person's attendance state today. Endpoint comes from a data
// attribute on the .card div.

document.addEventListener('DOMContentLoaded', function () {
    const card = document.querySelector('.card[data-endpoint]');
    if (!card) return; // not on an attendance page

    const ENDPOINT = card.dataset.endpoint;

    const video = document.getElementById('video');
    const overlay = document.getElementById('overlay');
    const canvas = document.getElementById('canvas');
    const startBtn = document.getElementById('startCam');
    const stopBtn = document.getElementById('stopCam');
    const resultDiv = document.getElementById('attendanceResult');
    const laserScan = document.getElementById('laserScan');
    const hudStatusPill = document.getElementById('hudStatusPill');

    const ctx = overlay.getContext('2d');
    const captureCtx = canvas.getContext('2d');

    let stream = null;
    let captureInterval = null;
    const NORMAL_INTERVAL_MS = 2000;  // normal polling rate
    const FAST_INTERVAL_MS = 250;     // fast rate while waiting for a blink
    let currentIntervalMs = NORMAL_INTERVAL_MS;

    const lastMessageByRoll = {};

    function syncCanvasSize() {
        if (!video || !video.videoWidth || !video.videoHeight) return;

        // Sync internal pixel dimensions of overlay and hidden capture canvas
        overlay.width = video.videoWidth;
        overlay.height = video.videoHeight;
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
    }

    video.addEventListener('loadedmetadata', syncCanvasSize);
    video.addEventListener('resize', syncCanvasSize);
    window.addEventListener('resize', syncCanvasSize);

    function updateHudPill(text, icon = '🔍', stateClass = 'scanning') {
        if (!hudStatusPill) return;
        hudStatusPill.className = `hud-status-pill ${stateClass}`;
        hudStatusPill.innerHTML = `<span class="pill-icon">${icon}</span> <span class="pill-text">${text}</span>`;
    }

    async function startCamera() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: true });
            video.srcObject = stream;
            video.onloadedmetadata = syncCanvasSize;

            if (laserScan) laserScan.classList.remove('paused');
            updateHudPill('Scanning for face...', '🔍', 'scanning');

            currentIntervalMs = NORMAL_INTERVAL_MS;
            captureInterval = setInterval(captureAndSend, currentIntervalMs);
        } catch (err) {
            console.error('Camera error:', err);
            if (typeof showToast === 'function') {
                showToast('Camera access denied or unavailable: ' + err.message, 'error');
            }
            if (laserScan) laserScan.classList.add('paused');
            updateHudPill('Camera Access Denied', '❌', 'error');
            resultDiv.innerHTML = '<p class="error">❌ Camera access denied or unavailable.</p>';
        }
    }

    function setCaptureRate(ms) {
        if (ms === currentIntervalMs) return;
        currentIntervalMs = ms;
        if (captureInterval) {
            clearInterval(captureInterval);
            captureInterval = setInterval(captureAndSend, currentIntervalMs);
        }
    }

    function stopCamera() {
        if (captureInterval) {
            clearInterval(captureInterval);
            captureInterval = null;
        }
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
            stream = null;
        }
        video.srcObject = null;
        ctx.clearRect(0, 0, overlay.width, overlay.height);

        if (laserScan) laserScan.classList.add('paused');
        updateHudPill('Camera Off', '⏹', 'scanning');
    }

    function captureAndSend() {
        if (!video.videoWidth || !video.videoHeight) return;

        syncCanvasSize();

        captureCtx.drawImage(video, 0, 0, canvas.width, canvas.height);
        const imageData = canvas.toDataURL('image/jpeg', 0.8);

        fetch(ENDPOINT, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image: imageData })
        })
            .then(res => res.json())
            .then(data => handleResponse(data))
            .catch(err => console.error('Fetch error:', err));
    }

    function handleResponse(data) {
        ctx.clearRect(0, 0, overlay.width, overlay.height);

        // Check for fire/smoke detection alert from backend
        if (data.fire_alert) {
            if (typeof triggerFireAlertUI === 'function') {
                const topCategory = (data.fire_boxes && data.fire_boxes.length > 0)
                    ? (data.fire_boxes[0].category || 'fire')
                    : 'fire';
                triggerFireAlertUI(false, topCategory);
            }
        }

        if (data.fire_boxes && data.fire_boxes.length > 0) {
            data.fire_boxes.forEach(fbox => drawFireBox(fbox));
        }

        if (data.error) {
            if (laserScan) laserScan.classList.remove('paused');
            updateHudPill('Error: ' + data.error, '❌', 'error');
            resultDiv.innerHTML = '<p class="error">❌ ' + data.error + '</p>';
            return;
        }

        if (!data.faces || data.faces.length === 0) {
            setCaptureRate(NORMAL_INTERVAL_MS);
            if (laserScan) laserScan.classList.remove('paused'); // resume laser scanning when searching
            updateHudPill('Scanning for face...', '🔍', 'scanning');

            if (data.message) {
                resultDiv.innerHTML = '<p>' + data.message + '</p>';
            }
            return;
        }

        // Face is actively detected -> Pause laser scanning to avoid distracting verification
        if (laserScan) laserScan.classList.add('paused');

        const anyPending = data.faces.some(f => f.status === 'liveness_pending');
        setCaptureRate(anyPending ? FAST_INTERVAL_MS : NORMAL_INTERVAL_MS);

        // Update HUD pill badge based on primary face status
        const primaryFace = data.faces[0];
        if (primaryFace.status === 'liveness_pending') {
            updateHudPill('Verifying Liveness (Please Blink)', '👁️', 'liveness');
        } else if (primaryFace.status === 'marked' && primaryFace.action === 'entry') {
            updateHudPill(`Entry Marked: ${primaryFace.name}`, '✅', 'success');
        } else if (primaryFace.status === 'marked' && primaryFace.action === 'exit') {
            updateHudPill(`Exit Marked: ${primaryFace.name}`, '🚪', 'success');
        } else if (primaryFace.status === 'already_marked') {
            updateHudPill(`Already Marked Today: ${primaryFace.name}`, '⏳', 'warning');
        } else if (primaryFace.status === 'unknown') {
            updateHudPill('Unknown Face Detected', '⚠️', 'error');
        }

        let messagesHtml = '';

        data.faces.forEach(face => {
            drawBox(face);

            if (face.status === 'liveness_pending' && typeof face.ear !== 'undefined') {
                console.log('[liveness] ' + face.name + ' EAR = ' + face.ear);
            }

            const key = face.roll_no || face.name || 'unknown';

            if (lastMessageByRoll[key] === face.message) return;
            lastMessageByRoll[key] = face.message;

            let cssClass = 'info';
            if (face.status === 'marked') cssClass = 'success';
            else if (face.status === 'already_marked') cssClass = 'warning';
            else if (face.status === 'liveness_pending') cssClass = 'info';
            else if (face.status === 'unknown') cssClass = 'error';

            messagesHtml += '<p class="' + cssClass + '">' + face.message + '</p>';
        });

        if (messagesHtml) {
            resultDiv.innerHTML = messagesHtml + resultDiv.innerHTML;
        }
    }

    function drawBox(face) {
        if (!face.box) return;
        const [top, right, bottom, left] = face.box;

        let color = '#888'; // unknown / default
        if (face.status === 'marked' && face.action === 'entry') color = '#22c55e';   // green = entry
        else if (face.status === 'marked' && face.action === 'exit') color = '#3b82f6'; // blue = exit
        else if (face.status === 'already_marked') color = '#f59e0b'; // amber = cooldown wait
        else if (face.status === 'liveness_pending') color = '#a855f7'; // purple = please blink

        ctx.strokeStyle = color;
        ctx.lineWidth = 3;
        ctx.strokeRect(left, top, right - left, bottom - top);

        const label = face.name || 'Unknown';
        ctx.font = '16px Outfit, sans-serif';
        const textWidth = ctx.measureText(label).width;

        ctx.fillStyle = color;
        ctx.fillRect(left, bottom, textWidth + 10, 22);

        ctx.fillStyle = '#000';
        ctx.fillText(label, left + 5, bottom + 16);
    }

    function drawFireBox(fbox) {
        if (!fbox.box) return;
        const [x1, y1, x2, y2] = fbox.box;
        ctx.strokeStyle = '#ef4444';
        ctx.lineWidth = 4;
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

        const label = `🔥 ${fbox.label || 'FIRE'} (${fbox.confidence || ''})`;
        ctx.font = 'bold 15px Outfit, sans-serif';
        const textWidth = ctx.measureText(label).width;

        ctx.fillStyle = '#ef4444';
        ctx.fillRect(x1, Math.max(y1 - 24, 0), textWidth + 10, 24);

        ctx.fillStyle = '#ffffff';
        ctx.fillText(label, x1 + 5, Math.max(y1 - 7, 17));
    }

    startBtn.addEventListener('click', startCamera);
    stopBtn.addEventListener('click', stopCamera);

    window.addEventListener('beforeunload', stopCamera);
});