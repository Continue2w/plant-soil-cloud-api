import os
import json
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.json.ensure_ascii = False
DB_FILE = "cloud_records.json"


def generate_prescription(payload: dict) -> dict:
    """Prescription Engine: วิเคราะห์ค่าดินและผล AI เพื่อสร้างคำแนะนำเชิงลึก"""
    soil = payload.get("soil_5in1", {})
    ai = payload.get("leaf_ai_analysis", {})

    n = float(soil.get("nitrogen_mg_kg", 0))
    p = float(soil.get("phosphorus_mg_kg", 0))
    k = float(soil.get("potassium_mg_kg", 0))
    ph = float(soil.get("ph", 6.5))
    moisture = float(soil.get("moisture_pct", 50))
    predicted_class = ai.get("predicted_class", "unknown")

    recommendations = []
    severity = "normal"

    if n < 25 or predicted_class == "low_n":
        recommendations.append("เสริมธาตุไนโตรเจน (N) ตามระยะการเจริญเติบโตของข้าว")
        severity = "warning"
    if p < 15 or predicted_class == "low_p":
        recommendations.append("เสริมธาตุฟอสฟอรัส (P) เพื่อกระตุ้นระบบรากและการแตกกอ")
        severity = "warning"
    if k < 80 or predicted_class == "low_k":
        recommendations.append("เสริมธาตุโพแทสเซียม (K) เพื่อเพิ่มความแข็งแรงของลำต้นและลดอาการขอบใบไหม้")
        severity = "warning"

    if ph < 5.5:
        recommendations.append("ดินมีสภาพเป็นกรดจัด ควรปรับปรุงดินด้วยปูนมาร์ลหรือโดโลไมท์ก่อนใส่ปุ๋ย")
        severity = "critical"
    elif ph > 7.5:
        recommendations.append("ดินมีสภาพเป็นด่าง อาจทำให้พืชดูดซึมธาตุอาหารรองได้น้อยลง")
        severity = "warning"

    if moisture < 30:
        recommendations.append("ความชื้นในดินต่ำกว่าเกณฑ์ ควรเพิ่มการให้น้ำในแปลง")
        severity = "critical"

    if len(recommendations) >= 3:
        severity = "critical"

    if not recommendations:
        recommendations.append("สภาพดินและใบข้าวอยู่ในเกณฑ์ปกติ รักษาระดับน้ำและติดตามตามรอบแปลง")

    return {
        "status": "evaluated",
        "severity": severity,
        "summary_metrics": {
            "npk_ratio": f"{int(n)}-{int(p)}-{int(k)}",
            "ph": ph,
            "moisture_pct": moisture
        },
        "prescription_notes": recommendations
    }


def load_records() -> list:
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_record(record: dict):
    records = load_records()
    records.append(record)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=4, ensure_ascii=False)


@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "Cloud API is running", "version": "1.1.0"})


@app.route("/api/v1/sensor-data", methods=["POST"])
def receive_sensor_data():
    data = request.get_json(silent=True)
    if not data or "soil_5in1" not in data:
        return jsonify({"error": "Missing required 'soil_5in1' data"}), 400

    prescription = generate_prescription(data)
    record = {
        "id": len(load_records()) + 1,
        "received_at": datetime.now().isoformat(),
        "payload": data,
        "prescription": prescription
    }
    save_record(record)

    return jsonify({
        "message": "Data received and processed",
        "record_id": record["id"],
        "prescription": prescription
    }), 201


@app.route("/api/v1/records", methods=["GET"])
def get_records():
    records = load_records()
    grid_filter = request.args.get("grid")
    if grid_filter:
        records = [
            r for r in records
            if r.get("payload", {}).get("grid_location") == grid_filter
        ]

    return jsonify({
        "count": len(records),
        "records": records
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
