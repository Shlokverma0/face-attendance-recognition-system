// ---------- GLOBAL UI UTILITIES (TOAST & MODAL) ----------
function showToast(message, type = 'info') {
    let container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;

    let icon = 'ℹ️';
    if (type === 'success') icon = '✅';
    if (type === 'error') icon = '❌';
    if (type === 'warning') icon = '⚠️';

    toast.innerHTML = `<span class="toast-icon">${icon}</span><span class="toast-message">${message}</span><span class="toast-close">&times;</span>`;

    container.appendChild(toast);

    setTimeout(() => toast.classList.add('show'), 10);

    const removeToast = () => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    };

    toast.querySelector('.toast-close').addEventListener('click', removeToast);
    setTimeout(removeToast, 4000);
}

function showConfirmModal(options) {
    return new Promise((resolve) => {
        let title = 'Confirm Action';
        let message = '';
        let confirmText = 'Confirm';
        let danger = true;

        if (typeof options === 'string') {
            message = options;
        } else if (typeof options === 'object') {
            title = options.title || title;
            message = options.message || message;
            confirmText = options.confirmText || confirmText;
            if (options.danger !== undefined) danger = options.danger;
        }

        const backdrop = document.createElement('div');
        backdrop.className = 'modal-backdrop';

        backdrop.innerHTML = `
            <div class="modal-card">
                <h3>${title}</h3>
                <p>${message}</p>
                <div class="modal-actions">
                    <button class="btn secondary modal-cancel">Cancel</button>
                    <button class="btn ${danger ? 'danger' : 'primary'} modal-confirm">${confirmText}</button>
                </div>
            </div>
        `;

        document.body.appendChild(backdrop);
        setTimeout(() => backdrop.classList.add('show'), 10);

        const close = (result) => {
            backdrop.classList.remove('show');
            setTimeout(() => backdrop.remove(), 300);
            resolve(result);
        };

        backdrop.querySelector('.modal-cancel').addEventListener('click', () => close(false));
        backdrop.querySelector('.modal-confirm').addEventListener('click', () => close(true));
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) close(false);
        });
    });
}

// ---------- FIRE ALARM AUDIBLE SIREN & RE-ARM STATE TRACKING ----------
let fireAudioCtx = null;
let fireSirenInterval = null;
let fireAlarmActive = false;
let fireDismissedAt = 0;
const REARM_WINDOW_MS = 30000; // Requirement 2: 30-second re-arm window

