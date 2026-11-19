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


def process_device_telemetry(payload: dict, received_at: str) -> dict:
    """ประมวลผลสถานะและความผิดปกติของข้อมูลที่ส่งมาจากตัวอุปกรณ์"""
    device_id = str(payload.get("device_id") or "unknown-device").strip()
    grid_location = str(payload.get("grid_location") or "unassigned").strip()
    soil = payload.get("soil_5in1") or {}
    env = payload.get("environment") or {}
    ai = payload.get("leaf_ai_analysis") or {}

    anomalies = []

    for key, label in [
        ("nitrogen_mg_kg", "N"),
        ("phosphorus_mg_kg", "P"),
        ("potassium_mg_kg", "K"),
        ("moisture_pct", "ความชื้นดิน"),
        ("ec", "EC"),
    ]:
        val = soil.get(key)
        if isinstance(val, (int, float)) and val < 0:
            anomalies.append(f"ค่า {label} ติดลบ ({val})")

    moist = soil.get("moisture_pct")
    if isinstance(moist, (int, float)) and (moist < 0 or moist > 100):
        anomalies.append(f"ความชื้นดินอยู่นอกช่วง 0-100% ({moist})")

    ph = soil.get("ph")
    if isinstance(ph, (int, float)) and (ph < 0 or ph > 14):
        anomalies.append(f"ค่า pH อยู่นอกช่วง 0-14 ({ph})")

    has_soil = any(
        soil.get(k) is not None
        for k in ("nitrogen_mg_kg", "phosphorus_mg_kg", "potassium_mg_kg", "ph", "moisture_pct", "ec")
    )
    has_env = any(env.get(k) is not None for k in ("temperature_c", "humidity_pct", "vpd_kpa"))
    has_ai = bool(ai.get("predicted_class"))

    if anomalies:
        device_health = "sensor_warning"
    elif has_soil and has_ai:
        device_health = "online_full_telemetry"
    elif has_soil:
        device_health = "online_soil_only"
    else:
        device_health = "online_partial"

    return {
        "device_id": device_id,
        "grid_location": grid_location,
        "last_seen": received_at,
        "device_health": device_health,
        "capabilities": {
            "soil_5in1": has_soil,
            "environment": has_env,
            "leaf_ai": has_ai,
        },
        "anomalies": anomalies,
    }


def generate_prescription(payload: dict) -> dict:
    """Prescription Engine: วิเคราะห์ค่าดิน สภาพอากาศ และผล AI เพื่อสร้างคำแนะนำ"""
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

    leach_factor = 1.25 if moist > 75 else 1.0
    decay_n = max(1, round((n / 3.5) / leach_factor))
    decay_p = max(1, round((p / 1.5) / leach_factor))
    decay_k = max(1, round((k / 4.5) / leach_factor))

    ph_score = max(0, 100 - abs(ph - target["ph"]) * 40)
    moist_score = max(0, 100 - abs(moist - target["moist"]) * 2)
    npk_score = min(100, ((min(n / target["n"], 1.2) + min(p / target["p"], 1.2) + min(k / target["k"], 1.2)) / 3.0) * 100)
    fao_index = round((ph_score + moist_score + npk_score) / 3.0, 1)
    fao_label = "เหมาะสมมาก" if fao_index >= 80 else ("เหมาะสมปานกลาง" if fao_index >= 60 else "ต้องปรับปรุงดิน")

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
            "vpd_kpa": vpd,
        },
        "decay_days": {
            "n": decay_n,
            "p": decay_p,
            "k": decay_k,
        },
        "fao_ecotope": {
            "score_pct": fao_index,
            "status": fao_label,
        },
        "yield_risk": {
            "risk_percent": risk_percent,
            "impact_label": loss_est,
        },
        "timeline": {
            "past_7d": past_text,
            "present": present_text,
            "future_72h": future_text,
            "proactive_protocol": proactive_protocol,
        },
        "prescription_notes": recommendations,
    }


def build_devices_summary(records: list) -> list:
    """สรุปสถานะของแต่ละอุปกรณ์จากประวัติข้อมูลทั้งหมด"""
    devices_map = {}
    for r in records:
        payload = r.get("payload") or {}
        dev_info = r.get("device_status") or process_device_telemetry(payload, r.get("received_at") or "")
        dev_id = dev_info["device_id"]

        if dev_id not in devices_map:
            devices_map[dev_id] = {
                "device_id": dev_id,
                "grid_location": dev_info["grid_location"],
                "total_packets": 0,
                "anomaly_packets": 0,
                "last_seen": dev_info["last_seen"],
                "device_health": dev_info["device_health"],
                "capabilities": dev_info["capabilities"],
                "latest_anomalies": dev_info["anomalies"],
            }

        entry = devices_map[dev_id]
        entry["total_packets"] += 1
        if dev_info.get("anomalies"):
            entry["anomaly_packets"] += 1
        entry["grid_location"] = dev_info["grid_location"]
        entry["last_seen"] = dev_info["last_seen"]
        entry["device_health"] = dev_info["device_health"]
        entry["capabilities"] = dev_info["capabilities"]
        entry["latest_anomalies"] = dev_info["anomalies"]

    return list(devices_map.values())


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
    return jsonify({"status": "Cloud API is running", "version": "2.1.0"})


@app.route("/api/v1/sensor-data", methods=["POST"])
def receive_sensor_data():
    data = request.get_json(silent=True)
    if not data or "soil_5in1" not in data:
        return jsonify({"error": "Missing required 'soil_5in1' data"}), 400

    received_at = datetime.now().isoformat()
    device_status = process_device_telemetry(data, received_at)
    prescription = generate_prescription(data)

    record = {
        "id": len(load_records()) + 1,
        "received_at": received_at,
        "device_status": device_status,
        "payload": data,
        "prescription": prescription,
    }
    save_record(record)

    return jsonify({
        "message": "Data received and processed",
        "record_id": record["id"],
        "device_status": device_status,
        "prescription": prescription,
    }), 201


@app.route("/api/v1/records", methods=["GET"])
def get_records():
    records = load_records()
    grid_filter = request.args.get("grid")
    device_filter = request.args.get("device_id")

    if grid_filter:
        records = [
            r for r in records
            if r.get("payload", {}).get("grid_location") == grid_filter
        ]
    if device_filter:
        records = [
            r for r in records
            if r.get("payload", {}).get("device_id") == device_filter
        ]

    return jsonify({
        "count": len(records),
        "records": records,
    })


@app.route("/api/v1/devices", methods=["GET"])
def get_devices():
    records = load_records()
    devices = build_devices_summary(records)
    return jsonify({
        "count": len(devices),
        "devices": devices,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
