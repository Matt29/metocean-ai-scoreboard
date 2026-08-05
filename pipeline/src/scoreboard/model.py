"""Per-station post-processing model: three candidate regressors on the features.

The model corrects the *physical* baseline (the best wave model for waves,
harmonic prediction for tide) — it never replaces it. For `kind="tide"` the
training target is the residual `obs - harmonic`; the published level is
reassembled as `harmonic + residual` by the caller.

Three candidates, compared per station by `train.py`:
* `hgb`          — histogram gradient boosting, the incumbent;
* `ridge`        — standardised linear ridge, the honest floor: if it ties the
                   boosting, the boosting is not buying skill;
* `hgb-per-lead` — one `hgb` per lead slice (1–12 / 13–24 / 25–48 h), routed at
                   predict time, in case the correction is lead-dependent.

The fitted column list travels with the artefact (`feature_columns`), so the
serving side reads it back instead of assuming a module constant.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"
MODEL_NAMES = ("hgb", "ridge", "hgb-per-lead")
LEAD_SLICES = ((1, 12), (13, 24), (25, 48))


def _hgb() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.06, early_stopping=True, random_state=0
    )


class PerLeadRegressor:
    """One `_hgb()` per `LEAD_SLICES` bucket, routed on `lead_h` at predict time.

    Plain fit/predict rather than a sklearn estimator: it is only ever used
    through this module, and `train.py` must be able to swap it for a Pipeline.
    """

    def fit(self, x: pd.DataFrame, y: pd.Series) -> PerLeadRegressor:
        self.feature_names_in_ = np.asarray(x.columns)
        self.models_ = {}
        for lo, hi in LEAD_SLICES:
            rows = x["lead_h"].between(lo, hi).to_numpy()
            if rows.any():
                self.models_[(lo, hi)] = _hgb().fit(x[rows], y[rows])
        if not self.models_:
            raise ValueError("no row in any lead slice")
        return self

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        # Routing on the upper bounds of the *fitted* slices: a slice left empty
        # by a short history widens its neighbour instead of raising at predict.
        fitted = list(self.models_)
        # `right=True`: a lead equal to a slice's upper bound belongs to it.
        routed = np.digitize(x["lead_h"].to_numpy(), [hi for _, hi in fitted[:-1]], right=True)
        out = np.empty(len(x), dtype=float)
        for i, key in enumerate(fitted):
            rows = routed == i
            if rows.any():
                out[rows] = self.models_[key].predict(x[rows])
        return out


def _estimator(name: str):
    if name == "hgb":
        return Pipeline([("gbr", _hgb())])
    if name == "ridge":
        return Pipeline([("scaler", StandardScaler()), ("ridge", Ridge(alpha=1.0))])
    if name == "hgb-per-lead":
        return PerLeadRegressor()
    raise ValueError(f"unknown model {name!r} — pick from {MODEL_NAMES}")


def _signature(est) -> str:
    """A structural fingerprint: Pipeline step names, or the bare type name."""
    if isinstance(est, Pipeline):
        return "pipeline:" + ",".join(step for step, _ in est.steps)
    return type(est).__name__


# Built from `MODEL_NAMES`/`_estimator` themselves (each candidate's *unfitted*
# signature), not a hand-copied list — a new candidate added to `_estimator`
# is picked up here for free. This is a dict, though: two candidates sharing a
# structural signature (e.g. two `Pipeline([("gbr", ...)])` variants) would
# silently collapse into one entry, and every artefact of the lost one would
# then be mis-inferred as the survivor. The assert below fails the import
# instead, the moment such a collision is introduced.
_SIGNATURE_TO_NAME = {_signature(_estimator(name)): name for name in MODEL_NAMES}
assert len(_SIGNATURE_TO_NAME) == len(MODEL_NAMES), (
    "two MODEL_NAMES candidates share a structural signature — "
    "infer_model_name can no longer tell them apart"
)


def infer_model_name(est) -> str | None:
    """The `MODEL_NAMES` candidate `est` structurally matches, or `None`.

    `None` — never a guess — for anything that isn't the exact fitted shape of
    one of the three candidates, e.g. an artefact from a future/removed model.
    """
    return _SIGNATURE_TO_NAME.get(_signature(est))


def train(x: pd.DataFrame, y: pd.Series, name: str = "hgb"):
    """Fit `name` on the columns of `x`, in that order.

    The fitted column list is then the estimator's own (`feature_names_in_`) —
    single source of truth, read back by `predict` and stored by `save`.

    `name` is stamped onto the estimator (`_model_name`) so `_dump` serializes
    the caller's own choice instead of re-inferring it from structure.
    """
    est = _estimator(name)
    est.fit(x, y)
    est._model_name = name
    return est


def predict(est, x: pd.DataFrame) -> np.ndarray:
    """Predict, reordering `x` to the columns seen at fit time."""
    return est.predict(_ordered(x, _fitted_columns(est)))


def save(
    est,
    station_id: str,
    models_dir: Path | None = None,
    baseline_model: str | None = None,
) -> Path:
    """Write one artefact immediately (legacy convenience API).

    Batch training uses :func:`stage` and :func:`promote_transaction` instead,
    so an evaluation failure cannot publish the preceding stations one by one.
    """
    path = (models_dir or MODELS_DIR) / f"{station_id}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump(est, path, baseline_model)
    return path


def stage(
    est,
    station_id: str,
    staging_dir: Path,
    baseline_model: str | None = None,
) -> Path:
    """Serialize an artefact into an isolated staging directory, not live models."""
    path = staging_dir / f"{station_id}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    _dump(est, path, baseline_model)
    return path


def _dump(est, path: Path, baseline_model: str | None) -> None:
    # `_model_name` is what `train()` was actually called with — the ground
    # truth. `infer_model_name` is only the fallback for an estimator that
    # reached here some other way (e.g. an artefact reloaded from disk before
    # this field existed and re-dumped as-is).
    model_name = getattr(est, "_model_name", None) or infer_model_name(est)
    joblib.dump(
        {
            "model": est,
            "baseline_model": baseline_model,
            "feature_columns": _fitted_columns(est),
            "model_name": model_name,
        },
        path,
    )


def promote_transaction(
    replacements: list[tuple[Path, Path]], backup_dir: Path
) -> None:
    """Promote staged files as one exception-recoverable publication.

    Each individual promotion is atomic. Before the first one, every live
    destination is copied byte-for-byte into ``backup_dir`` (or recorded as
    absent). If any ``os.replace`` raises, all destinations are restored to
    exactly that snapshot and the original exception is re-raised.

    This is an in-process rollback contract, not a claim of crash or SIGKILL
    atomicity across several filesystem entries.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshots: list[tuple[Path, Path | None]] = []
    for index, (_, destination) in enumerate(replacements):
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if destination.exists():
            backup = backup_dir / f"{index}.previous"
            shutil.copyfile(destination, backup)
        snapshots.append((destination, backup))

    try:
        for staged_path, destination in replacements:
            os.replace(staged_path, destination)
    except BaseException:
        for destination, backup in snapshots:
            if backup is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(backup, destination)
        raise


