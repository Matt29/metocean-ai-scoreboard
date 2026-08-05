#!/usr/bin/env python
"""Train one post-processing model per station and write the eval report.

Run:  cd pipeline && uv run python scripts/train.py [--test-days 30]

Split
-----
Temporal, **by issue day** — never random. A dataset row is one (issue, lead)
pair, so two rows of the same 06 UTC issue share `last_err` / `mean_err_24h`;
splitting on valid time would leak an issue across train and test. The issue day
is recovered as `valid_time - lead_h`. The last `--test-days` issue days are the
test set.

Wave baseline
-------------
The physical baseline is no longer a single hard-coded model: each wave station
picks, among the 5 Open-Meteo wave models of its raw parquet, the one closest to
its own buoy — **on the train issue days only** (`select_baseline`). Picking it
on the whole window would let the test set choose the yardstick it is then
measured against.

Tide stations
-------------
The learned target is the residual `obs - harmonic`; MAE is nevertheless
reported on the reassembled water level (`harmonic + residual`) so the numbers
are comparable with the wave stations.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from scoreboard import model
from scoreboard.config import Station, load_env, load_stations
from scoreboard.dataset import assemble
from scoreboard.features import FEATURE_COLUMNS, WAVE_FEATURE_COLUMNS, WIND_FEATURE_COLUMNS
from scoreboard.sources.marine import MODEL_COLUMNS
from scoreboard.sources.wind import MULTI_FORCING_COLUMNS, WIND_MODEL_COLUMNS

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "pipeline" / "data_train"
REPORT_PATH = ROOT / "docs" / "model-eval.md"
GATE_PATH = model.MODELS_DIR / "gate.json"
GATE = 0.05  # the model must beat the baseline, hors biais, by >= 5% to go live

# Held-out issue days, per kind. Tide keeps one full-year holdout. Wave and wind
# use 90-day blocks: with >= two years of history, four rolling origins cover
# them across seasons; shorter archives are explicitly labelled as one degraded
# holdout instead of being presented as multi-season evidence.
TEST_DAYS_BY_KIND = {"tide": 365, "wave": 90, "wind": 90}
DEFAULT_TEST_DAYS = 30
ROLLING_FOLDS_BY_KIND = {"wave": 4, "wind": 4}
ROLLING_FOLDS = 4
SEASONAL_HISTORY_DAYS = 730
SEASONAL_STRIDE_DAYS = 90
MIN_FOLD_COVERAGE = 0.8
ISSUE_DAY_PURGE = pd.Timedelta(hours=48)
# The validation slice ranks candidates inside each origin. The last origin's
# validation chooses the production model; the gate itself uses all test folds.
VAL_DAYS_CAP = 120
UNIT = {"wave": "m (Hs)", "tide": "m (water level)", "wind": "m/s (vent 10 m)"}
# Multi-model kinds: (candidate baseline columns, observation column). A `tide`
# station is absent — its baseline is a harmonic fit, not a column to choose.
KIND_MODELS = {"wave": (MODEL_COLUMNS, "hs"), "wind": (WIND_MODEL_COLUMNS, "wind_speed")}
ABLATABLE = sorted(set(FEATURE_COLUMNS) | set(WAVE_FEATURE_COLUMNS) | set(WIND_FEATURE_COLUMNS))


def _test_days(kind: str, override: int | None = None) -> int:
    """Held-out issue days for `kind` — `TEST_DAYS_BY_KIND`, or the CLI override."""
    return override if override is not None else TEST_DAYS_BY_KIND.get(kind, DEFAULT_TEST_DAYS)


def issue_days(x: pd.DataFrame) -> pd.DatetimeIndex:
    """Issue day of each row, recovered as `valid_time - lead_h`."""
    return pd.DatetimeIndex(x.index - pd.to_timedelta(x["lead_h"], unit="h")).normalize()


def split_by_issue_day(
    x: pd.DataFrame, test_days: int, cutoff: pd.Timestamp | None = None
) -> np.ndarray:
    """Boolean mask of the test rows: the last `test_days` issue days."""
    day = issue_days(x)
    cutoff = day.max() - pd.Timedelta(days=test_days) if cutoff is None else cutoff
    return np.asarray(day > cutoff)


def _rolling_cutoffs(days: pd.DatetimeIndex, test_days: int, folds: int) -> list[pd.Timestamp]:
    """Origins distributed across the available issue-day history.

    Each origin has a preceding training period and a following, contiguous
    ``test_days`` holdout.  Multi-season origins are allowed only with two
    calendar years of history; a shorter archive falls back to one honest,
    latest holdout instead of pretending to cover seasons it does not contain.
    """
    days = pd.DatetimeIndex(days).normalize().unique().sort_values()
    if len(days) == 0:
        return []
    if (days.max() - days.min()) < pd.Timedelta(days=SEASONAL_HISTORY_DAYS):
        latest = days.max() - pd.Timedelta(days=test_days)
        minimum_origin = days.min() + pd.Timedelta(days=2 * test_days) + ISSUE_DAY_PURGE
        return [latest] if latest >= minimum_origin else []
    latest = days.max() - pd.Timedelta(days=test_days)
    # Quarterly blocks cover one complete recent seasonal cycle. A longer CLI
    # override widens the stride as well, preserving disjoint test windows.
    # The two-year eligibility threshold leaves at least one full earlier year
    # for the expanding train of the first origin.
    stride_days = max(SEASONAL_STRIDE_DAYS, test_days)
    origins = [
        latest - pd.Timedelta(days=stride_days * offset)
        for offset in reversed(range(folds))
    ]
    minimum_origin = days.min() + pd.Timedelta(days=2 * test_days) + ISSUE_DAY_PURGE
    return [origin for origin in origins if origin >= minimum_origin]


def _origin_split(
    x: pd.DataFrame, origin: pd.Timestamp, test_days: int
) -> tuple[np.ndarray, np.ndarray]:
    """Train/test masks for one origin, grouped by issue day and purged 48 h."""
    day = issue_days(x)
    train = np.asarray(day <= origin - ISSUE_DAY_PURGE)
    test = np.asarray((day > origin) & (day <= origin + pd.Timedelta(days=test_days)))
    return train, test


def rolling_origin_splits(
    x: pd.DataFrame, test_days: int, folds: int = ROLLING_FOLDS
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Chronological, disjoint rolling-origin splits grouped by issue day."""
    day = issue_days(x)
    splits = []
    for cutoff in _rolling_cutoffs(day, test_days, folds):
        train, test = _origin_split(x, cutoff, test_days)
        if test.any() and train.any():
            splits.append((train, test))
    return splits


def _debiased_baseline_error(residual: np.ndarray, fold_ids: np.ndarray) -> np.ndarray:
    """Absolute baseline errors after fitting one constant bias per test fold."""
    error = np.empty_like(residual, dtype=float)
    for fold_id in np.unique(fold_ids):
        selected = fold_ids == fold_id
        error[selected] = np.abs(residual[selected] - residual[selected].mean())
    return error


