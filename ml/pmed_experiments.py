"""Run subject-independent PMED research and wearable-compatible experiments.

This is an offline research script. It does not modify the ESP32 firmware,
telemetry integration, or the model artifacts used by the live application.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
from scipy import signal
import sklearn
from sklearn.calibration import calibration_curve
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
    precision_recall_curve,
    brier_score_loss,
)
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier


SCRIPT_VERSION = "1.0.0"
DATASET_TITLE = (
    "The PainMonit Database: An Experimental and Clinical Physiological "
    "Signal Dataset for Automated Pain Recognition"
)
DATASET_DOI = "10.6084/m9.figshare.26965159.v3"
DATASET_VERSION = "3"
DATASET_LICENSE = "CC BY 4.0"
DATASET_ARCHIVE_MD5 = "0368bbb39621518378a631eb63c0983f"
EXPECTED_CHANNELS = ["Bvp", "Eda_E4", "Resp", "Eda_RB", "Ecg", "Emg"]
CHANNEL_INDEX = {name: index for index, name in enumerate(EXPECTED_CHANNELS)}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PMED_DIR = PROJECT_ROOT / "datasets" / "pmd" / "PMD" / "PMED"
DEFAULT_ARTIFACT_ROOT = Path(__file__).resolve().parent / "artifacts"


def _safe_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _load_official_config(pmed_dir: Path):
    config_path = pmed_dir / "config.py"
    if not config_path.exists():
        raise FileNotFoundError("PMED/config.py was not found: %s" % config_path)
    spec = importlib.util.spec_from_file_location("pmed_official_config", config_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run_official_preprocessing(pmed_dir: Path) -> Path:
    raw_dir = pmed_dir / "dataset" / "raw-data"
    np_dir = pmed_dir / "dataset" / "np-dataset"
    required = ["X.npy", "y_heater.npy", "y_covas.npy", "subjects.npy"]
    if not raw_dir.exists() or not list(raw_dir.glob("*.csv")):
        raise FileNotFoundError(
            "No PMED raw CSVs found under %s. Download PMED.zip and extract its "
            "synchronised subject CSVs there." % raw_dir
        )
    if not all((np_dir / name).exists() for name in required):
        import subprocess

        subprocess.run([sys.executable, "create_np_files.py"], cwd=str(pmed_dir), check=True)
    missing = [name for name in required if not (np_dir / name).exists()]
    if missing:
        raise FileNotFoundError("Official PMED preprocessing did not create: %s" % missing)
    return np_dir


def _read_np_labels(path: Path) -> np.ndarray:
    values = np.load(path, allow_pickle=False)
    if values.ndim == 2:
        return np.argmax(values, axis=1).astype(int)
    return values.astype(int).reshape(-1)


def _bandpass(values: np.ndarray, fs: float, low_hz: float, high_hz: float) -> Optional[np.ndarray]:
    values = np.asarray(values, dtype=float).reshape(-1)
    if values.size < max(20, int(fs * 2)) or not np.all(np.isfinite(values)):
        return None
    if float(np.std(values)) <= 1e-12:
        return None
    try:
        sos = signal.butter(3, [low_hz, high_hz], btype="bandpass", fs=fs, output="sos")
        filtered = signal.sosfiltfilt(sos, values)
    except (ValueError, FloatingPointError):
        return None
    if not np.all(np.isfinite(filtered)) or float(np.std(filtered)) <= 1e-12:
        return None
    return filtered


def _detect_cycles(
    values: np.ndarray,
    fs: float,
    low_hz: float,
    high_hz: float,
    min_rate_per_min: float,
    max_rate_per_min: float,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Return filtered signal and plausible peak indices for either polarity."""
    filtered = _bandpass(values, fs, low_hz, high_hz)
    if filtered is None:
        return None, None

    min_distance = max(1, int(fs * 60.0 / max_rate_per_min))
    scale = float(np.std(filtered))
    best = None
    best_score = (-1, -1.0)
    for polarity in (1.0, -1.0):
        candidates, props = signal.find_peaks(
            polarity * filtered,
            distance=min_distance,
            prominence=max(scale * 0.2, 1e-12),
        )
        if len(candidates) < 2:
            continue
        intervals = np.diff(candidates) / fs
        rates = 60.0 / intervals
        valid = np.isfinite(rates) & (rates >= min_rate_per_min) & (rates <= max_rate_per_min)
        score = (int(valid.sum()), float(np.median(props.get("prominences", [0.0]))))
        if score > best_score:
            best_score = score
            best = candidates
    return filtered, (best if best is not None else None)