def load(station_id: str, models_dir: Path | None = None):
    """The estimator alone — use `load_artifact` to also get its metadata."""
    return load_artifact(station_id, models_dir)["model"]


def load_artifact(station_id: str, models_dir: Path | None = None) -> dict:
    """`{model, baseline_model, feature_columns, model_name}`.

    Artefacts written before Task 5 are a bare estimator; they are read back
    into the same shape rather than being invalidated. Likewise `model_name`:
    artefacts written before this field existed get it filled in by structural
    inference (`infer_model_name`) rather than staying silently absent — the
    same joblib on disk, no retraining needed.
    """
    obj = joblib.load((models_dir or MODELS_DIR) / f"{station_id}.joblib")
    if isinstance(obj, dict):
        obj.setdefault("model_name", infer_model_name(obj["model"]))
        return obj
    return {
        "model": obj,
        "baseline_model": None,
        "feature_columns": _fitted_columns(obj),
        "model_name": infer_model_name(obj),
    }


def _fitted_columns(est) -> list[str]:
    """The columns seen at fit time. Loud rather than defaulted: guessing a
    column list for a wave model out of the tide constant would serve garbage."""
    names = getattr(est, "feature_names_in_", None)
    if names is None:
        raise ValueError("estimator was not fitted on a named DataFrame")
    return list(names)


def _ordered(x: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Reorder to `columns`, failing loudly on a missing one."""
    missing = [c for c in columns if c not in x.columns]
    if missing:
        raise ValueError(f"missing feature columns: {missing}")
    return x[columns]