def _gain_confidence_interval(
    level: np.ndarray,
    x_ev: pd.DataFrame,
    obs_ev: pd.Series,
    fold_ids: np.ndarray,
    draws: int = 2_000,
) -> tuple[float, float]:
    """Deterministic 95% cluster-bootstrap CI for gain, resampled by issue day.

    Leads from one run share inputs and errors, so resampling rows would invent
    precision.  Whole issue days are the independent units.  Summing errors
    within every selected day preserves the existing hourly-MAE estimand.
    """
    residual = obs_ev.to_numpy() - x_ev["baseline"].to_numpy()
    model_error = np.abs(level - obs_ev.to_numpy())
    groups = [
        np.flatnonzero(issue_days(x_ev) == day)
        for day in issue_days(x_ev).unique().sort_values()
    ]
    if len(groups) < 2:
        base = _debiased_baseline_error(residual, fold_ids).sum()
        point = (base - model_error.sum()) / base if base else 0.0
        return float(point), float(point)
    rng = np.random.default_rng(20260804)
    values = []
    for picks in rng.integers(0, len(groups), size=(draws, len(groups))):
        indices = np.concatenate([groups[i] for i in picks])
        # Refit the baseline bias inside each replicate and each fold. Fold
        # baselines may differ, so one global offset is not a valid comparator.
        base = _debiased_baseline_error(residual[indices], fold_ids[indices]).sum()
        if base:
            values.append((base - model_error[indices].sum()) / base)
    if not values:
        return 0.0, 0.0
    low, high = np.quantile(values, (0.025, 0.975))
    return float(low), float(high)


def select_baseline(
    raw: pd.DataFrame,
    train_days: pd.DatetimeIndex,
    model_columns: list[str] = MODEL_COLUMNS,
    obs_column: str = "hs",
) -> str:
    """Model column of lowest MAE vs `raw[obs_column]` over `train_days` only.

    Restricting to the train days is the whole point: the selected model is the
    yardstick every gain below is measured against, so it must be chosen without
    ever looking at the test window.
    """
    day = pd.DatetimeIndex(raw.index).normalize()
    sub = raw[day.isin(train_days)]
    mae = {
        col: float((sub[col] - sub[obs_column]).abs().mean())
        for col in model_columns
        if col in sub.columns
    }
    mae = {c: v for c, v in mae.items() if np.isfinite(v)}
    if not mae:
        raise ValueError("no model column overlaps the observations on the train days")
    return min(mae, key=mae.__getitem__)


def _model_origins(station: Station, test_days: int) -> list[pd.Timestamp] | None:
    """Eligible wave/wind origins from observed raw days, without model choice."""
    _, obs_column = KIND_MODELS[station.kind]
    path = DATA_DIR / f"{station.id}_raw.parquet"
    if not path.exists():
        print(f"  {station.id}: no raw dataset at {path} — skipped")
        return None
    raw = pd.read_parquet(path, columns=[obs_column])
    obs_days = pd.DatetimeIndex(raw.index[raw[obs_column].notna()]).normalize().unique()
    return _rolling_cutoffs(obs_days, test_days, ROLLING_FOLDS_BY_KIND[station.kind])


def _model_data(station: Station, test_days: int, origin: pd.Timestamp) -> tuple | None:
    """(x, obs, is_test, baseline_model) assembled from the raw multi-model parquet.

    One function for `wave` and `wind`: the two differ only by which columns
    carry the candidate baselines and the observation — `KIND_MODELS`. Keeping
    them on one code path is what keeps training and serving in step, which is
    the whole anti-skew argument of `features.py`.
    """
    model_columns, obs_column = KIND_MODELS[station.kind]
    path = DATA_DIR / f"{station.id}_raw.parquet"
    if not path.exists():
        print(f"  {station.id}: no raw dataset at {path} — skipped")
        return None

    raw = pd.read_parquet(path).sort_index()
    # Days are counted on those that carry an observation: a station whose sensor
    # stopped early must still get its 30 *usable* test days.
    obs_days = pd.DatetimeIndex(raw.index[raw[obs_column].notna()]).normalize().unique()
    # This selection is repeated at every origin.  A baseline selected on a
    # later fold would leak future observations into an earlier fold's yardstick.
    baseline_col = select_baseline(
        raw, obs_days[obs_days <= origin - ISSUE_DAY_PURGE], model_columns, obs_column
    )

    x, obs = assemble(
        station,
        raw[[obs_column]],
        raw[[baseline_col]],
        raw[MULTI_FORCING_COLUMNS],
        models=raw[model_columns],
    )
    if x.empty:
        print(f"  {station.id}: assembled 0 row — skipped")
        return None
    prefix = baseline_col.split("_", 1)[0] + "_"
    return (
        x,
        obs.astype(float),
        [_origin_split(x, origin, test_days)],
        baseline_col.removeprefix(prefix),
    )


def _tide_data(station: Station, test_days: int) -> tuple | None:
    """(x, obs, is_test, None) from the pre-assembled tide dataset."""
    path = DATA_DIR / f"{station.id}.parquet"
    if not path.exists():
        print(f"  {station.id}: no dataset at {path} — skipped")
        return None
    df = pd.read_parquet(path)
    x = df[FEATURE_COLUMNS].copy()
    test = split_by_issue_day(x, test_days)
    return x, df["y"].astype(float), [(~test, test)], None


def _reference(x_ev: pd.DataFrame, obs_ev: pd.Series) -> tuple[float, float, float]:
    """(MAE baseline, biais moyen, MAE baseline débiaisée) on an eval window."""
    resid = obs_ev.to_numpy() - x_ev["baseline"].to_numpy()
    mae_base = float(np.abs(resid).mean())
    bias = float(resid.mean())
    return mae_base, bias, float(np.abs(resid - bias).mean())


# Amplitude bands the event diagnostic reports on, beyond the whole window.
# `None` threshold = the top decile of |residual|, whatever it is worth at that
# station; the absolute one is what an operator actually cares about.
EVENT_BANDS = (("décile sup.", None), ("|résidu| > 30 cm", 0.30))


