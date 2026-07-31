from insightface.app import FaceAnalysis

app = FaceAnalysis()
app.prepare(ctx_id=0)

print("InsightFace working successfully!")