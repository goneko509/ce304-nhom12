"""DriverSafe / UAH-DriveSet leakage-safe multimodal CPU pipeline.

Public API
----------
load_manifest(path)
global_verify_manifest(manifest, output_dir=None)
run_d1_to_d2_experiment(manifest, output_dir, config=None)
run_lodo_experiment(manifest, output_dir, config=None)

The input manifest is created by the Colab intake/audit cell and must include:
route_id, route_dir, driver_id, recording_datetime, behavior, behavior_label,
road_type, raw_accelerometers_path, plus optional modality path columns.

Primary task
------------
Binary route-level NORMAL (0) versus AGGRESSIVE (1) driving-style classification.
DROWSY records are excluded from this primary task.

Critical data-policy decisions
------------------------------
- Route-folder/manifest behavior labels are authoritative. The second column in
  RAW_ACCELEROMETERS is system_active_over_50kmh, never a class label.
- RAW_ACCELEROMETERS is mandatory. GPS/lane/vehicle/OSM are optional modalities.
- Roll/pitch/yaw are orientation angles in radians. Their derivatives are named
  angular-rate proxies; they are not true gyroscope measurements.
- EVENTS_INERTIAL and EVENTS_LIST_LANE_CHANGES are excluded from behavior-model
  features. The former may be saved/audited if present; neither is a behavior
  target or feature in this module.
- SEMANTIC files are intentionally not read to avoid direct heuristic leakage.
- D1→D2: all model transforms fit on D1-train only; calibration and threshold
  selection use D1-validation only; D2 remains untouched until evaluation.
- LODO: test driver is entirely held out; validation driver is entirely distinct
  from train drivers. This is a strict cross-driver/cross-vehicle protocol.

Dependencies
------------
numpy, pandas, scipy, scikit-learn, joblib. Optional: sktime for MiniROCKET.
"""

from __future__ import annotations

import json
import math
import re
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from scipy import signal, stats
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_selection import SelectKBest, mutual_info_classif
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_recall_curve,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import RobustScaler

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=UserWarning)

G = 9.80665
EPS = 1e-9
RANDOM_STATE = 42
RNG = np.random.default_rng(RANDOM_STATE)

# Verified no-header schemas from the user's UAH-DriveSet audit.
SCHEMAS: Dict[str, List[str]] = {
    "RAW_ACCELEROMETERS": [
        "timestamp_s", "system_active_over_50kmh",
        "acc_x_raw_g", "acc_y_raw_g", "acc_z_raw_g",
        "acc_x_kf_g", "acc_y_kf_g", "acc_z_kf_g",
        "roll_rad", "pitch_rad", "yaw_rad",
    ],
    # Inspection shows 12 columns. Final three fields are preserved neutrally
    # because their semantics have not been independently verified.
    "RAW_GPS": [
        "timestamp_s", "gps_speed_kmh", "latitude_deg", "longitude_deg",
        "altitude_m", "vertical_accuracy", "horizontal_accuracy",
        "course_deg", "difcourse_deg", "gps_extra_1", "gps_extra_2", "gps_extra_3",
    ],
    "PROC_LANE_DETECTION": [
        "timestamp_s", "lane_offset_m", "lane_phi_deg", "road_width_m",
        "lane_detection_state",
    ],
    "PROC_VEHICLE_DETECTION": [
        "timestamp_s", "front_vehicle_distance_m", "time_to_collision_s",
        "detected_vehicle_count", "vehicle_gps_speed_kmh",
    ],
    "PROC_OPENSTREETMAP_DATA": [
        "timestamp_s", "speed_limit_kmh", "speed_limit_reliability",
        "road_type_raw", "road_lane_count", "estimated_lane_index",
        "osm_query_latitude_deg", "osm_query_longitude_deg",
        "osm_response_delay_s", "osm_gps_speed_kmh",
    ],
    "EVENTS_INERTIAL": [
        "timestamp_s", "event_type", "event_level", "latitude_deg",
        "longitude_deg", "event_datetime",
    ],
    "EVENTS_LIST_LANE_CHANGES": [
        "timestamp_s", "lane_change_type", "latitude_deg", "longitude_deg",
        "duration_s", "irregularity_threshold_s",
    ],
}

PATH_COLUMN_BY_MODALITY = {
    "RAW_ACCELEROMETERS": "raw_accelerometers_path",
    "RAW_GPS": "raw_gps_path",
    "PROC_LANE_DETECTION": "proc_lane_detection_path",
    "PROC_VEHICLE_DETECTION": "proc_vehicle_detection_path",
    "PROC_OPENSTREETMAP_DATA": "proc_openstreetmap_data_path",
    "EVENTS_INERTIAL": "events_inertial_path",
    "EVENTS_LIST_LANE_CHANGES": "events_list_lane_changes_path",
}

REQUIRED_MANIFEST_COLUMNS = {
    "route_id", "route_dir", "driver_id", "recording_datetime",
    "behavior", "behavior_label", "road_type", "raw_accelerometers_path",
}


@dataclass
class Config:
    target_hz: float = 10.0
    short_gap_s: float = 0.50
    jitter_fraction: float = 0.50
    saturation_quantile: float = 0.999
    lowpass_cutoff_hz: float = 4.0
    lowpass_order: int = 3
    angle_sg_window_s: float = 0.50
    angle_sg_polyorder: int = 3

    gps_stale_s: float = 1.50
    lane_stale_s: float = 0.30
    vehicle_stale_s: float = 0.60
    osm_stale_s: float = 8.00

    windows_s: Tuple[float, ...] = (2.56, 5.12)
    stride_s: float = 0.50
    min_window_valid_fraction: float = 0.70
    purge_gap_s: float = 5.12
    train_fraction_within_route: float = 0.75

    max_features: int = 120
    positive_augmentation_copies: int = 1
    min_validation_recall: float = 0.80

    smoothing_k: int = 2
    smoothing_n: int = 3
    hysteresis_on_floor: float = 0.55
    hysteresis_off: float = 0.40
    cooldown_s: float = 3.0

    rf_trees: int = 300
    et_trees: int = 300
    n_jobs: int = -1
    run_minirocket: bool = True
    minirocket_kernels: int = 5000

    development_driver: str = "D1"
    external_test_driver: str = "D2"
    binary_behaviors: Tuple[str, ...] = ("NORMAL", "AGGRESSIVE")