def _event_scores(level: np.ndarray, x_ev: pd.DataFrame, obs_ev: pd.Series) -> list[dict]:
    """Skill restricted to the hours where there is something to predict.

    A MAE over every hour is dominated by calm ones, where the physical baseline
    is already near-optimal and every candidate ties. That average answers "is
    the model better on an ordinary day", which nobody asks; the question is
    whether it holds up during the event. Reported as a **diagnostic only** —
    the gate stays on the whole window, because narrowing the metric to the
    hours a model does best on is exactly the move this project refuses.

    The debiasing uses the **whole-window** bias, never one recomputed on the
    band: a per-storm offset is not something the baseline could know in
    advance, and granting it one would flatter the competitor into a straw man
    in the opposite direction.
    """
    resid = obs_ev.to_numpy() - x_ev["baseline"].to_numpy()
    abs_resid = np.abs(resid)
    bias = float(resid.mean())
    err_model = np.abs(level - obs_ev.to_numpy())

    out = []
    for label, threshold in EVENT_BANDS:
        cut = np.quantile(abs_resid, 0.9) if threshold is None else threshold
        mask = abs_resid >= cut
        if mask.sum() < 24:  # less than a day of such hours: not worth a number
            continue
        mae_debiased = float(np.abs(resid[mask] - bias).mean())
        mae_model = float(err_model[mask].mean())
        out.append({
            "label": label,
            "n": int(mask.sum()),
            "mae_base": float(abs_resid[mask].mean()),
            "mae_debiased": mae_debiased,
            "mae_model": mae_model,
            "gain_debiased": (mae_debiased - mae_model) / mae_debiased if mae_debiased else 0.0,
        })
    return out


def _levels(est, x_ev: pd.DataFrame, kind: str) -> np.ndarray:
    """The candidate's prediction on the observation's own scale.

    A `tide` model learns the residual, so its output only becomes a water level
    once the harmonic baseline is added back; every other kind predicts the value
    directly. One place decides that, because two places drifting apart would
    silently compare a residual against a level.
    """
    pred = model.predict(est, x_ev)
    return x_ev["baseline"].to_numpy() + pred if kind == "tide" else pred


def _score(level: np.ndarray, x_ev: pd.DataFrame, obs_ev: pd.Series) -> dict:
    """MAE and both gains of one fitted candidate on one eval window."""
    mae_model = float(np.abs(level - obs_ev.to_numpy()).mean())
    mae_base, _, mae_debiased = _reference(x_ev, obs_ev)
    return {
        "mae_model": mae_model,
        "gain": (mae_base - mae_model) / mae_base if mae_base else 0.0,
        # The honest headline: gain over the baseline once its offset is gone.
        "gain_debiased": (mae_debiased - mae_model) / mae_debiased if mae_debiased else 0.0,
    }


def evaluate(
    station: Station,
    test_days: int,
    ablate: tuple[str, ...] = (),
    model_names: tuple[str, ...] = model.MODEL_NAMES,
) -> dict | None:
    """Pick candidates inside TRAIN, then score only on sealed temporal tests.

    Each rolling origin owns an earlier train/validation pair and one untouched
    test block. Choosing the candidate on a test block would make the published
    gain a max over several draws — the same leak ``select_baseline`` avoids.
    """
    if station.kind in KIND_MODELS:
        origins = _model_origins(station, test_days)
        loaded_folds = [
            _model_data(station, test_days, origin) for origin in origins or []
        ]
    else:
        loaded_folds = [_tide_data(station, test_days)]
    loaded_folds = [loaded for loaded in loaded_folds if loaded is not None]
    if not loaded_folds:
        return None

    fold_levels, fold_x, fold_obs = [], [], []
    fold_ids = []
    fold_models = []
    fold_baselines = []
    val_scores: dict = {}
    best = model_names[0]
    final_x = final_target = None
    baseline_model = None
    # Every origin repeats the exact same nested protocol: candidate selection
    # happens on validation inside that origin's train period, then only that
    # winner touches the following test block.
    for x, obs, test_masks, fold_baseline in loaded_folds:
        if ablate:
            x = x.copy()
            x[[c for c in ablate if c in x.columns]] = 0.0
        target = obs - x["baseline"] if station.kind == "tide" else obs
        baseline_model = fold_baseline
        for train_mask, is_test in test_masks:
            if is_test.all() or not is_test.any() or not train_mask.any():
                continue
            if station.kind in KIND_MODELS:
                observed_test_days = len(issue_days(x)[is_test].unique())
                if observed_test_days < int(np.ceil(test_days * MIN_FOLD_COVERAGE)):
                    continue
            x_train, target_train, obs_train = x[train_mask], target[train_mask], obs[train_mask]
            current_val_scores = {}
            if len(model_names) > 1:
                val_days = min(test_days, VAL_DAYS_CAP)
                is_val = split_by_issue_day(x_train, val_days)
                if is_val.all() or not is_val.any():
                    continue
                for name in model_names:
                    m = model.train(x_train[~is_val], target_train[~is_val], name=name)
                    current_val_scores[name] = _score(
                        _levels(m, x_train[is_val], station.kind), x_train[is_val], obs_train[is_val]
                    )
            current_best = (
                max(current_val_scores, key=lambda n: current_val_scores[n]["gain_debiased"])
                if current_val_scores
                else model_names[0]
            )
            candidate = model.train(x_train, target_train, name=current_best)
            x_test, obs_test = x[is_test], obs[is_test]
            level_test = _levels(candidate, x_test, station.kind)
            fold_levels.append(level_test)
            fold_x.append(x_test)
            fold_obs.append(obs_test)
            fold_ids.append(np.full(len(x_test), len(fold_ids), dtype=int))
            fold_models.append(current_best)
            fold_baselines.append(fold_baseline)
            # Keep the most recent origin's validation table for the report and use
            # its selected model for the production refit below. Its test remains
            # excluded from that choice.
            val_scores, best = current_val_scores, current_best
            final_x, final_target = x, target
    if not fold_levels:
        print(f"  {station.id}: no usable rolling origin — skipped")
        return None

    x_test = pd.concat(fold_x)
    obs_test = pd.concat(fold_obs)
    level_test = np.concatenate(fold_levels)
    test_fold_ids = np.concatenate(fold_ids)
    residual = obs_test.to_numpy() - x_test["baseline"].to_numpy()
    mae_base = float(np.abs(residual).mean())
    bias = float(residual.mean())
    mae_debiased = float(_debiased_baseline_error(residual, test_fold_ids).mean())
    mae_model = float(np.abs(level_test - obs_test.to_numpy()).mean())
    scores = {
        "mae_model": mae_model,
        "gain": (mae_base - mae_model) / mae_base if mae_base else 0.0,
        "gain_debiased": (
            (mae_debiased - mae_model) / mae_debiased if mae_debiased else 0.0
        ),
    }
    ci_low, ci_high = _gain_confidence_interval(
        level_test, x_test, obs_test, test_fold_ids
    )
    n_issue_days = int(len(issue_days(x_test).unique()))
    if station.kind in KIND_MODELS:
        protocol = (
            "rolling-origin multi-saisons"
            if len(fold_levels) >= ROLLING_FOLDS_BY_KIND[station.kind]
            else "holdout dégradé"
        )
    else:
        protocol = "holdout annuel"
    evaluation_ready = station.kind not in KIND_MODELS or protocol == "rolling-origin multi-saisons"
    # Once the reported heldouts are sealed, baseline selection and training may
    # use all available rows for the artefact served in production.  This is
    # intentionally after (and separate from) every reported fold.
    if station.kind in KIND_MODELS:
        production = _model_data(
            station, test_days, pd.Timestamp("2100-01-01", tz="UTC")
        )
        assert production is not None
        final_x, production_obs, _, baseline_model = production
        if ablate:
            final_x = final_x.copy()
            final_x[[c for c in ablate if c in final_x.columns]] = 0.0
        final_target = (
            production_obs
            if station.kind != "tide"
            else production_obs - final_x["baseline"]
        )
    final = model.train(final_x, final_target, name=best)
    row = {
        "events": _event_scores(level_test, x_test, obs_test),
        "test_days": test_days,
        "station": station.id,
        "kind": station.kind,
        "baseline_model": baseline_model,
        "n_train": int(len(final_x)),
        "n_test": int(len(x_test)),
        "n_val": int(is_val.sum()) if val_scores else 0,
        "n_folds": len(fold_levels),
        "fold_models": fold_models,
        "fold_baselines": fold_baselines,
        "n_issue_days": n_issue_days,
        "evaluation_protocol": protocol,
        "evaluation_ready": evaluation_ready,
        "gain_debiased_ci95_low": ci_low,
        "gain_debiased_ci95_high": ci_high,
        "ci_unit": "issue_day",
        "mae_base": mae_base,
        "bias": bias,
        "mae_debiased": mae_debiased,
        "ml_model": best,
        "val_scores": val_scores,
        **scores,
        # Gate on the gain hors biais (spec critère 3): a station whose displayed
        # gain is mostly a constant offset must not pass on that alone.
        # A positive lower bound is deliberately required even in degraded
        # mode: short history is labelled as such, never compensated by a
        # point estimate with indistinguishable-from-zero skill.
        "pass": evaluation_ready and scores["gain_debiased"] >= GATE and ci_low > 0.0,
        # "weak": the model brings nothing a constant offset would not. Now that
        # `pass` itself requires beating the debiased baseline by >= GATE, a
        # passing station can no longer be weak — the flag is kept as-is (still
        # computed, still in gate.json) for downstream compatibility.
        "weak": scores["mae_model"] >= mae_debiased,
    }
    print(
        f"  {station.id}: train {row['n_train']} / test {row['n_test']} rows "
        f"({row['n_folds']} origin(s)) | "
        f"baseline {baseline_model or station.baseline} | "
        f"MAE base {mae_base:.3f} -> {best} {row['mae_model']:.3f} ({row['gain']:+.1%}) | "
        f"{_verdict(row)} -> {'not released (ablation)' if ablate else 'ready for release'}"
    )
    for name, s in val_scores.items():
        chosen = " <- publié" if name == best else ""
        print(
            f"      [validation] {name:14} MAE {s['mae_model']:.4f}  "
            f"hors biais {s['gain_debiased']:+7.1%}{chosen}"
        )
    # Evaluation deliberately has no filesystem side effect. `main` stages and
    # releases every successful station only after this phase completes for all
    # requested stations, preventing a later evaluation error from replacing an
    # earlier station's live artefact.
    row["_estimator"] = final
    # Test-fold internals, for callers comparing two candidates on the same rows
    # (`scripts/compare_ridge.py`). Underscored: not part of the report or gate.
    row["_test_eval"] = (level_test, x_test, obs_test, test_fold_ids)
    return row


