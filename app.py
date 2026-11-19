import os
import json
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)
app.json.ensure_ascii = False
DB_FILE = "cloud_records.json"


def generate_prescription(payload: dict) -> dict:
    """Prescription Engine: ประมวลผลคำแนะนำสูตรปุ๋ยจากค่าดินและผล AI"""
    soil = payload.get("soil_5in1", {})
    ai = payload.get("leaf_ai_analysis", {})

    n = soil.get("nitrogen_mg_kg", 0)
    p = soil.get("phosphorus_mg_kg", 0)
    k = soil.get("potassium_mg_kg", 0)
    predicted_class = ai.get("predicted_class", "unknown")

    recommendations = []
    if n < 25 or predicted_class == "low_n":
        recommendations.append("เสริมธาตุไนโตรเจน (N) ตามระยะการเจริญเติบโตของข้าว")
    if p < 15 or predicted_class == "low_p":
        recommendations.append("ตรวจสอบและเสริมธาตุฟอสฟอรัส (P) เพื่อช่วยระบบรากและการแตกกอ")
    if k < 80 or predicted_class == "low_k":
        recommendations.append("เสริมธาตุโพแทสเซียม (K) เพื่อเพิ่มความแข็งแรงของลำต้นและลดอาการขอบใบไหม้")

    if not recommendations:
        recommendations.append("สภาพดินและใบข้าวอยู่ในเกณฑ์ปกติ รักษาระดับน้ำและติดตามตามรอบแปลง")

    return {
        "status": "evaluated",
        "prescription_notes": recommendations
    }


def save_record(record: dict):
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        records = []

    records.append(record)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=4, ensure_ascii=False)


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Cloud API is running"})


@app.route("/api/v1/sensor-data", methods=["POST"])
def receive_sensor_data():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON payload"}), 400

    prescription = generate_prescription(data)
    record = {
        "received_at": datetime.now().isoformat(),
        "payload": data,
        "prescription": prescription
    }
    save_record(record)

    return jsonify({
        "message": "Data received and processed",
        "prescription": prescription
    }), 201


@app.route("/api/v1/records", methods=["GET"])
def get_records():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        records = []
    return jsonify({"count": len(records), "records": records})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
