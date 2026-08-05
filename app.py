from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from functools import wraps
import sqlite3
import base64
import numpy as np
from dotenv import load_dotenv
load_dotenv()
import face_recognition
import pickle
from datetime import datetime
from io import BytesIO
from PIL import Image
from openpyxl import Workbook
from openpyxl.styles import Font
import cv2
import threading
import time
import os
from events import init_events_table, log_event, get_recent_events

app = Flask(__name__)
app.secret_key = 'shlok-face-attendance-secret-key-2026'  # used to sign the session cookie

# ---- White-Label Application Configuration ----
APP_NAME = os.environ.get('APP_NAME', '[APP_NAME]')
APP_DESCRIPTION = os.environ.get('APP_DESCRIPTION', 'Automatic attendance marking using facial recognition.')


@app.context_processor
def inject_app_config():
    return {
        'APP_NAME': APP_NAME,
        'APP_DESCRIPTION': APP_DESCRIPTION
    }


# ---- Admin credentials ----
ADMIN_USERNAME = 'ADMIN'
ADMIN_PASSWORD = 'ADMIN'


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def init_db():
    print("Database setup start kar raha hoon...")
    conn = sqlite3.connect('database.db', timeout=10)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        roll_no TEXT UNIQUE NOT NULL,
        class TEXT NOT NULL,
        face_encoding BLOB NOT NULL
    )''')
    # NOTE: entry_time / exit_time replace the old single "time" column.
    c.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        date DATE NOT NULL,
        entry_time TIME,
        exit_time TIME,
        status TEXT DEFAULT 'Present'
    )''')
    # Fire/smoke detection alerts table
    c.execute('''CREATE TABLE IF NOT EXISTS fire_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        confidence REAL NOT NULL,
        label TEXT DEFAULT 'fire',
        camera_source TEXT NOT NULL
    )''')
    # Time-window intrusion / after-hours alerts table
    c.execute('''CREATE TABLE IF NOT EXISTS after_hours_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME NOT NULL,
        person_name TEXT NOT NULL,
        confidence REAL DEFAULT 0.0,
        camera_source TEXT NOT NULL
    )''')
    c.execute("PRAGMA table_info(after_hours_alerts)")
    cols = [row[1] for row in c.fetchall()]
    if 'confidence' not in cols:
        c.execute("ALTER TABLE after_hours_alerts ADD COLUMN confidence REAL DEFAULT 0.0")
    conn.commit()
    conn.close()
    print("Database ready.")

init_db()
init_events_table()

def decode_base64_image(base64_string):
    if ',' in base64_string:
        base64_string = base64_string.split(',')[1]
    img_bytes = base64.b64decode(base64_string)
    img = Image.open(BytesIO(img_bytes)).convert('RGB')
    return np.array(img)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    username = request.form.get('username')
    password = request.form.get('password')

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session['logged_in'] = True
        session['username'] = username
        return redirect(url_for('dashboard'))
    else:
        return render_template('login.html', error='Galat username ya password!')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if request.method == 'GET':
        return render_template('register.html')

    data = request.get_json()
    name = data.get('name')
    roll_no = data.get('roll_no')
    student_class = data.get('class')
    images = data.get('images', [])

    if not name or not roll_no or not student_class:
        return jsonify({'success': False, 'message': 'Sab fields fill karo!'})

    if len(images) < 5:
        return jsonify({'success': False, 'message': 'Kam se kam 5 images chahiye!'})

    encodings = []
    for img_data in images:
        try:
            img_array = decode_base64_image(img_data)
            face_locations = face_recognition.face_locations(img_array)
            if len(face_locations) == 0:
                continue
            face_encs = face_recognition.face_encodings(img_array, face_locations)
            if len(face_encs) > 0:
                encodings.append(face_encs[0])
        except Exception as e:
            print(e)
            continue

    if len(encodings) == 0:
        return jsonify({'success': False, 'message': 'Face detect nahi hua. Dobara try karo.'})

    avg_encoding = np.mean(encodings, axis=0)
    encoding_blob = pickle.dumps(avg_encoding)

    DUPLICATE_FACE_THRESHOLD = 0.5  # kept in sync with the recognition threshold in match_faces

    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()

        # ---- Duplicate face check ----
        # Reject registration if this face is already registered under a
        # different (or the same) roll number, so one person can't be
        # enrolled twice and double up their attendance.
        c.execute('SELECT name, roll_no, face_encoding FROM students')
        existing_students = c.fetchall()

        for existing_name, existing_roll_no, existing_blob in existing_students:
            existing_encoding = pickle.loads(existing_blob)
            distance = np.linalg.norm(existing_encoding - avg_encoding)
            if distance < DUPLICATE_FACE_THRESHOLD:
                conn.close()
                return jsonify({
                    'success': False,
                    'message': 'Yeh face pehle se register hai: ' + existing_name + ' (Roll No: ' + existing_roll_no + ')'
                })

        c.execute('INSERT INTO students (name, roll_no, class, face_encoding) VALUES (?, ?, ?, ?)', (name, roll_no, student_class, encoding_blob))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': name + ' successfully register ho gaya!'})
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'Yeh Roll Number pehle se register hai!'})
    except Exception as e:
        return jsonify({'success': False, 'message': 'Database error: ' + str(e)})


def match_faces(img_array, known):
    """Run face detection + matching against known encodings. Returns list of
    (box, student_tuple_or_None) pairs.

    Two safeguards against mixing up different people:
    1. A tighter acceptance threshold (0.5 instead of the looser 0.6
       default) — fewer borderline matches get accepted at all.
    2. A margin check: if the two closest students are both close to the
       current face (i.e. genuinely hard to tell apart), we reject the
       match as unknown rather than guess. Better to ask someone to
       re-scan than to mark the wrong person present.
    """
    face_locations = face_recognition.face_locations(img_array)
    if len(face_locations) == 0:
        return []

    face_encs = face_recognition.face_encodings(img_array, face_locations)
    matches = []

    RECOGNITION_THRESHOLD = 0.5   # tighter than the 0.6 default -> fewer false positives
    MIN_MARGIN = 0.07             # best match must be clearly better than the runner-up

    for (top, right, bottom, left), current_encoding in zip(face_locations, face_encs):
        distances = []
        for student_id, name, roll_no, student_class, saved_encoding in known:
            distance = np.linalg.norm(saved_encoding - current_encoding)
            distances.append((distance, student_id, name, roll_no, student_class))

        best_match = None

        if distances:
            distances.sort(key=lambda d: d[0])
            best_distance, best_id, best_name, best_roll_no, best_class = distances[0]

            if best_distance < RECOGNITION_THRESHOLD:
                # If a second candidate is almost as close as the best one,
                # this face is ambiguous between two real students -> reject
                # rather than risk marking the wrong person.
                if len(distances) > 1:
                    second_best_distance = distances[1][0]
                    if (second_best_distance - best_distance) >= MIN_MARGIN:
                        best_match = (best_id, best_name, best_roll_no, best_class)
                else:
                    best_match = (best_id, best_name, best_roll_no, best_class)

        matches.append(([top, right, bottom, left], best_match))

    return matches


def load_known_students(c):
    """Fetch and unpickle all known face encodings. Shared by mark-attendance
    and mark-exit so both pages use the identical recognition pipeline."""
    c.execute('SELECT id, name, roll_no, class, face_encoding FROM students')
    students = c.fetchall()
    known = []
    for student_id, name, roll_no, student_class, encoding_blob in students:
        known.append((student_id, name, roll_no, student_class, pickle.loads(encoding_blob)))
    return known


