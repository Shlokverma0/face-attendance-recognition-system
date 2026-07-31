# 📸 Face Attendance Recognition System

An AI-powered face recognition based attendance management system built with **Python**, **Flask**, **OpenCV**, and **face_recognition** library. Students register their face once, and attendance is marked automatically using live camera-based face recognition.

---

## 🚀 Features

- **Student Registration** — Capture 5 face images via webcam and register with Name, Roll Number, and Class
- **Live Face Recognition** — Mark attendance instantly using webcam face detection
- **Duplicate Prevention** — Prevents marking attendance twice on the same day
- **Attendance Dashboard** — View all attendance records in a table format
- **SQLite Database** — Lightweight local database for storing student and attendance data

---

## 🛠️ Tech Stack

- **Backend:** Python, Flask
- **Face Recognition:** face_recognition, dlib, OpenCV
- **Database:** SQLite3
- **Frontend:** HTML, CSS, JavaScript

---

## 📂 Project Structure
face-attendance-recognition-system/
│
├── static/
│   ├── style.css
│   └── script.js
│
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── register.html
│   ├── mark_attendance.html
│   └── dashboard.html
│
├── app.py
├── database.db
├── requirements.txt
└── README.md

---

## ⚙️ Setup Instructions

### 1. Clone the repository
```bash
git clone https://github.com/Shlokverma0/face-attendance-recognition-system.git
cd face-attendance-recognition-system
```

### 2. Create a virtual environment
```bash
python -m venv venv
```

### 3. Activate the virtual environment

**Windows:**
```bash
venv\Scripts\activate
```

**Mac/Linux:**
```bash
source venv/bin/activate
```

### 4. Install dependencies
```bash
pip install -r requirements.txt
```

⚠️ **Note:** If you get a `pkg_resources` error while installing `face_recognition`, run these commands:
```bash
pip install "setuptools<81"
pip install git+https://github.com/ageitgey/face_recognition_models
```

### 5. Run the application
```bash
python app.py
```

### 6. Open in browser
http://127.0.0.1:5000

---

## 📖 How to Use

1. Go to **Register** page → Start Camera → Capture 5 Images → Fill details → Click Register
2. Go to **Mark Attendance** page → Start Camera → Click Mark Present
3. Go to **Dashboard** page → View all attendance records

---

## 👥 Contributors

- [Arpit Tyagi](https://github.com/Arpit-tyagi001)
- [Shlok Verma](https://github.com/shlokverma0)


---

## 📄 License

This project is open source and available for educational purposes.
