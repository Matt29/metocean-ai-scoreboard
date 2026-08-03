"""Per-station post-processing model: gradient boosting on the Task-6 features.

The model corrects the *physical* baseline (MFWAM analysis for waves, harmonic
prediction for tide) — it never replaces it. For `kind="tide"` the training
target is the residual `obs - harmonic`; the published level is reassembled as
`harmonic + residual` by the caller.
"""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline

from scoreboard.features import FEATURE_COLUMNS

MODELS_DIR = Path(__file__).resolve().parents[2] / "models"


def train(x: pd.DataFrame, y: pd.Series) -> Pipeline:
    """Fit on exactly `FEATURE_COLUMNS` (order enforced)."""
    pipe = Pipeline(
        [
            (
                "gbr",
                HistGradientBoostingRegressor(
                    max_iter=300,
                    learning_rate=0.06,
                    early_stopping=True,
                    random_state=0,
                ),
            )
        ]
    )
    pipe.fit(_ordered(x), y)
    return pipe


def predict(pipe: Pipeline, x: pd.DataFrame) -> np.ndarray:
    return pipe.predict(_ordered(x))


def save(pipe: Pipeline, station_id: str, models_dir: Path | None = None) -> Path:
    path = (models_dir or MODELS_DIR) / f"{station_id}.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, path)
    return path


def load(station_id: str, models_dir: Path | None = None) -> Pipeline:
    return joblib.load((models_dir or MODELS_DIR) / f"{station_id}.joblib")


def _ordered(x: pd.DataFrame) -> pd.DataFrame:
    """Reorder to FEATURE_COLUMNS, failing loudly on a missing one."""
    missing = [c for c in FEATURE_COLUMNS if c not in x.columns]
    if missing:
        raise ValueError(f"missing feature columns: {missing}")
    return x[FEATURE_COLUMNS]