# ---------------------------------------------------------------------------
# Liveness detection (anti-photo-spoofing)
#
# Uses the classic Eye Aspect Ratio (EAR) blink test: a real, live face will
# blink within a few seconds of being in front of the camera; a printed
# photo or a static image on a phone screen never will. Before any
# entry/exit is actually written to the database, the person must be
# observed going eyes-open -> eyes-closed -> eyes-open at least once.
#
# State is kept in a simple in-memory dict keyed by student_id. This is
# fine for a single-process college project; it resets if the server
# restarts, which just means everyone needs to blink again — not a problem.
# ---------------------------------------------------------------------------

liveness_state = {}

# Guards the read-then-write attendance logic (check if a row is "open",
# then update/insert). With threaded=True, two nearly-simultaneous
# requests for the same student could otherwise both read "not yet
# marked" before either commits, causing duplicate writes. Held only
# briefly around each student's DB check+write, not around whole requests.
attendance_write_lock = threading.Lock()

EAR_CLOSED_THRESHOLD = 0.25    # eyes counted as "closed" (i.e. a blink happened) below this
LIVENESS_WINDOW_SECONDS = 8    # if no blink within this long, restart tracking


def eye_aspect_ratio(eye_points):
    """Standard 6-point EAR formula. eye_points is a list of 6 (x, y) tuples
    as returned by face_recognition.face_landmarks()."""
    try:
        p = np.array(eye_points)
        vertical_1 = np.linalg.norm(p[1] - p[5])
        vertical_2 = np.linalg.norm(p[2] - p[4])
        horizontal = np.linalg.norm(p[0] - p[3])
        if horizontal == 0:
            return None
        return (vertical_1 + vertical_2) / (2.0 * horizontal)
    except Exception:
        return None


def compute_face_ear(img_array, face_location):
    """face_location must be a (top, right, bottom, left) tuple. Returns the
    average EAR across both eyes, or None if landmarks aren't available
    (e.g. face too small, extreme angle)."""
    try:
        landmarks_list = face_recognition.face_landmarks(img_array, [face_location])
    except Exception as e:
        print('Landmark detection error:', e)
        return None

    if not landmarks_list:
        return None

    landmarks = landmarks_list[0]
    left_eye = landmarks.get('left_eye')
    right_eye = landmarks.get('right_eye')
    if not left_eye or not right_eye:
        return None

    left_ear = eye_aspect_ratio(left_eye)
    right_ear = eye_aspect_ratio(right_eye)
    if left_ear is None or right_ear is None:
        return None

    return (left_ear + right_ear) / 2.0


def check_liveness(student_id, ear_value):
    """A real, live face will show at least one clearly low EAR reading
    (a blink) within a few seconds of being in front of the camera. A
    printed photo or a phone screen has no real eyelid geometry, so it
    essentially never produces a genuinely low reading.

    This intentionally does NOT require a strict open->closed->open
    sequence — webcam EAR readings are naturally noisy frame to frame
    (landmark jitter), which made the stricter version unreliable in
    practice. Requiring just one qualifying dip is simpler and more
    robust, while still blocking static photo spoofing."""
    now = datetime.now()
    entry = liveness_state.get(student_id)

    if entry is None or (now - entry['last_seen']).total_seconds() > LIVENESS_WINDOW_SECONDS:
        entry = {'blinked': False, 'last_seen': now}

    if ear_value is not None and ear_value < EAR_CLOSED_THRESHOLD:
        entry['blinked'] = True

    entry['last_seen'] = now
    liveness_state[student_id] = entry
    return entry['blinked']


def clear_liveness(student_id):
    """Call this once a student's blink has been used to confirm an actual
    entry/exit write, so the next person doesn't inherit stale state."""
    liveness_state.pop(student_id, None)


@app.route('/mark-attendance', methods=['GET', 'POST'])
def mark_attendance():
    frame_counter = 0
#while True:
    """Single camera page. Automatically decides ENTRY vs EXIT per person:
    - No record today yet -> mark entry
    - Entry exists, no exit yet -> mark exit
    - Both entry & exit already done -> mark a fresh re-entry (new cycle)
    A cooldown prevents the same person from toggling entry/exit every
    couple seconds just by standing in front of the camera.

    Coexists safely with the dedicated /mark-exit route (browser or RTSP):
    both share attendance_write_lock, so even if both cameras see the same
    person at nearly the same moment, only one can actually claim the
    write — whichever gets there first. No more duplicate/racing marks.
    """
    if request.method == 'GET':
        return render_template('mark_attendance.html')

    data = request.get_json()
    image_data = data.get('image')

    if not image_data:
        return jsonify({'faces': [], 'error': 'Image nahi mili'})

    # Minimum gap (in seconds) required after a completed cycle before a
    # fresh re-entry is allowed, so a person standing in front of the
    # camera doesn't get marked in and out repeatedly.
    COOLDOWN_SECONDS = 60

    try:
        img_array = decode_base64_image(image_data)
        run_fire_detection(img_array, camera_source="Attendance Phone Cam")

        # Run fire detection on incoming frame
        img_bgr = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
        fire_boxes, fire_alert = run_fire_detection(img_bgr, conf_thresh=0.50, camera_source="Webcam")

        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('SELECT id, name, roll_no, class, face_encoding FROM students')
        students = c.fetchall()

        known = []
        for student_id, name, roll_no, student_class, encoding_blob in students:
            known.append((student_id, name, roll_no, student_class, pickle.loads(encoding_blob)))

        matches = match_faces(img_array, known)

        if len(matches) == 0:
            conn.close()
            return jsonify({
                'faces': [],
                'message': 'Koi face detect nahi hua.',
                'fire_alert': fire_alert,
                'fire_boxes': fire_boxes
            })

        today = datetime.now().strftime('%Y-%m-%d')
        now_dt = datetime.now()
        now_time = now_dt.strftime('%H:%M:%S')

        results = []

        for box, best_match in matches:
            face_result = {'box': box}

            if best_match is None:
                face_result['name'] = 'Unknown'
                face_result['status'] = 'unknown'
                face_result['message'] = 'Face match nahi hua. Pehle register karo!'
                results.append(face_result)
                continue

            student_id, name, roll_no, student_class = best_match
            face_result['name'] = name
            face_result['roll_no'] = roll_no

            # Get the most recent row for this student today
            with attendance_write_lock:
                c.execute(
                    'SELECT id, entry_time, exit_time FROM attendance '
                    'WHERE student_id = ? AND date = ? ORDER BY id DESC LIMIT 1',
                    (student_id, today)
                )
                existing = c.fetchone()

                def seconds_since(time_str):
                    last_dt = datetime.strptime(today + ' ' + time_str, '%Y-%m-%d %H:%M:%S')
                    return (now_dt - last_dt).total_seconds()

                if existing is None:
                    ear = compute_face_ear(img_array, tuple(box))
                    if not check_liveness(student_id, ear):
                        face_result['status'] = 'liveness_pending'
                        face_result['ear'] = ear
                        face_result['message'] = '👁 ' + name + ' - Please blink to verify you are a real person.'
                    else:
                        clear_liveness(student_id)
                        c.execute(
                            'INSERT INTO attendance (student_id, date, entry_time, status) VALUES (?, ?, ?, ?)',
                            (student_id, today, now_time, 'Present')
                        )
                        conn.commit()
                        face_result['status'] = 'marked'
                        face_result['action'] = 'entry'
                        face_result['message'] = '✅ ' + name + ' - Entry marked! Time: ' + now_time
                        log_event('attendance', 'info', f'{name} — entry marked', camera_source='Webcam', conn=conn)
                else:
                    attendance_id, entry_time, exit_time = existing

                    if entry_time is not None and exit_time is None:
                        if seconds_since(entry_time) < COOLDOWN_SECONDS:
                            face_result['status'] = 'already_marked'
                            face_result['action'] = 'entry'
                            face_result['message'] = name + ' ki entry abhi mark hui hai.'
                        else:
                            ear = compute_face_ear(img_array, tuple(box))
                            if not check_liveness(student_id, ear):
                                face_result['status'] = 'liveness_pending'
                                face_result['ear'] = ear
                                face_result['message'] = '👁 ' + name + ' - Please blink to verify you are a real person.'
                            else:
                                clear_liveness(student_id)
                                c.execute('UPDATE attendance SET exit_time = ? WHERE id = ?', (now_time, attendance_id))
                                conn.commit()
                                face_result['status'] = 'marked'
                                face_result['action'] = 'exit'
                                face_result['message'] = '👋 ' + name + ' - Exit marked! Time: ' + now_time
                                log_event('attendance', 'info', f'{name} — exit marked', camera_source='Webcam', conn=conn)
                    else:
                        if exit_time is not None and seconds_since(exit_time) < COOLDOWN_SECONDS:
                            face_result['status'] = 'already_marked'
                            face_result['action'] = 'exit'
                            face_result['message'] = name + ' ki exit abhi mark hui hai.'
                        else:
                            ear = compute_face_ear(img_array, tuple(box))
                            if not check_liveness(student_id, ear):
                                face_result['status'] = 'liveness_pending'
                                face_result['ear'] = ear
                                face_result['message'] = '👁 ' + name + ' - Please blink to verify you are a real person.'
                            else:
                                clear_liveness(student_id)
                                c.execute(
                                    'INSERT INTO attendance (student_id, date, entry_time, status) VALUES (?, ?, ?, ?)',
                                    (student_id, today, now_time, 'Present')
                                )
                                conn.commit()
                                face_result['status'] = 'marked'
                                face_result['action'] = 'entry'
                                face_result['message'] = '✅ ' + name + ' - Re-entry marked! Time: ' + now_time
                                log_event('attendance', 'info', f'{name} — re-entry marked', camera_source='Webcam', conn=conn)
            results.append(face_result)

        conn.close()

        # Check for after-hours / intrusion window
        after_hours_alert, alert_person = process_after_hours_check(results, camera_source="Webcam")

        return jsonify({
            'faces': results,
            'fire_alert': fire_alert,
            'fire_boxes': fire_boxes,
            'after_hours_alert': after_hours_alert
        })

    except Exception as e:
        print(e)
        return jsonify({'faces': [], 'error': str(e)})


