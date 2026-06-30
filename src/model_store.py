"""
Model artifact store (roadmap §5 Phase 1, W3).

Persists per-ticker LightGBM ensembles plus their calibration and feature
reference to disk, and tracks the single active model version via a small
pointer file. The weekly retrain writes artifacts; the daily run reads the
active model for inference and falls back to legacy daily training when the
pointer is missing or corrupt.

Artifact layout (committed to git so daily CI can read it)::

    data/models/
      active_model.json
      per-ticker-v1-YYYYMMDD/
        metadata.json
        7011.JP/
          fold_0.txt fold_1.txt fold_2.txt final.txt
          calibration.json
          feature_reference.json
          ticker_metadata.json   (optional: cv_metrics, expected-return stats)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import DATA_DIR


def _model_dir(model_dir: str | None = None) -> Path:
    base = model_dir or os.environ.get("TRADER_MODEL_DIR") or str(DATA_DIR / "models")
    return Path(base)


def _active_file(active_file: str | None = None, model_dir: str | None = None) -> Path:
    raw = active_file or os.environ.get("TRADER_MODEL_ACTIVE_FILE")
    if raw:
        return Path(raw)
    return _model_dir(model_dir) / "active_model.json"


def version_dir(version: str, model_dir: str | None = None) -> Path:
    return _model_dir(model_dir) / version


def ticker_dir(version: str, ticker: str, model_dir: str | None = None) -> Path:
    return version_dir(version, model_dir) / ticker


def artifact_uri(version: str, model_dir: str | None = None) -> str:
    """Path recorded in model_registry.artifact_uri."""
    return str(version_dir(version, model_dir) / "metadata.json")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_model_bundle(
    version: str,
    ticker: str,
    boosters: dict,
    metadata: dict,
    model_dir: str | None = None,
) -> str:
    """
    Persist one ticker's ensemble + metadata.

    boosters: {"folds": [lgb.Booster, ...], "final": lgb.Booster}
    metadata: {"calibration": {...}|None, "feature_reference": {...}, ...}
    """
    d = ticker_dir(version, ticker, model_dir)
    d.mkdir(parents=True, exist_ok=True)

    folds = boosters.get("folds") or []
    for i, booster in enumerate(folds):
        booster.save_model(str(d / f"fold_{i}.txt"))
    final = boosters.get("final")
    if final is not None:
        final.save_model(str(d / "final.txt"))

    _write_json(d / "calibration.json", metadata.get("calibration"))
    _write_json(d / "feature_reference.json", metadata.get("feature_reference") or {})
    extra = {
        k: v
        for k, v in metadata.items()
        if k not in ("calibration", "feature_reference")
    }
    if extra:
        _write_json(d / "ticker_metadata.json", extra)
    return str(d)


def load_model_bundle(
    version: str, ticker: str, model_dir: str | None = None
) -> dict | None:
    """Load a ticker's ensemble + calibration + feature reference, or None."""
    import lightgbm as lgb

    d = ticker_dir(version, ticker, model_dir)
    final_path = d / "final.txt"
    if not d.exists() or not final_path.exists():
        return None

    folds = []
    i = 0
    while (d / f"fold_{i}.txt").exists():
        folds.append(lgb.Booster(model_file=str(d / f"fold_{i}.txt")))
        i += 1

    try:
        final = lgb.Booster(model_file=str(final_path))
    except Exception:  # noqa: BLE001 — corrupt artifact -> treat as missing
        return None

    return {
        "folds": folds,
        "final": final,
        "calibration": _read_json(d / "calibration.json"),
        "feature_reference": _read_json(d / "feature_reference.json") or {},
        "ticker_metadata": _read_json(d / "ticker_metadata.json") or {},
    }


def save_version_metadata(
    version: str, metadata: dict, model_dir: str | None = None
) -> str:
    """Write the version-level metadata.json (artifact_uri target)."""
    path = version_dir(version, model_dir) / "metadata.json"
    _write_json(path, metadata)
    return str(path)


def read_version_metadata(version: str, model_dir: str | None = None) -> dict | None:
    return _read_json(version_dir(version, model_dir) / "metadata.json")


def write_active_model(
    version: str,
    metadata: dict | None = None,
    model_dir: str | None = None,
    active_file: str | None = None,
) -> str:
    """Point the active model at `version`. metadata is merged into the pointer."""
    payload = {"version": version}
    if metadata:
        payload.update(metadata)
    path = _active_file(active_file, model_dir)
    _write_json(path, payload)
    return str(path)


def read_active_model(
    active_file: str | None = None, model_dir: str | None = None
) -> dict | None:
    """Return the active-model pointer, or None when missing / corrupt / invalid."""
    path = _active_file(active_file, model_dir)
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict) or not data.get("version"):
        return None
    return data


def clear_active_model(
    active_file: str | None = None, model_dir: str | None = None
) -> None:
    """Remove the active pointer (used to force a legacy-training fallback)."""
    path = _active_file(active_file, model_dir)
    if path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Phase 2 — cross-sectional (single-model) bundle
# ---------------------------------------------------------------------------