function playFireAlarmLoop() {
    if (fireAlarmActive) return;
    fireAlarmActive = true;

    try {
        if (!fireAudioCtx) {
            fireAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (fireAudioCtx.state === 'suspended') {
            fireAudioCtx.resume();
        }

        let toggle = false;
        fireSirenInterval = setInterval(() => {
            if (!fireAlarmActive) return;
            try {
                const osc = fireAudioCtx.createOscillator();
                const gain = fireAudioCtx.createGain();
                osc.connect(gain);
                gain.connect(fireAudioCtx.destination);

                osc.type = 'sawtooth';
                osc.frequency.value = toggle ? 1200 : 750;
                gain.gain.value = 0.35; // distinct multi-tone loud siren

                osc.start();
                osc.stop(fireAudioCtx.currentTime + 0.22);
                toggle = !toggle;
            } catch (e) {
                console.error("Audio tone error:", e);
            }
        }, 250);
    } catch (e) {
        console.error("Fire alarm audio context error:", e);
    }
}

function stopFireAlarmLoop() {
    fireAlarmActive = false;
    if (fireSirenInterval) {
        clearInterval(fireSirenInterval);
        fireSirenInterval = null;
    }
}

let latestFireDetectedState = false;
let fireRearmTimeout = null;

function triggerFireAlertUI(forceRearm = false, category = 'fire') {
    latestFireDetectedState = true;
    const now = Date.now();
    const banner = document.getElementById('fireAlertBanner');

    // Check 30-second re-arm window requirement:
    if (!forceRearm && (now - fireDismissedAt < REARM_WINDOW_MS)) {
        // Suppress audio & banner during 30s re-arm window
        return;
    }

    if (banner) {
        banner.style.display = 'block';
        // Flex banner icon/wording between fire and smoke without touching
        // the existing DOM structure — banner markup itself is unchanged.
        const icon = banner.querySelector('.fire-icon');
        const text = banner.querySelector('.fire-text');
        if (icon) icon.textContent = category === 'smoke' ? '💨' : '🔥';
        if (text) {
            text.innerHTML = category === 'smoke'
                ? '<strong>WARNING:</strong> SMOKE DETECTED!'
                : '<strong>EMERGENCY WARNING:</strong> FIRE / SMOKE DETECTED!';
        }
    }
    // playFireAlarmLoop has a strict (fireAlarmActive) guard to prevent stacking oscillators
    playFireAlarmLoop();
}

function dismissFireAlertUI() {
    fireDismissedAt = Date.now();
    const banner = document.getElementById('fireAlertBanner');
    if (banner) {
        banner.style.display = 'none';
    }
    stopFireAlarmLoop();

    if (typeof showToast === 'function') {
        showToast('Fire alarm dismissed. Siren silenced for 30s re-arm window.', 'info');
    }

    // Explicit automatic re-arm timer: after 30 seconds, if fire is still detected, reactivate alarm!
    if (fireRearmTimeout) clearTimeout(fireRearmTimeout);
    fireRearmTimeout = setTimeout(() => {
        if (latestFireDetectedState) {
            console.log('[Fire Alarm] 30s re-arm window expired while fire remains present -> REACTIVATING ALARM!');
            triggerFireAlertUI(true);
        }
    }, REARM_WINDOW_MS);
}

function playBeep() {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioCtx.createOscillator();
    const gainNode = audioCtx.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    oscillator.type = 'sine';
    oscillator.frequency.value = 880;
    gainNode.gain.value = 0.3;

    oscillator.start();
    setTimeout(function () {
        oscillator.stop();
    }, 200);
}

function playClickSound() {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioCtx.createOscillator();
    const gainNode = audioCtx.createGain();

    oscillator.connect(gainNode);
    gainNode.connect(audioCtx.destination);

    oscillator.type = 'square';
    oscillator.frequency.value = 600;
    gainNode.gain.value = 0.15;

    oscillator.start();
    setTimeout(function () {
        oscillator.stop();
    }, 80);
}

document.addEventListener('DOMContentLoaded', function () {
    const dismissBtn = document.getElementById('dismissFireAlert');
    if (dismissBtn) {
        dismissBtn.addEventListener('click', dismissFireAlertUI);
    }

    console.log('Attendance System loaded.');

    let stream = null;
    let capturedImages = [];

    const video = document.getElementById('video');
    const canvas = document.getElementById('canvas');       // hidden capture canvas
    const overlay = document.getElementById('overlay');      // visible box-drawing canvas

    // ---------- START CAMERA ----------
    const startCam = document.getElementById('startCam');
    if (startCam && video && !overlay) {
        startCam.addEventListener('click', async function () {
            playClickSound();
            try {
                stream = await navigator.mediaDevices.getUserMedia({ video: true });
                video.srcObject = stream;
                console.log('Camera started successfully.');
                showToast('Camera started successfully.', 'info');
            } catch (err) {
                console.error('Camera error:', err);
                showToast('Camera access nahi mil paayi: ' + err.message, 'error');
            }
        });
    }

    // ---------- STOP CAMERA ----------
    const stopCam = document.getElementById('stopCam');
    if (stopCam && video && !overlay) {
        stopCam.addEventListener('click', function () {
            playClickSound();
            if (stream) {
                stream.getTracks().forEach(track => track.stop());
                video.srcObject = null;
                stream = null;
                console.log('Camera stopped.');
                showToast('Camera stopped.', 'info');
            }
        });
    }

    // ---------- REGISTER PAGE: CAPTURE 5 IMAGES WITH GALLERY & PROGRESS ----------
    const captureBtn = document.getElementById('captureBtn');
    const thumbGallery = document.getElementById('thumbGallery');
    const stepBadge = document.getElementById('stepBadge');
    const progressFill = document.getElementById('progressFill');

    if (captureBtn && video && canvas) {
        captureBtn.addEventListener('click', async function () {
            playClickSound();
            if (!stream) {
                showToast('Pehle camera start karo!', 'warning');
                return;
            }

            capturedImages = [];
            if (thumbGallery) thumbGallery.innerHTML = '';
            const statusDiv = document.getElementById('captureStatus');
            const ctx = canvas.getContext('2d');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;

            for (let i = 0; i < 5; i++) {
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                const dataUrl = canvas.toDataURL('image/jpeg');
                capturedImages.push(dataUrl);

                const currentStep = i + 1;
                if (stepBadge) stepBadge.textContent = `Step ${currentStep} of 5`;
                if (progressFill) progressFill.style.width = `${(currentStep / 5) * 100}%`;

                if (thumbGallery) {
                    const thumb = document.createElement('div');
                    thumb.className = 'thumb-card';
                    thumb.innerHTML = `<img src="${dataUrl}" alt="Face ${currentStep}"><span class="thumb-badge">✓ #${currentStep}</span>`;
                    thumbGallery.appendChild(thumb);
                }

                if (statusDiv) statusDiv.textContent = `📸 Capturing... Step ${currentStep} of 5`;
                await new Promise(resolve => setTimeout(resolve, 500));
            }

            if (statusDiv) statusDiv.textContent = '✅ 5 images captured successfully! Confirm info below and submit.';
            showToast('5 face images captured successfully!', 'success');
        });
    }

    // ---------- REGISTER FORM SUBMIT ----------
    const registerForm = document.getElementById('registerForm');
    if (registerForm) {
        registerForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            if (capturedImages.length < 5) {
                showToast('Pehle 5 images capture karo!', 'warning');
                return;
            }

            const name = document.getElementById('name').value;
            const roll_no = document.getElementById('roll_no').value;
            const studentClass = document.getElementById('class').value;

            const statusDiv = document.getElementById('captureStatus');
            if (statusDiv) statusDiv.textContent = '⏳ Registering student... please wait';

            try {
                const response = await fetch('/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: name,
                        roll_no: roll_no,
                        class: studentClass,
                        images: capturedImages
                    })
                });

                const result = await response.json();

                if (result.success) {
                    if (statusDiv) statusDiv.textContent = '✅ ' + result.message;
                    showToast('Student registered successfully!', 'success');
                    registerForm.reset();
                    capturedImages = [];
                    if (thumbGallery) thumbGallery.innerHTML = '';
                    if (stepBadge) stepBadge.textContent = 'Step 0 of 5';
                    if (progressFill) progressFill.style.width = '0%';
                    if (stream) {
                        stream.getTracks().forEach(track => track.stop());
                        video.srcObject = null;
                        stream = null;
                    }
                } else {
                    if (statusDiv) statusDiv.textContent = '❌ ' + result.message;
                    showToast('Error: ' + result.message, 'error');
                }
            } catch (err) {
                console.error('Registration error:', err);
                if (statusDiv) statusDiv.textContent = '❌ Registration failed';
                showToast('Server error: ' + err.message, 'error');
            }
        });
    }

    // ---------- DASHBOARD: DELETE ATTENDANCE ----------
    const deleteButtons = document.querySelectorAll('.delete-btn');
    deleteButtons.forEach(function (btn) {
        btn.addEventListener('click', async function () {
            const attendanceId = btn.getAttribute('data-id');
            const confirmed = await showConfirmModal({
                title: 'Delete Record',
                message: 'Kya aap yeh record delete karna chahte ho?',
                confirmText: 'Delete',
                danger: true
            });

            if (!confirmed) return;

            try {
                const response = await fetch('/delete-attendance/' + attendanceId, {
                    method: 'POST'
                });
                const result = await response.json();

                if (result.success) {
                    const row = document.getElementById('row-' + attendanceId);
                    if (row) row.remove();
                    showToast('Record deleted successfully', 'success');
                } else {
                    showToast('Delete fail hua: ' + result.message, 'error');
                }
            } catch (err) {
                console.error('Delete error:', err);
                showToast('Server error hua delete karte waqt.', 'error');
            }
        });
    });
});