def load_manifest(path: str | Path) -> pd.DataFrame:
    manifest = pd.read_csv(path)
    missing = REQUIRED_MANIFEST_COLUMNS.difference(manifest.columns)
    if missing:
        raise KeyError(f"Manifest lacks required columns: {sorted(missing)}")
    manifest = manifest.copy()
    manifest["driver_id"] = manifest["driver_id"].astype(str).str.upper()
    manifest["behavior"] = manifest["behavior"].astype(str).str.upper()
    return manifest


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.ndarray,)):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _ensure_dirs(output_dir: Path) -> None:
    for name in [
        "audits", "conditioned", "features", "models", "predictions",
        "metrics", "errors", "splits",
    ]:
        (output_dir / name).mkdir(parents=True, exist_ok=True)


def _read_raw_tokens(path: Path, nrows: Optional[int] = None) -> pd.DataFrame:
    return pd.read_csv(path, sep=r"\s+", header=None, engine="python", dtype=str, nrows=nrows)


def read_no_header(path: str | Path, modality: str, required: bool = False) -> Optional[pd.DataFrame]:
    if path is None or (isinstance(path, float) and np.isnan(path)):
        if required:
            raise FileNotFoundError(f"Missing required {modality} path")
        return None
    path = Path(path)
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return None
    if modality not in SCHEMAS:
        raise KeyError(f"Unknown modality schema: {modality}")
    raw = _read_raw_tokens(path)
    cols = SCHEMAS[modality]
    if raw.shape[1] != len(cols):
        raise ValueError(f"{path}: {modality} expected {len(cols)} columns, got {raw.shape[1]}")
    raw.columns = cols
    for col in cols:
        if modality == "PROC_OPENSTREETMAP_DATA" and col == "road_type_raw":
            raw[col] = raw[col].astype("string")
        elif modality == "EVENTS_INERTIAL" and col == "event_datetime":
            raw[col] = raw[col].astype("string")
        else:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
    if "timestamp_s" in raw:
        raw = raw.dropna(subset=["timestamp_s"]).sort_values("timestamp_s", kind="mergesort").reset_index(drop=True)
    return raw


def _acc_schema_check(path: str | Path) -> Dict[str, Any]:
    path = Path(path)
    result: Dict[str, Any] = {"path": str(path), "valid": False}
    try:
        raw = _read_raw_tokens(path)
        result["n_rows"] = int(len(raw))
        result["n_columns"] = int(raw.shape[1])
        if raw.shape[1] != 11:
            result["reason"] = "expected_11_columns"
            return result
        col2 = pd.to_numeric(raw.iloc[:, 1], errors="coerce")
        bad = sorted(set(col2.dropna().unique()).difference({0, 1}))
        result.update({
            "column2_unique": [float(x) for x in sorted(col2.dropna().unique())],
            "column2_missing_fraction": float(col2.isna().mean()),
            "column2_active_fraction": float((col2 == 1).mean()),
            "column2_unexpected_values": [float(x) for x in bad],
        })
        if bad or col2.isna().all():
            result["reason"] = "column2_not_binary_activation_flag"
            return result
        result["valid"] = True
        result["reason"] = "ok"
        return result
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def global_verify_manifest(manifest: pd.DataFrame, output_dir: Optional[str | Path] = None) -> pd.DataFrame:
    """Globally verify all D1–D6 accelerometer files before fitting any model."""
    manifest = _validate_manifest(manifest)
    audit_rows = []
    for _, route in manifest.iterrows():
        audit = _acc_schema_check(route["raw_accelerometers_path"])
        audit.update({
            "route_id": route["route_id"], "driver_id": route["driver_id"],
            "behavior": route["behavior"], "manifest_label": int(route["behavior_label"]),
        })
        audit_rows.append(audit)
    audit_df = pd.DataFrame(audit_rows)
    if output_dir is not None:
        output_dir = Path(output_dir)
        (output_dir / "audits").mkdir(parents=True, exist_ok=True)
        audit_df.to_csv(output_dir / "audits" / "global_raw_accelerometer_audit.csv", index=False)
    invalid = audit_df[~audit_df["valid"].astype(bool)]
    if not invalid.empty:
        text = invalid[["route_id", "reason"]].to_string(index=False)
        raise ValueError("Global RAW_ACCELEROMETERS verification failed:\n" + text)
    return audit_df