def process_exit_image(img_array):
    """Runs the full exit-marking pipeline (recognition + liveness + DB
    write) on a single image array. Shared by the browser-based /mark-exit
    POST route and the RTSP background camera worker, so both use
    identical logic."""
    conn = None
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        known = load_known_students(c)

        matches = match_faces(img_array, known)

        if len(matches) == 0:
            conn.close()
            return {'faces': [], 'message': 'Koi face detect nahi hua.'}

        today = datetime.now().strftime('%Y-%m-%d')
        now_time = datetime.now().strftime('%H:%M:%S')

        results = []

        for box, best_match in matches:
            face_result = {'box': box}

            if best_match is None:
                face_result['name'] = 'Unknown'
                face_result['status'] = 'unknown'
                face_result['message'] = 'Face match nahi hua. Pehle register karo!'
                results.append(face_result)
                continue

            student_id, name, roll_no, student_class = best_match
            face_result['name'] = name
            face_result['roll_no'] = roll_no

            try:
                with attendance_write_lock:
                    # 1) Is there an OPEN record today (entry marked, exit not yet)?
                    c.execute(
                        'SELECT id, entry_time, exit_time FROM attendance '
                        'WHERE student_id = ? AND date = ? AND entry_time IS NOT NULL AND exit_time IS NULL '
                        'ORDER BY id DESC LIMIT 1',
                        (student_id, today)
                    )
                    open_record = c.fetchone()

                    if open_record:
                        attendance_id, entry_time, _ = open_record
                        ear = compute_face_ear(img_array, tuple(box))
                        if not check_liveness(student_id, ear):
                            face_result['status'] = 'liveness_pending'
                            face_result['entry_time'] = entry_time
                            face_result['ear'] = ear
                            face_result['message'] = '👁 ' + name + ' - Please blink to verify you are a real person.'
                        else:
                            clear_liveness(student_id)
                            c.execute(
                                "UPDATE attendance SET exit_time = ?, status = 'Completed' WHERE id = ?",
                                (now_time, attendance_id)
                            )
                            conn.commit()

                            face_result['status'] = 'marked'
                            face_result['entry_time'] = entry_time
                            face_result['exit_time'] = now_time
                            face_result['emp_status'] = 'Completed'
                            face_result['message'] = '👋 ' + name + ' - Exit marked! Time: ' + now_time
                            log_event('attendance', 'info', f'{name} — exit marked', camera_source='RTSP/Exit Camera', conn=conn)
                    else:
                        # 2) No open record — either already exited today, or never entered.
                        c.execute(
                            'SELECT entry_time, exit_time FROM attendance '
                            'WHERE student_id = ? AND date = ? AND entry_time IS NOT NULL AND exit_time IS NOT NULL '
                            'ORDER BY id DESC LIMIT 1',
                            (student_id, today)
                        )
                        completed = c.fetchone()

                        if completed:
                            entry_time, exit_time = completed
                            face_result['status'] = 'already_exited'
                            face_result['entry_time'] = entry_time
                            face_result['exit_time'] = exit_time
                            face_result['emp_status'] = 'Completed'
                            face_result['message'] = 'Exit already recorded.'
                        else:
                            face_result['status'] = 'no_entry'
                            face_result['entry_time'] = None
                            face_result['exit_time'] = None
                            face_result['emp_status'] = 'Not Entered'
                            face_result['message'] = 'Please mark entry first.'

            except sqlite3.Error as db_err:
                print('DB error while processing exit for student', student_id, ':', db_err)
                face_result['status'] = 'error'
                face_result['message'] = 'Database error while marking exit. Try again.'

            results.append(face_result)

        conn.close()
        return {'faces': results}

    except Exception as e:
        print(e)
        if conn:
            conn.close()
        return {'faces': [], 'error': str(e)}


@app.route('/mark-exit', methods=['GET', 'POST'])
def mark_exit():
    """Dedicated exit-only page/route. Does NOT touch /mark-attendance.

    This still works for browser-webcam testing (kept for backward
    compatibility), calling the same process_exit_image() function that
    the RTSP background camera worker uses below.
    """
    if request.method == 'GET':
        return render_template('mark_exit.html')

    data = request.get_json(silent=True) or {}
    image_data = data.get('image')

    if not image_data:
        return jsonify({'faces': [], 'error': 'Image nahi mili'})

    img_array = decode_base64_image(image_data)
    run_fire_detection(img_array, camera_source="Exit Phone Cam")
    return jsonify(process_exit_image(img_array))


# ---------------------------------------------------------------------------
# RTSP mobile-camera integration & Frame Grabber
# ---------------------------------------------------------------------------

