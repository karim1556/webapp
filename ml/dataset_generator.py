"""
dataset_generator.py — AE490 Phantom Limb Pain Detection
Generates a synthetic physiologically-grounded dataset for training ML models.

Features (6):
  posture_score  : 0–100  (anomalous limb posture / attempted phantom movement)
  emg_rms        : 0–350  (residual-limb muscle activity in ADC units)
  heart_rate     : 50–130 (BPM)
  hrv_ms         : 2–90   (RMSSD in ms)
  breath_rate    : 6–32   (breaths / min)
  breath_depth   : 0.0–1.0 (normalized tidal volume proxy)

Labels:
  pain_level     : 0–10  (continuous)
  pain_flag      : 0 / 1 (binary: pain_level >= 4)

Physiological rationale:
  Phantom limb pain activates the sympathetic nervous system →
    ↑ HR, ↓ HRV, ↑ EMG (involuntary motor attempts), ↑ posture_score,
    irregular breathing (breath-holding or rapid shallow breathing).
"""

import numpy as np
import pandas as pd
import os

RNG = np.random.default_rng(42)
N_SAMPLES = 6000

def generate_dataset(n: int = N_SAMPLES) -> pd.DataFrame:
    rows = []

    for _ in range(n):
        # Pain level: skewed toward low pain (realistic population distribution)
        pain_level = float(np.clip(RNG.beta(2, 4) * 10, 0, 10))
        p = pain_level / 10.0        # normalized 0–1 pain intensity
        p_noise = RNG.uniform(-0.05, 0.05)

        # ── Posture score ─────────────────────────────────────────────────────
        # Low pain → near-normal posture (0–25)
        # High pain → compensatory posture + phantom movement attempts (60–100)
        posture_base = 5 + 90 * (p ** 1.5)
        posture_score = float(np.clip(
            posture_base + RNG.normal(0, 8), 0, 100
        ))

        # ── EMG RMS ───────────────────────────────────────────────────────────
        # Higher pain → more involuntary motor cortex drive → higher EMG burst
        emg_base = 20 + 300 * (p ** 1.2)
        emg_rms = float(np.clip(
            emg_base + RNG.normal(0, 25), 0, 350
        ))

        # ── Heart rate ────────────────────────────────────────────────────────
        # Rest: 60–80 BPM; severe pain: 95–125 BPM
        hr_base = 65 + 55 * p
        heart_rate = float(np.clip(
            hr_base + RNG.normal(0, 7), 50, 130
        ))

        # ── HRV (RMSSD) ───────────────────────────────────────────────────────
        # Higher pain → sympathetic dominance → lower HRV
        hrv_base = 80 - 75 * p
        hrv_ms = float(np.clip(
            hrv_base + RNG.normal(0, 6), 2, 90
        ))

        # ── Breathing rate ────────────────────────────────────────────────────
        # Mild pain → breath-holding (slow, 8–12)
        # Severe pain → rapid shallow breathing (22–32)
        if p < 0.4:
            br_base = 16 - 8 * p
        else:
            br_base = 10 + 22 * p
        breath_rate = float(np.clip(
            br_base + RNG.normal(0, 2.5), 6, 32
        ))

        # ── Breath depth ──────────────────────────────────────────────────────
        # Pain → shallower breathing
        depth_base = 0.85 - 0.7 * p
        breath_depth = float(np.clip(
            depth_base + RNG.normal(0, 0.07), 0.0, 1.0
        ))

        # ── Labels ────────────────────────────────────────────────────────────
        pain_flag = 1 if pain_level >= 4.0 else 0

        rows.append({
            "posture_score": round(posture_score, 2),
            "emg_rms":       round(emg_rms, 2),
            "heart_rate":    round(heart_rate, 2),
            "hrv_ms":        round(hrv_ms, 2),
            "breath_rate":   round(breath_rate, 2),
            "breath_depth":  round(breath_depth, 4),
            "pain_level":    round(pain_level, 2),
            "pain_flag":     pain_flag,
        })

    df = pd.DataFrame(rows)
    return df


if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "synthetic_dataset.csv")

    df = generate_dataset()
    df.to_csv(out_path, index=False)

    print(f"[OK] Generated {len(df)} samples -> {out_path}")
    print(f"     Pain flag distribution:\n{df['pain_flag'].value_counts()}")
    print(f"\n     Feature stats:\n{df.describe().to_string()}")

