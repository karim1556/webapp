"""Reproducible synthetic inputs for the same display fields as the ESP32."""

import math
from random import Random


def clamp(value, low=0., high=1.):
    return min(high, max(low, value))


def firmware_score(fields, spasm):
    """Port of evaluatePainModel for generated rows with optical contact."""
    hr = clamp((fields["heart_rate"] - 78) / 32)
    hrv = clamp((28 - fields["hrv_rmssd"]) / 20)
    perfusion = clamp((1.10 - fields["perfusion_index"]) / .70)
    breathing = clamp((fields["respiration_rate"] - 18) / 10)
    humidity = clamp((fields["humidity_sweat"] - 55) / 25)
    temperature = clamp((fields["temperature_c"] - 35.5) / 2.5)
    motion = clamp((fields["posture_score"] - 20) / 45, 0, .60) + (.40 if spasm else 0)
    oxygen = clamp((96 - fields["spo2"]) / 6)
    bonus = .15 if hr > .50 and temperature > .50 and humidity > .50 else 0
    return clamp(.18 * hr + .15 * hrv + .15 * perfusion + .12 * breathing
                 + .13 * humidity + .12 * temperature + .10 * motion + .05 * oxygen + bonus)


def generate_fields(source):
    patient = int(source["patient_id"][1:])
    day = int(source["day"])
    minute = int(source["timestamp"][11:13]) * 60 + int(source["timestamp"][14:16])
    rng = Random(int(source["unix_time"]) + 1_000_003 * patient)

    # Three different schedules create both quiet and elevated segments for every patient.
    centers = (525 + 35 * patient + 11 * (day % 3), 1050 + 33 * patient)
    strain = max(clamp(1 - abs(minute - center) / (50 + 10 * patient)) for center in centers)
    strain *= .82 + .18 * ((day + patient) % 4) / 3
    baseline = math.sin(2 * math.pi * minute / 1440)
    source_hr = float(source["heart_rate_bpm"] or (69 + patient * 3))
    temperature = float(source["socket_temp_c"] or 32.0)
    humidity = float(source["socket_humidity_pct"] or 55.0)
    peak = float(source["accel_peak_g"] or 1.0)
    mean = float(source["accel_mag_mean_g"] or 1.0)
    angular_speed = float(source["gyro_mean_dps"] or 0.0)

    jerk = max(0., (peak - mean) * 9.81) + 15 * strain ** 3
    posture = clamp(abs(mean - 1.) * 9.81 * 14 + 10 * math.radians(angular_speed) + 50 * strain,
                    0, 100)
    fields = {
        "heart_rate": round(clamp(source_hr + 46 * strain, 42, 180), 1),
        "hrv_rmssd": round(clamp(47 + 7 * baseline - 44 * strain + rng.gauss(0, 3), 6, 85), 1),
        "spo2": round(clamp(98.4 - 5.2 * strain + rng.gauss(0, .24), 91, 100), 1),
        "perfusion_index": round(clamp(2.4 + .35 * baseline - 2.25 * strain + rng.gauss(0, .09), .20, 4.5), 2),
        "respiration_rate": round(clamp(13.5 + 17.5 * strain + rng.gauss(0, .8), 8, 36), 1),
        "temperature_c": round(clamp(temperature + 6.1 * strain, 24, 39.2), 1),
        "humidity_sweat": round(clamp(humidity + 30 * strain, 20, 95), 1),
        "muscle_spasm_jerk": round(jerk, 2),
        "posture_score": round(posture, 1),
    }
    score = firmware_score(fields, jerk > 12)
    device_class = ("SEVERE" if score >= .70 else "MODERATE" if score >= .45 else
                    "MILD" if score >= .25 else "NORMAL")
    fields.update({
        "hrv_sdnn": round(fields["hrv_rmssd"] * (1.16 + rng.uniform(-.08, .08)), 1),
        "pulse_raw_adc": round(clamp(2350 + 150 * baseline + 95 * fields["perfusion_index"]
                                     + rng.gauss(0, 125), 0, 4095)),
        "spo2_status": "In range" if fields["spo2"] >= 95 else "Low",
        "respiration_method": "PPG Sinus Arrhythmia",
        "blood_flow_status": "Reduced flow" if fields["perfusion_index"] < .8 else "Normal",
        "posture_status": "Agitated / Spasm" if posture > 60 else "Normal",
        "pain_index": round(score * 100, 1),
        "vas_score": round(score * 10, 1),
        "pain_status": device_class,
        "finger_detected": True,
    })
    return fields