class RTSPFrameGrabber:
    """Daemon thread frame grabber for OpenCV RTSP stream. Continuously reads
    frames in a tight loop and keeps only the latest frame to eliminate buffer lag."""
    def __init__(self, rtsp_url, transport="tcp"):
        self.rtsp_url = rtsp_url
        self.transport = transport
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = f"rtsp_transport;{transport}"
        self.cap = cv2.VideoCapture(rtsp_url)
        # Set buffer size to 1 as specified in requirement 2
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.lock = threading.Lock()
        self.latest_frame = None
        self.stopped = False
        self.thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.thread.start()

    def _reader_loop(self):
        frame_counter = 0
        while not self.stopped:
            if not self.cap.isOpened():
                time.sleep(0.05)
                continue
            success, frame = self.cap.read()
            if success and frame is not None:
                frame_counter += 1
                if frame_counter % 5 != 0:
                    continue
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
                frame = cv2.resize(frame, (640, 480))
                with self.lock:
                    self.latest_frame = frame
            else:
                time.sleep(0.01)

    def read(self):
        with self.lock:
            if self.latest_frame is None:
                return False, None
            return True, self.latest_frame.copy()

    def isOpened(self):
        return self.cap.isOpened()

    def stop(self):
        self.stopped = True
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap:
            self.cap.release()


# ---- YOLO Fire/Smoke Detection Setup ----
fire_model = None
try:
    from ultralytics import YOLO
    if os.path.exists('models/best.pt'):
        fire_model = YOLO('models/best.pt')
        print("Loaded fine-tuned YOLO fire/smoke model from models/best.pt")
    else:
        print("Warning: models/best.pt not found.")
except Exception as e:
    print("Warning: Could not load ultralytics YOLO model:", e)


fire_alert_tracker = {
    'last_logged_time': 0.0,
    'last_detected_time': 0.0,
    'lock': threading.Lock()
}
FIRE_CONF_THRESHOLD = float(os.environ.get('FIRE_CONF_THRESHOLD', 0.50))
SMOKE_CONF_THRESHOLD = float(os.environ.get('SMOKE_CONF_THRESHOLD', 0.45))
FIRE_LOG_COOLDOWN = 60.0  # seconds between logging new fire_alerts DB rows
FIRE_RESET_GAP = 5.0      # seconds gap of no fire before treating next fire as a new event


# ---- External Alert Notifications (Email & Twilio SMS/WhatsApp) ----

# ---- External Alert Notifications (Email & Twilio SMS/WhatsApp) ----

def send_external_fire_alerts(timestamp_str, confidence, label, camera_source):
    """Dispatches Email (Resend/SendGrid) and SMS/WhatsApp (Twilio) alerts
    in an asynchronous background thread so external network calls never block
    or slow down the live vision processing loops."""
    print(f"[ALERT] send_external_fire_alerts() invoked for source: '{camera_source}' at {timestamp_str} (Conf: {confidence})", flush=True)
    thread = threading.Thread(
        target=_send_alerts_worker,
        args=(timestamp_str, confidence, label, camera_source),
        daemon=True
    )
    thread.start()


def _send_alerts_worker(timestamp_str, confidence, label, camera_source):
    print(f"[ALERT WORKER] Fire alert thread started for source '{camera_source}'", flush=True)
    alert_email = os.environ.get('ALERT_EMAIL')
    alert_phone = os.environ.get('ALERT_PHONE')

    # 1. EMAIL ALERT (via Resend or SendGrid API)
    if alert_email:
        resend_key = os.environ.get('RESEND_API_KEY')
        sendgrid_key = os.environ.get('SENDGRID_API_KEY')

        if resend_key:
            print(f"[ALERT] Attempting to send Email via Resend to {alert_email}...", flush=True)
            try:
                import resend
                resend.api_key = resend_key
                from_email = os.environ.get('RESEND_FROM_EMAIL', 'onboarding@resend.dev')
                r = resend.Emails.send({
                    "from": from_email,
                    "to": [alert_email],
                    "subject": "🔥 Fire/Smoke Detected",
                    "html": f"""
                    <h2>🔥 Emergency Fire / Smoke Alert</h2>
                    <p>An emergency fire/smoke event has been detected by SentinelVision AI system.</p>
                    <ul>
                        <li><strong>Timestamp:</strong> {timestamp_str}</li>
                        <li><strong>Camera Source:</strong> {camera_source}</li>
                        <li><strong>Detected Class:</strong> {label}</li>
                        <li><strong>Confidence Score:</strong> {confidence * 100:.1f}%</li>
                    </ul>
                    <p>Please inspect the premises immediately and take necessary safety precautions.</p>
                    """
                })
                print(f"[ALERT SUCCESS] Resend Email alert sent successfully to {alert_email}. Response: {r}", flush=True)
            except Exception as e_resend:
                print(f"[ALERT ERROR] Failed to send Resend email alert to {alert_email}: {e_resend}", flush=True)

        elif sendgrid_key:
            print(f"[ALERT] Attempting to send Email via SendGrid to {alert_email}...", flush=True)
            try:
                import requests
                headers = {
                    "Authorization": f"Bearer {sendgrid_key}",
                    "Content-Type": "application/json"
                }
                from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'alerts@sentinelvision.ai')
                data = {
                    "personalizations": [{"to": [{"email": alert_email}]}],
                    "from": {"email": from_email},
                    "subject": "🔥 Fire/Smoke Detected",
                    "content": [{
                        "type": "text/html",
                        "value": f"<p>🔥 <strong>Fire/Smoke Detected</strong> at {timestamp_str} on {camera_source} (Conf: {confidence * 100:.1f}%).</p>"
                    }]
                }
                res = requests.post("https://api.sendgrid.com/v3/mail/send", json=data, headers=headers, timeout=10)
                print(f"[ALERT SUCCESS] SendGrid email alert status code: {res.status_code}", flush=True)
            except Exception as e_sg:
                print(f"[ALERT ERROR] Failed to send SendGrid email alert to {alert_email}: {e_sg}", flush=True)
        else:
            print(f"[ALERT NOTICE] ALERT_EMAIL is set ({alert_email}), but neither RESEND_API_KEY nor SENDGRID_API_KEY is configured in .env.", flush=True)
    else:
        print(f"[ALERT NOTICE] External email alert skipped (ALERT_EMAIL environment variable not set in .env).", flush=True)

    # 2. SMS & WHATSAPP ALERTS (via Twilio API)
    if alert_phone:
        twilio_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        twilio_token = os.environ.get('TWILIO_AUTH_TOKEN')
        twilio_from = os.environ.get('TWILIO_PHONE_NUMBER')
        twilio_wa_from = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

        if twilio_sid and twilio_token:
            sms_body = f"🔥 FIRE ALERT: {label.upper()} detected at {timestamp_str} on {camera_source} (Conf: {confidence*100:.0f}%). Inspect immediately!"
            if len(sms_body) > 160:
                sms_body = sms_body[:157] + "..."

            if twilio_from:
                print(f"[ALERT] Attempting to send Twilio SMS to {alert_phone}...", flush=True)
                try:
                    from twilio.rest import Client
                    client = Client(twilio_sid, twilio_token)
                    sms_msg = client.messages.create(
                        body=sms_body,
                        from_=twilio_from,
                        to=alert_phone
                    )
                    print(f"[ALERT SUCCESS] Twilio SMS alert sent to {alert_phone} (SID: {sms_msg.sid})", flush=True)
                except Exception as e_sms:
                    print(f"[ALERT ERROR] Failed to send Twilio SMS alert to {alert_phone}: {e_sms}", flush=True)
            else:
                print(f"[ALERT NOTICE] TWILIO_PHONE_NUMBER not set in .env; skipping SMS.", flush=True)

            wa_to = alert_phone if alert_phone.startswith('whatsapp:') else f"whatsapp:{alert_phone}"
            print(f"[ALERT] Attempting to send Twilio WhatsApp to {wa_to}...", flush=True)
            try:
                from twilio.rest import Client
                client = Client(twilio_sid, twilio_token)
                wa_body = f"🔥 *EMERGENCY FIRE ALERT*\n\nFire/Smoke detected at *{timestamp_str}* on *{camera_source}*\nConfidence: *{confidence*100:.1f}%*\n\nPlease take immediate safety precautions."
                wa_msg = client.messages.create(
                    body=wa_body,
                    from_=twilio_wa_from,
                    to=wa_to
                )
                print(f"[ALERT SUCCESS] Twilio WhatsApp alert sent to {wa_to} (SID: {wa_msg.sid})", flush=True)
            except Exception as e_wa:
                print(f"[ALERT ERROR] Failed to send Twilio WhatsApp alert to {wa_to}: {e_wa}", flush=True)
        else:
            print(f"[ALERT NOTICE] ALERT_PHONE is set ({alert_phone}), but TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN environment variables not configured in .env.", flush=True)
    else:
        print(f"[ALERT NOTICE] External SMS/WhatsApp alert skipped (ALERT_PHONE environment variable not set in .env).", flush=True)


