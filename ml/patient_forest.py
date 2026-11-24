"""Apply the trained Random Forest to complete wearable readings."""

from functools import lru_cache
from math import isfinite

import joblib
import pandas as pd
import sklearn
from flask import current_app

import config


@lru_cache(maxsize=1)
def load_forest():
    try:
        bundle = joblib.load(config.PATIENT_FOREST_PATH)
        if bundle["sklearn_version"] != sklearn.__version__:
            raise ValueError("Incompatible scikit-learn version")
        regressor = bundle["regressor"]
        regressor.n_jobs = 1
        return regressor, bundle["features"]
    except (OSError, EOFError, ImportError, ValueError, KeyError, AttributeError):
        current_app.logger.exception("Random Forest pain model could not be loaded")
        return None


def score_readings(rows):
    """Project one model score per packet without falling back to the firmware score."""
    for row in rows:
        row["pain_index"] = None
        for field in ("vas_score", "pain_status", "assessment_label"):
            row.pop(field, None)
        if "button_event" in row:
            row["button_event"]["measurements"]["pain_index"] = None

    candidates = [row for row in rows if row.get("finger_detected") is True]
    if not candidates:
        return rows
    forest = load_forest()
    if forest is None:
        return rows

    regressor, features = forest
    complete = [row for row in candidates if all(
        isinstance(row.get(field), (int, float)) and isfinite(row[field]) for field in features
    )]
    if complete:
        inputs = pd.DataFrame.from_records(complete, columns=features)
        for row, estimate in zip(complete, regressor.predict(inputs)):
            row["pain_index"] = round(float(estimate), 1)
            if "button_event" in row:
                row["button_event"]["measurements"]["pain_index"] = row["pain_index"]
    return rows
