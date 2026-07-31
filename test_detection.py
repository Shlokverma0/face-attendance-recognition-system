import cv2
from insightface.app import FaceAnalysis

# Initialize InsightFace
app = FaceAnalysis()
app.prepare(ctx_id=0)

# Open webcam
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Could not open camera!")
    exit()

while True:
    ret, frame = cap.read()

    if not ret:
        break

    # Detect faces
    faces = app.get(frame)

    for face in faces:
        # Bounding box
        x1, y1, x2, y2 = map(int, face.bbox)

        # Confidence score
        confidence = face.det_score

        # Draw rectangle
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # Show confidence
        cv2.putText(
            frame,
            f"{confidence:.2f}",
            (x1, y1 - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2
        )

    cv2.imshow("Face Detection", frame)

    # Press q to quit
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()