def evaluate_all(
    stations: list[Station],
    test_days_override: int | None,
    ablate: tuple[str, ...],
    model_names: tuple[str, ...],
) -> list[dict]:
    """Evaluate all requested stations before any artefact can be published."""
    rows = []
    for station in stations:
        row = evaluate(station, _test_days(station.kind, test_days_override), ablate, model_names)
        if row is not None:
            rows.append(row)
    return rows


def release(rows: list[dict], gate: dict) -> None:
    """Publish all evaluated models and ``gate.json`` as one transaction.

    Staging happens only after the global evaluation phase. The live models and
    gate are snapshotted before promotion; an exception during any per-file
    replacement rolls every destination back. This does not promise recovery
    from process termination between replacements.
    """
    model.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".train-stage-", dir=model.MODELS_DIR.parent) as tmp:
        staging_dir = Path(tmp)
        replacements = [
            (
                model.stage(
                    row["_estimator"],
                    row["station"],
                    staging_dir,
                    baseline_model=row["baseline_model"],
                ),
                model.MODELS_DIR / f"{row['station']}.joblib",
            )
            for row in rows
        ]
        staged_gate = staging_dir / "gate.json"
        staged_gate.write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n")
        replacements.append((staged_gate, GATE_PATH))
        model.promote_transaction(replacements, staging_dir / "backups")


def _verdict(r: dict) -> str:
    """`PASS*` = au-dessus du gate mais sans battre un simple débiaisage."""
    if not r["pass"]:
        return "FAIL"
    return "PASS*" if r["weak"] else "PASS"


def _movement(r: dict, previous: dict) -> str:
    """Une ligne par station : où on en est, et ce que CE run a bougé.

    Le delta se lit contre `gate.json` d'AVANT l'écriture — c'est la seule
    lecture qui répond à « mon changement a-t-il servi à quelque chose ».
    """
    before = previous.get(r["station"], {}).get("gain_debiased")
    delta = "    nouveau" if before is None else f"{r['gain_debiased'] - before:+9.1%}"
    return f"  {r['station']:16} {r['gain_debiased']:+7.1%} hors biais {delta}  {_verdict(r)}"


def _failure_notes(rows: list[dict], gate_failed: list[str]) -> list[str]:
    """Réserve 5, entièrement dérivée des chiffres — aucune station codée en dur."""
    failing = [r for r in rows if not r["pass"]]
    stale = [s for s in gate_failed if s not in {r["station"] for r in rows}]
    stale_note = (
        [
            f"   Hors de ce run, `gate.json` garde sous le gate : {', '.join(stale)} —",
            "   station(s) non ré-entraînée(s) ici, verdict inchangé.",
            "",
        ]
        if stale
        else []
    )
    if not failing:
        return [
            "5. **Aucune station ré-entraînée n'est sous le gate sur cette fenêtre de",
            "   test.**",
            "",
            *stale_note,
        ]
    notes = [
        "5. **Stations sous le gate — à ne pas publier en l'état.** Le modèle n'y",
        f"   atteint pas les +{GATE:.0%} exigés : il ne trouve pas de signal exploitable",
        "   dans les features actuelles. Le forçage vent 10 m (`wind_u10`/`wind_v10`)",
        "   en fait partie depuis Task 7B — il a payé sur les stations de houle exposée",
        "   mais **pas** sur celles ci-dessous. La pression au niveau de la mer, elle,",
        "   n'est servie qu'aux stations `tide` (voir « Pistes testées et écartées ») :",
        "   ce n'est donc pas un levier disponible ici. L'explication est ailleurs :",
        "   historique d'entraînement trop court, forçage local mal représenté par la",
        "   maille du modèle atmosphérique, ou grandeur encore absente. À trancher",
        "   station par station, mesure à l'appui — `train.py --ablate <colonnes>` chiffre",
        "   ce que chaque feature apporte réellement (p. ex.",
        "   `--ablate wind_u10,wind_v10`).",
        "",
    ]
    notes += [
        f"   * `{r['station']}` ({r['kind']}) : {r['n_train']} lignes de train, MAE "
        f"baseline {r['mae_base']:.3f} → modèle {r['mae_model']:.3f} "
        f"({r['gain']:+.1%} affiché, {r['gain_debiased']:+.1%} hors biais)"
        for r in failing
    ]
    return notes + [""] + stale_note


