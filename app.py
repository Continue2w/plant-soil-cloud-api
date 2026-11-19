import os
import json
import math
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
app.json.ensure_ascii = False
DB_FILE = "cloud_records.json"

CROP_PROFILES = {
    "rice": {
        "early": {"moist": 75.0, "ph": 6.0, "ec": 1.2, "n": 55.0, "p": 25.0, "k": 40.0},
        "mid":   {"moist": 80.0, "ph": 6.2, "ec": 1.4, "n": 45.0, "p": 30.0, "k": 50.0},
        "late":  {"moist": 60.0, "ph": 6.2, "ec": 1.0, "n": 20.0, "p": 45.0, "k": 75.0},
    }
}


def compute_vpd(temp_c, humid_pct):
    if temp_c is None or humid_pct is None:
        return None
    svp = 0.61078 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    avp = svp * (humid_pct / 100.0)
    return round(max(0.0, svp - avp), 2)


def generate_prescription(payload: dict) -> dict:
    """Prescription Engine: วิเคราะห์ค่าจริงทุกพารามิเตอร์ให้ตรงกับหน้า Web Dashboard"""
    plant = payload.get("plant", "rice")
    stage = payload.get("growth_stage", "early")
    target = CROP_PROFILES.get(plant, CROP_PROFILES["rice"]).get(stage, CROP_PROFILES["rice"]["early"])

    soil = payload.get("soil_5in1", {})
    env = payload.get("environment", {})
    ai = payload.get("leaf_ai_analysis", {})
    forecast = payload.get("forecast", {})
    history = payload.get("historical_trend", {})

    n = float(soil.get("nitrogen_mg_kg", 0))
    p = float(soil.get("phosphorus_mg_kg", 0))
    k = float(soil.get("potassium_mg_kg", 0))
    ph = float(soil.get("ph", 6.2))
    ec = float(soil.get("ec", 1.0))
    moist = float(soil.get("moisture_pct", 50.0))

    temp = env.get("temperature_c")
    humid = env.get("humidity_pct")
    vpd = env.get("vpd_kpa")
    if vpd is None and temp is not None and humid is not None:
        vpd = compute_vpd(float(temp), float(humid))

    predicted_class = ai.get("predicted_class", "unknown")
    confidence = ai.get("confidence_pct")

    recommendations = []
    severity = "normal"
    risk_score = 0.0

    # 1. วิเคราะห์ N-P-K เทียบกับเป้าหมายของระยะการเติบโตและผล AI
    if n < target["n"] or predicted_class == "low_n":
        recommendations.append(f"เสริมธาตุไนโตรเจน (N) ให้ถึงเป้าหมาย {int(target['n'])} mg/kg")
        severity = "warning"
        risk_score += max(5.0, (target["n"] - n) * 0.4)
    if p < target["p"] or predicted_class == "low_p":
        recommendations.append(f"เสริมธาตุฟอสฟอรัส (P) ให้ถึงเป้าหมาย {int(target['p'])} mg/kg เพื่อกระตุ้นรากและการแตกกอ")
        severity = "warning"
        risk_score += max(5.0, (target["p"] - p) * 0.5)
    if k < target["k"] or predicted_class == "low_k":
        recommendations.append(f"เสริมธาตุโพแทสเซียม (K) ให้ถึงเป้าหมาย {int(target['k'])} mg/kg เพื่อเสริมความแข็งแรงลำต้น")
        severity = "warning"
        risk_score += max(5.0, (target["k"] - k) * 0.3)

    # 2. วิเคราะห์ pH, EC, ความชื้น และ VPD
    if ph < 5.5:
        recommendations.append("ดินเป็นกรดจัด ควรปรับปรุงดินด้วยปูนมาร์ลหรือโดโลไมท์ก่อนใส่ปุ๋ย")
        severity = "critical"
        risk_score += 15.0
    elif ph > 7.5:
        recommendations.append("ดินเป็นด่าง อาจทำให้พืชดูดซึมธาตุอาหารรองได้น้อยลง")
        severity = "warning"
        risk_score += 10.0

    if ec > target["ec"] + 0.4:
        recommendations.append(f"ค่า EC สูง ({ec:.2f} mS/cm) เสี่ยงดินเค็ม ควรระบายน้ำล้างเกลือ")
        severity = "critical"
        risk_score += 12.0

    if moist < target["moist"] - 10:
        recommendations.append(f"ความชื้นดินต่ำกว่าเป้าหมาย ({moist:.1f}% / เป้าหมาย {int(target['moist'])}%) ควรเพิ่มการให้น้ำ")
        severity = "critical"
        risk_score += 12.0

    if vpd is not None and float(vpd) > 1.6:
        recommendations.append(f"ค่า VPD สูง ({vpd} kPa) อากาศแห้งจัด พืชปิดปากใบ ควรงดพ่นปุ๋ยทางใบช่วงแดดจัด")
        risk_score += 8.0

    if predicted_class in ("fungal", "blast", "blight"):
        recommendations.append(f"ตรวจพบโรคทางใบ ({predicted_class}) ควรแยกจัดการและใช้สารชีวภัณฑ์ควบคุมเชื้อรา")
        severity = "critical"
        risk_score += 20.0

    if len(recommendations) >= 3:
        severity = "critical"

    if not recommendations:
        recommendations.append("สภาพดิน ใบข้าว และสภาพอากาศอยู่ในเกณฑ์สมดุล รักษาระดับน้ำและติดตามตามรอบ")

    # 3. คำนวณวันคงเหลือของธาตุอาหาร (Decay Days) จากปริมาณจริงและอัตราชะล้างตามความชื้น
    leach_factor = 1.25 if moist > 75 else 1.0
    decay_n = max(1, round((n / 3.5) / leach_factor))
    decay_p = max(1, round((p / 1.5) / leach_factor))
    decay_k = max(1, round((k / 4.5) / leach_factor))

    # 4. คำนวณดัชนีดิน FAO Ecotope (0-100%)
    ph_score = max(0, 100 - abs(ph - target["ph"]) * 40)
    moist_score = max(0, 100 - abs(moist - target["moist"]) * 2)
    npk_score = min(100, ((min(n / target["n"], 1.2) + min(p / target["p"], 1.2) + min(k / target["k"], 1.2)) / 3.0) * 100)
    fao_index = round((ph_score + moist_score + npk_score) / 3.0, 1)
    fao_label = "เหมาะสมมาก" if fao_index >= 80 else ("เหมาะสมปานกลาง" if fao_index >= 60 else "ต้องปรับปรุงดิน")

    # 5. สรุปวิเคราะห์ 3 ช่วงเวลา (อดีต / ปัจจุบัน / พยากรณ์ 72 ชม.)
    moist_drop = history.get("moist_drop_rate_pct_day")
    past_text = (
        f"ความชื้นลดลงเฉลี่ย {float(moist_drop):.1f}%/วัน จากข้อมูลย้อนหลังจริง"
        if moist_drop is not None
        else f"บันทึกข้อมูลสะสมในระบบ ณ จุดตรวจ {payload.get('grid_location', 'point-1')}"
    )

    present_text = (
        f"N-P-K: {int(n)}-{int(p)}-{int(k)} mg/kg | pH {ph:.2f} | ผลใบ: {predicted_class}"
        + (f" ({confidence}%)" if confidence is not None else "")
    )

    rain_prob = forecast.get("rain_prob_pct")
    storm_alert = bool(forecast.get("storm_alert", False))
    if rain_prob is not None:
        future_text = (
            f"แจ้งเตือนพายุ โอกาสฝน {int(rain_prob)}%"
            if storm_alert
            else f"พยากรณ์ 72 ชม. โอกาสฝน {int(rain_prob)}%"
        )
    else:
        future_text = "ติดตามความชื้นดินและสภาพอากาศรอบถัดไป"

    proactive_protocol = (
        "งดการให้น้ำและปุ๋ยล่วงหน้า เปิดระบบระบายน้ำเพื่อเตรียมรับพายุ"
        if storm_alert
        else recommendations[0]
    )

    risk_percent = round(min(95.0, risk_score), 1)
    loss_est = "เสี่ยงผลผลิตตกต่ำ" if risk_percent > 20 else "อยู่ในเกณฑ์ควบคุมได้"

    return {
        "status": "evaluated",
        "severity": severity,
        "formula_summary": f"เป้าหมาย {int(target['n'])}-{int(target['p'])}-{int(target['k'])} | ปัจจุบัน {int(n)}-{int(p)}-{int(k)}",
        "summary_metrics": {
            "npk_ratio": f"{int(n)}-{int(p)}-{int(k)}",
            "ph": ph,
            "ec": ec,
            "moisture_pct": moist,
            "vpd_kpa": vpd
        },
        "decay_days": {
            "n": decay_n,
            "p": decay_p,
            "k": decay_k
        },
        "fao_ecotope": {
            "score_pct": fao_index,
            "status": fao_label
        },
        "yield_risk": {
            "risk_percent": risk_percent,
            "impact_label": loss_est
        },
        "timeline": {
            "past_7d": past_text,
            "present": present_text,
            "future_72h": future_text,
            "proactive_protocol": proactive_protocol
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
    return jsonify({"status": "Cloud API is running", "version": "2.0.0"})


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