# ---- After-Hours / Restricted Time-Window Intrusion Detection ----

after_hours_alert_tracker = {
    'last_logged_time': 0.0,
    'lock': threading.Lock()
}

AFTER_HOURS_LOG_COOLDOWN = 60.0  # seconds between logging after-hours alerts per camera event cycle


def is_in_restricted_window():
    """Checks if the current server local time falls within the configured
    RESTRICTED_START and RESTRICTED_END time window.
    Default test window: RESTRICTED_START='15:25' (3:25 PM today).
    Supports open-ended ('after HH:MM') or overnight ranges ('22:00' to '06:00')."""
    start_str = (os.environ.get('RESTRICTED_START') or '15:25').strip()
    end_str = (os.environ.get('RESTRICTED_END') or '').strip()

    if not start_str:
        return False

    now_t = datetime.now().time()

    try:
        start_t = datetime.strptime(start_str, "%H:%M").time()
    except Exception as e:
        print("Invalid RESTRICTED_START format in .env (expected HH:MM):", start_str, e)
        return False

    if end_str:
        try:
            end_t = datetime.strptime(end_str, "%H:%M").time()
            if start_t <= end_t:
                return start_t <= now_t <= end_t
            else:
                # Overnight window (e.g. 22:00 -> 06:00)
                return now_t >= start_t or now_t <= end_t
        except Exception:
            pass

    # Open-ended ("after start_t")
    return now_t >= start_t


def process_after_hours_check(faces, camera_source="Unknown", force_check=False):
    """If current time is inside the restricted window and faces are detected (recognized or unrecognized),
    logs event to after_hours_alerts DB table and dispatches Email/SMS/WhatsApp alerts.
    Respects 60-second cooldown."""
    if not faces:
        return False, None

    in_window = force_check or is_in_restricted_window()
    if not in_window:
        return False, None

    now = time.time()
    should_log = False
    with after_hours_alert_tracker['lock']:
        if (now - after_hours_alert_tracker['last_logged_time']) >= AFTER_HOURS_LOG_COOLDOWN:
            should_log = True
            after_hours_alert_tracker['last_logged_time'] = now

    if not should_log:
        return True, "Suppressed by 60s cooldown"

    timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    top_conf = 0.0
    names = []
    for f in faces:
        name = f.get('name') or 'Unrecognized Person'
        roll = f.get('roll_no')
        conf = float(f.get('confidence') or f.get('det_score') or 0.88)
        if conf > top_conf:
            top_conf = conf
        if name and name != 'Unknown':
            names.append(f"{name} (ID: {roll})" if roll else name)
        else:
            names.append("Unrecognized Person")

    person_summary = ", ".join(names) if names else "Unrecognized Person"

    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute(
            'INSERT INTO after_hours_alerts (timestamp, person_name, confidence, camera_source) VALUES (?, ?, ?, ?)',
            (timestamp_str, person_summary, round(top_conf, 2), camera_source)
        )
        conn.commit()
        conn.close()
        print(f"[{timestamp_str}] ⚠️ AFTER-HOURS INTRUSION ALERT: Person detected ({person_summary}, Conf: {top_conf*100:.0f}%) from {camera_source}")
    except Exception as e:
        print("After-hours DB insert error:", e)

    log_event(
        event_type='after_hours',
        severity='warning',
        message=f'{person_summary} detected during restricted hours',
        camera_source=camera_source,
        confidence=top_conf,
        ref_table='after_hours_alerts'
    )

    # Dispatch external Email, SMS & WhatsApp alerts asynchronously
    send_external_after_hours_alerts(timestamp_str, person_summary, top_conf, camera_source)
    return True, person_summary


def send_external_after_hours_alerts(timestamp_str, person_summary, confidence, camera_source):
    """Dispatches after-hours Email, SMS & WhatsApp alerts in a non-blocking background thread."""
    print(f"[ALERT] send_external_after_hours_alerts() invoked for source: '{camera_source}' at {timestamp_str} (Person: {person_summary}, Conf: {confidence})", flush=True)
    thread = threading.Thread(
        target=_send_after_hours_alerts_worker,
        args=(timestamp_str, person_summary, confidence, camera_source),
        daemon=True
    )
    thread.start()