def _rejected_leads() -> list[str]:
    """Résultat négatif figé (Task 7C) — écrit pour qu'on ne le re-tente pas à l'aveugle.

    Chiffres non recalculés à chaque run : ils décrivent une expérience datée, sur
    une fenêtre datée. Le code de mesure, lui, est toujours là (`--ablate`).
    """
    return [
        "",
        "## Pistes testées et écartées",
        "",
        "* **Pression au niveau de la mer** (`pressure_msl` Open-Meteo, servie dans la",
        "  même requête que le vent, ajoutée comme anomalie à 1013,25 hPa). Motivation :",
        "  le baromètre inverse (~1 cm de niveau par hPa) est le premier moteur de la",
        "  surcote, donc du résidu à prédire sur les stations `tide`. **Mesurée le",
        "  2026-08-03 par ablation à fenêtre identique, elle dégrade 5 stations sur 6 et",
        "  a été retirée.** Δ de gain hors biais dus à la seule pression :",
        "",
        "  | station | kind | Δ pression |",
        "  |---|---|---|",
        "  | pierres-noires | wave | −2,0 pts |",
        "  | belle-ile | wave | −1,0 pt |",
        "  | anglet | wave | −2,4 pts |",
        "  | cherbourg | wave | −5,1 pts |",
        "  | brest | tide | −2,0 pts |",
        "  | saint-malo | tide | **+4,8 pts** (mais reste sous le gate) |",
        "",
        "  Seule `saint-malo` en profitait, `anglet` tombait sous le gate à cause",
        "  d'elle. Lecture d'alors : sur un historique court, une colonne sans effet",
        "  direct sur les stations `wave` ajoute surtout de la variance.",
        "",
        "  **Verdict rouvert le 2026-08-04, et inversé pour les `tide` seulement.**",
        "  Les deux mesures `tide` ci-dessus comparaient à la baseline harmonique de",
        "  90 jours, dont la constituante annuelle non résolue laissait une dérive",
        "  saisonnière dans le résidu. Pression et dérive sont toutes deux basse",
        "  fréquence : l'ablation ne pouvait pas les séparer, et le verdict a donc été",
        "  pris dans le seul régime où il était ininterprétable. Re-mesurée sur la",
        "  baseline à 730 jours, la pression rapporte **+17 points** sur `brest`.",
        "  Elle est servie aux `tide` via `wind.TIDE_FORCING_COLUMNS`, et à elles",
        "  seules — une houle n'a pas de réponse baromètre inverse. L'objection",
        "  « deux chemins de features » ne tenait pas : `features.py` porte déjà",
        "  `WAVE_FEATURE_COLUMNS` et `WIND_FEATURE_COLUMNS`, seule la *fonction*",
        "  `build_features` est unique, et elle le reste.",
        "  Détail : `.superpowers/sdd/2026-07-30-scoreboard-metocean-ia/task-7C-report.md`.",
        "",
    ]


def _gain_cell(row: dict, name: str) -> str:
    """Gain hors biais of one candidate, bold when it is the published one."""
    if name not in row["val_scores"]:
        return "—"  # candidate not run for this station (e.g. `--model`)
    gain = f"{row['val_scores'][name]['gain_debiased']:+.1%}"
    return f"**{gain}**" if name == row["ml_model"] else gain


def _ml_comparison(rows: list[dict]) -> list[str]:
    """Table station × candidate ML model, on the *validation* gain hors biais."""
    names = list(dict.fromkeys(n for r in rows for n in r["val_scores"]))
    if not names:
        return []
    lines = [
        "## Comparaison des modèles ML",
        "",
        "Gain **hors biais sur la dernière fenêtre de VALIDATION** — pas sur le test.",
        "Chaque origine rolling répète cette sélection dans son seul passé, puis son",
        "gagnant touche le test suivant. Le tableau montre la validation la plus",
        "récente, celle qui choisit aussi l'artefact de production ; le score agrégé",
        "n'est jamais utilisé pour choisir entre les candidats. Les valeurs ci-dessous",
        "ne sont donc pas comparables au test — fenêtre différente, modèle entraîné",
        "sur moins de données.",
        "",
        "`ridge` est le **plancher honnête** : un gradient boosting qui ne le bat pas",
        "ne paie pas sa complexité, et c'est un résultat, pas un échec.",
        "",
        "| Station | Baseline physique | " + " | ".join(f"`{n}`" for n in names) + " | Publié |",
        "|---|---|" + "---|" * (len(names) + 1),
    ]
    for r in rows:
        cells = [_gain_cell(r, n) for n in names]
        lines.append(
            f"| {r['station']} | {r['baseline_model'] or r['kind']} | "
            + " | ".join(cells)
            + f" | `{r['ml_model']}` |"
        )
    return lines + [""]


