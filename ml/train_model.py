"""
train_model.py — AE490 Phantom Limb Pain Detection
Trains and saves the ML models used for pain inference.

Models:
  1. pain_classifier  — RandomForestClassifier  (predicts pain_flag 0/1)
  2. pain_regressor   — RandomForestRegressor    (predicts pain_level 0–10)
  3. scaler           — StandardScaler           (feature normalisation)

Bayesian prior fusion weights are embedded as feature importances
within the Random Forest (which handles multimodal uncertainty natively).
An additional hand-crafted Bayesian likelihood layer pre-weights the
six sensor channels before the RF sees the data.

Run:
    python train_model.py
"""

import os
import sys
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import (
    classification_report, mean_absolute_error, r2_score
)

# ─── Paths ────────────────────────────────────────────────────────────────────
ML_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ML_DIR, "data")
MODEL_DIR = os.path.join(ML_DIR, "models")
os.makedirs(MODEL_DIR, exist_ok=True)

DATASET_PATH    = os.path.join(DATA_DIR, "synthetic_dataset.csv")
CLASSIFIER_PATH = os.path.join(MODEL_DIR, "pain_classifier.pkl")
REGRESSOR_PATH  = os.path.join(MODEL_DIR, "pain_regressor.pkl")
SCALER_PATH     = os.path.join(MODEL_DIR, "scaler.pkl")

FEATURES = [
    "posture_score",
    "emg_rms",
    "heart_rate",
    "hrv_ms",
    "breath_rate",
    "breath_depth",
]

# ─── Bayesian Likelihood Layer ─────────────────────────────────────────────────
# Prior sensor reliability weights (higher = more trusted signal)
# These act as soft feature multipliers before the RF models.
SENSOR_PRIOR_WEIGHTS = {
    "posture_score": 1.20,  # strong behavioural indicator
    "emg_rms":       1.25,  # strongest single predictor (direct motor activity)
    "heart_rate":    1.10,
    "hrv_ms":        1.15,
    "breath_rate":   1.05,
    "breath_depth":  1.00,
}

def apply_bayesian_weights(X: pd.DataFrame) -> pd.DataFrame:
    """Multiply each feature column by its prior reliability weight."""
    X_w = X.copy()
    for col, w in SENSOR_PRIOR_WEIGHTS.items():
        if col in X_w.columns:
            X_w[col] = X_w[col] * w
    return X_w


def load_or_generate_dataset() -> pd.DataFrame:
    if not os.path.exists(DATASET_PATH):
        print("[INFO] Dataset not found — running dataset_generator.py …")
        import subprocess
        gen_script = os.path.join(ML_DIR, "dataset_generator.py")
        subprocess.run([sys.executable, gen_script], check=True)

    df = pd.read_csv(DATASET_PATH)
    print(f"[INFO] Loaded {len(df)} samples from {DATASET_PATH}")
    return df


def train():
    df = load_or_generate_dataset()

    X = df[FEATURES]
    y_class = df["pain_flag"]
    y_reg   = df["pain_level"]

    # Apply Bayesian weights
    X_weighted = apply_bayesian_weights(X)

    # Train/test split (80/20 stratified on pain_flag)
    X_train, X_test, yc_train, yc_test, yr_train, yr_test = train_test_split(
        X_weighted, y_class, y_reg,
        test_size=0.2, random_state=42, stratify=y_class
    )

    # ── Scaler ─────────────────────────────────────────────────────────────────
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    # ── Classifier ─────────────────────────────────────────────────────────────
    print("\n[TRAIN] Training RandomForestClassifier …")
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_split=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_train_s, yc_train)

    yc_pred = clf.predict(X_test_s)
    print("\n[CLASSIFIER] Evaluation:")
    print(classification_report(yc_test, yc_pred, target_names=["No Pain", "Pain"]))

    cv_scores = cross_val_score(clf, scaler.transform(X_weighted), y_class, cv=5, scoring="f1")
    print(f"[CLASSIFIER] 5-Fold F1: {cv_scores.mean():.3f} ± {cv_scores.std():.3f}")

    # ── Regressor ──────────────────────────────────────────────────────────────
    print("\n[TRAIN] Training RandomForestRegressor ...")
    reg = RandomForestRegressor(
        n_estimators=200,
        max_depth=12,
        min_samples_split=5,
        random_state=42,
        n_jobs=-1,
    )
    reg.fit(X_train_s, yr_train)

    yr_pred = reg.predict(X_test_s)
    mae = mean_absolute_error(yr_test, yr_pred)
    r2  = r2_score(yr_test, yr_pred)
    print(f"\n[REGRESSOR] MAE: {mae:.3f}  |  R2: {r2:.3f}")

    # ── Feature importances ────────────────────────────────────────────────────
    print("\n[FEATURE IMPORTANCES] (classifier):")
    for feat, imp in sorted(
        zip(FEATURES, clf.feature_importances_), key=lambda x: -x[1]
    ):
        bar = "#" * int(imp * 40)
        print(f"  {feat:<18} {bar} {imp:.4f}")

    # ── Save models ────────────────────────────────────────────────────────────
    joblib.dump(clf,    CLASSIFIER_PATH)
    joblib.dump(reg,    REGRESSOR_PATH)
    joblib.dump(scaler, SCALER_PATH)
    print(f"\n[SAVED] -> {CLASSIFIER_PATH}")
    print(f"[SAVED] -> {REGRESSOR_PATH}")
    print(f"[SAVED] -> {SCALER_PATH}")



if __name__ == "__main__":
    train()