def _send_after_hours_alerts_worker(timestamp_str, person_summary, confidence, camera_source):
    print(f"[ALERT WORKER] After-hours alert thread started for source '{camera_source}'", flush=True)
    alert_email = os.environ.get('ALERT_EMAIL')
    alert_phone = os.environ.get('ALERT_PHONE')

    # 1. EMAIL ALERT
    if alert_email:
        resend_key = os.environ.get('RESEND_API_KEY')
        sendgrid_key = os.environ.get('SENDGRID_API_KEY')

        if resend_key:
            print(f"[ALERT] Attempting to send after-hours Email via Resend to {alert_email}...", flush=True)
            try:
                import resend
                resend.api_key = resend_key
                from_email = os.environ.get('RESEND_FROM_EMAIL', 'onboarding@resend.dev')
                r = resend.Emails.send({
                    "from": from_email,
                    "to": [alert_email],
                    "subject": "⚠️ Person Detected After Hours",
                    "html": f"""
                    <h2>⚠️ Intrusion / After-Hours Alert</h2>
                    <p>A person has been detected on camera during the configured restricted time window.</p>
                    <ul>
                        <li><strong>Timestamp:</strong> {timestamp_str}</li>
                        <li><strong>Camera Source:</strong> {camera_source}</li>
                        <li><strong>Person Identified:</strong> {person_summary}</li>
                        <li><strong>Confidence Score:</strong> {confidence * 100:.1f}%</li>
                    </ul>
                    <p>Please inspect camera feeds and verify facility security immediately.</p>
                    """
                })
                print(f"[ALERT SUCCESS] After-hours Email alert sent to {alert_email} via Resend. Response: {r}", flush=True)
            except Exception as e:
                print(f"[ALERT ERROR] Failed to send Resend after-hours email to {alert_email}: {e}", flush=True)

        elif sendgrid_key:
            print(f"[ALERT] Attempting to send after-hours Email via SendGrid to {alert_email}...", flush=True)
            try:
                import requests
                headers = {"Authorization": f"Bearer {sendgrid_key}", "Content-Type": "application/json"}
                from_email = os.environ.get('SENDGRID_FROM_EMAIL', 'alerts@sentinelvision.ai')
                data = {
                    "personalizations": [{"to": [{"email": alert_email}]}],
                    "from": {"email": from_email},
                    "subject": "⚠️ Person Detected After Hours",
                    "content": [{"type": "text/html", "value": f"<p>⚠️ <strong>Person Detected After Hours</strong> ({person_summary}) at {timestamp_str} on {camera_source}.</p>"}]
                }
                res = requests.post("https://api.sendgrid.com/v3/mail/send", json=data, headers=headers, timeout=10)
                print(f"[ALERT SUCCESS] SendGrid after-hours email status code: {res.status_code}", flush=True)
            except Exception as e:
                print(f"[ALERT ERROR] Failed to send SendGrid after-hours email to {alert_email}: {e}", flush=True)
        else:
            print(f"[ALERT NOTICE] ALERT_EMAIL is set ({alert_email}), but neither RESEND_API_KEY nor SENDGRID_API_KEY is configured in .env.", flush=True)
    else:
        print(f"[ALERT NOTICE] After-hours email alert skipped (ALERT_EMAIL environment variable not set in .env).", flush=True)

    # 2. SMS & WHATSAPP ALERTS (Twilio)
    if alert_phone:
        twilio_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        twilio_token = os.environ.get('TWILIO_AUTH_TOKEN')
        twilio_from = os.environ.get('TWILIO_PHONE_NUMBER')
        twilio_wa_from = os.environ.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')

        if twilio_sid and twilio_token:
            sms_body = f"⚠️ AFTER HOURS ALERT: {person_summary} detected at {timestamp_str} on {camera_source}. Inspect immediately!"
            if len(sms_body) > 160:
                sms_body = sms_body[:157] + "..."

            if twilio_from:
                print(f"[ALERT] Attempting to send after-hours Twilio SMS to {alert_phone}...", flush=True)
                try:
                    from twilio.rest import Client
                    client = Client(twilio_sid, twilio_token)
                    sms_msg = client.messages.create(body=sms_body, from_=twilio_from, to=alert_phone)
                    print(f"[ALERT SUCCESS] Twilio after-hours SMS sent to {alert_phone} (SID: {sms_msg.sid})", flush=True)
                except Exception as e:
                    print(f"[ALERT ERROR] Failed to send Twilio after-hours SMS to {alert_phone}: {e}", flush=True)
            else:
                print(f"[ALERT NOTICE] TWILIO_PHONE_NUMBER not set in .env; skipping SMS.", flush=True)

            wa_to = alert_phone if alert_phone.startswith('whatsapp:') else f"whatsapp:{alert_phone}"
            print(f"[ALERT] Attempting to send after-hours Twilio WhatsApp to {wa_to}...", flush=True)
            try:
                from twilio.rest import Client
                client = Client(twilio_sid, twilio_token)
                wa_body = f"⚠️ *AFTER HOURS INTRUSION ALERT*\n\nPerson Detected: *{person_summary}*\nTimestamp: *{timestamp_str}*\nCamera: *{camera_source}*\n\nPlease verify facility security immediately."
                wa_msg = client.messages.create(body=wa_body, from_=twilio_wa_from, to=wa_to)
                print(f"[ALERT SUCCESS] Twilio after-hours WhatsApp sent to {wa_to} (SID: {wa_msg.sid})", flush=True)
            except Exception as e:
                print(f"[ALERT ERROR] Failed to send Twilio after-hours WhatsApp to {wa_to}: {e}", flush=True)
        else:
            print(f"[ALERT NOTICE] ALERT_PHONE is set ({alert_phone}), but TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN environment variables not configured in .env.", flush=True)
    else:
        print(f"[ALERT NOTICE] After-hours SMS/WhatsApp alert skipped (ALERT_PHONE environment variable not set in .env).", flush=True)


def run_fire_detection(frame_bgr, conf_thresh=0.60, camera_source="Unknown", force_log=False):
    """Runs YOLO fire/smoke inference on frame_bgr (OpenCV format).
    Returns (detections, fire_found).
    Logs event to fire_alerts DB table with 60-second cooldown / 5s reset gap
    and triggers asynchronous external Email, SMS & WhatsApp notifications."""
    if fire_model is None or frame_bgr is None:
        return [], False

    try:
        results = fire_model(frame_bgr, conf=conf_thresh, verbose=False)
        detections = []
        fire_found = False

        if results and len(results) > 0:
            boxes = results[0].boxes
            for box in boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                label = results[0].names.get(cls_id, "fire")
                detections.append({
                    'box': [x1, y1, x2, y2],
                    'confidence': round(conf, 2),
                    'label': label
                })
                fire_found = True

        if fire_found:
            now = time.time()
            should_log = force_log
            with fire_alert_tracker['lock']:
                last_logged = fire_alert_tracker['last_logged_time']
                last_detected = fire_alert_tracker['last_detected_time']

                if force_log or (now - last_logged >= FIRE_LOG_COOLDOWN) or (now - last_detected >= FIRE_RESET_GAP):
                    should_log = True
                    fire_alert_tracker['last_logged_time'] = now

                fire_alert_tracker['last_detected_time'] = now

            if should_log and detections:
                timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                top_conf = max(d['confidence'] for d in detections)
                top_label = detections[0]['label']

                try:
                    conn = sqlite3.connect('database.db', timeout=10)
                    c = conn.cursor()
                    c.execute(
                        'INSERT INTO fire_alerts (timestamp, confidence, label, camera_source) VALUES (?, ?, ?, ?)',
                        (timestamp_str, top_conf, top_label, camera_source)
                    )
                    conn.commit()
                    conn.close()
                    print(f"[{timestamp_str}] Fire alert logged to database from {camera_source}")
                except Exception as db_err:
                    print("Fire alert DB insert error:", db_err)

                log_event(
                    event_type='fire',
                    severity='critical',
                    message=f'{top_label} detected ({top_conf * 100:.0f}% confidence)',
                    camera_source=camera_source,
                    confidence=top_conf,
                    ref_table='fire_alerts'
                )

                # Send external Email, SMS, and WhatsApp alerts asynchronously
                send_external_fire_alerts(timestamp_str, top_conf, top_label, camera_source)

        return detections, fire_found

    except Exception as err:
        print("Fire detection inference error:", err)
        return [], False


exit_camera_lock = threading.Lock()
exit_camera_state = {
    'running': False,
    'grabber': None,
    'thread': None,
    'stop_event': None,
    'latest': {'faces': [], 'message': 'Exit camera not started yet.'}
}