def _validate_manifest(manifest: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_MANIFEST_COLUMNS.difference(manifest.columns)
    if missing:
        raise KeyError(f"Manifest lacks columns: {sorted(missing)}")
    out = manifest.copy()
    out["driver_id"] = out["driver_id"].astype(str).str.upper()
    out["behavior"] = out["behavior"].astype(str).str.upper()
    expected = {"NORMAL": 0, "AGGRESSIVE": 1, "DROWSY": 2}
    if not out["behavior"].isin(expected).all():
        raise ValueError("Manifest contains an unsupported behavior")
    target = out["behavior"].map(expected).astype(int)
    if not (target == out["behavior_label"].astype(int)).all():
        raise ValueError("Manifest behavior_label is inconsistent with folder-derived behavior")
    if out["route_id"].duplicated().any():
        raise ValueError("Duplicate route_id in manifest")
    return out


def _sampling_audit(df: pd.DataFrame, modality: str, route_id: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {"route_id": route_id, "modality": modality, "available": df is not None}
    if df is None or df.empty:
        return item
    item.update({"n_rows": int(len(df)), "n_columns": int(df.shape[1])})
    if "timestamp_s" in df and len(df) > 1:
        t = df["timestamp_s"].to_numpy(float)
        dt = np.diff(t)
        p = dt[dt > 0]
        item.update({
            "start_s": float(t[0]), "end_s": float(t[-1]), "duration_s": float(t[-1] - t[0]),
            "duplicate_or_reverse_steps": int((dt <= 0).sum()),
            "median_dt_s": float(np.median(p)) if len(p) else np.nan,
            "median_hz": float(1.0 / np.median(p)) if len(p) else np.nan,
        })
    return item


def _grid(t: np.ndarray, hz: float) -> np.ndarray:
    dt = 1.0 / hz
    return np.arange(t[0], t[-1] + 0.5 * dt, dt)


def _timestamp_flags(t: np.ndarray, grid: np.ndarray, cfg: Config) -> pd.DataFrame:
    dt = np.diff(t)
    pos = dt[dt > 0]
    med = float(np.median(pos)) if len(pos) else 1.0 / cfg.target_hz
    jitter = np.zeros(len(grid), dtype=np.int8)
    reset = np.zeros(len(grid), dtype=np.int8)
    long_gap = np.zeros(len(grid), dtype=np.int8)
    for i, step in enumerate(dt):
        nearest = int(np.clip(round((t[i + 1] - t[0]) * cfg.target_hz), 0, len(grid) - 1))
        if step <= 0:
            reset[nearest] = 1
        elif abs(step - med) > cfg.jitter_fraction * med:
            jitter[nearest] = 1
        if step > cfg.short_gap_s:
            long_gap[(grid > t[i]) & (grid < t[i + 1])] = 1
    return pd.DataFrame({"timestamp_jitter_flag": jitter, "sensor_reset_flag": reset, "long_gap_flag": long_gap})


def _interpolate(df: pd.DataFrame, grid: np.ndarray, columns: Sequence[str]) -> pd.DataFrame:
    out = pd.DataFrame({"timestamp_s": grid})
    source = df[["timestamp_s", *columns]].copy().groupby("timestamp_s", as_index=True).median(numeric_only=True)
    for col in columns:
        union = source.index.union(pd.Index(grid)).sort_values()
        values = source[col].reindex(union).interpolate(method="index", limit_area="inside")
        out[col] = values.reindex(grid).to_numpy(float)
    return out


def _lowpass(x: pd.Series, fs: float, cfg: Config) -> np.ndarray:
    values = x.interpolate(limit_direction="both").to_numpy(float)
    if len(values) < 8:
        return values
    cutoff = min(cfg.lowpass_cutoff_hz, fs * 0.45)
    sos = signal.butter(cfg.lowpass_order, cutoff / (fs / 2), btype="low", output="sos")
    return signal.sosfilt(sos, values)


def _angle_proxy(angle: pd.Series, fs: float, cfg: Config) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = angle.interpolate(limit_direction="both").to_numpy(float)
    u = np.unwrap(x)
    win = max(int(round(cfg.angle_sg_window_s * fs)), cfg.angle_sg_polyorder + 2)
    if win % 2 == 0:
        win += 1
    smooth = signal.savgol_filter(u, win, cfg.angle_sg_polyorder, mode="interp") if len(u) >= win else u
    rate = np.gradient(smooth, 1.0 / fs)
    accel = np.gradient(rate, 1.0 / fs)
    return smooth, rate, accel


def _condition_accelerometer(raw: pd.DataFrame, route: pd.Series, cfg: Config) -> pd.DataFrame:
    t = raw["timestamp_s"].to_numpy(float)
    grid = _grid(t, cfg.target_hz)
    cols = [c for c in SCHEMAS["RAW_ACCELEROMETERS"] if c != "timestamp_s"]
    out = _interpolate(raw, grid, cols)
    flags = _timestamp_flags(t, grid, cfg)
    out = pd.concat([out, flags], axis=1)
    mask = out["long_gap_flag"].astype(bool)
    for c in cols:
        out.loc[mask, c] = np.nan
    for family in ["raw", "kf"]:
        for axis in ["x", "y", "z"]:
            src = f"acc_{axis}_{family}_g"
            dst = f"acc_{axis}_{family}_ms2"
            out[dst] = out[src] * G
            bias = float(np.nanmedian(out[dst]))
            out[f"{dst}_bias_corrected"] = out[dst] - bias
            out[f"{dst}_lowpass"] = _lowpass(out[f"{dst}_bias_corrected"], cfg.target_hz, cfg)
    # Dataset-calibrated axes: Y lateral; Z longitudinal.
    for family in ["raw", "kf"]:
        out[f"lateral_acc_{family}_ms2"] = out[f"acc_y_{family}_ms2_bias_corrected"]
        out[f"longitudinal_acc_{family}_ms2"] = out[f"acc_z_{family}_ms2_bias_corrected"]
        out[f"vertical_acc_{family}_ms2"] = out[f"acc_x_{family}_ms2_bias_corrected"]
        out[f"acc_mag_{family}_ms2"] = np.sqrt(sum(out[f"acc_{a}_{family}_ms2_bias_corrected"] ** 2 for a in ["x", "y", "z"]))
        for channel in [f"lateral_acc_{family}_ms2", f"longitudinal_acc_{family}_ms2", f"vertical_acc_{family}_ms2"]:
            out[f"{channel}_jerk_ms3"] = np.gradient(out[channel].fillna(0).to_numpy(), 1.0 / cfg.target_hz)
    for angle in ["roll", "pitch", "yaw"]:
        u, r, a = _angle_proxy(out[f"{angle}_rad"], cfg.target_hz, cfg)
        out[f"{angle}_unwrapped_rad"] = u
        out[f"{angle}_rate_proxy_rad_s"] = r
        out[f"{angle}_acc_proxy_rad_s2"] = a
    core = ["longitudinal_acc_raw_ms2", "lateral_acc_raw_ms2", "vertical_acc_raw_ms2"]
    out["imu_missingness_flag"] = out[core].isna().any(axis=1).astype(np.int8)
    sats = []
    for c in core:
        name = f"{c}_saturation_flag"
        q = out[c].abs().quantile(cfg.saturation_quantile)
        out[name] = ((out[c].abs() >= q) & np.isfinite(q)).astype(np.int8)
        sats.append(name)
    out["imu_saturation_flag"] = out[sats].max(axis=1).astype(np.int8)
    out["route_id"] = route["route_id"]
    out["driver_id"] = route["driver_id"]
    out["behavior"] = route["behavior"]
    out["label"] = int(route["behavior_label"])
    return out


def _prepare_gps(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    x = df.copy()
    x["gps_speed_ms"] = x["gps_speed_kmh"] / 3.6
    course = np.unwrap(np.deg2rad(x["course_deg"].interpolate(limit_direction="both").to_numpy()))
    x["gps_course_unwrapped_rad"] = course
    if len(x) > 1:
        t = x["timestamp_s"].to_numpy(float)
        x["gps_course_rate_proxy_rad_s"] = np.gradient(course, t)
        x["gps_speed_acc_proxy_ms2"] = np.gradient(x["gps_speed_ms"].interpolate(limit_direction="both").to_numpy(), t)
    return x


def _prepare_lane(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    x = df.copy()
    for c in ["lane_offset_m", "lane_phi_deg", "road_width_m"]:
        x.loc[x[c] == -9, c] = np.nan
    x["lane_detected_valid"] = (x["lane_detection_state"] == 2).astype(np.int8)
    return x


def _prepare_vehicle(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    x = df.copy()
    x["front_vehicle_detected"] = (x["front_vehicle_distance_m"] >= 0).astype(np.int8)
    x.loc[x["front_vehicle_distance_m"] < 0, "front_vehicle_distance_m"] = np.nan
    x.loc[x["time_to_collision_s"] < 0, "time_to_collision_s"] = np.nan
    x["vehicle_gps_speed_ms"] = x["vehicle_gps_speed_kmh"] / 3.6
    return x


def _prepare_osm(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None:
        return None
    x = df.copy()
    for c in ["speed_limit_kmh", "road_lane_count", "estimated_lane_index"]:
        x.loc[x[c] < 0, c] = np.nan
    x["road_type_raw"] = x["road_type_raw"].replace({"-": np.nan, "": np.nan})
    x["speed_limit_ms"] = x["speed_limit_kmh"] / 3.6
    x["osm_gps_speed_ms"] = x["osm_gps_speed_kmh"] / 3.6
    x["speed_limit_valid"] = (x["speed_limit_reliability"] > 0).astype(np.int8)
    return x


def _asof(base: pd.DataFrame, source: Optional[pd.DataFrame], prefix: str, tolerance: float) -> pd.DataFrame:
    out = base.copy().sort_values("timestamp_s")
    if source is None or source.empty:
        out[f"{prefix}_available"] = 0
        out[f"{prefix}_age_s"] = np.nan
        return out
    src = source.copy().sort_values("timestamp_s").rename(columns={"timestamp_s": f"{prefix}_source_timestamp_s"})
    out = pd.merge_asof(out, src, left_on="timestamp_s", right_on=f"{prefix}_source_timestamp_s", direction="backward", tolerance=tolerance)
    out[f"{prefix}_age_s"] = out["timestamp_s"] - out[f"{prefix}_source_timestamp_s"]
    out[f"{prefix}_available"] = out[f"{prefix}_source_timestamp_s"].notna().astype(np.int8)
    return out


def _fuse(imu: pd.DataFrame, gps: Optional[pd.DataFrame], lane: Optional[pd.DataFrame], vehicle: Optional[pd.DataFrame], osm: Optional[pd.DataFrame], cfg: Config) -> pd.DataFrame:
    out = _asof(imu, _prepare_gps(gps), "gps", cfg.gps_stale_s)
    out = _asof(out, _prepare_lane(lane), "lane", cfg.lane_stale_s)
    out = _asof(out, _prepare_vehicle(vehicle), "vehicle", cfg.vehicle_stale_s)
    out = _asof(out, _prepare_osm(osm), "osm", cfg.osm_stale_s)
    out["friction_demand_proxy"] = np.sqrt(out["longitudinal_acc_raw_ms2"] ** 2 + out["lateral_acc_raw_ms2"] ** 2) / G
    if "gps_speed_ms" in out:
        out["speed_over_limit_ms"] = out["gps_speed_ms"] - out.get("speed_limit_ms", np.nan)
        out["curvature_proxy_m_inv"] = out["yaw_rate_proxy_rad_s"] / out["gps_speed_ms"].clip(lower=0.5)
        out["lateral_yaw_consistency_abs"] = np.abs(out["lateral_acc_raw_ms2"] - out["gps_speed_ms"] * out["yaw_rate_proxy_rad_s"])
    return out


def _robust_corr(x: np.ndarray, y: np.ndarray) -> float:
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.std(x[ok]) < EPS or np.std(y[ok]) < EPS:
        return 0.0
    return float(np.corrcoef(x[ok], y[ok])[0, 1])


def _stats_features(x: np.ndarray, fs: float, prefix: str) -> Dict[str, float]:
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    names = ["mean", "median", "min", "max", "range", "std", "rms", "p05", "p25", "p75", "p95", "iqr", "skew", "kurtosis", "energy", "zcr", "acf1", "acf5", "dominant_hz", "spectral_entropy"]
    out = {f"{prefix}_{n}": 0.0 for n in names}
    if len(x) < 4:
        return out
    q = np.percentile(x, [5, 25, 75, 95])
    f, power = signal.periodogram(x - np.mean(x), fs=fs)
    total = power.sum() + EPS
    p = power / total
    lag5 = min(5, len(x) - 1)
    out.update({
        f"{prefix}_mean": float(x.mean()), f"{prefix}_median": float(np.median(x)),
        f"{prefix}_min": float(x.min()), f"{prefix}_max": float(x.max()), f"{prefix}_range": float(np.ptp(x)),
        f"{prefix}_std": float(x.std()), f"{prefix}_rms": float(np.sqrt(np.mean(x**2))),
        f"{prefix}_p05": float(q[0]), f"{prefix}_p25": float(q[1]), f"{prefix}_p75": float(q[2]), f"{prefix}_p95": float(q[3]), f"{prefix}_iqr": float(q[2]-q[1]),
        f"{prefix}_skew": float(stats.skew(x, bias=False)) if len(x) > 2 else 0.0,
        f"{prefix}_kurtosis": float(stats.kurtosis(x, bias=False)) if len(x) > 3 else 0.0,
        f"{prefix}_energy": float(np.mean(x**2)), f"{prefix}_zcr": float(np.mean(np.diff(np.signbit(x)) != 0)),
        f"{prefix}_acf1": _robust_corr(x[:-1], x[1:]), f"{prefix}_acf5": _robust_corr(x[:-lag5], x[lag5:]) if lag5 else 0.0,
        f"{prefix}_dominant_hz": float(f[np.argmax(power)]) if len(f) else 0.0,
        f"{prefix}_spectral_entropy": float(-(p*np.log2(p+EPS)).sum() / np.log2(len(p)+EPS)),
    })
    return out


def _window_features(w: pd.DataFrame, fs: float) -> Dict[str, float]:
    channels = [
        "longitudinal_acc_raw_ms2", "lateral_acc_raw_ms2", "vertical_acc_raw_ms2",
        "longitudinal_acc_kf_ms2", "lateral_acc_kf_ms2", "vertical_acc_kf_ms2",
        "acc_mag_raw_ms2", "acc_mag_kf_ms2",
        "longitudinal_acc_raw_ms2_jerk_ms3", "lateral_acc_raw_ms2_jerk_ms3",
        "yaw_rate_proxy_rad_s", "yaw_acc_proxy_rad_s2", "gps_speed_ms",
        "gps_speed_acc_proxy_ms2", "gps_course_rate_proxy_rad_s",
        "lane_offset_m", "lane_phi_deg", "road_width_m", "front_vehicle_distance_m",
        "time_to_collision_s", "detected_vehicle_count", "speed_limit_ms",
        "speed_over_limit_ms", "friction_demand_proxy", "curvature_proxy_m_inv",
        "lateral_yaw_consistency_abs",
    ]
    out: Dict[str, float] = {}
    for c in channels:
        if c in w:
            out.update(_stats_features(w[c].to_numpy(float), fs, c))
    long = w["longitudinal_acc_raw_ms2"].to_numpy(float)
    lat = w["lateral_acc_raw_ms2"].to_numpy(float)
    jerk = w["longitudinal_acc_raw_ms2_jerk_ms3"].to_numpy(float)
    out.update({
        "phys_peak_deceleration": float(max(0.0, -np.nanmin(long))),
        "phys_peak_acceleration": float(max(0.0, np.nanmax(long))),
        "phys_peak_lateral_acceleration": float(np.nanmax(np.abs(lat))),
        "phys_peak_longitudinal_jerk": float(np.nanmax(np.abs(jerk))),
        "phys_lateral_g_peak": float(np.nanmax(np.abs(lat)) / G),
        "phys_friction_peak": float(np.nanmax(np.sqrt(long**2 + lat**2)) / G),
        "corr_lat_yaw_proxy": _robust_corr(lat, w["yaw_rate_proxy_rad_s"].to_numpy(float)),
    })
    if "gps_speed_ms" in w:
        out["corr_long_speed"] = _robust_corr(long, w["gps_speed_ms"].to_numpy(float))
    for c in w.columns:
        if c.endswith("_flag") or c.endswith("_available"):
            if pd.api.types.is_numeric_dtype(w[c]):
                out[f"quality_{c}_fraction"] = float(w[c].fillna(0).mean())
    return out


def _make_windows(fused: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    stride = max(1, int(round(cfg.stride_s * cfg.target_hz)))
    for seconds in cfg.windows_s:
        n = int(round(seconds * cfg.target_hz))
        for start in range(0, len(fused) - n + 1, stride):
            w = fused.iloc[start:start+n]
            valid = 1.0 - float(w["imu_missingness_flag"].mean())
            if valid < cfg.min_window_valid_fraction:
                continue
            row = _window_features(w, cfg.target_hz)
            row.update({
                "route_id": w["route_id"].iloc[0], "driver_id": w["driver_id"].iloc[0],
                "behavior": w["behavior"].iloc[0], "label": int(w["label"].iloc[0]),
                "window_seconds": seconds, "start_time_s": float(w["timestamp_s"].iloc[0]),
                "end_time_s": float(w["timestamp_s"].iloc[-1]), "valid_fraction": valid,
                "active_speed_fraction": float(w["system_active_over_50kmh"].fillna(0).mean()),
            })
            rows.append(row)
    return pd.DataFrame(rows)


def _feature_columns(df: pd.DataFrame) -> List[str]:
    meta = {"route_id", "driver_id", "behavior", "label", "split", "window_seconds", "start_time_s", "end_time_s", "valid_fraction", "active_speed_fraction"}
    return [c for c in df.columns if c not in meta and pd.api.types.is_numeric_dtype(df[c])]


def _purged_within_route_split(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy(); out["split"] = "purged"
    for route_id, g in out.groupby("route_id", sort=False):
        g = g.sort_values("start_time_s")
        cut = max(1, min(int(math.floor(len(g)*cfg.train_fraction_within_route)), len(g)-1))
        boundary = g["start_time_s"].iloc[cut]
        purge = np.abs(g["start_time_s"] - boundary) <= cfg.purge_gap_s
        out.loc[g.index[(np.arange(len(g)) < cut) & ~purge.to_numpy()], "split"] = "train"
        out.loc[g.index[(np.arange(len(g)) >= cut) & ~purge.to_numpy()], "split"] = "validation"
    return out


def _weights(y: np.ndarray) -> np.ndarray:
    counts = pd.Series(y).value_counts()
    lookup = {k: len(y)/(len(counts)*v) for k,v in counts.items()}
    return np.asarray([lookup[v] for v in y], float)


def _augment(X: pd.DataFrame, y: pd.Series, copies: int) -> Tuple[pd.DataFrame, pd.Series]:
    if copies <= 0 or y.sum() == 0:
        return X, y
    pos = X.loc[y.to_numpy() == 1].copy()
    cols = [c for c in X.columns if not any(t in c.lower() for t in ["flag", "available", "quality"])]
    scale = X[cols].std().replace(0, 1.0)
    xs, ys = [X], [y]
    for _ in range(copies):
        a = pos.copy(); noise = RNG.normal(0, 0.01, size=(len(a), len(cols)))
        a.loc[:, cols] = a[cols].to_numpy() + noise * scale.to_numpy()
        xs.append(a); ys.append(pd.Series(np.ones(len(a), dtype=int)))
    return pd.concat(xs, ignore_index=True), pd.concat(ys, ignore_index=True)


def _pipeline(estimator: Any, k: int) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
        ("scaler", RobustScaler()),
        ("selector", SelectKBest(mutual_info_classif, k=k)),
        ("model", estimator),
    ])


def _choose_threshold(y: np.ndarray, p: np.ndarray, minimum_recall: float) -> float:
    prec, rec, thr = precision_recall_curve(y, p)
    if len(thr) == 0:
        return 0.5
    f1 = 2*prec[:-1]*rec[:-1]/(prec[:-1]+rec[:-1]+EPS)
    eligible = np.flatnonzero(rec[:-1] >= minimum_recall)
    idx = eligible[np.argmax(f1[eligible])] if len(eligible) else int(np.argmax(f1))
    return float(thr[idx])


def _metrics(y: np.ndarray, p: np.ndarray, threshold: float) -> Dict[str, Any]:
    pred = (p >= threshold).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(y, pred, labels=[0,1], zero_division=0)
    return {
        "n": int(len(y)), "threshold": float(threshold), "positive_rate": float(np.mean(y)),
        "predicted_positive_rate": float(np.mean(pred)),
        "pr_auc": float(average_precision_score(y,p)), "roc_auc": float(roc_auc_score(y,p)),
        "macro_f1": float(f1_score(y,pred,average="macro",zero_division=0)),
        "balanced_accuracy": float(balanced_accuracy_score(y,pred)),
        "mcc": float(matthews_corrcoef(y,pred)) if len(np.unique(pred)) > 1 else 0.0,
        "precision_normal": float(precision[0]), "recall_normal": float(recall[0]), "f1_normal": float(f1[0]),
        "precision_aggressive": float(precision[1]), "recall_aggressive": float(recall[1]), "f1_aggressive": float(f1[1]),
        "confusion_matrix": confusion_matrix(y,pred,labels=[0,1]).tolist(),
    }


def _rule_score(df: pd.DataFrame) -> np.ndarray:
    def get(c: str) -> np.ndarray:
        return pd.to_numeric(df[c], errors="coerce").fillna(0).to_numpy(float) if c in df else np.zeros(len(df))
    evidence = np.maximum.reduce([
        get("phys_peak_deceleration")/5.0, get("phys_peak_acceleration")/3.0,
        get("phys_peak_lateral_acceleration")/3.0, get("phys_peak_longitudinal_jerk")/6.0,
        get("yaw_rate_proxy_rad_s_max")/0.5,
    ])
    return 1/(1+np.exp(-3*(evidence-1)))


def _alerts(pred: pd.DataFrame, threshold: float, cfg: Config) -> pd.DataFrame:
    out = pred.copy(); out["alert"] = 0
    on = max(threshold, cfg.hysteresis_on_floor); off = min(threshold, cfg.hysteresis_off)
    for route, idx in out.groupby("route_id", sort=False).groups.items():
        ix = list(idx); state=False; hist=[]; last=-np.inf; vals=out.loc[ix,"probability"].to_numpy(); times=out.loc[ix,"start_time_s"].to_numpy(); alerts=[]
        for t,p in zip(times,vals):
            hist.append(p >= on); hist=hist[-cfg.smoothing_n:]
            if not state and sum(hist) >= cfg.smoothing_k and t-last >= cfg.cooldown_s:
                state=True; last=t
            elif state and p < off:
                state=False
            alerts.append(int(state))
        out.loc[ix,"alert"] = alerts
    return out


def _false_alert_rate(pred: pd.DataFrame) -> float:
    n = pred[pred["label"] == 0]
    if n.empty: return np.nan
    starts=0; duration=0.0
    for _,g in n.groupby("route_id"):
        g=g.sort_values("start_time_s"); starts += int((g["alert"].eq(1)&g["alert"].shift(fill_value=0).eq(0)).sum()); duration += max(g["end_time_s"].max()-g["start_time_s"].min(),0)
    return starts/(duration/3600) if duration>0 else np.nan


def _route_data(route: pd.Series, cfg: Config, audits: List[Dict[str,Any]]) -> pd.DataFrame:
    acc=read_no_header(route["raw_accelerometers_path"],"RAW_ACCELEROMETERS",True)
    gps=read_no_header(route.get("raw_gps_path"),"RAW_GPS") if "raw_gps_path" in route else None
    lane=read_no_header(route.get("proc_lane_detection_path"),"PROC_LANE_DETECTION") if "proc_lane_detection_path" in route else None
    vehicle=read_no_header(route.get("proc_vehicle_detection_path"),"PROC_VEHICLE_DETECTION") if "proc_vehicle_detection_path" in route else None
    osm=read_no_header(route.get("proc_openstreetmap_data_path"),"PROC_OPENSTREETMAP_DATA") if "proc_openstreetmap_data_path" in route else None
    for name,df in [("RAW_ACCELEROMETERS",acc),("RAW_GPS",gps),("PROC_LANE_DETECTION",lane),("PROC_VEHICLE_DETECTION",vehicle),("PROC_OPENSTREETMAP_DATA",osm)]:
        audits.append(_sampling_audit(df,name,route["route_id"]))
    return _fuse(_condition_accelerometer(acc,route,cfg),gps,lane,vehicle,osm,cfg)


def _build_features(manifest: pd.DataFrame, cfg: Config, output_dir: Path) -> pd.DataFrame:
    audits=[]; blocks=[]
    for _,route in manifest.iterrows():
        fused=_route_data(route,cfg,audits)
        fused.to_csv(output_dir/"conditioned"/f"{route['route_id']}_fused.csv",index=False)
        blocks.append(_make_windows(fused,cfg))
    with (output_dir/"audits"/"modality_timestamp_audit.json").open("w") as f: json.dump(audits,f,indent=2,default=_json_default)
    features=pd.concat(blocks,ignore_index=True)
    features.to_csv(output_dir/"features"/"engineered_windows.csv",index=False)
    return features


def _fit_compare(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame, cfg: Config, output_dir: Path, fold_name: str) -> pd.DataFrame:
    feature_cols=_feature_columns(train)
    Xtr=train[feature_cols].replace([np.inf,-np.inf],np.nan); ytr=train["label"].astype(int).reset_index(drop=True)
    Xva=val[feature_cols].replace([np.inf,-np.inf],np.nan); yva=val["label"].astype(int).to_numpy()
    Xte=test[feature_cols].replace([np.inf,-np.inf],np.nan); yte=test["label"].astype(int).to_numpy()
    Xaug,yaug=_augment(Xtr.reset_index(drop=True),ytr,cfg.positive_augmentation_copies)
    specs={
        "hist_gradient_boosting": HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=21,l2_regularization=1.0,max_iter=300,random_state=RANDOM_STATE),
        "extra_trees": ExtraTreesClassifier(n_estimators=cfg.et_trees,min_samples_leaf=2,max_features="sqrt",class_weight="balanced",n_jobs=cfg.n_jobs,random_state=RANDOM_STATE),
        "random_forest": RandomForestClassifier(n_estimators=cfg.rf_trees,min_samples_leaf=2,max_features="sqrt",class_weight="balanced_subsample",n_jobs=cfg.n_jobs,random_state=RANDOM_STATE),
    }
    results=[]
    # Rule baseline.
    rpva=_rule_score(val); rthr=_choose_threshold(yva,rpva,cfg.min_validation_recall); rpte=_rule_score(test)
    rmet=_metrics(yte,rpte,rthr); results.append({"fold":fold_name,"model":"physics_rule","fit_seconds":0.0,"predict_seconds":0.0,**rmet})
    rule_pred=test[["route_id","driver_id","label","start_time_s","end_time_s"]].copy(); rule_pred["probability"]=rpte; rule_pred["threshold"]=rthr; rule_pred["raw_prediction"]=(rpte>=rthr).astype(int); rule_pred=_alerts(rule_pred,rthr,cfg); rule_pred.to_csv(output_dir/"predictions"/f"{fold_name}_physics_rule.csv",index=False)
    for name,est in specs.items():
        pipe=_pipeline(est,min(cfg.max_features,Xaug.shape[1])); start=time.perf_counter(); pipe.fit(Xaug,yaug,model__sample_weight=_weights(yaug.to_numpy())); fit=time.perf_counter()-start
        raw_val=pipe.predict_proba(Xva)[:,1]; cal=LogisticRegression(class_weight="balanced",random_state=RANDOM_STATE).fit(raw_val.reshape(-1,1),yva); pval=cal.predict_proba(raw_val.reshape(-1,1))[:,1]; thr=_choose_threshold(yva,pval,cfg.min_validation_recall)
        start=time.perf_counter(); raw_test=pipe.predict_proba(Xte)[:,1]; ptest=cal.predict_proba(raw_test.reshape(-1,1))[:,1]; pred_time=time.perf_counter()-start
        met=_metrics(yte,ptest,thr); results.append({"fold":fold_name,"model":name,"fit_seconds":fit,"predict_seconds":pred_time,**met})
        pred=test[["route_id","driver_id","label","start_time_s","end_time_s"]].copy(); pred["probability"]=ptest; pred["threshold"]=thr; pred["raw_prediction"]=(ptest>=thr).astype(int); pred=_alerts(pred,thr,cfg); pred.to_csv(output_dir/"predictions"/f"{fold_name}_{name}.csv",index=False)
        joblib.dump({"pipeline":pipe,"calibrator":cal,"feature_columns":feature_cols,"threshold":thr,"config":asdict(cfg)},output_dir/"models"/f"{fold_name}_{name}.joblib")
        if name=="hist_gradient_boosting":
            try:
                pi=permutation_importance(pipe,Xte,yte,scoring="average_precision",n_repeats=3,random_state=RANDOM_STATE,n_jobs=cfg.n_jobs)
                pd.DataFrame({"feature":feature_cols,"importance_mean":pi.importances_mean,"importance_std":pi.importances_std}).sort_values("importance_mean",ascending=False).to_csv(output_dir/"metrics"/f"{fold_name}_{name}_permutation_importance.csv",index=False)
            except Exception as exc:
                (output_dir/"metrics"/f"{fold_name}_importance_error.txt").write_text(str(exc))
    df=pd.DataFrame(results)
    # Attach alert burden from saved predictions.
    fars={}
    for model in df["model"]:
        pfile=output_dir/"predictions"/f"{fold_name}_{model}.csv"; fars[model]=_false_alert_rate(pd.read_csv(pfile)) if pfile.exists() else np.nan
    df["false_alert_episodes_per_hour"]=df["model"].map(fars)
    return df


def _binary_manifest(manifest: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    m=manifest[manifest["behavior"].isin(cfg.binary_behaviors)].copy()
    if m.empty: raise ValueError("No NORMAL/AGGRESSIVE routes in manifest")
    return m


def run_d1_to_d2_experiment(manifest: pd.DataFrame, output_dir: str | Path, config: Optional[Config] = None) -> Dict[str,Any]:
    cfg=config or Config(); output_dir=Path(output_dir); _ensure_dirs(output_dir)
    manifest=_validate_manifest(manifest); global_verify_manifest(manifest,output_dir)
    m=_binary_manifest(manifest,cfg); dev=m[m.driver_id==cfg.development_driver]; ext=m[m.driver_id==cfg.external_test_driver]
    if dev.empty or ext.empty: raise ValueError("Development or external-test driver has no binary routes")
    if set(dev.behavior) != set(cfg.binary_behaviors) or set(ext.behavior) != set(cfg.binary_behaviors): raise ValueError("Both D1 development and D2 external must include NORMAL and AGGRESSIVE")
    selected=pd.concat([dev,ext],ignore_index=True); selected.to_csv(output_dir/"selected_manifest.csv",index=False)
    features=_build_features(selected,cfg,output_dir)
    devf=_purged_within_route_split(features[features.driver_id==cfg.development_driver].copy(),cfg); extf=features[features.driver_id==cfg.external_test_driver].copy(); extf["split"]="external_test"
    allf=pd.concat([devf,extf],ignore_index=True); allf.to_csv(output_dir/"splits"/"d1_d2_windows_with_splits.csv",index=False)
    train=allf[allf.split=="train"]; val=allf[allf.split=="validation"]; test=allf[allf.split=="external_test"]
    if min(len(train),len(val),len(test))<20: raise RuntimeError("Insufficient train/validation/test windows after purging")
    results=_fit_compare(train,val,test,cfg,output_dir,"D1_to_D2"); results.to_csv(output_dir/"metrics"/"D1_to_D2_model_comparison.csv",index=False)
    report={"protocol":"D1 route-chronological train/validation with purge, D2 external test","config":asdict(cfg),"n_train":len(train),"n_validation":len(val),"n_external_test":len(test),"models":results.to_dict(orient="records"),"limitations":["Route-folder behavior labels are route-level weak labels.","D2 was not used to fit preprocessing, models, calibrator, or thresholds.","Orientation derivatives are proxies, not true gyroscope data.","SEMANTIC and heuristic event outputs are excluded from behavior-model features."]}
    with (output_dir/"run_report.json").open("w") as f: json.dump(report,f,indent=2,default=_json_default)
    return {"features":allf,"metrics":results,"report":report}


def run_lodo_experiment(manifest: pd.DataFrame, output_dir: str | Path, config: Optional[Config] = None) -> Dict[str,Any]:
    cfg=config or Config(); output_dir=Path(output_dir); _ensure_dirs(output_dir)
    manifest=_validate_manifest(manifest); global_verify_manifest(manifest,output_dir)
    m=_binary_manifest(manifest,cfg)
    drivers=sorted(m.driver_id.unique())
    if len(drivers)<3: raise ValueError("LODO needs at least 3 drivers")
    # Process all routes once, then reuse feature table across folds.
    m.to_csv(output_dir/"selected_manifest.csv",index=False)
    features=_build_features(m,cfg,output_dir)
    fold_results=[]; split_rows=[]
    for i,test_driver in enumerate(drivers):
        remaining=[d for d in drivers if d!=test_driver]
        validation_driver=remaining[i % len(remaining)]
        train_drivers=[d for d in remaining if d!=validation_driver]
        train=features[features.driver_id.isin(train_drivers)].copy(); val=features[features.driver_id==validation_driver].copy(); test=features[features.driver_id==test_driver].copy()
        for name,df in [("train",train),("validation",val),("test",test)]:
            if set(df.label.unique()) != {0,1}: raise ValueError(f"LODO fold {test_driver}: {name} lacks a binary class")
        fold=f"LODO_test_{test_driver}_val_{validation_driver}"
        split_rows.extend([{"fold":fold,"route_id":r.route_id,"driver_id":r.driver_id,"split":"train" if r.driver_id in train_drivers else "validation" if r.driver_id==validation_driver else "test"} for _,r in m.iterrows()])
        fold_results.append(_fit_compare(train,val,test,cfg,output_dir,fold))
    all_results=pd.concat(fold_results,ignore_index=True); all_results.to_csv(output_dir/"metrics"/"LODO_per_fold_model_metrics.csv",index=False)
    summary=all_results.groupby("model").agg(["mean","std"])[["pr_auc","roc_auc","macro_f1","balanced_accuracy","mcc","precision_aggressive","recall_aggressive","false_alert_episodes_per_hour"]]; summary.to_csv(output_dir/"metrics"/"LODO_summary_mean_std.csv")
    pd.DataFrame(split_rows).to_csv(output_dir/"splits"/"LODO_route_splits.csv",index=False)
    report={"protocol":"Strict leave-one-driver-out; distinct validation driver selected from remaining drivers","drivers":drivers,"config":asdict(cfg),"summary_file":"metrics/LODO_summary_mean_std.csv","limitations":["Each driver corresponds to a vehicle, so this is simultaneous cross-driver/cross-vehicle testing.","Behavior labels are route-level weak labels.","No semantic or event output is used as a behavior model feature."]}
    with (output_dir/"run_report.json").open("w") as f: json.dump(report,f,indent=2,default=_json_default)
    return {"features":features,"per_fold_metrics":all_results,"summary":summary,"report":report}