def _active_cs_file(
    active_file: str | None = None, model_dir: str | None = None
) -> Path:
    raw = active_file or os.environ.get("TRADER_CS_MODEL_ACTIVE_FILE")
    if raw:
        return Path(raw)
    return _model_dir(model_dir) / "active_cs_model.json"


def save_cs_bundle(
    version: str,
    booster,
    *,
    feature_schema: dict,
    calibration: dict | None = None,
    feature_reference: dict | None = None,
    sector_encoder: dict | None = None,
    universe: list | None = None,
    oos_predictions=None,
    version_metadata: dict | None = None,
    model_dir: str | None = None,
) -> str:
    """
    Persist the single cross-sectional model bundle under version_dir.

    booster: a trained lightgbm.Booster (saved to model.txt).
    feature_schema: dict, REQUIRED (written to feature_schema.json).
    calibration / feature_reference: dicts (JSON), optional -> None when absent.
    sector_encoder: dict, optional -> {} when absent.
    universe: list[str], optional -> [] when absent.
    oos_predictions: pandas.DataFrame -> oos_predictions.parquet (skipped when None/empty).
    version_metadata: dict -> metadata.json via save_version_metadata (skipped when None).
    Returns the version_dir path as str.
    """
    vdir = version_dir(version, model_dir)
    vdir.mkdir(parents=True, exist_ok=True)

    booster.save_model(str(vdir / "model.txt"))

    _write_json(vdir / "feature_schema.json", feature_schema)
    _write_json(vdir / "calibration.json", calibration)
    _write_json(vdir / "feature_reference.json", feature_reference)
    _write_json(
        vdir / "sector_encoder.json",
        sector_encoder if sector_encoder is not None else {},
    )
    _write_json(vdir / "universe.json", universe if universe is not None else [])

    if oos_predictions is not None:
        import pandas as pd  # noqa: PLC0415 — lazy import

        if isinstance(oos_predictions, pd.DataFrame) and not oos_predictions.empty:
            oos_predictions.to_parquet(str(vdir / "oos_predictions.parquet"))

    if version_metadata is not None:
        save_version_metadata(version, version_metadata, model_dir)

    return str(vdir)


def load_cs_bundle(version: str, model_dir: str | None = None) -> dict | None:
    """
    Load the CS bundle, or None when model.txt is missing/corrupt.

    Returns:
      {"version": version, "booster": lgb.Booster,
       "feature_schema": {...}, "calibration": {...}|None,
       "feature_reference": {...}|None, "sector_encoder": {...},
       "universe": [...], "oos_predictions": pd.DataFrame|None,
       "metadata": {...}|None}
    """
    import lightgbm as lgb  # noqa: PLC0415 — lazy import

    vdir = version_dir(version, model_dir)
    model_path = vdir / "model.txt"
    if not model_path.exists():
        return None

    try:
        booster = lgb.Booster(model_file=str(model_path))
    except Exception:  # noqa: BLE001 — corrupt artifact -> treat as missing
        return None

    feature_schema = _read_json(vdir / "feature_schema.json") or {}
    calibration = _read_json(vdir / "calibration.json")
    feature_reference = _read_json(vdir / "feature_reference.json")
    sector_encoder = _read_json(vdir / "sector_encoder.json") or {}
    universe_raw = _read_json(vdir / "universe.json")
    universe = universe_raw if isinstance(universe_raw, list) else []

    oos_predictions = None
    parquet_path = vdir / "oos_predictions.parquet"
    if parquet_path.exists():
        try:
            import pandas as pd  # noqa: PLC0415 — lazy import

            oos_predictions = pd.read_parquet(str(parquet_path))
        except Exception:  # noqa: BLE001 — corrupt/missing -> None
            oos_predictions = None

    metadata = read_version_metadata(version, model_dir)

    feature_cols = feature_schema.get("feature_cols")
    if not isinstance(feature_cols, list):
        feature_cols = []

    return {
        "version": version,
        "booster": booster,
        "feature_cols": feature_cols,
        "feature_schema": feature_schema,
        "calibration": calibration,
        "feature_reference": feature_reference,
        "sector_encoder": sector_encoder,
        "universe": universe,
        "oos_predictions": oos_predictions,
        "metadata": metadata,
    }


def write_active_cs_model(
    version: str,
    metadata: dict | None = None,
    model_dir: str | None = None,
    active_file: str | None = None,
) -> str:
    """Point the CS active model at `version`; metadata merged into the pointer."""
    payload = {"version": version}
    if metadata:
        payload.update(metadata)
    path = _active_cs_file(active_file, model_dir)
    _write_json(path, payload)
    return str(path)


def read_active_cs_model(
    active_file: str | None = None, model_dir: str | None = None
) -> dict | None:
    """Return the CS active-model pointer, or None when missing/corrupt/invalid (no 'version')."""
    path = _active_cs_file(active_file, model_dir)
    if not path.exists():
        return None
    data = _read_json(path)
    if not isinstance(data, dict) or not data.get("version"):
        return None
    return data


def clear_active_cs_model(
    active_file: str | None = None, model_dir: str | None = None
) -> None:
    """Remove the CS active pointer (rollback parity with clear_active_model)."""
    path = _active_cs_file(active_file, model_dir)
    if path.exists():
        path.unlink(missing_ok=True)
