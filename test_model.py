from ultralytics import YOLO
model = YOLO('models/best.pt') 
results = model('test_fire.jpg') 
results[0].show()