def _event_diagnostic(rows: list[dict]) -> list[str]:
    """Skill on the hours that carry an event — reported, never gated on."""
    if not any(r.get("events") for r in rows):
        return []
    lines = [
        "## Skill sur les événements — diagnostic, pas un critère",
        "",
        "La MAE sur la fenêtre entière est dominée par les heures calmes, où la",
        "baseline physique est déjà quasi optimale et où tous les candidats font",
        "match nul. Elle répond à « le modèle est-il meilleur un jour ordinaire ? »,",
        "que personne ne demande. Le tableau ci-dessous restreint la mesure aux",
        "heures où il y a quelque chose à prévoir.",
        "",
        "**Ce tableau ne décide rien.** Le gate reste sur la fenêtre entière :",
        "restreindre la métrique aux heures où un modèle réussit le mieux serait",
        "exactement le déplacement de poteaux que ce projet refuse. Il est là pour",
        "dire *où* le skill se trouve, pas pour repêcher une station.",
        "",
        "Le débiaisage utilise le biais de la **fenêtre entière**, jamais un biais",
        "recalculé sur la bande : une correction par tempête n'est pas quelque chose",
        "que la baseline pourrait connaître à l'avance.",
        "",
        "| Station | Bande | Heures | MAE baseline | MAE baseline débiaisée | MAE modèle | Gain hors biais |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        for e in r.get("events", []):
            lines.append(
                f"| {r['station']} | {e['label']} | {e['n']} | {e['mae_base']:.3f} | "
                f"{e['mae_debiased']:.3f} | {e['mae_model']:.3f} | "
                f"**{e['gain_debiased']:+.1%}** |"
            )
    lines.append("")
    return lines


def _val_window(rows: list[dict]) -> int:
    """Validation slice actually used, i.e. the test window capped by `VAL_DAYS_CAP`."""
    return min(max((r["test_days"] for r in rows), default=DEFAULT_TEST_DAYS), VAL_DAYS_CAP)


def write_report(
    rows: list[dict],
    gate: dict | None = None,
    skipped: list[str] | None = None,
) -> None:
    """`gate` is the *merged* verdict file — the headline sentences are computed
    on it, not on `rows`, so a station skipped this run cannot be silently
    absolved by a report that only saw the retrained ones."""
    gate = gate or {r["station"]: {"pass": r["pass"], "weak": r["weak"]} for r in rows}
    lines = [
        "# Évaluation des modèles de post-traitement",
        "",
        f"Généré par `pipeline/scripts/train.py` le "
        f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
        "(fenêtres temporelles et protocole détaillés par station ci-dessous).",
        "",
        "Le modèle **post-traite** une prévision physique officielle : il la corrige, il",
        "ne la remplace jamais. Cette baseline n'est plus imposée : pour une station",
        "`wave`, c'est le **meilleur modèle physique** parmi les 5 modèles de vagues",
        "Open-Meteo, et pour une station `wind` le meilleur des 3 modèles de vent",
        "Open-Meteo — dans les deux cas choisi station par station comme le plus proche",
        "de son observation **sur les seuls jours d'émission d'entraînement** (colonne",
        "« Baseline »). Pour une station `tide`, c'est la prédiction harmonique.",
        "",
        "## Résultats par station",
        "",
        "| Station | Type | Baseline production / folds de test | Modèle ML |"
        " Rows train / test | MAE baseline | MAE baseline débiaisée |"
        " MAE modèle | Gain affiché | **Gain hors biais** | IC95% gain | Protocole | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        production_baseline = r["baseline_model"] or "harmonique"
        test_baselines = ", ".join(
            baseline or "harmonique" for baseline in r.get("fold_baselines", [])
        ) or production_baseline
        confidence = (
            f"[{r['gain_debiased_ci95_low']:+.1%}, "
            f"{r['gain_debiased_ci95_high']:+.1%}] ({r['n_issue_days']} jours)"
            if "gain_debiased_ci95_low" in r
            else "—"
        )
        lines.append(
            f"| {r['station']} | {r['kind']} | {production_baseline} / {test_baselines} | "
            f"`{r['ml_model']}` | {r['n_train']} / {r['n_test']} | "
            f"{r['mae_base']:.3f} | {r['mae_debiased']:.3f} | {r['mae_model']:.3f} | "
            f"{r['gain']:+.1%} | **{r['gain_debiased']:+.1%}** | "
            f"{confidence} | {r.get('evaluation_protocol', 'holdout')} "
            f"({r.get('n_folds', 1)}×{r['test_days']}j) | "
            f"{_verdict(r).replace('*', r'\*')} |"
        )
    if skipped:
        lines += [
            "",
            f"**Stations non ré-entraînées sur cette fenêtre : {', '.join(skipped)}** — leur",
            "jeu d'entraînement est absent de `pipeline/data_train/`. Leur artefact et leur",
            "entrée `gate.json` du run précédent sont **conservés tels quels** : ils ne sont",
            "ni supprimés ni rafraîchis, et les chiffres ci-dessus ne les couvrent pas.",
        ]
    lines += [
        "",
        # Dérivée des `kind` réellement présents : une phrase figée finirait par
        # annoncer des mètres pour une station servie en m/s.
        "MAE en "
        + ", ".join(f"{UNIT[k]} pour les stations `{k}`" for k in dict.fromkeys(r["kind"] for r in rows))
        + ". « MAE baseline débiaisée » = MAE de la baseline après retrait",
        "de son biais moyen dans chaque fold de test — c'est le garde-fou de la",
        "réserve 4 : un modèle qui ne bat pas cette colonne n'apporte rien de plus",
        "qu'une constante. Gate de mise en ligne : **+5 % de MAE gagnée hors biais**",
        "(critère 3 de la spec) — le gate porte sur `gain_debiased`, jamais sur le",
        "gain affiché, précisément pour qu'une station ne passe pas sur un simple",
        "débiaisage. Une station FAIL reste entraînée et son artefact reste",
        "versionné, mais elle ne doit pas être publiée telle quelle sur le",
        "scoreboard. Le gain est aussi assorti d'un **IC95 % bootstrap par jour",
        "d'émission** : les leads d'un même run ne sont jamais traités comme des",
        "observations indépendantes. Le gate exige en plus une borne basse strictement",
        "positive ; il ne transforme donc pas un gain ponctuel incertain en PASS.",
        "Une station `wave` ou `wind` en `holdout dégradé` reste FAIL jusqu'à ce que",
        "les quatre folds saisonniers soient suffisamment couverts.",
        "",
        "**`PASS*`** = la station passe le gate mais **ne bat pas sa propre baseline",
        "débiaisée** : son gain affiché est essentiellement une constante, pas du skill.",
        "Ne pas mettre ce chiffre en avant sans la réserve 4. Le gate portant désormais",
        "sur le gain hors biais, une station `PASS` ne peut plus être `weak` — `PASS*`",
        "ne peut donc plus apparaître pour une station retrainée ici ; le mécanisme est",
        "conservé tel quel pour compatibilité avec `gate.json`.",
        "",
        "Ce verdict est aussi émis en donnée dans `pipeline/models/gate.json`",
        "(`{station: {pass, weak, mae_model, mae_baseline, gain, gain_debiased,",
        "gain_debiased_ci95_low, gain_debiased_ci95_high, n_folds, n_issue_days,",
        "evaluation_protocol, evaluation_ready, ci_unit, baseline_model,",
        "fold_baselines}}`) — c'est cette",
        "source, pas ce tableau, que le publisher doit lire.",
        "",
    ]
    # Computed on the merged gate: a station not retrained this run keeps its
    # verdict, and the report must not claim otherwise.
    gate_failed = sorted(s for s, e in gate.items() if not e["pass"])
    lines += [
        f"**Stations sous le gate dans `gate.json` : {', '.join(gate_failed)}** — à ne pas"
        " mettre en ligne en l'état.\n"
        if gate_failed
        else "**Toutes les stations de `gate.json` passent le gate.**\n",
    ]
    lines += _event_diagnostic(rows)
    lines += _ml_comparison(rows)
    lines += [
        "## Protocole",
        "",
        "* **Split temporel par jour d'émission.** Une ligne du dataset est un couple",
        "  (émission 06 UTC, lead 1–48 h) ; les lignes d'une même émission partagent",
        "  `last_err` / `mean_err_24h`. Découper sur le temps de validité ferait donc",
        "  fuir une émission entre train et test. Le jour d'émission est reconstruit",
        "  comme `valid_time - lead_h`. Une émission entière reste toujours du même",
        "  côté d'une frontière. Jamais de split aléatoire.",
        "* **Choix de la baseline (stations `wave`).** Les 5 modèles de vagues",
        "  Open-Meteo sont comparés à la bouée **sur les seuls jours d'émission",
        "  d'entraînement**, et le plus proche devient la baseline de la station — donc",
        "  le dénominateur de tous les gains ci-dessus. La sélection ne voit jamais la",
        "  fenêtre de test : sinon la baseline serait choisie par les données mêmes qui",
        "  servent à la juger, ce qui gonflerait mécaniquement le gain. Elle peut donc",
        "  différer entre folds : le rapport et `gate.json` publient cette liste",
        "  séparément de la baseline re-sélectionnée sur tout l'historique pour la",
        "  production.",
        "* **Choix du modèle ML — sur validation, jamais sur le test.** Les",
        f"  {_val_window(rows)} derniers jours d'émission **du train** forment une fenêtre de",
        "  validation. Les trois candidats (`hgb`, `ridge`, `hgb-per-lead`) y sont",
        "  comparés, à features et baseline identiques ; le meilleur gain hors biais",
        "  gagne, est ré-entraîné sur tout le train, puis évalué **une seule fois** sur",
        "  le test. Choisir le modèle sur le test publierait un maximum sur trois",
        "  tirages faits sur la même fenêtre — la même fuite que la sélection de",
        "  baseline évite, un étage plus haut.",
        "* **Stations `wave` / `wind` — rolling-origin multi-saisons quand l'archive le",
        "  permet.** À partir de 730 jours d'émissions observées, quatre origines",
        "  chronologiques sont espacées d'au moins 90 jours et évaluent chacune 90",
        "  jours par défaut. Un `--test-days` plus long élargit aussi l'espacement,",
        "  afin que les tests ne se chevauchent jamais. Chaque origine",
        "  re-sélectionne baseline et modèle uniquement sur son passé, avec une purge",
        "  de 48 h avant le test. Les fenêtres sont non chevauchantes. Avec moins de",
        "  730 jours, le rapport dit `holdout dégradé`, ne prétend pas couvrir",
        "  plusieurs saisons et ne permet pas un PASS. Chaque fold doit aussi couvrir",
        f"  au moins {MIN_FOLD_COVERAGE:.0%} de ses jours attendus.",
        "* **Incertitude par station.** L'IC95 % du gain hors biais est un bootstrap",
        "  déterministe de jours d'émission entiers : les 48 leads corrélés d'un run",
        "  ne deviennent jamais 48 pseudo-réplications. Le biais de la baseline est",
        "  ré-estimé dans chaque réplication et chaque fold. Le gate exige à la fois",
        f"  un gain ponctuel d'au moins {GATE:.0%} et une borne basse strictement positive.",
        "* **Cible.** Stations `wave` : l'observation Hs. Stations `tide` : le résidu",
        "  `obs - harmonique` ; le niveau publié est réassemblé en",
        "  `harmonique + résidu prédit`, et c'est sur ce niveau reconstitué que la MAE",
        "  ci-dessus est calculée — sinon les chiffres ne seraient pas comparables",
        "  entre stations.",
        "* Tous les horodatages sont en UTC.",
        "",
        "## Réserves importantes sur l'interprétation",
        "",
        "1. **Le skill des stations `wave` est un plafond mesuré sur passé reconstitué,",
        "   pas sur prévision réelle.** Faute d'archive libre des runs de vagues passés,",
        "   la baseline d'entraînement vient de la fenêtre historique de l'API Open-Meteo",
        "   Marine, qui n'est pas le run à +1–48 h qu'aura la production. Le couple",
        "   (baseline, obs) vu à l'entraînement n'est donc pas celui que verra la",
        "   production : ces gains sont un **plafond**, pas une estimation du skill",
        "   opérationnel, et la direction de l'écart n'est pas déterminable a priori. Le",
        "   ré-entraînement sur de vraies prévisions archivées interviendra après ~1 mois",
        "   de runs quotidiens ; ces chiffres seront alors remplacés.",
        "2. **Pour les stations `tide`, le forçage est une prévision ECMWF passée,",
        "   stratifiée par âge de run.** L'entraînement et le service emploient le même",
        "   modèle `ecmwf_ifs025` via l'API Previous Runs : pour chaque émission, les",
        "   features vent 10 m **et pression** viennent du run qui était réellement",
        "   disponible à cette date et à ce lead. Ce choix supprime le skew antérieur",
        "   ERA5/ARPEGE et le faux avantage d'un vent connu après coup. Il reste une",
        "   limite d'archive : ce forçage n'est disponible qu'à partir du 2024-02-05,",
        "   ce qui borne l'historique `tide` utilisable. **La granularité des Previous",
        "   Runs reste journalière** : aux leads courts, le run le plus frais du jour",
        "   peut être postérieur à l'émission de 06 UTC. Le replay est donc plus proche",
        "   de l'opérationnel que ERA5, mais pas une reconstruction causalement exacte",
        "   à l'heure près.",
        "3. **Le gate de +5 % s'applique quand même** au replay stratifié par âge",
        "   de run, avec la limite de granularité journalière explicitée ci-dessus :",
        "   ce n'est plus une analyse parfaite a posteriori, sans être une causalité",
        "   exacte à l'heure près.",
    ]
    # Tout est dérivé : la fermeté de la phrase suit les chiffres, elle ne les précède pas.
    inflated = [r for r in rows if r["gain"] > 0 and r["gain_debiased"] < 0.5 * r["gain"]]
    weak = sorted(s for s, e in gate.items() if e["weak"])
    lines += [
        f"4. **Sur {len(inflated)} des {len(rows)} stations ré-entraînées, plus de la",
        "   moitié du gain",
        "   affiché n'est qu'une correction de biais constant** — chaque baseline dérive",
        "   sur la fenêtre de test, et retirer ce seul offset capte déjà l'essentiel du",
        "   gain. Le chiffre à citer est donc **« Gain hors biais »**, jamais « Gain",
        "   affiché ». Détail par station (biais obs − baseline, puis les deux gains) :",
        "",
    ]
    lines += [
        f"   * `{r['station']}` : biais {r['bias']:+.3f} m — "
        f"gain affiché {r['gain']:+.1%}, **hors biais {r['gain_debiased']:+.1%}**"
        for r in rows
    ]
    lines += [
        "",
        (
            "   Stations dont le gain affiché vaut **au moins le double** de son gain "
            f"hors biais : {', '.join(f'`{r['station']}`' for r in inflated)} — leur "
            "chiffre de tête est d'abord du débiaisage."
            if inflated
            else "   Aucune station ré-entraînée n'a un gain affiché supérieur au double "
            "de son gain hors biais."
        ),
        (
            f"   Stations `weak` dans `gate.json` (le modèle **ne bat pas** ce simple "
            f"débiaisage) : {', '.join(f'`{s}`' for s in weak)} — il n'y apporte rien de "
            "plus qu'une constante, à ne pas présenter comme du skill météo-océanique."
            if weak
            else "   Aucune station de `gate.json` n'est `weak` : toutes battent ce "
            "simple débiaisage."
        ),
    ]
    lines += _failure_notes(rows, gate_failed)
    lines += _rejected_leads()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))