def exit_camera_worker(grabber, stop_event):
    if not grabber.isOpened():
        with exit_camera_lock:
            exit_camera_state['latest'] = {
                'faces': [],
                'error': 'Could not connect to the RTSP stream. Check the URL and make sure the phone app is running and on the same network.'
            }
        return

    NORMAL_INTERVAL = 1.0   # seconds between recognition runs when idle
    FAST_INTERVAL = 0.25    # seconds between runs while waiting on a blink
    current_interval = NORMAL_INTERVAL
    last_processed = 0.0
    frame_counter = 0
    latest_fire_boxes = []
    latest_fire_alert = False

    try:
        while not stop_event.is_set():
            success, frame_bgr = grabber.read()
            if not success or frame_bgr is None:
                time.sleep(0.05)
                continue

            now = time.time()
            if now - last_processed < current_interval:
                time.sleep(0.01)
                continue  # Skip heavy processing this frame

            last_processed = now
            frame_counter += 1
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

            # Fire detection run every 3rd frame as requested in Part 2 requirement 2
            if frame_counter % 3 == 0 or frame_counter == 1:
                latest_fire_boxes, latest_fire_alert = run_fire_detection(frame_bgr, conf_thresh=0.50, camera_source="RTSP Exit Camera")

            try:
                result = process_exit_image(frame_rgb)
            except Exception as e:
                result = {'faces': [], 'error': str(e)}

            result['fire_alert'] = latest_fire_alert
            result['fire_boxes'] = latest_fire_boxes

            if result.get('faces'):
                ah_alert, _ = process_after_hours_check(result['faces'], camera_source="RTSP Exit Camera")
                result['after_hours_alert'] = ah_alert

            any_pending = any(f.get('status') == 'liveness_pending' for f in result.get('faces', []))
            current_interval = FAST_INTERVAL if any_pending else NORMAL_INTERVAL

            # Generate live snapshot preview frame with face and fire bounding box overlays
            try:
                snapshot = frame_bgr.copy()

                # 1. Draw Face boxes
                for face in result.get('faces', []):
                    if 'box' in face:
                        top, right, bottom, left = face['box']
                        color = (255, 107, 107) # BGR
                        if face.get('status') == 'marked':
                            color = (246, 130, 59)
                        elif face.get('status') == 'already_exited':
                            color = (11, 158, 245)
                        cv2.rectangle(snapshot, (left, top), (right, bottom), color, 2)
                        label = face.get('name', 'Unknown')
                        cv2.putText(snapshot, label, (left, max(top - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                # 2. Draw Fire boxes (in red BGR)
                for fbox in latest_fire_boxes:
                    if 'box' in fbox:
                        x1, y1, x2, y2 = fbox['box']
                        cv2.rectangle(snapshot, (x1, y1), (x2, y2), (0, 0, 255), 3)
                        flabel = f"🔥 {fbox.get('label', 'fire')} ({fbox.get('confidence', 0.0):.2f})"
                        cv2.putText(snapshot, flabel, (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

                h, w = snapshot.shape[:2]
                if w > 640:
                    new_h = int(h * (640 / w))
                    snapshot = cv2.resize(snapshot, (640, new_h))

                ret, buffer = cv2.imencode('.jpg', snapshot, [cv2.IMWRITE_JPEG_QUALITY, 65])
                if ret:
                    result['frame'] = 'data:image/jpeg;base64,' + base64.b64encode(buffer).decode('utf-8')
            except Exception as snap_err:
                print('Snapshot encode error:', snap_err)

            with exit_camera_lock:
                exit_camera_state['latest'] = result
    finally:
        grabber.stop()


@app.route('/exit-camera/start', methods=['POST'])
@login_required
def start_exit_camera():
    data = request.get_json(silent=True) or {}
    rtsp_url = (data.get('rtsp_url') or '').strip()
    transport = (data.get('transport') or 'tcp').strip().lower()

    if not rtsp_url:
        return jsonify({'success': False, 'message': 'RTSP URL is required.'})

    with exit_camera_lock:
        if exit_camera_state['running']:
            return jsonify({'success': False, 'message': 'Exit camera is already running.'})

        try:
            grabber = RTSPFrameGrabber(rtsp_url, transport=transport)
        except Exception as e:
            return jsonify({'success': False, 'message': f'Failed to open RTSP stream ({transport}): {str(e)}'})

        stop_event = threading.Event()
        thread = threading.Thread(target=exit_camera_worker, args=(grabber, stop_event), daemon=True)
        exit_camera_state['grabber'] = grabber
        exit_camera_state['stop_event'] = stop_event
        exit_camera_state['thread'] = thread
        exit_camera_state['running'] = True
        exit_camera_state['latest'] = {'faces': [], 'message': f'Connecting to RTSP stream via {transport.upper()}...'}
        thread.start()

    return jsonify({'success': True, 'message': f'Exit camera starting ({transport.upper()})...'})


@app.route('/exit-camera/stop', methods=['POST'])
@login_required
def stop_exit_camera():
    with exit_camera_lock:
        if not exit_camera_state['running']:
            return jsonify({'success': False, 'message': 'Exit camera is not running.'})

        exit_camera_state['stop_event'].set()
        if exit_camera_state.get('grabber'):
            exit_camera_state['grabber'].stop()
        exit_camera_state['running'] = False
        exit_camera_state['latest'] = {'faces': [], 'message': 'Camera stopped.'}

    return jsonify({'success': True, 'message': 'Exit camera stopped.'})


@app.route('/exit-camera/status')
def exit_camera_status():
    """Lightweight polling endpoint — no image upload, just returns
    whatever the background worker last computed."""
    with exit_camera_lock:
        return jsonify(exit_camera_state['latest'])


@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect('database.db', timeout=10)
    c = conn.cursor()
    query = """
        SELECT attendance.id, 
               COALESCE(students.name, '[Deleted Student]'), 
               COALESCE(students.roll_no, 'N/A'), 
               COALESCE(students.class, 'N/A'),
               attendance.date, attendance.entry_time, attendance.exit_time, attendance.status
        FROM attendance
        LEFT JOIN students ON attendance.student_id = students.id
        ORDER BY attendance.date DESC, attendance.entry_time DESC
    """
    c.execute(query)
    records = c.fetchall()

    c.execute('SELECT id, name, roll_no, class FROM students ORDER BY name ASC')
    students_list = c.fetchall()

    # ---- Dashboard statistics (today only) ----
    today = datetime.now().strftime('%Y-%m-%d')

    c.execute(
        'SELECT COUNT(DISTINCT student_id) FROM attendance WHERE date = ? AND entry_time IS NOT NULL',
        (today,)
    )
    total_present = c.fetchone()[0]

    c.execute(
        'SELECT COUNT(*) FROM attendance WHERE date = ? AND entry_time IS NOT NULL',
        (today,)
    )
    entry_count = c.fetchone()[0]

    c.execute(
        'SELECT COUNT(*) FROM attendance WHERE date = ? AND exit_time IS NOT NULL',
        (today,)
    )
    exit_count = c.fetchone()[0]

    c.execute(
        'SELECT COUNT(*) FROM attendance WHERE date = ? AND entry_time IS NOT NULL AND exit_time IS NULL',
        (today,)
    )
    still_inside = c.fetchone()[0]

    c.execute(
        'SELECT COUNT(*) FROM attendance WHERE date = ? AND entry_time IS NOT NULL AND exit_time IS NOT NULL',
        (today,)
    )
    completed_day = c.fetchone()[0]

    conn.close()

    stats = {
        'total_present': total_present,
        'entry_count': entry_count,
        'exit_count': exit_count,
        'still_inside': still_inside,
        'completed_day': completed_day,
    }

    return render_template('dashboard.html', records=records, stats=stats, students_list=students_list)


@app.route('/export-attendance')
@login_required
def export_attendance():
    """Generate an .xlsx export of the full attendance table on demand."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        query = """
            SELECT COALESCE(students.roll_no, 'N/A'), 
                   COALESCE(students.name, '[Deleted Student]'), 
                   COALESCE(students.class, 'N/A'),
                   attendance.date, attendance.entry_time, attendance.exit_time, attendance.status
            FROM attendance
            LEFT JOIN students ON attendance.student_id = students.id
            ORDER BY attendance.date DESC, attendance.entry_time DESC
        """
        c.execute(query)
        records = c.fetchall()
        conn.close()

        wb = Workbook()
        ws = wb.active
        ws.title = 'Attendance'

        headers = ['Roll No', 'Name', 'Class', 'Date', 'Entry Time', 'Exit Time', 'Status']
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=1, column=col_idx).font = Font(bold=True)

        for roll_no, name, student_class, date, entry_time, exit_time, status in records:
            ws.append([
                roll_no,
                name,
                student_class,
                date,
                entry_time if entry_time else '-',
                exit_time if exit_time else '-',
                status
            ])

        # Auto-fit column widths roughly based on content length
        for column_cells in ws.columns:
            max_length = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
            ws.column_dimensions[column_cells[0].column_letter].width = max_length + 4

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filename = 'attendance_export_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.xlsx'

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        print('Export error:', e)
        return jsonify({'success': False, 'message': 'Export failed: ' + str(e)}), 500


@app.route('/delete-attendance/<int:attendance_id>', methods=['POST'])
@login_required
def delete_attendance(attendance_id):
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('DELETE FROM attendance WHERE id = ?', (attendance_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Record deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/delete-student/<int:student_id>', methods=['POST'])
@login_required
def delete_student(student_id):
    """Deletes a single student's registered face embedding and profile
    from the students table. Existing attendance records remain intact
    in the database and show as [Deleted Student] on the dashboard/export
    for audit trail preservation."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('DELETE FROM students WHERE id = ?', (student_id,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Student profile and face data deleted successfully.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/delete-all-attendance', methods=['POST'])
@login_required
def delete_all_attendance():
    """Clears every row from the attendance table. Does NOT touch the
    students table — registered faces are unaffected."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('DELETE FROM attendance')
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'All attendance records deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/delete-all-students', methods=['POST'])
@login_required
def delete_all_students():
    """Clears every registered face from the students table. Also clears
    attendance, since attendance rows pointing at a deleted student would
    otherwise silently disappear from the dashboard's JOIN query anyway —
    better to remove them explicitly than leave invisible orphan rows."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('DELETE FROM attendance')
        c.execute('DELETE FROM students')
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'All registered faces deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/test-fire', methods=['GET', 'POST'])
def test_fire_endpoint():
    """Test Mode Endpoint (Part 2 requirement 6): Runs fire detection on
    test_fire.jpg (or uploaded image/frame), generates red bounding box
    snapshot, triggers DB log entry, and returns payload to test UI alarm."""
    try:
        data = request.get_json(silent=True) or {}
        image_data = data.get('image')

        if image_data:
            img_bgr = decode_base64_image(image_data)
            # convert RGB from PIL back to BGR for cv2 drawing
            img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_RGB2BGR)
        else:
            test_path = os.path.join(os.path.dirname(__file__), 'test_fire_clear.jpg')
            if not os.path.exists(test_path):
                test_path = os.path.join(os.path.dirname(__file__), 'test_fire.jpg')
            if not os.path.exists(test_path):
                return jsonify({'success': False, 'error': 'Test fire image not found on server.'})
            img_bgr = cv2.imread(test_path)

        if img_bgr is None:
            return jsonify({'success': False, 'error': 'Could not decode test image.'})

        fire_boxes, fire_alert = run_fire_detection(img_bgr, conf_thresh=0.50, camera_source="Test Mode", force_log=True)

        # Draw red bounding boxes for preview
        snapshot = img_bgr.copy()
        for fbox in fire_boxes:
            if 'box' in fbox:
                x1, y1, x2, y2 = fbox['box']
                cv2.rectangle(snapshot, (x1, y1), (x2, y2), (0, 0, 255), 3)
                flabel = f"🔥 {fbox.get('label', 'fire')} ({fbox.get('confidence', 0.0):.2f})"
                cv2.putText(snapshot, flabel, (x1, max(y1 - 10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

        h, w = snapshot.shape[:2]
        if w > 640:
            new_h = int(h * (640 / w))
            snapshot = cv2.resize(snapshot, (640, new_h))

        ret, buffer = cv2.imencode('.jpg', snapshot, [cv2.IMWRITE_JPEG_QUALITY, 75])
        frame_base64 = ''
        if ret:
            frame_base64 = 'data:image/jpeg;base64,' + base64.b64encode(buffer).decode('utf-8')

        return jsonify({
            'success': True,
            'fire_alert': fire_alert,
            'fire_boxes': fire_boxes,
            'frame': frame_base64,
            'message': f"Detection completed: {len(fire_boxes)} fire/smoke region(s) found." if fire_alert else "No fire detected in test image."
        })
    except Exception as e:
        print("Test fire endpoint error:", e)
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/fire-alerts', methods=['GET'])
@login_required
def get_fire_alerts():
    """Retrieve logged fire alerts from database for dashboard viewing."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('SELECT id, timestamp, confidence, label, camera_source FROM fire_alerts ORDER BY id DESC LIMIT 50')
        rows = c.fetchall()
        conn.close()

        alerts = []
        for r in rows:
            alerts.append({
                'id': r[0],
                'timestamp': r[1],
                'confidence': r[2],
                'label': r[3],
                'camera_source': r[4]
            })
        return jsonify({'success': True, 'alerts': alerts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/test-after-hours', methods=['GET', 'POST'])
def test_after_hours_endpoint():
    """Test Mode Endpoint for After-Hours Intrusion Detection: Simulates a person
    detected during the restricted window, logs row to after_hours_alerts DB,
    and dispatches Email + SMS/WhatsApp alerts."""
    try:
        data = request.get_json(silent=True) or {}
        person_name = data.get('person_name', 'Test Intruder / Employee')
        roll_no = data.get('roll_no', 'TEST-888')

        test_faces = [{'name': person_name, 'roll_no': roll_no}]
        triggered, summary = process_after_hours_check(test_faces, camera_source="Test Mode", force_check=True)

        return jsonify({
            'success': True,
            'after_hours_alert': triggered,
            'person_summary': summary,
            'message': f"After-hours intrusion test executed for {summary}. Email/SMS/WhatsApp dispatch initiated."
        })
    except Exception as e:
        print("Test after-hours endpoint error:", e)
        return jsonify({'success': False, 'error': str(e)})
@app.route('/api/events', methods=['GET'])
@login_required
def api_events():
    """Unified event timeline — fire, after-hours, and attendance events
    in one feed. Optional query params: type, severity, limit."""
    event_type = request.args.get('type')
    severity = request.args.get('severity')
    limit = int(request.args.get('limit', 50))
    return jsonify({'success': True, 'events': get_recent_events(limit, event_type, severity)})

@app.route('/api/after-hours-alerts', methods=['GET'])
@login_required
def get_after_hours_alerts():
    """Retrieve logged intrusion/after-hours alerts from database for dashboard viewing."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        c.execute('SELECT id, timestamp, person_name, confidence, camera_source FROM after_hours_alerts ORDER BY id DESC LIMIT 50')
        rows = c.fetchall()
        conn.close()

        alerts = []
        for r in rows:
            alerts.append({
                'id': r[0],
                'timestamp': r[1],
                'person_name': r[2],
                'confidence': r[3],
                'camera_source': r[4]
            })
        return jsonify({'success': True, 'alerts': alerts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    print("Flask server ab start ho raha hai...")
    app.run(debug=True, threaded=True)

