// exit.js
// This page no longer touches the browser's own camera. Instead it tells
// the Flask server to start/stop reading a phone's RTSP stream in a
// background thread, and polls /exit-camera/status to show whatever that
// background worker last recognized.

document.addEventListener('DOMContentLoaded', function () {
    const rtspInput = document.getElementById('rtspUrl');
    const startBtn = document.getElementById('startExitCam');
    const stopBtn = document.getElementById('stopExitCam');
    const statusDiv = document.getElementById('exitCamStatus');
    const detailsDiv = document.getElementById('exitDetails');
    const resultDiv = document.getElementById('attendanceResult');

    if (!startBtn) return; // not on the exit page

    let pollTimer = null;
    const lastMessageByRoll = {};

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
    }

    function handleStatus(data) {
        if (data.error) {
            statusDiv.className = 'error';
            statusDiv.textContent = '❌ ' + data.error;
            return;
        }

        if (!data.faces || data.faces.length === 0) {
            statusDiv.className = 'info';
            statusDiv.textContent = data.message || 'Watching for a face...';
            return;
        }

        statusDiv.className = 'info';
        statusDiv.textContent = 'Camera active — watching for faces.';

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
            alert("Please enter the RTSP URL shown in your phone's camera app first.");
            return;
        }

        statusDiv.className = 'info';
        statusDiv.textContent = 'Connecting to camera...';

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
                return;
            }

            startPolling();
        } catch (err) {
            console.error('Start exit camera error:', err);
            statusDiv.className = 'error';
            statusDiv.textContent = '❌ Could not reach the server.';
        }
    });

    stopBtn.addEventListener('click', async function () {
        stopPolling();
        try {
            const res = await fetch('/exit-camera/stop', { method: 'POST' });
            const data = await res.json();
            statusDiv.className = 'info';
            statusDiv.textContent = data.message || 'Camera stopped.';
        } catch (err) {
            console.error('Stop exit camera error:', err);
        }
    });

    window.addEventListener('beforeunload', stopPolling);
});