def merge_gate(previous: dict, rows: list[dict], known: set[str]) -> dict:
    """Previous verdicts + this run's, minus the stations no longer configured.

    Merged, never rewritten from scratch: a station skipped this run (no dataset
    on disk) keeps its previous artefact, so it must keep its verdict.
    """
    gate = {k: v for k, v in previous.items() if k in known}
    for r in rows:
        entry = {
            "pass": r["pass"],
            "weak": r["weak"],
            "mae_model": round(r["mae_model"], 4),
            "mae_baseline": round(r["mae_base"], 4),
            "gain": round(r["gain"], 4),
            "gain_debiased": round(r["gain_debiased"], 4),
        }
        if r["baseline_model"]:
            entry["baseline_model"] = r["baseline_model"]
        for key in ("gain_debiased_ci95_low", "gain_debiased_ci95_high"):
            if key in r:
                entry[key] = round(r[key], 4)
        for key in (
            "n_folds",
            "n_issue_days",
            "test_days",
            "evaluation_protocol",
            "evaluation_ready",
            "ci_unit",
            "fold_baselines",
        ):
            if key in r:
                entry[key] = r[key]
        gate[r["station"]] = entry
    return gate


def load_gate(path: Path) -> dict:
    """Read the existing gate and reject malformed publication state early."""
    if not path.exists():
        return {}
    gate = json.loads(path.read_text())
    if not isinstance(gate, dict):
        raise TypeError("gate.json must contain a station-to-verdict object")
    for station, verdict in gate.items():
        if not isinstance(station, str) or not isinstance(verdict, dict):
            raise TypeError("gate.json entries must map station ids to verdict objects")
        if not isinstance(verdict.get("pass"), bool) or not isinstance(verdict.get("weak"), bool):
            raise TypeError(f"gate.json entry {station!r} needs boolean pass and weak fields")
    return gate