def _cycle_series(
    values: np.ndarray,
    fs: float,
    low_hz: float,
    high_hz: float,
    min_rate_per_min: float,
    max_rate_per_min: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return cycle rate/time and peak-to-trough amplitude/time series."""
    filtered, peaks = _detect_cycles(
        values, fs, low_hz, high_hz, min_rate_per_min, max_rate_per_min
    )
    if filtered is None or peaks is None or len(peaks) < 2:
        empty = np.asarray([], dtype=float)
        return empty, empty, empty, empty

    intervals = np.diff(peaks) / fs
    rates = 60.0 / intervals
    times = ((peaks[:-1] + peaks[1:]) / 2.0) / fs
    valid = (
        np.isfinite(rates)
        & (rates >= min_rate_per_min)
        & (rates <= max_rate_per_min)
    )
    rates = rates[valid]
    times = times[valid]
    amplitudes: List[float] = []
    amp_times: List[float] = []
    for left, right, keep in zip(peaks[:-1], peaks[1:], valid):
        if not keep or right <= left + 1:
            continue
        cycle = filtered[left : right + 1]
        amplitudes.append(float(np.max(cycle) - np.min(cycle)))
        amp_times.append(float((left + right) / (2.0 * fs)))
    return (
        np.asarray(rates, dtype=float),
        np.asarray(times, dtype=float),
        np.asarray(amplitudes, dtype=float),
        np.asarray(amp_times, dtype=float),
    )


def _heart_series(
    values: np.ndarray, fs: float, low_hz: float, high_hz: float
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    filtered, peaks = _detect_cycles(values, fs, low_hz, high_hz, 35.0, 200.0)
    if filtered is None or peaks is None or len(peaks) < 2:
        empty = np.asarray([], dtype=float)
        return empty, empty, empty
    ibi_seconds = np.diff(peaks) / fs
    hr = 60.0 / ibi_seconds
    times = ((peaks[:-1] + peaks[1:]) / 2.0) / fs
    valid = np.isfinite(hr) & (hr >= 35.0) & (hr <= 200.0)
    return hr[valid], times[valid], ibi_seconds[valid] * 1000.0


def _series_summary(
    prefix: str, values: Sequence[float], times: Sequence[float]
) -> Dict[str, float]:
    vals = np.asarray(values, dtype=float)
    ts = np.asarray(times, dtype=float)
    valid = np.isfinite(vals)
    vals = vals[valid]
    ts = ts[valid] if ts.size == valid.size else np.arange(vals.size, dtype=float)
    if vals.size == 0:
        return {
            prefix + "_mean": float("nan"),
            prefix + "_min": float("nan"),
            prefix + "_max": float("nan"),
            prefix + "_std": float("nan"),
            prefix + "_slope": float("nan"),
        }
    slope = float("nan")
    if vals.size >= 2 and np.unique(ts).size >= 2:
        slope = float(np.polyfit(ts, vals, 1)[0])
    return {
        prefix + "_mean": float(np.mean(vals)),
        prefix + "_min": float(np.min(vals)),
        prefix + "_max": float(np.max(vals)),
        prefix + "_std": float(np.std(vals, ddof=0)),
        prefix + "_slope": slope,
    }


def _research_wave_features(window: np.ndarray, fs: float) -> Dict[str, float]:
    bvp = window[:, CHANNEL_INDEX["Bvp"]]
    eda_e4 = window[:, CHANNEL_INDEX["Eda_E4"]]
    respiration = window[:, CHANNEL_INDEX["Resp"]]
    eda_rb = window[:, CHANNEL_INDEX["Eda_RB"]]
    ecg = window[:, CHANNEL_INDEX["Ecg"]]
    emg = window[:, CHANNEL_INDEX["Emg"]]

    bvp_hr, bvp_t, _ = _heart_series(bvp, fs, 0.4, 8.0)
    ecg_hr, ecg_t, ecg_ibi = _heart_series(ecg, fs, 5.0, 25.0)
    resp_rate, resp_t, resp_amp, resp_amp_t = _cycle_series(
        respiration, fs, 0.06, 0.8, 5.0, 40.0
    )

    out: Dict[str, float] = {}
    out.update(_series_summary("bvp_hr", bvp_hr, bvp_t))
    out.update(_series_summary("ecg_hr", ecg_hr, ecg_t))
    out.update(_series_summary("respiration_rate", resp_rate, resp_t))
    out["respiration_amplitude"] = (
        float(np.median(resp_amp)) if resp_amp.size else float("nan")
    )
    out["respiration_amplitude_std"] = (
        float(np.std(resp_amp)) if resp_amp.size else float("nan")
    )
    out["ecg_rmssd_ms"] = (
        float(np.sqrt(np.mean(np.diff(ecg_ibi) ** 2)))
        if ecg_ibi.size >= 3
        else float("nan")
    )
    out["ecg_sdnn_ms"] = (
        float(np.std(ecg_ibi, ddof=1)) if ecg_ibi.size >= 3 else float("nan")
    )
    for name, values in (("eda_e4", eda_e4), ("eda_rb", eda_rb)):
        good = values[np.isfinite(values)]
        is_complete = good.size == values.size and good.size > 0
        out[name + "_mean"] = float(np.mean(good)) if is_complete else float("nan")
        out[name + "_std"] = float(np.std(good)) if is_complete else float("nan")
    good_emg = emg[np.isfinite(emg)]
    emg_complete = good_emg.size == emg.size and good_emg.size > 0
    out["emg_rms"] = (
        float(np.sqrt(np.mean(np.square(good_emg)))) if emg_complete else float("nan")
    )
    out["emg_std"] = float(np.std(good_emg)) if emg_complete else float("nan")
    return out


def extract_live_telemetry_features(
    samples: Sequence[Dict[str, Any]],
    baseline: Dict[str, float],
    window_seconds: int = 10,
) -> Dict[str, float]:
    """Extract Model B features from live telemetry with the same definitions.

    Each sample must contain timestamp, heart_rate_bpm, breath_rate, and
    breath_depth. Invalid/missing readings are omitted rather than imputed.
    Baseline values are personal calibration medians in the same units.
    """
    if window_seconds not in (10, 20, 30):
        raise ValueError("window_seconds must be one of 10, 20, or 30")
    parsed: List[Tuple[float, Dict[str, Any]]] = []
    for row in samples:
        stamp = row.get("timestamp")
        if isinstance(stamp, (int, float)):
            seconds = float(stamp)
        else:
            try:
                seconds = pd.Timestamp(stamp).timestamp()
            except Exception:
                continue
        parsed.append((seconds, row))
    if not parsed:
        return {}
    parsed.sort(key=lambda pair: pair[0])
    cutoff = parsed[-1][0] - float(window_seconds)
    parsed = [(t, row) for t, row in parsed if t >= cutoff]
    origin = parsed[0][0]

    hr_times: List[float] = []
    resp_times: List[float] = []
    depth_times: List[float] = []
    heart: List[float] = []
    resp: List[float] = []
    depth: List[float] = []
    base_depth = _safe_float(baseline.get("breath_depth"))
    for timestamp, row in parsed:
        hr = _safe_float(row.get("heart_rate_bpm"))
        rr = _safe_float(row.get("breath_rate"))
        dep = _safe_float(row.get("breath_depth"))
        if math.isfinite(hr) and 35.0 <= hr <= 200.0:
            hr_times.append(timestamp - origin)
            heart.append(hr)
        if math.isfinite(rr) and 4.0 <= rr <= 45.0:
            resp_times.append(timestamp - origin)
            resp.append(rr)
        if math.isfinite(dep) and math.isfinite(base_depth) and base_depth > 0:
            depth_times.append(timestamp - origin)
            depth.append(dep / base_depth)

    result = {}
    result.update(_series_summary("heart_rate", heart, hr_times))
    result.update(_series_summary("respiration_rate", resp, resp_times))
    depth_values = np.asarray(depth, dtype=float)
    result["breath_depth_mean"] = (
        float(np.mean(depth_values)) if depth_values.size else float("nan")
    )
    result["breath_depth_std"] = (
        float(np.std(depth_values)) if depth_values.size else float("nan")
    )
    base_hr = _safe_float(baseline.get("heart_rate_bpm"))
    base_resp = _safe_float(baseline.get("breath_rate"))
    hr_mean = result["heart_rate_mean"]
    rr_mean = result["respiration_rate_mean"]
    result["delta_hr_from_baseline"] = hr_mean - base_hr if math.isfinite(hr_mean + base_hr) else float("nan")
    result["relative_hr"] = hr_mean / base_hr if math.isfinite(hr_mean + base_hr) and base_hr != 0 else float("nan")
    result["delta_resp_from_baseline"] = rr_mean - base_resp if math.isfinite(rr_mean + base_resp) else float("nan")
    result["relative_resp"] = rr_mean / base_resp if math.isfinite(rr_mean + base_resp) and base_resp != 0 else float("nan")
    return result


def _reconstruct_segments(
    csv_path: Path, fs: int, window_secs: int, baseline_temp: float, num_repetitions: int
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Reconstruct official segment indexes only to preserve timestamps."""
    df = pd.read_csv(csv_path, sep=";", decimal=",")
    required = set(EXPECTED_CHANNELS + ["Heater_cleaned", "COVAS"])
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError("Missing PMED columns in %s: %s" % (csv_path.name, missing))

    heater = df["Heater_cleaned"].to_numpy()
    covas = df["COVAS"].to_numpy()
    stim = np.where(heater != baseline_temp, 1, -1)
    starts = np.flatnonzero((stim[:-1] < 0) & (stim[1:] > 0))
    window = int(window_secs * fs)
    segments: List[Dict[str, Any]] = []
    baseline_count = 0
    baseline_shift = 5
    for start0 in starts:
        baseline_start = int(start0 - baseline_shift * fs)
        if (
            baseline_count < num_repetitions
            and baseline_start > window
            and np.all(heater[baseline_start - window : baseline_start] == baseline_temp)
        ):
            segments.append(
                {
                    "start_index": baseline_start - window,
                    "end_index": baseline_start,
                    "raw_heater": baseline_temp,
                    "raw_covas": 0.0,
                    "segment_kind": "baseline",
                }
            )
            baseline_count += 1

        start = int(start0 + 1)
        end = int(start + window)
        if end <= len(heater) and np.all(heater[start:end] == heater[start]):
            segments.append(
                {
                    "start_index": start,
                    "end_index": end,
                    "raw_heater": float(heater[start]),
                    "raw_covas": float(np.sum(covas[start:end])),
                    "segment_kind": "stimulus",
                }
            )

    if not segments:
        raise ValueError("No official-style PMED segments reconstructed for %s" % csv_path.name)

    heater_values = np.asarray([row["raw_heater"] for row in segments])
    temps = np.unique(heater_values)
    heater_conversion = {value: index for index, value in enumerate(temps)}
    heater_classes = np.asarray([heater_conversion[value] for value in heater_values], dtype=int)

    covas_values = np.asarray([row["raw_covas"] for row in segments], dtype=float)
    maximum = float(np.max(covas_values))
    if maximum > 0:
        scaled = covas_values / maximum * 100.0
        covas_classes = np.asarray(
            [min(int(value // 25) + 1, 4) if value > 0 else 0 for value in scaled],
            dtype=int,
        )
    else:
        covas_classes = np.zeros(len(covas_values), dtype=int)

    seconds = df["Seconds"].to_numpy() if "Seconds" in df.columns else None
    for index, row in enumerate(segments):
        start_index, end_index = row["start_index"], row["end_index"]
        if seconds is not None and len(seconds) > end_index - 1:
            start_s = float(seconds[start_index])
            end_s = float(seconds[end_index - 1])
        else:
            start_s = start_index / float(fs)
            end_s = (end_index - 1) / float(fs)
        row.update(
            {
                "window_index": index,
                "window_start_s": start_s,
                "window_end_s": end_s,
                "heater_class_reconstructed": int(heater_classes[index]),
                "covas_class_reconstructed": int(covas_classes[index]),
            }
        )
    return df, segments


def load_pmed_windows(pmed_dir: Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[Dict[str, Any]], Dict[str, Any]]:
    cfg = _load_official_config(pmed_dir)
    if list(getattr(cfg, "sensor_names", [])) != EXPECTED_CHANNELS:
        raise ValueError("PMED channel order changed; inspect the official config before training.")
    fs = int(cfg.sampling_rate)
    window_secs = int(cfg.window_secs)
    np_dir = _run_official_preprocessing(pmed_dir)
    X = np.load(np_dir / "X.npy", mmap_mode="r")
    y_heater = _read_np_labels(np_dir / "y_heater.npy")
    y_covas = _read_np_labels(np_dir / "y_covas.npy")
    subjects = np.load(np_dir / "subjects.npy", allow_pickle=False).reshape(-1)
    if X.ndim == 4 and X.shape[-1] == 1:
        X = X[..., 0]
    if X.ndim != 3 or X.shape[2] != len(EXPECTED_CHANNELS):
        raise ValueError("Unexpected PMED X.npy shape: %r" % (X.shape,))
    if not (len(X) == len(y_heater) == len(y_covas) == len(subjects)):
        raise ValueError("PMED X/label/subject array lengths do not match")

    raw_files = sorted((pmed_dir / "dataset" / "raw-data").glob("*.csv"))
    if not raw_files:
        raise FileNotFoundError("No raw PMED subject files found")
    if len(set(subjects.tolist())) > len(raw_files):
        raise ValueError("subjects.npy contains more subject IDs than raw CSV files")

    records: List[Dict[str, Any]] = []
    raw_windows: List[np.ndarray] = []
    offset = 0
    for subject_index, csv_path in enumerate(raw_files):
        subject_rows = np.flatnonzero(subjects == subject_index)
        if subject_rows.size == 0:
            continue
        df, segments = _reconstruct_segments(
            csv_path,
            fs=fs,
            window_secs=window_secs,
            baseline_temp=float(cfg.baseline_temp),
            num_repetitions=int(cfg.num_repetitions),
        )
        expected = len(subject_rows)
        if len(segments) != expected:
            raise ValueError(
                "Official/reconstructed window count mismatch for %s: %s vs %s"
                % (csv_path.name, expected, len(segments))
            )
        sub_heater = y_heater[subject_rows]
        sub_covas = y_covas[subject_rows]
        rec_heater = np.asarray([r["heater_class_reconstructed"] for r in segments])
        rec_covas = np.asarray([r["covas_class_reconstructed"] for r in segments])
        if not np.array_equal(sub_heater, rec_heater) or not np.array_equal(sub_covas, rec_covas):
            raise ValueError("Official/reconstructed labels disagree for %s" % csv_path.name)
        if not np.array_equal(subject_rows, np.arange(offset, offset + expected)):
            raise ValueError("Official PMED subject window order is not contiguous as expected")
        match = re.search(r"S_(\d+)", csv_path.name)
        subject_name = "S_%s" % match.group(1) if match else csv_path.stem
        for local_index, segment in enumerate(segments):
            raw_window = df[EXPECTED_CHANNELS].iloc[
                segment["start_index"] : segment["end_index"]
            ].to_numpy(dtype=float)
            official_window = np.asarray(X[offset + local_index], dtype=float)
            if not np.allclose(
                np.nan_to_num(raw_window), official_window, rtol=1e-5, atol=1e-6
            ):
                raise ValueError(
                    "Raw and official preprocessed window differ for %s window %s"
                    % (csv_path.name, local_index)
                )
            raw_windows.append(raw_window)
            records.append(
                {
                    "sample_index": offset + local_index,
                    "subject_index": subject_index,
                    "subject_id": subject_name,
                    "window_index": int(segment["window_index"]),
                    "window_start_s": float(segment["window_start_s"]),
                    "window_end_s": float(segment["window_end_s"]),
                    "segment_kind": segment["segment_kind"],
                    "pain_heater_class": int(y_heater[offset + local_index]),
                    "pain_covas_class": int(y_covas[offset + local_index]),
                    "source_dataset": "PMED",
                    "label_type_heater": "heater_temperature_class",
                    "label_type_covas": "subject_relative_COVAS_quartile",
                }
            )
        offset += expected
    if len(records) != len(X):
        raise ValueError("Reconstructed metadata count does not match PMED windows")
    metadata = {
        "sampling_rate_hz": fs,
        "window_seconds": window_secs,
        "channel_order": list(cfg.sensor_names),
        "raw_csv_count": len(raw_files),
        "official_np_dir": str(np_dir),
    }
    # Return raw signal windows so missing samples remain missing. The official
    # preprocessor's X.npy replaces NaN values with zero; it is used above only
    # to verify that our window extraction matches the official output.
    return np.stack(raw_windows), y_heater, y_covas, subjects, records, metadata


def extract_pmed_features(
    X: np.ndarray, records: List[Dict[str, Any]], fs: int
) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for sample_index, record in enumerate(records):
        window = np.asarray(X[sample_index], dtype=float)
        row = dict(record)
        row.update(_research_wave_features(window, fs))
        rows.append(row)
    frame = pd.DataFrame(rows)

    # Baselines are protocol-marked no-stimulus windows only; no pain labels
    # from held-out subjects are used to form these physiological references.
    baseline_rows = frame[frame["pain_heater_class"] == 0]
    baseline = baseline_rows.groupby("subject_id").agg(
        baseline_hr=("bvp_hr_mean", "median"),
        baseline_resp=("respiration_rate_mean", "median"),
        baseline_depth=("respiration_amplitude", "median"),
    )
    frame = frame.join(baseline, on="subject_id")
    frame["delta_hr_from_baseline"] = frame["bvp_hr_mean"] - frame["baseline_hr"]
    frame["relative_hr"] = frame["bvp_hr_mean"] / frame["baseline_hr"]
    frame["delta_resp_from_baseline"] = (
        frame["respiration_rate_mean"] - frame["baseline_resp"]
    )
    frame["relative_resp"] = frame["respiration_rate_mean"] / frame["baseline_resp"]
    frame["breath_depth_mean"] = frame["respiration_amplitude"] / frame["baseline_depth"]
    frame["breath_depth_std"] = frame["respiration_amplitude_std"] / frame["baseline_depth"]

    # For B, temporal slopes and summaries are calculated from derived pulse
    # and breath-cycle measurements within the same official 10-second window.
    # Live telemetry uses the same summary equations in extract_live_telemetry_features.
    return frame


RESEARCH_FEATURES = [
    "bvp_hr_mean",
    "bvp_hr_min",
    "bvp_hr_max",
    "bvp_hr_std",
    "ecg_hr_mean",
    "ecg_hr_min",
    "ecg_hr_max",
    "ecg_hr_std",
    "ecg_rmssd_ms",
    "ecg_sdnn_ms",
    "respiration_rate_mean",
    "respiration_rate_std",
    "respiration_amplitude",
    "eda_e4_mean",
    "eda_e4_std",
    "eda_rb_mean",
    "eda_rb_std",
    "emg_rms",
    "emg_std",
    "delta_hr_from_baseline",
    "delta_resp_from_baseline",
]

WEARABLE_FEATURES = [
    "heart_rate_mean",
    "heart_rate_min",
    "heart_rate_max",
    "heart_rate_std",
    "heart_rate_slope",
    "respiration_rate_mean",
    "respiration_rate_min",
    "respiration_rate_max",
    "respiration_rate_std",
    "respiration_rate_slope",
    "breath_depth_mean",
    "breath_depth_std",
    "delta_hr_from_baseline",
    "relative_hr",
    "delta_resp_from_baseline",
    "relative_resp",
]


def build_model_b_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Rename PMED-derived BVP/respiration summaries to Model B schema."""
    result = pd.DataFrame(index=frame.index)
    for key in ("mean", "min", "max", "std", "slope"):
        result["heart_rate_" + key] = frame["bvp_hr_" + key]
        result["respiration_rate_" + key] = frame["respiration_rate_" + key]
    for key in ("mean", "std"):
        result["breath_depth_" + key] = frame["breath_depth_" + key]
    for key in ("delta_hr_from_baseline", "relative_hr", "delta_resp_from_baseline", "relative_resp"):
        result[key] = frame[key]
    return result


WEARABLE_FEATURES_NO_DEPTH = [
    name for name in WEARABLE_FEATURES
    if not name.startswith("breath_depth_")
]


def _estimators(y_train: np.ndarray) -> Dict[str, Any]:
    positives = max(1, int(np.sum(y_train == 1)))
    negatives = max(1, int(np.sum(y_train == 0)))
    return {
        "logistic_regression": Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(
                        max_iter=3000,
                        class_weight="balanced",
                        solver="liblinear",
                        random_state=42,
                    ),
                ),
            ]
        ),
        "random_forest": RandomForestClassifier(
            n_estimators=300,
            min_samples_leaf=2,
            class_weight="balanced_subsample",
            random_state=42,
            n_jobs=-1,
        ),
        "xgboost": XGBClassifier(
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=1.0,
            min_child_weight=1,
            scale_pos_weight=negatives / float(positives),
            eval_metric="logloss",
            random_state=42,
            n_jobs=1,
            verbosity=0,
        ),
    }


def _positive_probability(model: Any, values: pd.DataFrame) -> np.ndarray:
    probabilities = model.predict_proba(values)
    classes = np.asarray(model.classes_)
    positive_index = int(np.flatnonzero(classes == 1)[0])
    return np.asarray(probabilities[:, positive_index], dtype=float)


def _safe_auc(y: Sequence[int], probability: Sequence[float]) -> float:
    y_arr = np.asarray(y, dtype=int)
    if np.unique(y_arr).size < 2:
        return float("nan")
    return float(roc_auc_score(y_arr, probability))


def _metrics(y: Sequence[int], probability: Sequence[float]) -> Dict[str, Any]:
    y_arr = np.asarray(y, dtype=int)
    p_arr = np.asarray(probability, dtype=float)
    predicted = (p_arr >= 0.5).astype(int)
    matrix = confusion_matrix(y_arr, predicted, labels=[0, 1])
    tn, fp, fn, tp = [int(x) for x in matrix.ravel()]
    specificity = float(tn / (tn + fp)) if tn + fp else float("nan")
    return {
        "n_windows": int(y_arr.size),
        "roc_auc": _safe_auc(y_arr, p_arr),
        "pr_auc": float(average_precision_score(y_arr, p_arr)) if y_arr.size else float("nan"),
        "balanced_accuracy": float(balanced_accuracy_score(y_arr, predicted)) if y_arr.size else float("nan"),
        "accuracy": float(accuracy_score(y_arr, predicted)) if y_arr.size else float("nan"),
        "precision": float(precision_score(y_arr, predicted, zero_division=0)) if y_arr.size else float("nan"),
        "recall_sensitivity": float(recall_score(y_arr, predicted, zero_division=0)) if y_arr.size else float("nan"),
        "specificity": specificity,
        "f1": float(f1_score(y_arr, predicted, zero_division=0)) if y_arr.size else float("nan"),
        "brier_score": float(brier_score_loss(y_arr, p_arr)) if y_arr.size else float("nan"),
        "confusion_matrix_labels_0_1": [[tn, fp], [fn, tp]],
        "threshold": 0.5,
    }


def _subject_bootstrap_auc_ci(
    y: np.ndarray, probability: np.ndarray, groups: np.ndarray, samples: int = 1000
) -> List[float]:
    unique_groups = np.unique(groups)
    if unique_groups.size < 2:
        return [float("nan"), float("nan")]
    rng = np.random.default_rng(2718)
    by_group = {group: np.flatnonzero(groups == group) for group in unique_groups}
    scores: List[float] = []
    for _ in range(samples):
        chosen = rng.choice(unique_groups, size=len(unique_groups), replace=True)
        indexes = np.concatenate([by_group[group] for group in chosen])
        if np.unique(y[indexes]).size < 2:
            continue
        scores.append(float(roc_auc_score(y[indexes], probability[indexes])))
    if not scores:
        return [float("nan"), float("nan")]
    return [float(np.quantile(scores, 0.025)), float(np.quantile(scores, 0.975))]


def _per_subject_metrics(
    y: np.ndarray, probability: np.ndarray, groups: np.ndarray
) -> pd.DataFrame:
    rows = []
    for subject in np.unique(groups):
        indexes = np.flatnonzero(groups == subject)
        result = _metrics(y[indexes], probability[indexes])
        rows.append({"subject_id": str(subject), **result})
    return pd.DataFrame(rows)


def _feature_importance(model: Any, feature_names: Sequence[str]) -> pd.DataFrame:
    estimator = model
    if isinstance(model, Pipeline):
        estimator = model.named_steps["classifier"]
    if hasattr(estimator, "feature_importances_"):
        values = np.asarray(estimator.feature_importances_, dtype=float)
        kind = "split_gain_or_impurity_importance"
    elif hasattr(estimator, "coef_"):
        values = np.abs(np.asarray(estimator.coef_, dtype=float).reshape(-1))
        kind = "absolute_standardized_coefficient"
    else:
        values = np.full(len(feature_names), np.nan)
        kind = "unavailable"
    return pd.DataFrame(
        {"feature": list(feature_names), "importance": values, "importance_kind": kind}
    ).sort_values("importance", ascending=False, na_position="last")


def _plot_run(
    output_path: Path,
    title: str,
    y: np.ndarray,
    probability: np.ndarray,
    metrics: Dict[str, Any],
) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    ax_roc, ax_pr, ax_cm, ax_cal = axes.ravel()
    if np.unique(y).size == 2:
        fpr, tpr, _ = roc_curve(y, probability)
        ax_roc.plot(fpr, tpr, label="OOF ROC-AUC %.3f" % metrics["roc_auc"])
        ax_roc.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Chance")
    ax_roc.set(title="ROC", xlabel="False positive rate", ylabel="True positive rate")
    ax_roc.legend(loc="lower right")

    precision, recall, _ = precision_recall_curve(y, probability)
    ax_pr.plot(recall, precision, label="PR-AUC %.3f" % metrics["pr_auc"])
    ax_pr.axhline(float(np.mean(y)), linestyle="--", color="gray", label="Positive prevalence")
    ax_pr.set(title="Precision–recall", xlabel="Recall", ylabel="Precision")
    ax_pr.legend(loc="lower left")

    matrix = np.asarray(metrics["confusion_matrix_labels_0_1"], dtype=int)
    ax_cm.imshow(matrix, cmap="Blues")
    for row in range(2):
        for col in range(2):
            ax_cm.text(col, row, str(matrix[row, col]), ha="center", va="center")
    ax_cm.set(
        title="Confusion matrix at threshold 0.5",
        xlabel="Predicted class",
        ylabel="True class",
        xticks=[0, 1],
        yticks=[0, 1],
    )

    if np.unique(y).size == 2:
        bins = min(10, max(2, int(math.sqrt(len(y)))))
        fraction_positive, mean_predicted = calibration_curve(
            y, probability, n_bins=bins, strategy="quantile"
        )
        ax_cal.plot(mean_predicted, fraction_positive, marker="o", label="OOF")
        ax_cal.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Perfect")
    ax_cal.set(title="Calibration", xlabel="Mean predicted probability", ylabel="Observed positive fraction")
    ax_cal.legend(loc="best")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def _plot_feature_importance(frame: pd.DataFrame, output_path: Path, title: str) -> None:
    shown = frame.dropna(subset=["importance"]).head(20).sort_values("importance")
    if shown.empty:
        return
    height = max(4, 0.28 * len(shown))
    fig, ax = plt.subplots(figsize=(8, height))
    ax.barh(shown["feature"], shown["importance"], color="#376f9f")
    ax.set_xlabel(str(shown["importance_kind"].iloc[0]))
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.ndarray,)):
        return [_json_safe(item) for item in value.tolist()]
    if isinstance(value, Path):
        return str(value)
    return value


def _class_counts(values: Sequence[int]) -> Dict[str, int]:
    unique, counts = np.unique(np.asarray(values, dtype=int), return_counts=True)
    return {str(int(label)): int(count) for label, count in zip(unique, counts)}


def _file_md5(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    digest = hashlib.md5()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_one(
    feature_frame: pd.DataFrame,
    features: Sequence[str],
    label_name: str,
    category: str,
    label_definition: str,
    output_root: Path,
    global_metadata: Dict[str, Any],
    loso: bool,
) -> Dict[str, Any]:
    columns = list(features)
    working = feature_frame.copy()
    y_source = working[label_name].to_numpy(dtype=int)
    X_source = working[columns].replace([np.inf, -np.inf], np.nan)
    complete = X_source.notna().all(axis=1).to_numpy()
    complete &= np.isfinite(y_source)
    working = working.loc[complete].reset_index(drop=True)
    X = working[columns].astype(float)
    y = working[label_name].to_numpy(dtype=int)
    # subject_index is sourced from the official subjects.npy array; subject_id
    # is the corresponding readable raw-file identifier used in reports.
    groups = working["subject_index"].to_numpy(dtype=int)
    subject_names = working["subject_id"].astype(str).to_numpy()
    subjects = np.unique(groups)
    if len(subjects) < 2 or np.unique(y).size != 2:
        raise ValueError(
            "%s/%s has too few subjects or only one binary class after complete-case filtering"
            % (category, label_name)
        )
    n_splits = min(5, len(subjects))
    splitter = GroupKFold(n_splits=n_splits)
    category_root = output_root / category
    category_root.mkdir(parents=True, exist_ok=True)
    print(
        "\n[%s / %s] %s complete windows across %s subjects; GroupKFold=%s; LOSO=%s"
        % (category, label_name, len(y), len(subjects), n_splits, loso),
        flush=True,
    )

    summaries: Dict[str, Any] = {}
    run_results: Dict[str, Any] = {}
    for algorithm in ("logistic_regression", "random_forest", "xgboost"):
        print("  fitting %s" % algorithm, flush=True)
        oof_probability = np.full(len(y), np.nan, dtype=float)
        oof_fold = np.full(len(y), -1, dtype=int)
        for fold, (train_index, test_index) in enumerate(splitter.split(X, y, groups), start=1):
            if np.unique(y[train_index]).size < 2:
                raise ValueError("Training fold contains one class: %s fold %s" % (category, fold))
            model = _estimators(y[train_index])[algorithm]
            model.fit(X.iloc[train_index], y[train_index])
            oof_probability[test_index] = _positive_probability(model, X.iloc[test_index])
            oof_fold[test_index] = fold
        if not np.all(np.isfinite(oof_probability)):
            raise RuntimeError("GroupKFold did not produce a prediction for every sample")

        gkf_metrics = _metrics(y, oof_probability)
        print(
            "  %s GroupKFold ROC-AUC=%.3f; proceeding to LOSO/artifacts"
            % (algorithm, gkf_metrics["roc_auc"]),
            flush=True,
        )
        auc_ci = _subject_bootstrap_auc_ci(y, oof_probability, groups)
        gkf_metrics["roc_auc_subject_bootstrap_95ci"] = auc_ci
        gkf_metrics["n_subjects"] = int(len(subjects))
        gkf_metrics["n_splits"] = int(n_splits)
        gkf_metrics["subjects_disjoint_by_fold"] = True

        prediction_frame = working[
            ["sample_index", "subject_id", "window_index", "window_start_s", "window_end_s"]
        ].copy()
        prediction_frame["y_true"] = y
        prediction_frame["probability_pain"] = oof_probability
        prediction_frame["y_pred_at_0_5"] = (oof_probability >= 0.5).astype(int)
        prediction_frame["group_kfold_fold"] = oof_fold

        per_subject = _per_subject_metrics(y, oof_probability, subject_names)
        loso_summary = None
        loso_subject_metrics = None
        if loso:
            logo = LeaveOneGroupOut()
            loso_probability = np.full(len(y), np.nan, dtype=float)
            loso_subject = np.full(len(y), "", dtype=object)
            loso_skipped = []
            for train_index, test_index in logo.split(X, y, groups):
                held_out = str(subject_names[test_index[0]])
                if np.unique(y[train_index]).size < 2:
                    loso_skipped.append(held_out)
                    continue
                model = _estimators(y[train_index])[algorithm]
                model.fit(X.iloc[train_index], y[train_index])
                loso_probability[test_index] = _positive_probability(model, X.iloc[test_index])
                loso_subject[test_index] = held_out
            valid_loso = np.isfinite(loso_probability)
            if valid_loso.any():
                loso_summary = _metrics(y[valid_loso], loso_probability[valid_loso])
                loso_summary["n_subjects_scored"] = int(len(np.unique(loso_subject[valid_loso])))
                loso_summary["skipped_subjects"] = loso_skipped
                loso_subject_metrics = _per_subject_metrics(
                    y[valid_loso], loso_probability[valid_loso], subject_names[valid_loso]
                )
                loso_subject_metrics.to_csv(
                    category_root / (algorithm + "__" + label_name + "__loso_per_subject.csv"),
                    index=False,
                )
                prediction_frame["loso_probability_pain"] = loso_probability
            else:
                loso_summary = {"error": "No LOSO folds could be scored", "skipped_subjects": loso_skipped}

        run_dir = category_root / label_name / algorithm
        run_dir.mkdir(parents=True, exist_ok=True)
        full_model = _estimators(y)[algorithm]
        full_model.fit(X, y)
        joblib.dump(full_model, run_dir / "model.joblib")
        prediction_frame.to_csv(run_dir / "groupkfold_oof_predictions.csv", index=False)
        per_subject.to_csv(run_dir / "groupkfold_per_subject_metrics.csv", index=False)
        _plot_run(
            run_dir / "groupkfold_diagnostics.png",
            "%s | %s | %s" % (category, label_name, algorithm),
            y,
            oof_probability,
            gkf_metrics,
        )
        importance = _feature_importance(full_model, columns)
        importance.to_csv(run_dir / "feature_importance.csv", index=False)
        _plot_feature_importance(
            importance,
            run_dir / "feature_importance.png",
            "%s | %s | %s" % (category, label_name, algorithm),
        )

        schema = {
            "feature_names": columns,
            "signal_processing": {
                "bvp_bandpass_hz": [0.4, 8.0],
                "ecg_bandpass_hz": [5.0, 25.0],
                "respiration_bandpass_hz": [0.06, 0.8],
                "plausible_heart_rate_bpm": [35.0, 200.0],
                "plausible_respiration_rate_bpm": [5.0, 40.0],
                "filter": "third-order Butterworth SOS + forward/backward filtering",
                "peak_prominence": "0.2 times the filtered channel standard deviation",
            },
            "feature_definitions": {
                "heart_rate_*": "BVP-derived beat rate summaries over one PMED window; bpm.",
                "heart_rate_slope": "Least-squares bpm change per second over valid beat intervals.",
                "respiration_rate_*": "Resp waveform cycle-rate summaries; breaths per minute.",
                "respiration_rate_slope": "Least-squares breaths/min change per second over valid cycles.",
                "breath_depth_*": "Respiration peak-to-trough amplitude divided by that subject's median baseline amplitude; dimensionless proxy.",
                "delta_hr_from_baseline": "Window mean BVP-derived HR minus that subject's baseline-window median; bpm.",
                "relative_hr": "Window mean BVP-derived HR divided by that subject's baseline-window median.",
                "delta_resp_from_baseline": "Window mean respiration rate minus that subject's baseline-window median; breaths/min.",
                "relative_resp": "Window mean respiration rate divided by that subject's baseline-window median.",
                "ecg_rmssd_ms": "Research-only RMSSD from detected ECG R-R intervals; milliseconds.",
                "ecg_sdnn_ms": "Research-only SDNN from detected ECG R-R intervals; milliseconds.",
                "eda_*": "Research-only EDA channel mean or standard deviation in source units.",
                "emg_*": "Research-only EMG RMS or standard deviation in source units.",
            },
            "missing_values": "Rows with any required feature missing/non-finite are excluded; no feature imputation.",
            "online_extractor": "ml.pmed_experiments.extract_live_telemetry_features",
            "window_seconds": global_metadata["pmed"]["window_seconds"],
            "baseline_definition": "Median feature value across that subject's heater class 0 protocol baseline windows.",
            "feature_compatibility_note": (
                "The breath-depth feature is a within-subject normalized PMED Resp amplitude proxy. "
                "Its equivalence to the firmware's breath_depth scale has not been established; "
                "the with-depth and without-depth wearable results must both be reviewed."
            ),
        }
        metadata = {
            **global_metadata,
            "experiment": category,
            "label_name": label_name,
            "label_definition": label_definition,
            "algorithm": algorithm,
            "feature_schema": columns,
            "n_complete_windows": int(len(y)),
            "n_subjects": int(len(subjects)),
            "group_split_strategy": "GroupKFold(groups=subject_id), no subject overlaps between train/test folds",
            "threshold_for_reported_class_metrics": 0.5,
            "model_usage": "research artifact only; not loaded by the live application",
        }
        metrics = {
            "groupkfold_oof": gkf_metrics,
            "leave_one_subject_out_oof": loso_summary,
            "per_subject_metrics_file": "groupkfold_per_subject_metrics.csv",
            "confusion_matrix_order": ["non_pain", "pain"],
        }
        (run_dir / "feature_schema.json").write_text(
            json.dumps(_json_safe(schema), indent=2), encoding="utf-8"
        )
        (run_dir / "metadata.json").write_text(
            json.dumps(_json_safe(metadata), indent=2), encoding="utf-8"
        )
        (run_dir / "metrics.json").write_text(
            json.dumps(_json_safe(metrics), indent=2), encoding="utf-8"
        )

        result = {
            "experiment": category,
            "label_name": label_name,
            "algorithm": algorithm,
            "features": columns,
            "n_windows": int(len(y)),
            "n_subjects": int(len(subjects)),
            "groupkfold": gkf_metrics,
            "loso": loso_summary,
            "feature_importance": importance.head(10).to_dict(orient="records"),
            "run_dir": str(run_dir),
        }
        summaries[algorithm] = result
        run_results[algorithm] = result
    return {
        "category": category,
        "label_name": label_name,
        "label_definition": label_definition,
        "features": columns,
        "n_complete_windows": int(len(y)),
        "n_subjects": int(len(subjects)),
        "algorithms": summaries,
    }


def _format_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(number) else "%.3f" % number


def _write_report(
    report_path: Path,
    dataset_info: Dict[str, Any],
    metadata: Dict[str, Any],
    full_windows: pd.DataFrame,
    results: List[Dict[str, Any]],
) -> None:
    lines = [
        "# PMED subject-independent pain experiments",
        "",
        "Generated: %s" % metadata["generated_at_utc"],
        "",
        "## Dataset and labels",
        "",
        "- Dataset: %s (DOI %s, v%s, %s)." % (
            DATASET_TITLE, DATASET_DOI, DATASET_VERSION, DATASET_LICENSE
        ),
        "- Raw PMED subjects found: %s." % dataset_info["raw_csv_count"],
        "- Official preprocessed windows: %s." % len(full_windows),
        "- Input shape: %s; official sampling rate: %s Hz; window: %s seconds." % (
            dataset_info["X_shape"],
            dataset_info["sampling_rate_hz"],
            dataset_info["window_seconds"],
        ),
        "- Heater class counts: `%s`." % json.dumps(dataset_info["heater_class_counts"], sort_keys=True),
        "- COVAS class counts: `%s`." % json.dumps(dataset_info["covas_class_counts"], sort_keys=True),
        "- Heater binary label: class 0/1 = non-pain, classes 2–5 = painful heat stimulus.",
        "- COVAS binary label: class 0 = no reported pain, classes 1–4 = reported pain; original class retained.",
        "- These targets were trained separately and never combined.",
        "",
        "## Feature extraction",
        "",
        "- PMED channel order: Bvp, Eda_E4, Resp, Eda_RB, Ecg, Emg.",
        "- BVP was third-order Butterworth bandpass filtered at 0.4–8 Hz; ECG at 5–25 Hz; candidate peak intervals outside 35–200 bpm were discarded.",
        "- Respiration was third-order Butterworth bandpass filtered at 0.06–0.8 Hz; cycles outside 5–40 breaths/min were discarded. Rate is 60 divided by consecutive-peak interval; amplitude is the median peak-to-trough range between consecutive peaks.",
        "- ECG research RMSSD is sqrt(mean(diff(RR_ms)^2)); SDNN is sample standard deviation of RR_ms, each requiring at least three intervals.",
        "- Model A includes ECG-derived HR/true RMSSD/SDNN, EDA, EMG, BVP-derived HR and respiration features; these are research-only.",
        "- Model B excludes hrv_ms, RMSSD, SDNN, EDA, ECG features, and EMG. It uses BVP-derived HR and Resp-derived rate, plus baseline-relative respiration amplitude as a breath-depth proxy.",
        "- Model B live feature extraction uses the same rolling summary definitions over available telemetry samples. Window sizes 10/20/30 seconds are supported by the helper; this experiment evaluates the PMED 10-second windows only.",
        "- PMED features are estimated from continuous waveforms while live summaries use approximately 2-second telemetry updates; min/max/std/slope therefore have different temporal sampling resolution and need live-distribution review before integration.",
        "- Per-subject baselines use heater class 0 no-stimulus windows. Held-out subjects' pain labels are not used for baseline calculations; baseline sensor readings are treated as calibration data.",
        "- Complete-case rows only; invalid/non-finite required features were excluded without imputation.",
        "- PMED raw respiration amplitude is not known to be on the same scale as firmware breath_depth. The wearable results with and without this proxy are both reported.",
        "",
        "## Validation",
        "",
        "- Primary: five-fold GroupKFold by subject (or fewer folds only if fewer than five subjects). No subject has windows in both sides of a fold.",
        (
            "- Leave-One-Subject-Out out-of-fold evaluation was also run where possible."
            if metadata.get("run_loso")
            else "- Leave-One-Subject-Out evaluation was skipped (%s); only subject-wise GroupKFold results are reported."
            % (metadata.get("loso_skipped_reason") or "disabled")
        ),
        "- Reported sensitivity, specificity, precision, recall and F1 use a fixed probability threshold of 0.5; it was not tuned against held-out data.",
        "- ROC-AUC uncertainty uses a subject-cluster bootstrap 95% interval over GroupKFold out-of-fold predictions.",
        "- No winning model was automatically selected for deployment; all saved models are research artifacts only.",
        "",
        "## Results",
        "",
    ]
    for experiment in results:
        lines.extend(
            [
                "### %s — %s" % (experiment["category"], experiment["label_name"]),
                "",
                "Label: %s. Complete windows: %s across %s subjects." % (
                    experiment["label_definition"],
                    experiment["n_complete_windows"],
                    experiment["n_subjects"],
                ),
                "",
                "| Algorithm | ROC-AUC (95% subject CI) | PR-AUC | Balanced accuracy | Precision | Sensitivity | Specificity | F1 | LOSO ROC-AUC |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for algorithm, result in experiment["algorithms"].items():
            gkf = result["groupkfold"]
            ci = gkf.get("roc_auc_subject_bootstrap_95ci", [None, None])
            loso_metrics = result.get("loso") or {}
            lines.append(
                "| %s | %s [%s, %s] | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    algorithm,
                    _format_metric(gkf.get("roc_auc")),
                    _format_metric(ci[0]),
                    _format_metric(ci[1]),
                    _format_metric(gkf.get("pr_auc")),
                    _format_metric(gkf.get("balanced_accuracy")),
                    _format_metric(gkf.get("precision")),
                    _format_metric(gkf.get("recall_sensitivity")),
                    _format_metric(gkf.get("specificity")),
                    _format_metric(gkf.get("f1")),
                    _format_metric(loso_metrics.get("roc_auc")),
                )
            )
            lines.append("")
            lines.append(
                "Confusion matrix for %s: `[[TN, FP], [FN, TP]] = %s`. "
                "Per-subject metrics and ROC/PR/confusion/calibration plots are saved under `%s`."
                % (
                    algorithm,
                    json.dumps(gkf.get("confusion_matrix_labels_0_1")),
                    result["run_dir"],
                )
            )
            importance = result.get("feature_importance", [])
            if importance:
                top = ", ".join(
                    "%s (%s)" % (item["feature"], _format_metric(item["importance"]))
                    for item in importance[:8]
                )
                lines.append("Top full-data feature importances (descriptive only): %s." % top)
            lines.append("")

    by_key = {(item["category"], item["label_name"]): item for item in results}
    model_algorithms = ("logistic_regression", "random_forest", "xgboost")
    if (
        ("wearable_compatible_with_depth", "pain_heater_binary") in by_key
        and ("wearable_compatible_with_depth", "pain_covas_binary") in by_key
    ):
        lines.extend(
            [
                "## Direct experiment comparisons",
                "",
                "### Heater stimulus versus COVAS reported-pain labels (wearable features)",
                "",
                "| Algorithm | Heater ROC-AUC | COVAS ROC-AUC | Heater balanced accuracy | COVAS balanced accuracy |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        with_depth_heater = by_key.get(("wearable_compatible_with_depth", "pain_heater_binary"))
        with_depth_covas = by_key.get(("wearable_compatible_with_depth", "pain_covas_binary"))
        if with_depth_heater and with_depth_covas:
            for algorithm in model_algorithms:
                h = with_depth_heater["algorithms"][algorithm]["groupkfold"]
                c = with_depth_covas["algorithms"][algorithm]["groupkfold"]
                lines.append(
                    "| %s | %s | %s | %s | %s |"
                    % (
                        algorithm,
                        _format_metric(h.get("roc_auc")),
                        _format_metric(c.get("roc_auc")),
                        _format_metric(h.get("balanced_accuracy")),
                        _format_metric(c.get("balanced_accuracy")),
                    )
                )
    research_covas = by_key.get(("research_rich", "pain_covas_binary"))
    wearable_depth_covas = by_key.get(("wearable_compatible_with_depth", "pain_covas_binary"))
    wearable_no_depth_covas = by_key.get(("wearable_compatible_without_depth", "pain_covas_binary"))
    if research_covas and wearable_depth_covas and wearable_no_depth_covas:
        lines.extend(
            [
                "### Rich research versus wearable-compatible features (COVAS target)",
                "",
                "| Algorithm | Research ROC-AUC | Wearable with depth ROC-AUC | Wearable without depth ROC-AUC |",
                "|---|---:|---:|---:|",
            ]
        )
        if research_covas and wearable_depth_covas and wearable_no_depth_covas:
            for algorithm in model_algorithms:
                r = research_covas["algorithms"][algorithm]["groupkfold"]
                wd = wearable_depth_covas["algorithms"][algorithm]["groupkfold"]
                wnd = wearable_no_depth_covas["algorithms"][algorithm]["groupkfold"]
                lines.append(
                    "| %s | %s | %s | %s |"
                    % (
                        algorithm,
                        _format_metric(r.get("roc_auc")),
                        _format_metric(wd.get("roc_auc")),
                        _format_metric(wnd.get("roc_auc")),
                    )
                )
        lines.append("")

    wearable_cov = [
        item for item in results
        if item["category"].startswith("wearable_compatible")
        and item["label_name"] == "pain_covas_binary"
    ]
    if wearable_cov:
        usable = []
        point_estimates = []
        for experiment in wearable_cov:
            for result in experiment["algorithms"].values():
                ci = result["groupkfold"].get("roc_auc_subject_bootstrap_95ci", [None, None])
                usable.append(ci[0] if ci else None)
                point_estimates.append(result["groupkfold"].get("roc_auc"))
        usable = [float(value) for value in usable if value is not None and math.isfinite(float(value))]
        if usable and max(usable) > 0.5:
            recommendation = (
                "At least one COVAS wearable-feature model, with or without the depth proxy, has a subject-bootstrap lower ROC-AUC bound above 0.5. "
                "This is evidence of above-chance signal in PMED under this feature mapping, not evidence of phantom-limb-pain performance. "
                "Review per-subject results and the depth-proxy ablation before deciding whether to collect a PLP pilot."
            )
        elif any(_safe_float(value) > 0.5 for value in point_estimates):
            recommendation = (
                "Some COVAS wearable-feature point estimates are above 0.5, but their subject-bootstrap intervals do not establish robust above-chance performance. "
                "Treat the result as inconclusive and review the per-subject and depth-proxy ablation results before collecting a PLP pilot."
            )
        else:
            recommendation = (
                "The COVAS wearable-feature results do not show a point estimate above chance in the tested models. "
                "This PMED experiment does not support proceeding to deployment; inspect feature quality and per-subject results before any further study."
            )
        lines.extend(["## Recommendation", "", recommendation, ""])
    lines.extend(
        [
            "## Limits",
            "",
            "- PMED is an experimentally induced heat-pain dataset. Results do not establish performance for phantom limb pain or any clinical decision.",
            "- The wearable-compatible experiment is only as comparable as the feature mapping, particularly the normalized respiration-amplitude proxy.",
            "- PMCD and WESAD were not pooled into this experiment. PMCD has a different clinical protocol; WESAD is reserved for separate stress-confounder evaluation.",
            "- No live inference, telemetry, haptic trigger, or ESP32 code was changed.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_experiments(pmed_dir: Path, artifact_root: Path, run_loso: bool = True) -> Path:
    X, y_heater_onehot, y_covas_onehot, subjects, records, pmed_metadata = load_pmed_windows(pmed_dir)
    fs = int(pmed_metadata["sampling_rate_hz"])
    all_features = extract_pmed_features(X, records, fs)
    model_b_features = build_model_b_features(all_features)
    frame = pd.concat([all_features, model_b_features], axis=1)
    frame["pain_heater_binary"] = np.isin(frame["pain_heater_class"], [2, 3, 4, 5]).astype(int)
    frame["pain_covas_binary"] = (frame["pain_covas_class"] > 0).astype(int)

    artifact_root.mkdir(parents=True, exist_ok=True)
    research_root = artifact_root / "pmed_research"
    wearable_root = artifact_root / "pmed_wearable"
    research_root.mkdir(parents=True, exist_ok=True)
    wearable_root.mkdir(parents=True, exist_ok=True)
    frame.to_csv(research_root / "pmed_feature_windows.csv", index=False)
    frame.to_csv(wearable_root / "pmed_feature_windows.csv", index=False)

    unique_subjects = np.unique(frame["subject_id"].astype(str))
    # This dataset has only 52 participants; the full 52-fold LOSO run is
    # tractable for the three lightweight models and is part of the requested
    # subject-independent evaluation.
    actual_run_loso = bool(run_loso and len(unique_subjects) <= 60)
    archive_path = pmed_dir.parent.parent / "PMED.zip"
    archive_md5_actual = _file_md5(archive_path)
    if archive_md5_actual is not None and archive_md5_actual != DATASET_ARCHIVE_MD5:
        raise ValueError(
            "PMED.zip checksum mismatch: %s != %s"
            % (archive_md5_actual, DATASET_ARCHIVE_MD5)
        )
    metadata = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dataset": DATASET_TITLE,
        "dataset_doi": DATASET_DOI,
        "dataset_version": DATASET_VERSION,
        "dataset_license": DATASET_LICENSE,
        "figshare_file_id": 49510668,
        "archive_path": str(archive_path),
        "archive_md5_expected": DATASET_ARCHIVE_MD5,
        "archive_md5_actual": archive_md5_actual,
        "official_repository": "https://github.com/gouverneurp/PMD",
        "official_preprocessor": str(pmed_dir / "create_np_files.py"),
        "script_version": SCRIPT_VERSION,
        "python_version": platform.python_version(),
        "package_versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": scipy.__version__,
            "scikit_learn": sklearn.__version__,
            "xgboost": __import__("xgboost").__version__,
            "matplotlib": matplotlib.__version__,
            "joblib": joblib.__version__,
        },
        "pmed": pmed_metadata,
        "X_shape": list(X.shape),
        "sampling_rate_hz": pmed_metadata["sampling_rate_hz"],
        "window_seconds": pmed_metadata["window_seconds"],
        "raw_csv_count": pmed_metadata["raw_csv_count"],
        "raw_subject_count": int(pmed_metadata["raw_csv_count"]),
        "preprocessed_window_count": int(len(frame)),
        "subject_count_with_windows": int(len(unique_subjects)),
        "heater_class_counts": _class_counts(frame["pain_heater_class"]),
        "covas_class_counts": _class_counts(frame["pain_covas_class"]),
        "heater_binary_counts": _class_counts(frame["pain_heater_binary"]),
        "covas_binary_counts": _class_counts(frame["pain_covas_binary"]),
        "run_loso": actual_run_loso,
        "loso_skipped_reason": (
            "More than 60 subjects; skipped to keep runtime reasonable."
            if run_loso and len(unique_subjects) > 60
            else ("Disabled by command line." if not run_loso else None)
        ),
        "haptic_firmware_modified": False,
        "live_inference_or_telemetry_modified": False,
    }
    (research_root / "dataset_metadata.json").write_text(
        json.dumps(_json_safe(metadata), indent=2), encoding="utf-8"
    )
    (wearable_root / "dataset_metadata.json").write_text(
        json.dumps(_json_safe(metadata), indent=2), encoding="utf-8"
    )

    results: List[Dict[str, Any]] = []
    label_specs = [
        (
            "pain_heater_binary",
            "heater_label",
            "Heater class 0/1 is non-pain (baseline or non-painful temperature); classes 2–5 are painful heat levels.",
        ),
        (
            "pain_covas_binary",
            "covas_label",
            "COVAS class 0 is no reported pain; classes 1–4 are reported pain. Original COVAS class retained.",
        ),
    ]

    for target_col, label_slug, definition in label_specs:
        results.append(
            _run_one(
                frame,
                RESEARCH_FEATURES,
                target_col,
                "research_rich",
                definition,
                research_root,
                metadata,
                loso=actual_run_loso,
            )
        )
        results.append(
            _run_one(
                frame,
                WEARABLE_FEATURES,
                target_col,
                "wearable_compatible_with_depth",
                definition,
                wearable_root,
                metadata,
                loso=actual_run_loso,
            )
        )
        results.append(
            _run_one(
                frame,
                WEARABLE_FEATURES_NO_DEPTH,
                target_col,
                "wearable_compatible_without_depth",
                definition,
                wearable_root,
                metadata,
                loso=actual_run_loso,
            )
        )

    _write_report(
        research_root / "experiment_report.md",
        metadata,
        metadata,
        frame,
        [item for item in results if item["category"] == "research_rich"],
    )
    _write_report(
        wearable_root / "experiment_report.md",
        metadata,
        metadata,
        frame,
        [item for item in results if item["category"].startswith("wearable_compatible")],
    )
    combined_report = artifact_root / "pmed_experiment_report.md"
    _write_report(combined_report, metadata, metadata, frame, results)
    (artifact_root / "summary.json").write_text(
        json.dumps(_json_safe({"metadata": metadata, "experiments": results}), indent=2),
        encoding="utf-8",
    )
    return combined_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmed-dir", type=Path, default=DEFAULT_PMED_DIR)
    parser.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--skip-loso", action="store_true")
    args = parser.parse_args()
    report = run_experiments(args.pmed_dir.resolve(), args.artifacts_dir.resolve(), run_loso=not args.skip_loso)
    print("[DONE] Report: %s" % report)


if __name__ == "__main__":
    main()
