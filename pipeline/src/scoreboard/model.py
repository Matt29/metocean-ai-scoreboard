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


def train(x: pd.DataFrame, y: pd.Series, name: str = "hgb"):
    """Fit `name` on the columns of `x`, in that order.

    The fitted column list is then the estimator's own (`feature_names_in_`) —
    single source of truth, read back by `predict` and stored by `save`.
    """
    est = _estimator(name)
    est.fit(x, y)
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
    """Write the artefact: the estimator plus what it was fitted against."""
    path = (models_dir or MODELS_DIR) / f"{station_id}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": est,
            "baseline_model": baseline_model,
            "feature_columns": _fitted_columns(est),
        },
        path,
    )
    return path


def load(station_id: str, models_dir: Path | None = None):
    """The estimator alone — use `load_artifact` to also get its metadata."""
    return load_artifact(station_id, models_dir)["model"]


def load_artifact(station_id: str, models_dir: Path | None = None) -> dict:
    """`{model, baseline_model, feature_columns}`.

    Artefacts written before Task 5 are a bare estimator; they are read back
    into the same shape rather than being invalidated.
    """
    obj = joblib.load((models_dir or MODELS_DIR) / f"{station_id}.joblib")
    if isinstance(obj, dict):
        return obj
    return {"model": obj, "baseline_model": None, "feature_columns": _fitted_columns(obj)}


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