def validate_gate_for_release(
    previous: dict, merged: dict, known: set[str], *, partial: bool
) -> None:
    """Ensure the runtime gate is complete before publishing it.

    A targeted ``--station`` run is an update, not an initializer: its previous
    gate must already describe every configured station. A full run may rebuild
    from an empty or incomplete gate, but the merged result must still cover all
    configured stations. In both cases at least one station must remain live.
    """
    if partial and (missing := known - previous.keys()):
        raise ValueError(
            "partial training requires an existing gate entry for every configured "
            f"station; missing {sorted(missing)}"
        )
    if missing := known - merged.keys():
        raise ValueError(f"merged gate is missing configured stations {sorted(missing)}")
    if not any(verdict["pass"] for verdict in merged.values()):
        raise ValueError("merged gate must contain at least one passing station")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--test-days",
        type=int,
        help=f"issue days held out for test (default: per kind, {TEST_DAYS_BY_KIND} "
        f"else {DEFAULT_TEST_DAYS})",
    )
    ap.add_argument(
        "--model",
        choices=model.MODEL_NAMES,
        help="train only this candidate (default: train all three, publish the best "
        "per station on the gain hors biais)",
    )
    ap.add_argument(
        "--station",
        default="",
        metavar="IDS",
        help="comma-separated station ids to train (default: all). Les autres gardent "
        "leur artefact et leur verdict `gate.json` — `merge_gate` les préserve déjà.",
    )
    ap.add_argument(
        "--ablate",
        default="",
        metavar="COLS",
        help="comma-separated feature columns to zero — measures what they actually buy "
        f"(e.g. 'wind_u10,wind_v10'). Choices: {','.join(ABLATABLE)}",
    )
    args = ap.parse_args()

    ablate = tuple(c.strip() for c in args.ablate.split(",") if c.strip())
    if unknown := [c for c in ablate if c not in ABLATABLE]:
        ap.error(f"unknown feature column(s) {unknown} — pick from {ABLATABLE}")
    load_env()

    model_names = (args.model,) if args.model else model.MODEL_NAMES
    print(f"Training {', '.join(model_names)}:")
    if ablate:
        print(f"  ABLATION: {', '.join(ablate)} zeroed — artefacts and report NOT written")
    # Deux listes, jamais une seule : `configured` est ce que le dépôt déclare
    # (c'est lui qui dit à `merge_gate` quels verdicts restent légitimes), et
    # `stations` est ce que CE run entraîne. Les confondre ferait supprimer de
    # `gate.json` le verdict de toute station non entraînée ici — c'est-à-dire
    # dépublier une station en marge d'un entraînement ciblé.
    configured = load_stations()
    stations = configured
    if only := {s.strip() for s in args.station.split(",") if s.strip()}:
        if unknown := only - {s.id for s in configured}:
            ap.error(f"unknown station id(s) {sorted(unknown)}")
        stations = [s for s in configured if s.id in only]
    rows = evaluate_all(stations, args.test_days, ablate, model_names)
    if not rows:
        print("nothing trained")
        return 1

    if ablate:
        print(f"\nablation ({', '.join(ablate)} = 0), gain hors biais:")
        for r in rows:
            print(f"  {r['station']:16} {r['gain_debiased']:+8.1%}  MAE {r['mae_model']:.4f}")
        return 0

    # Validate and merge publication state before replacing any live artefact:
    # a corrupt gate must abort this run without releasing evaluated models.
    previous = load_gate(GATE_PATH)
    gate = merge_gate(previous, rows, {s.id for s in configured})
    validate_gate_for_release(
        previous,
        gate,
        {s.id for s in configured},
        partial=bool(only),
    )

    # No artefact has been touched before here: all stations and the current
    # publication state validated above. Stage the complete release now.
    release(rows, gate)

    skipped = [s.id for s in configured if s.id not in {r["station"] for r in rows}]
    write_report(rows, gate, skipped)
    failed = [r["station"] for r in rows if not r["pass"]]

    print("\nverdict (gain hors biais, delta vs run précédent):")
    for r in rows:
        print(_movement(r, previous))

    print(f"\nreport -> {REPORT_PATH}")
    print(f"gate: {len(rows) - len(failed)}/{len(rows)} PASS" + (f", FAIL: {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
