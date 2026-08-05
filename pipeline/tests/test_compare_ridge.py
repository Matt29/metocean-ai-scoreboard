"""compare_ridge.py: the paired issue-day bootstrap on the ridge-vs-incumbent gap."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("train")
compare_ridge = _load("compare_ridge")


def _eval(errors: np.ndarray) -> dict:
    """A fake `_test_eval` whose model error is exactly `errors`, over 30 issue days.

    The baseline residual must vary, otherwise its debiased error — the
    denominator of every gain here — is identically zero.
    """
    n = len(errors)
    index = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    x = pd.DataFrame({"baseline": np.zeros(n), "lead_h": 0}, index=index)
    obs = pd.Series(1.0 + np.sin(np.arange(n) / 12.0), index=index)
    return {"_test_eval": (obs.to_numpy() + errors, x, obs, np.zeros(n, dtype=int))}


def test_uniformly_better_incumbent_has_a_strictly_positive_interval():
    n = 30 * 24
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.05, n)
    ridge = _eval(0.4 + noise)
    incumbent = _eval(0.2 + noise)
    point, low, high = compare_ridge.paired_gain_delta(incumbent, ridge)
    assert point > 0 and low > 0 and high > low


def test_identical_candidates_give_a_zero_gap_bracketing_zero():
    errors = np.full(30 * 24, 0.3)
    point, low, high = compare_ridge.paired_gain_delta(_eval(errors), _eval(errors))
    assert point == 0.0
    assert low <= 0.0 <= high
