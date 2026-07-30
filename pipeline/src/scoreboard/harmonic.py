"""Thin wrapper around utide.solve/reconstruct — astronomical tide baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
import utide


@dataclass
class HarmonicModel:
    coef: object  # utide.Bunch, opaque solution container

    def predict(self, times: pd.DatetimeIndex) -> pd.Series:
        recon = utide.reconstruct(times, self.coef, verbose=False)
        return pd.Series(recon.h, index=times)

    def save(self, path: str | Path) -> None:
        joblib.dump(self.coef, path)

    @classmethod
    def load(cls, path: str | Path) -> "HarmonicModel":
        return cls(coef=joblib.load(path))


def fit(obs: pd.Series, lat: float) -> HarmonicModel:
    coef = utide.solve(obs.index, obs.values, lat=lat, verbose=False)
    return HarmonicModel(coef=coef)
