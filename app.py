from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from functools import wraps
import sqlite3
import base64
import numpy as np
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

app = Flask(__name__)
app.secret_key = 'shlok-face-attendance-secret-key-2026'  # used to sign the session cookie

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
    conn.commit()
    conn.close()
    print("Database ready.")

init_db()


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
            return jsonify({'faces': [], 'message': 'Koi face detect nahi hua.'})

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
                    # No record today at all -> mark entry, but only once a
                    # live blink has been confirmed for this student.
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

                else:
                    attendance_id, entry_time, exit_time = existing

                    if entry_time is not None and exit_time is None:
                        # Currently "inside" -> auto-mark exit here too,
                        # same as before. Now safe alongside the dedicated
                        # /mark-exit route because attendance_write_lock
                        # serializes both, so only one can actually write
                        # even if both cameras see the person at once.
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

                    else:
                        # Both entry & exit already done -> allow fresh re-entry,
                        # but respect cooldown after the exit.
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

            results.append(face_result)

        conn.close()
        return jsonify({'faces': results})

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
    return jsonify(process_exit_image(img_array))


# ---------------------------------------------------------------------------
# RTSP mobile-camera integration for the Exit station.
#
# Instead of the browser capturing frames from a webcam, a phone running an
# app like "IP Webcam" streams RTSP video directly to this server. A
# background thread continuously reads frames from that stream and runs
# them through the exact same process_exit_image() pipeline used above.
# The exit page just polls /exit-camera/status to show the latest result —
# it doesn't touch the camera itself at all.
# ---------------------------------------------------------------------------

exit_camera_lock = threading.Lock()
exit_camera_state = {
    'running': False,
    'thread': None,
    'stop_event': None,
    'latest': {'faces': [], 'message': 'Exit camera not started yet.'}
}


def exit_camera_worker(rtsp_url, stop_event):
    cap = cv2.VideoCapture(rtsp_url)

    if not cap.isOpened():
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

    try:
        while not stop_event.is_set():
            success, frame_bgr = cap.read()
            if not success:
                time.sleep(0.2)
                continue

            now = time.time()
            if now - last_processed < current_interval:
                continue  # keep draining the stream, but skip heavy processing this frame

            last_processed = now
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

            try:
                result = process_exit_image(frame_rgb)
            except Exception as e:
                result = {'faces': [], 'error': str(e)}

            any_pending = any(f.get('status') == 'liveness_pending' for f in result.get('faces', []))
            current_interval = FAST_INTERVAL if any_pending else NORMAL_INTERVAL

            with exit_camera_lock:
                exit_camera_state['latest'] = result
    finally:
        cap.release()


@app.route('/exit-camera/start', methods=['POST'])
@login_required
def start_exit_camera():
    data = request.get_json(silent=True) or {}
    rtsp_url = (data.get('rtsp_url') or '').strip()

    if not rtsp_url:
        return jsonify({'success': False, 'message': 'RTSP URL is required.'})

    with exit_camera_lock:
        if exit_camera_state['running']:
            return jsonify({'success': False, 'message': 'Exit camera is already running.'})

        stop_event = threading.Event()
        thread = threading.Thread(target=exit_camera_worker, args=(rtsp_url, stop_event), daemon=True)
        exit_camera_state['stop_event'] = stop_event
        exit_camera_state['thread'] = thread
        exit_camera_state['running'] = True
        exit_camera_state['latest'] = {'faces': [], 'message': 'Connecting to camera...'}
        thread.start()

    return jsonify({'success': True, 'message': 'Exit camera starting...'})


@app.route('/exit-camera/stop', methods=['POST'])
@login_required
def stop_exit_camera():
    with exit_camera_lock:
        if not exit_camera_state['running']:
            return jsonify({'success': False, 'message': 'Exit camera is not running.'})

        exit_camera_state['stop_event'].set()
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
        SELECT attendance.id, students.name, students.roll_no, students.class,
               attendance.date, attendance.entry_time, attendance.exit_time, attendance.status
        FROM attendance
        JOIN students ON attendance.student_id = students.id
        ORDER BY attendance.date DESC, attendance.entry_time DESC
    """
    c.execute(query)
    records = c.fetchall()

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

    return render_template('dashboard.html', records=records, stats=stats)


@app.route('/export-attendance')
@login_required
def export_attendance():
    """Generate an .xlsx export of the full attendance table on demand."""
    try:
        conn = sqlite3.connect('database.db', timeout=10)
        c = conn.cursor()
        query = """
            SELECT students.roll_no, students.name, students.class,
                   attendance.date, attendance.entry_time, attendance.exit_time, attendance.status
            FROM attendance
            JOIN students ON attendance.student_id = students.id
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


if __name__ == '__main__':
    print("Flask server ab start ho raha hai...")
    app.run(debug=True, threaded=True)
