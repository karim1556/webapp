"""
inference.py — AE490 Phantom Limb Pain Detection
Loads trained ML models and provides a single predict() function.

Usage:
    from ml.inference import PainInference
    engine = PainInference()
    result = engine.predict({
        "posture_score": 55.0,
        "emg_rms":       120,
        "heart_rate":    92.0,
        "hrv_ms":        18.0,
        "breath_rate":   22.0,
        "breath_depth":  0.35,
    })
    # result → {"pain_flag": 1, "pain_probability": 0.74, "pain_level": 6.2}
"""

import os
import numpy as np
import joblib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ML_DIR   = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(ML_DIR, "models")

FEATURES = [
    "posture_score",
    "emg_rms",
    "heart_rate",
    "hrv_ms",
    "breath_rate",
    "breath_depth",
]

# Bayesian prior sensor weights (must match train_model.py)
SENSOR_PRIOR_WEIGHTS = {
    "posture_score": 1.20,
    "emg_rms":       1.25,
    "heart_rate":    1.10,
    "hrv_ms":        1.15,
    "breath_rate":   1.05,
    "breath_depth":  1.00,
}

# Safe physiological fallbacks when sensor data is missing / leads off
FEATURE_DEFAULTS = {
    "posture_score": 10.0,
    "emg_rms":       20,
    "heart_rate":    72.0,
    "hrv_ms":        45.0,
    "breath_rate":   15.0,
    "breath_depth":  0.70,
}


class PainInference:
    """Singleton-style ML inference engine for phantom limb pain."""

    def __init__(self):
        self._loaded = False
        self._clf    = None
        self._reg    = None
        self._scaler = None
        self._load_models()

    def _load_models(self):
        clf_path  = os.path.join(MODEL_DIR, "pain_classifier.pkl")
        reg_path  = os.path.join(MODEL_DIR, "pain_regressor.pkl")
        scl_path  = os.path.join(MODEL_DIR, "scaler.pkl")

        if not all(os.path.exists(p) for p in [clf_path, reg_path, scl_path]):
            logger.warning(
                "[ML] Models not found at %s. "
                "Run ml/train_model.py first. Using rule-based fallback.",
                MODEL_DIR,
            )
            return

        try:
            self._clf    = joblib.load(clf_path)
            self._reg    = joblib.load(reg_path)
            self._scaler = joblib.load(scl_path)
            self._loaded = True
            logger.info("[ML] Models loaded from %s", MODEL_DIR)
        except Exception as exc:
            logger.error("[ML] Failed to load models: %s", exc)

    @property
    def models_ready(self) -> bool:
        return self._loaded

    def _apply_bayesian_weights(self, X: np.ndarray) -> np.ndarray:
        """Apply per-sensor reliability priors to feature vector."""
        weights = np.array([SENSOR_PRIOR_WEIGHTS[f] for f in FEATURES])
        return X * weights

    def _rule_based_fallback(self, feat: dict) -> dict:
        """
        Heuristic pain estimate when ML models are not yet trained.
        Computes a weighted Z-score proxy from each sensor channel.
        """
        score = 0.0
        if feat["posture_score"] > 50:   score += 0.25
        if feat["emg_rms"]       > 100:  score += 0.30
        if feat["heart_rate"]    > 90:   score += 0.15
        if feat["hrv_ms"]        < 25:   score += 0.15
        if feat["breath_rate"]   > 20 or feat["breath_rate"] < 10:
            score += 0.10
        if feat["breath_depth"]  < 0.4:  score += 0.05

        pain_prob  = float(np.clip(score, 0.0, 1.0))
        pain_level = float(pain_prob * 10.0)
        return {
            "pain_flag":        int(pain_prob >= 0.60),
            "pain_probability": round(pain_prob, 4),
            "pain_level":       round(pain_level, 2),
            "model":            "rule_based_fallback",
        }

    def predict(self, sensor_dict: dict) -> dict:
        """
        Predict pain from a sensor reading dictionary.

        Parameters
        ----------
        sensor_dict : dict
            Keys: posture_score, emg_rms, heart_rate, hrv_ms,
                  breath_rate, breath_depth
            Missing keys are filled with safe physiological defaults.

        Returns
        -------
        dict with keys:
            pain_flag (int 0/1), pain_probability (float), pain_level (float),
            model (str: 'ml' | 'rule_based_fallback')
        """
        # Fill missing features with defaults
        feat = {f: sensor_dict.get(f, FEATURE_DEFAULTS[f]) or FEATURE_DEFAULTS[f]
                for f in FEATURES}

        if not self._loaded:
            return self._rule_based_fallback(feat)

        try:
            X = np.array([[feat[f] for f in FEATURES]], dtype=float)
            X = self._apply_bayesian_weights(X)
            X = self._scaler.transform(X)

            pain_flag   = int(self._clf.predict(X)[0])
            pain_prob   = float(self._clf.predict_proba(X)[0, 1])
            pain_level  = float(np.clip(self._reg.predict(X)[0], 0, 10))

            return {
                "pain_flag":        pain_flag,
                "pain_probability": round(pain_prob, 4),
                "pain_level":       round(pain_level, 2),
                "model":            "ml",
            }
        except Exception as exc:
            logger.error("[ML] Inference error: %s", exc)
            return self._rule_based_fallback(feat)


# ─── Module-level singleton ───────────────────────────────────────────────────
_engine: Optional[PainInference] = None

def get_engine() -> PainInference:
    global _engine
    if _engine is None:
        _engine = PainInference()
    return _engine


if __name__ == "__main__":
    engine = get_engine()
    test_cases = [
        {"posture_score": 5,  "emg_rms": 15,  "heart_rate": 68,  "hrv_ms": 60, "breath_rate": 15, "breath_depth": 0.8},
        {"posture_score": 70, "emg_rms": 180, "heart_rate": 105, "hrv_ms": 12, "breath_rate": 26, "breath_depth": 0.2},
        {"posture_score": 40, "emg_rms": 90,  "heart_rate": 85,  "hrv_ms": 28, "breath_rate": 9,  "breath_depth": 0.45},
    ]
    labels = ["No Pain", "Severe Pain", "Moderate Pain"]
    for label, t in zip(labels, test_cases):
        r = engine.predict(t)
        print(f"[{label:^15}] flag={r['pain_flag']} prob={r['pain_probability']:.3f} level={r['pain_level']:.1f} ({r['model']})")
