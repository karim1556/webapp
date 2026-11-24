"""Train random forests against device-score labels in the three patient archives."""

import argparse
from pathlib import Path

import joblib
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import balanced_accuracy_score, f1_score, mean_absolute_error
from sklearn.model_selection import LeaveOneGroupOut

from synthetic_archive import load_bundle


FEATURES = (
    "heart_rate", "hrv_rmssd", "perfusion_index", "respiration_rate",
    "humidity_sweat", "temperature_c", "muscle_spasm_jerk", "posture_score", "spo2",
)
BANDS = ("NORMAL", "MILD", "MODERATE", "SEVERE")
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "models" / "patient_pain_forest.joblib"
FOREST_OPTIONS = {"n_estimators": 128, "max_depth": 14, "min_samples_leaf": 3, "random_state": 42, "n_jobs": 4}


def training_rows(folder):
    for patient_id, dataset in load_bundle(folder).items():
        for sample in dataset["samples"]:
            telemetry = sample["readings"]["neurosync_telemetry"]
            yield (patient_id, *(telemetry[key] for key in FEATURES),
                   telemetry["pain_status"], telemetry["pain_index"])


def fit(features, band, index):
    classifier = RandomForestClassifier(class_weight="balanced", **FOREST_OPTIONS)
    regressor = RandomForestRegressor(**FOREST_OPTIONS)
    return classifier.fit(features, band), regressor.fit(features, index)


def train(folder, output):
    data = pd.DataFrame.from_records(
        training_rows(folder), columns=("patient_id", *FEATURES, "pain_status", "pain_index"),
    )
    features = data[list(FEATURES)]
    band, index, patient = data["pain_status"], data["pain_index"], data["patient_id"]
    results = {}
    for train_rows, test_rows in LeaveOneGroupOut().split(features, groups=patient):
        held_out = patient.iloc[test_rows[0]]
        classifier, regressor = fit(features.iloc[train_rows], band.iloc[train_rows], index.iloc[train_rows])
        test_features = features.iloc[test_rows]
        expected_band = band.iloc[test_rows]
        predicted_band = classifier.predict(test_features)
        results[held_out] = {
            "rows": len(test_rows),
            "balanced_accuracy": float(balanced_accuracy_score(expected_band, predicted_band)),
            "band_macro_f1": float(f1_score(expected_band, predicted_band, labels=BANDS, average="macro")),
            "elevated_f1": float(f1_score(expected_band != "NORMAL", predicted_band != "NORMAL")),
            "index_mae": float(mean_absolute_error(index.iloc[test_rows], regressor.predict(test_features))),
        }
        print(f"{held_out} held out: {results[held_out]}", flush=True)

    classifier, regressor = fit(features, band, index)
    output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "classifier": classifier, "regressor": regressor, "features": FEATURES, "bands": BANDS,
        "label_source": "Firmware scoring applied to generated patient telemetry",
        "patient_sample_counts": patient.value_counts().sort_index().to_dict(),
        "holdout_metrics": results, "sklearn_version": sklearn.__version__,
    }, output, compress=3)
    print(f"Saved models fitted on all {len(data):,} readings to {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder containing the three patient CSVs and study metadata")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    train(args.folder, args.output)


if __name__ == "__main__":
    main()
