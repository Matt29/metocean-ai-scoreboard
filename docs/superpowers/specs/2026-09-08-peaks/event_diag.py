"""Replay train.evaluate() on sealed folds, no promotion, with extra peak bands.

Bands added on top of train.EVENT_BANDS (which condition on |obs - baseline|):
  * obs décile sup.      — selection on the observation (biased toward under-forecast, kept for comparison)
  * prévu décile sup.    — selection on the baseline forecast (no selection on obs)
Also prints bias of model and baseline on each band, and the amplitude ratio
std(model - baseline) / std(obs - baseline) i.e. predicted vs observed correction.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "scripts")
import train  # noqa: E402
from scoreboard.config import load_env, load_stations  # noqa: E402

_orig = train._event_scores


def _extra(level, x_ev, obs_ev):
    out = _orig(level, x_ev, obs_ev)
    obs = obs_ev.to_numpy()
    base = x_ev["baseline"].to_numpy()
    resid = obs - base
    bias = float(resid.mean())
    for label, sel in (("obs décile sup.", obs), ("prévu décile sup.", base)):
        mask = sel >= np.quantile(sel, 0.9)
        if mask.sum() < 24:
            continue
        mae_deb = float(np.abs(resid[mask] - bias).mean())
        mae_model = float(np.abs(level[mask] - obs[mask]).mean())
        out.append({
            "label": label, "n": int(mask.sum()),
            "mae_base": float(np.abs(resid[mask]).mean()),
            "mae_debiased": mae_deb, "mae_model": mae_model,
            "gain_debiased": (mae_deb - mae_model) / mae_deb if mae_deb else 0.0,
            "bias_model": float((level[mask] - obs[mask]).mean()),
            "bias_base": float(-resid[mask].mean()),
        })
    out.append({"label": "amplitude", "std_ratio": float(np.std(level - base) / np.std(resid)),
                "corr": float(np.corrcoef(level - base, resid)[0, 1])})
    return out


train._event_scores = _extra

load_env()
wanted = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else None
stations = [s for s in load_stations() if wanted is None or s.id in wanted]
rows = []
for st in stations:
    try:
        r = train.evaluate(st, train._test_days(st.kind))
    except Exception as e:  # keep going, report the failure
        print(f"  {st.id}: FAILED {e!r}")
        continue
    if r:
        rows.append(r)
        print(json.dumps({k: r[k] for k in ("station", "kind", "baseline_model", "ml_model", "n_folds",
                                             "evaluation_protocol", "gain_debiased", "gain_debiased_ci95_low",
                                             "gain_debiased_ci95_high", "events")}, ensure_ascii=False))
        sys.stdout.flush()

print("\nRESULT_JSON " + json.dumps([{k: r[k] for k in ("station", "kind", "baseline_model", "ml_model", "n_folds",
                                                          "evaluation_protocol", "mae_model", "mae_base", "mae_debiased",
                                                          "gain_debiased", "gain_debiased_ci95_low",
                                                          "gain_debiased_ci95_high", "events")} for r in rows],
                                   ensure_ascii=False))
