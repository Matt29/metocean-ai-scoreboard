"""Diagnostic exploratoire du « plafond propre à saint-malo » (réserve ouverte).

Caractérise, ne résout pas. Quatre questions, chiffrées :

1. Où vit l'erreur du modèle publié ? Stratification par phase de marée, avec
   une définition opérationnelle du secteur « flot début ». Contraste brest.
2. Le résidu semi-diurne est-il non stationnaire ? Décomposition cycle par
   cycle, puis stratification saison / vive-eau-morte-eau.
3. Plafond = modèle ou baseline ? Erreur de la baseline harmonique seule,
   même stratification, plus deux tests de mauvais ajustement (décalage
   temporel, gain d'amplitude).
4. Reste-t-il un signal exploitable ? Cohérence d'un cycle au suivant,
   oracles (parfait / causal) et plancher de bruit d'observation.

SUR QUELLES DONNÉES ÇA TOURNE
-----------------------------
* Q1 (erreur du **modèle**) : le backtest de `train.py` est rejoué à
  l'identique, candidat ML **figé a priori** à `hgb-per-lead` (celui que la
  validation a déjà publié). Aucun candidat, aucune feature, aucun
  hyperparamètre n'est choisi sur le test : le test n'est que *décrit*. Un
  second pli (« antérieur », origine à J-730) est rejoué comme contrôle, il
  ne touche jamais le pli scellé.
* Q2/Q3/Q4 (résidu de **baseline**) : série horaire complète du dataset
  (train + test). C'est une statistique descriptive d'une baseline physique
  causale (`harmonic.causal_predict`, refit 30 j) — aucun modèle n'y est
  ajusté, donc rien à sceller.

Rejouer :
    cd pipeline && .venv/bin/python scripts/diag_saint_malo_ceiling.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from scoreboard import model
from scoreboard.features import FEATURE_COLUMNS
from train import ISSUE_DAY_PURGE, issue_days

DATA_DIR = ROOT / "data_train"
STATIONS = ("saint-malo", "brest")
ML_MODEL = "hgb-per-lead"  # figé : le candidat déjà publié, jamais choisi ici
TEST_DAYS = 365
DRAWS = 2_000
SEED = 20260804  # même graine déterministe que `train._gain_confidence_interval`

T_M2 = 12.4206  # h, période de l'onde M2

# 8 secteurs de 45°, l'angle croît avec le temps depuis la pleine mer.
SECTORS = [
    (0, "PM → jusant"),
    (45, "jusant plein"),
    (90, "jusant fin"),
    (135, "BM approche"),
    (180, "flot début"),  # <- le secteur de la réserve
    (225, "flot plein"),
    (270, "flot fin"),
    (315, "PM approche"),
]
SECTOR_LABELS = [label for _, label in SECTORS]
EARLY_FLOOD = "flot début"


# --------------------------------------------------------------------------
# Phase de marée : définition opérationnelle, reproductible
# --------------------------------------------------------------------------
def tide_phase(baseline: pd.Series) -> pd.DataFrame:
    """Phase M2 locale, marnage local et secteur, depuis la seule baseline.

    Normalisation locale sur une fenêtre centrée de 13 h (~une période M2, donc
    elle contient toujours une PM et une BM) : `mid = (max+min)/2`,
    `amp = (max-min)/2`. On pose alors

        h^ = (h - mid) / amp                (≈ cos θ)
        r^ = (dh/dt) / (2π·amp / T_M2)      (≈ -sin θ)
        θ  = atan2(-r^, h^)  mod 360°

    θ = 0° pleine mer, 90° mi-jusant, 180° basse mer, 270° mi-flot. θ croît
    avec le temps. **« flot début » ≡ θ ∈ [180°, 225°)**, soit le premier
    huitième de cycle après la basse mer.
    """
    b = baseline.astype(float)
    win = 13
    hi = b.rolling(win, center=True, min_periods=win).max()
    lo = b.rolling(win, center=True, min_periods=win).min()
    mid, amp = (hi + lo) / 2.0, (hi - lo) / 2.0
    dh = (b.shift(-1) - b.shift(1)) / 2.0  # m/h, centré, grille horaire
    h_hat = ((b - mid) / amp).clip(-1.0, 1.0)
    r_hat = dh / (2 * np.pi * amp / T_M2)
    theta = np.degrees(np.arctan2(-r_hat, h_hat)) % 360.0
    edges = [e for e, _ in SECTORS] + [360.0]
    sector = pd.cut(theta, bins=edges, right=False, labels=SECTOR_LABELS)
    out = pd.DataFrame(
        {"theta": theta, "range_m": 2 * amp, "sector": sector}, index=b.index
    )
    # Cycles M2 successifs, numérotés par déroulage de θ.
    ok = out["theta"].notna()
    cyc = pd.Series(np.nan, index=out.index)
    unwrapped = np.unwrap(np.radians(out.loc[ok, "theta"].to_numpy()))
    cyc.loc[ok] = np.floor(unwrapped / (2 * np.pi))
    out["cycle"] = cyc
    return out


# --------------------------------------------------------------------------
# Bootstrap par jour d'émission — même unité et même graine que train.py
# --------------------------------------------------------------------------
def _day_picks(day: pd.DatetimeIndex, draws: int = DRAWS) -> tuple:
    codes, uniques = pd.factorize(pd.DatetimeIndex(day).normalize())
    rng = np.random.default_rng(SEED)
    return codes, len(uniques), rng.integers(0, len(uniques), size=(draws, len(uniques)))


def boot_mean(values: np.ndarray, day, draws: int = DRAWS) -> tuple[float, float]:
    """IC95 % de la moyenne de `values`, ré-échantillonnage par jour (cluster)."""
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return (np.nan, np.nan)
    codes, n, picks = _day_picks(day, draws)
    s = np.bincount(codes, weights=values, minlength=n)
    c = np.bincount(codes, minlength=n).astype(float)
    means = s[picks].sum(1) / c[picks].sum(1)
    return tuple(float(q) for q in np.quantile(means, (0.025, 0.975)))


def boot_ratio(num: np.ndarray, den: np.ndarray, day, draws: int = DRAWS):
    """IC95 % du rapport des deux moyennes (mêmes lignes, même rééchantillon)."""
    num, den = np.asarray(num, float), np.asarray(den, float)
    if len(num) == 0:
        return (np.nan, np.nan)
    codes, n, picks = _day_picks(day, draws)
    sn = np.bincount(codes, weights=num, minlength=n)
    sd = np.bincount(codes, weights=den, minlength=n)
    r = sn[picks].sum(1) / sd[picks].sum(1)
    return tuple(float(q) for q in np.quantile(r, (0.025, 0.975)))


# --------------------------------------------------------------------------
# Q1 — erreur du modèle publié, stratifiée par phase de marée
# --------------------------------------------------------------------------
def backtest(station: str, origin_back_days: int) -> pd.DataFrame:
    """Rejoue un pli du protocole `train.py` avec le candidat ML figé.

    `origin_back_days = 365` : le pli publié (test = les 365 derniers jours
    d'émission). `= 730` : un pli antérieur, disjoint du test scellé.
    """
    df = pd.read_parquet(DATA_DIR / f"{station}.parquet")
    x = df[FEATURE_COLUMNS].copy()
    obs = df["y"].astype(float)
    day = issue_days(x)
    origin = day.max() - pd.Timedelta(days=origin_back_days)
    train_mask = np.asarray(day <= origin - ISSUE_DAY_PURGE)
    test_mask = np.asarray(
        (day > origin) & (day <= origin + pd.Timedelta(days=TEST_DAYS))
    )
    target = obs - x["baseline"]
    est = model.train(x[train_mask], target[train_mask], name=ML_MODEL)
    x_te = x[test_mask]
    level = x_te["baseline"].to_numpy() + model.predict(est, x_te)
    return pd.DataFrame(
        {
            "baseline": x_te["baseline"].to_numpy(),
            "obs": obs[test_mask].to_numpy(),
            "level": level,
            "lead_h": x_te["lead_h"].to_numpy(),
            "issue_day": issue_days(x_te),
        },
        index=x_te.index,
    )


def stratify(ev: pd.DataFrame, phase: pd.DataFrame) -> pd.DataFrame:
    ev = ev.join(phase[["sector", "range_m", "theta"]], how="left")
    resid = ev["obs"] - ev["baseline"]
    bias = resid.mean()  # un biais constant par pli, comme `_debiased_baseline_error`
    ev = ev.assign(
        e_base=resid.abs(),
        e_base_deb=(resid - bias).abs(),
        e_model=(ev["level"] - ev["obs"]).abs(),
    )
    rows = []
    for label in SECTOR_LABELS + ["TOUS"]:
        sub = ev if label == "TOUS" else ev[ev["sector"] == label]
        if sub.empty:
            continue
        d = sub["issue_day"]
        ratio = sub["e_model"].sum() / sub["e_base"].sum()
        lo, hi = boot_ratio(sub["e_model"], sub["e_base"], d)
        gain = 1 - sub["e_model"].sum() / sub["e_base_deb"].sum()
        glo, ghi = boot_ratio(sub["e_model"], sub["e_base_deb"], d)
        rows.append(
            {
                "secteur": label,
                "n": len(sub),
                "mae_base": sub["e_base"].mean(),
                "mae_base_deb": sub["e_base_deb"].mean(),
                "mae_model": sub["e_model"].mean(),
                "mae_model_ic": boot_mean(sub["e_model"], d),
                "ratio": ratio,
                "ratio_ic": (lo, hi),
                "gain_deb": gain,
                "gain_deb_ic": (1 - ghi, 1 - glo),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Q2/Q3/Q4 — structure du résidu de baseline, cycle par cycle
# --------------------------------------------------------------------------
def hourly_residual(station: str) -> pd.DataFrame:
    """Série horaire dédupliquée obs / baseline / phase (train + test)."""
    df = pd.read_parquet(DATA_DIR / f"{station}.parquet")
    g = df.groupby(level=0)
    s = pd.DataFrame({"baseline": g["baseline"].first(), "obs": g["y"].first()})
    s["resid"] = s["obs"] - s["baseline"]
    return s.join(tide_phase(s["baseline"]))


def cycle_decomposition(s: pd.DataFrame, min_points: int = 9) -> pd.DataFrame:
    """Par cycle M2 : composante semi-diurne complexe Z du résidu.

    Moindres carrés de `resid ~ a + b·cos θ + c·sin θ` sur les points du cycle.
    Z = b + i·c : |Z| est l'amplitude semi-diurne du résidu sur ce cycle,
    arg Z sa phase par rapport à la pleine mer.
    """
    d = s.dropna(subset=["theta", "cycle", "resid"])
    out = []
    for cyc, sub in d.groupby("cycle"):
        if len(sub) < min_points:
            continue
        th = np.radians(sub["theta"].to_numpy())
        A = np.column_stack([np.ones(len(th)), np.cos(th), np.sin(th)])
        coef, *_ = np.linalg.lstsq(A, sub["resid"].to_numpy(), rcond=None)
        fit = A @ coef
        out.append(
            {
                "cycle": cyc,
                "t": sub.index[0],
                "mean": coef[0],
                "Z": complex(coef[1], coef[2]),
                "range_m": float(sub["range_m"].mean()),
                "var_resid": float(np.var(sub["resid"].to_numpy())),
                "var_semi": float(np.var(fit - coef[0])),
                "n": len(sub),
            }
        )
    c = pd.DataFrame(out).set_index("cycle")
    c["absZ"] = c["Z"].abs()
    c["month"] = pd.DatetimeIndex(c["t"]).month
    return c


def coherent_fraction(z: pd.Series) -> tuple[float, float, float, float]:
    """(|moyenne Z|, moyenne |Z|, rapport, phase de la moyenne en °)."""
    m = complex(np.mean(z.to_numpy()))
    return abs(m), float(z.abs().mean()), abs(m) / float(z.abs().mean()), float(
        np.degrees(np.angle(m))
    )


def lag1_coherence(c: pd.DataFrame) -> tuple[complex, float]:
    """Corrélation complexe entre cycles consécutifs : ρ = <Z_n Z*_{n-1}>/<|Z|²>."""
    z = c["Z"]
    idx = c.index.to_numpy()
    consecutive = np.isclose(np.diff(idx), 1.0)
    a, b = z.to_numpy()[1:][consecutive], z.to_numpy()[:-1][consecutive]
    rho = np.sum(a * np.conj(b)) / np.sum(np.abs(b) ** 2)
    return complex(rho), int(consecutive.sum())


def noise_floor(resid: pd.Series) -> dict:
    """Deux estimateurs de la variance de bruit blanc (obs + baseline).

    `γ(0) - γ(1)` majore le bruit (il compte aussi la physique à l'échelle
    horaire) ; l'extrapolation quadratique `γ(0) - (4γ(1) - γ(2))/3` la serre.
    """
    r = resid.dropna().to_numpy()
    r = r - r.mean()
    g = [float(np.mean(r[: len(r) - k] * r[k:])) for k in (0, 1, 2)]
    v_hi = g[0] - g[1]
    v_lo = g[0] - (4 * g[1] - g[2]) / 3.0
    k = np.sqrt(2 / np.pi)  # MAE d'une gaussienne centrée
    return {
        "sigma_hi_cm": 100 * np.sqrt(max(v_hi, 0)),
        "sigma_lo_cm": 100 * np.sqrt(max(v_lo, 0)),
        "mae_floor_hi_cm": 100 * k * np.sqrt(max(v_hi, 0)),
        "mae_floor_lo_cm": 100 * k * np.sqrt(max(v_lo, 0)),
    }


def timing_test(s: pd.DataFrame, minutes: tuple[int, ...]) -> pd.DataFrame:
    """MAE du résidu si la baseline est décalée en temps de δ minutes.

    Un minimum en δ ≠ 0 signerait une baseline mal *calée*, pas un modèle
    insuffisant — le piège déjà payé une fois par ce dépôt.
    """
    b = s["baseline"]
    rows = []
    for dt in minutes:
        shifted = (
            b.reindex(b.index.union(b.index + pd.Timedelta(minutes=dt)))
            .interpolate(method="time")
            .reindex(b.index + pd.Timedelta(minutes=dt))
        )
        shifted.index = b.index
        r = s["obs"] - shifted
        m = s["sector"] == EARLY_FLOOD
        rows.append(
            {
                "delta_min": dt,
                "mae_cm": 100 * r.abs().mean(),
                "mae_flot_debut_cm": 100 * r[m].abs().mean(),
            }
        )
    return pd.DataFrame(rows)


def coherence_decay(c: pd.DataFrame, kmax: int = 8) -> list[float]:
    """|ρ_k| = |<Z_n Z*_{n-k}>| / <|Z|²>, k cycles d'écart (1 cycle ≈ 12,42 h)."""
    z = c["Z"].to_numpy()
    idx = c.index.to_numpy()
    out = []
    for k in range(1, kmax + 1):
        ok = np.isclose(idx[k:] - idx[:-k], float(k))
        a, b = z[k:][ok], z[:-k][ok]
        out.append(float(abs(np.sum(a * np.conj(b)) / np.sum(np.abs(b) ** 2))))
    return out


def oracles(ev: pd.DataFrame, phase: pd.DataFrame, cyc_res: pd.DataFrame,
            rho_k: list[float]) -> dict:
    """Ce que l'erreur du **modèle** perdrait si on lui retirait sa composante
    semi-diurne par cycle.

    * `parfait` : on ajuste et on retire la composante vraie du cycle courant.
      Borne haute absolue, non atteignable — et à lire contre `null`.
    * `null` : le même ajustement à 3 paramètres sur une **permutation** des
      erreurs dans le cycle. C'est la part purement mécanique du gain de
      `parfait` (3 degrés de liberté par cycle), pas du signal.
    * `causal` : la composante du **dernier cycle complet avant t0** (donc
      réellement disponible à l'émission, tous horizons confondus), amortie par
      `|ρ_k|` avec k le nombre de cycles d'écart. C'est la borne haute d'une
      feature causale « phase semi-diurne du résidu observé ».
    """
    e = ev.join(phase[["theta", "cycle"]], how="left").dropna(subset=["theta", "cycle"])
    err = (e["level"] - e["obs"]).to_numpy()
    th = np.radians(e["theta"].to_numpy())
    cos_t, sin_t = np.cos(th), np.sin(th)
    cyc = e["cycle"].to_numpy()
    # Cycle du dernier instant <= t0 : le plus récent que l'émission ait vu.
    t0 = e.index - pd.to_timedelta(e["lead_h"], unit="h")
    cyc_at_t0 = phase["cycle"].reindex(t0, method="ffill").to_numpy()
    z_obs = cyc_res["Z"].to_dict()  # semi-diurne du résidu de baseline observé

    rng = np.random.default_rng(SEED)
    fitted = np.zeros(len(e))
    null = np.zeros(len(e))
    causal = np.zeros(len(e))
    for c0 in np.unique(cyc):
        rows = np.flatnonzero(cyc == c0)
        A = np.column_stack([np.ones(len(rows)), cos_t[rows], sin_t[rows]])
        coef, *_ = np.linalg.lstsq(A, err[rows], rcond=None)
        fitted[rows] = coef[1] * cos_t[rows] + coef[2] * sin_t[rows]
        shuffled = rng.permutation(err[rows])
        cn, *_ = np.linalg.lstsq(A, shuffled, rcond=None)
        null[rows] = cn[1] * cos_t[rows] + cn[2] * sin_t[rows]
    for i in range(len(e)):
        c_past = cyc_at_t0[i]
        # dernier cycle *complet* avant t0
        c_past = c_past - 1 if np.isfinite(c_past) else np.nan
        if not np.isfinite(c_past) or c_past not in z_obs:
            continue
        k = int(round(cyc[i] - c_past))
        if k < 1 or k > len(rho_k):
            continue
        zc = rho_k[k - 1] * z_obs[c_past]
        causal[i] = zc.real * cos_t[i] + zc.imag * sin_t[i]

    base = np.abs(err)
    d = e["issue_day"]
    res = {"mae_model_cm": 100 * base.mean()}
    # `fitted`/`null` sont ajustés sur l'erreur du modèle : on les retranche.
    # `causal` est ajusté sur le résidu de baseline `obs - baseline`, dont
    # l'erreur du modèle porte l'opposé — on l'ajoute.
    for name, f in (("parfait", fitted), ("null", null), ("causal", -causal)):
        m = np.abs(err - f)
        res[f"mae_{name}_cm"] = 100 * m.mean()
        lo, hi = boot_ratio(m, base, d)
        res[f"gain_{name}"] = 1 - m.mean() / base.mean()
        res[f"gain_{name}_ic"] = (1 - hi, 1 - lo)
    # Contrôle décisif : la même correction causale appliquée à la **baseline
    # seule**, et par tranche d'horizon. Si elle ne rend rien même là, c'est que
    # l'information n'existe pas ; si elle rend sur la baseline mais pas après
    # le modèle, c'est que le modèle la tient déjà.
    resid = (e["obs"] - e["baseline"]).to_numpy()
    lead = e["lead_h"].to_numpy()
    res["par_horizon"] = []
    for lo_h, hi_h in ((1, 12), (13, 24), (25, 48), (1, 48)):
        sel = (lead >= lo_h) & (lead <= hi_h)
        row = {"lead": f"{lo_h}-{hi_h}h", "n": int(sel.sum())}
        for tag, before, after in (
            ("baseline", np.abs(resid[sel]), np.abs(resid[sel] - causal[sel])),
            ("modele", base[sel], np.abs(err[sel] + causal[sel])),
        ):
            g_lo, g_hi = boot_ratio(after, before, d[sel])
            row[tag] = (1 - after.mean() / before.mean(), (1 - g_hi, 1 - g_lo))
        res["par_horizon"].append(row)
    return res


# --------------------------------------------------------------------------
# Correction déterministe « phase de marée × marnage », estimée hors test
# --------------------------------------------------------------------------
PHASE_BINS, RANGE_BINS = 16, 5


def phase_range_table(s: pd.DataFrame, end: pd.Timestamp, q_edges: np.ndarray):
    """Correction moyenne du résidu par (bin de phase, quintile de marnage),
    estimée **uniquement** sur les heures strictement avant `end`."""
    d = s.loc[s.index < end].dropna(subset=["theta", "range_m", "resid"])
    key = _cell(d, q_edges)
    return d.groupby(key, observed=True)["resid"].mean()


def _cell(d: pd.DataFrame, q_edges: np.ndarray | None) -> pd.Series:
    p = np.floor(d["theta"].to_numpy() / (360 / PHASE_BINS)).astype(int)
    if q_edges is None:  # variante phase seule, sans marnage
        return pd.Series(p, index=d.index)
    r = np.clip(np.searchsorted(q_edges, d["range_m"].to_numpy(), side="right") - 1,
                0, RANGE_BINS - 1)
    return pd.Series(p * RANGE_BINS + r, index=d.index)


def deterministic_correction(ev: pd.DataFrame, s: pd.DataFrame, with_range: bool) -> dict:
    """Espérance de gain d'une correction purement tidale f(phase, marnage).

    Elle est ajustée sur le passé (les heures antérieures au premier jour
    d'émission du pli de test) et appliquée telle quelle au pli de test, sur la
    baseline seule **et** après le modèle publié. Hors échantillon des deux
    côtés : c'est une estimation d'espérance de gain, pas un ajustement.
    """
    start = ev.index.min() - pd.Timedelta(hours=48)
    past = s.loc[s.index < start, "range_m"].dropna()
    q_edges = (
        np.quantile(past, np.linspace(0, 1, RANGE_BINS + 1))[:-1] if with_range else None
    )
    table = phase_range_table(s, start, q_edges)

    e = ev.join(s[["theta", "range_m"]], how="left").dropna(subset=["theta", "range_m"])
    corr = _cell(e, q_edges).map(table).fillna(0.0).to_numpy()
    resid = (e["obs"] - e["baseline"]).to_numpy()
    err = (e["level"] - e["obs"]).to_numpy()
    d = e["issue_day"]
    out = {"n_cells": int(table.notna().sum()), "n": len(e)}
    for tag, before, after in (
        ("baseline", np.abs(resid), np.abs(resid - corr)),
        ("modele", np.abs(err), np.abs(err + corr)),
    ):
        lo, hi = boot_ratio(after, before, d)
        out[tag] = {
            "avant_cm": 100 * before.mean(),
            "apres_cm": 100 * after.mean(),
            "gain": 1 - after.mean() / before.mean(),
            "ic": (1 - hi, 1 - lo),
        }
    return out


# --------------------------------------------------------------------------
def fmt_ic(ic) -> str:
    return f"[{ic[0]:+.3f} ; {ic[1]:+.3f}]"


def main() -> int:
    print("=" * 78)
    print("DIAGNOSTIC — plafond saint-malo. Exploratoire, sans sélection.")
    print("=" * 78)

    residuals = {st: hourly_residual(st) for st in STATIONS}
    cycles = {st: cycle_decomposition(residuals[st]) for st in STATIONS}

    # ---- Q1 -------------------------------------------------------------
    for back, tag in ((365, "pli PUBLIÉ (test scellé, décrit sans sélection)"),
                      (730, "pli ANTÉRIEUR (contrôle, hors test scellé)")):
        print(f"\n\n### Q1 — erreur du modèle par secteur de marée — {tag}")
        for st in STATIONS:
            ev = backtest(st, back)
            tab = stratify(ev, residuals[st])
            print(f"\n-- {st} — {len(ev)} lignes, "
                  f"{ev['issue_day'].nunique()} jours d'émission")
            print(f"{'secteur':<14}{'n':>6}{'MAEbase':>9}{'MAEdeb':>9}"
                  f"{'MAEmod':>9}  {'IC95 MAEmod':<20}{'ratio':>7}  {'IC95 ratio':<18}"
                  f"{'gain hb':>9}  IC95 gain")
            for _, r in tab.iterrows():
                print(
                    f"{r['secteur']:<14}{r['n']:>6}{100*r['mae_base']:>9.2f}"
                    f"{100*r['mae_base_deb']:>9.2f}{100*r['mae_model']:>9.2f}  "
                    f"{'[{:5.2f} ; {:5.2f}]'.format(100*r['mae_model_ic'][0], 100*r['mae_model_ic'][1]):<20}"
                    f"{r['ratio']:>7.3f}  "
                    f"{'[{:.3f} ; {:.3f}]'.format(*r['ratio_ic']):<18}"
                    f"{100*r['gain_deb']:>8.1f}%  "
                    f"[{100*r['gain_deb_ic'][0]:.1f}% ; {100*r['gain_deb_ic'][1]:.1f}%]"
                )
        if back == 365:
            published = {st: backtest(st, 365) for st in STATIONS}

    # ---- Q2 -------------------------------------------------------------
    print("\n\n### Q2 — non-stationnarité de la composante semi-diurne du résidu")
    print("(série horaire complète, train+test — statistique descriptive)")
    for st in STATIONS:
        c = cycles[st]
        aZ, mZ, ratio, ang = coherent_fraction(c["Z"])
        rho, n_pairs = lag1_coherence(c)
        share = float((c["var_semi"] / c["var_resid"]).replace([np.inf], np.nan).mean())
        print(f"\n-- {st} — {len(c)} cycles M2")
        print(f"   part semi-diurne de la variance du résidu, par cycle : {share:.1%}")
        print(f"   amplitude semi-diurne moyenne <|Z|>      : {100*mZ:.2f} cm")
        print(f"   partie stationnaire |<Z>|                : {100*aZ:.2f} cm "
              f"(phase {ang:+.0f}°)")
        print(f"   rapport |<Z>| / <|Z|>                    : {ratio:.3f}  "
              f"(1 = raie stationnaire, 0 = phase aléatoire)")
        print(f"   cohérence cycle→cycle |ρ|                : {abs(rho):.3f} "
              f"(phase {np.degrees(np.angle(rho)):+.0f}°, {n_pairs} paires)")
        for key, label, bins in (
            ("month", "saison (mois)", None),
            ("range_m", "marnage (quintile, m)", 5),
        ):
            print(f"   — stratification par {label} :")
            if bins:
                c = c.assign(_g=pd.qcut(c[key], bins))
            else:
                c = c.assign(_g=c[key])
            for g, sub in c.groupby("_g", observed=True):
                if len(sub) < 20:
                    continue
                a, m, r, an = coherent_fraction(sub["Z"])
                print(f"       {g!s:<22} n={len(sub):>4}  <|Z|>={100*m:5.2f} cm  "
                      f"|<Z>|={100*a:5.2f} cm  ratio={r:.3f}  phase={an:+7.0f}°")

    # ---- Q3 -------------------------------------------------------------
    print("\n\n### Q3 — modèle ou baseline ? Erreur de la baseline seule")
    for st in STATIONS:
        s = residuals[st].dropna(subset=["sector"])
        print(f"\n-- {st} — biais/MAE du résidu de baseline par secteur "
              f"({len(s)} heures)")
        day = pd.DatetimeIndex(s.index).normalize()
        print(f"{'secteur':<14}{'n':>7}{'biais cm':>10}  {'IC95 biais':<20}"
              f"{'MAE cm':>9}{'marnage m':>11}")
        for label in SECTOR_LABELS + ["TOUS"]:
            sub = s if label == "TOUS" else s[s["sector"] == label]
            d = day if label == "TOUS" else pd.DatetimeIndex(sub.index).normalize()
            lo, hi = boot_mean(sub["resid"], d)
            print(f"{label:<14}{len(sub):>7}{100*sub['resid'].mean():>10.2f}  "
                  f"{f'[{100*lo:+.2f} ; {100*hi:+.2f}]':<20}"
                  f"{100*sub['resid'].abs().mean():>9.2f}"
                  f"{sub['range_m'].mean():>11.2f}")
        # Gain d'amplitude : obs ≈ α·(baseline - <baseline>) + β
        b = s["baseline"] - s["baseline"].mean()
        alpha = float(np.polyfit(b, s["obs"] - s["obs"].mean(), 1)[0])
        print(f"   gain d'amplitude ajusté α (1.000 = baseline juste) : {alpha:.4f}")
        print("   test de calage temporel (décalage δ de la baseline) :")
        t = timing_test(s, (-60, -30, -15, -5, 0, 5, 15, 30, 60))
        print("       " + "  ".join(f"{int(r.delta_min):+4d}min" for r in t.itertuples()))
        print("       " + "  ".join(f"{r.mae_cm:7.2f}" for r in t.itertuples()) + "   (tout)")
        print("       " + "  ".join(f"{r.mae_flot_debut_cm:7.2f}" for r in t.itertuples())
              + "   (flot début)")

    # ---- Q4 -------------------------------------------------------------
    print("\n\n### Q4 — signal exploitable restant, ou bruit ?")
    for st in STATIONS:
        decay = coherence_decay(cycles[st])
        nf = noise_floor(residuals[st]["resid"])
        o = oracles(published[st], residuals[st], cycles[st], decay)
        print(f"\n-- {st}")
        print("   cohérence |ρ_k| du résidu semi-diurne, k cycles (12,42 h) d'écart :")
        print("       k     " + "".join(f"{k:>7}" for k in range(1, len(decay) + 1)))
        print("       |ρ_k| " + "".join(f"{v:>7.3f}" for v in decay))
        print(f"   plancher de bruit (obs+baseline), σ : "
              f"{nf['sigma_lo_cm']:.2f} à {nf['sigma_hi_cm']:.2f} cm "
              f"→ MAE plancher {nf['mae_floor_lo_cm']:.2f} à "
              f"{nf['mae_floor_hi_cm']:.2f} cm")
        print(f"   MAE du modèle publié sur le pli test : {o['mae_model_cm']:.2f} cm")
        for tag, label in (
            ("parfait", "oracle PARFAIT (composante vraie du cycle courant)"),
            ("null", "  dont MÉCANIQUE (permutation, 3 ddl/cycle)"),
            ("causal", "oracle CAUSAL (dernier cycle complet avant t0, amorti)"),
        ):
            print(f"   {label:<55} {o['mae_' + tag + '_cm']:6.2f} cm  "
                  f"gain {100*o['gain_' + tag]:+6.1f}%  "
                  f"IC95 [{100*o['gain_' + tag + '_ic'][0]:+.1f}% ; "
                  f"{100*o['gain_' + tag + '_ic'][1]:+.1f}%]")
        print("   oracle CAUSAL par horizon — sur la baseline seule / après le modèle :")
        for r in o["par_horizon"]:
            b, m = r["baseline"], r["modele"]
            print(f"       {r['lead']:<7} n={r['n']:>6}  "
                  f"baseline {100*b[0]:+6.1f}% [{100*b[1][0]:+.1f} ; {100*b[1][1]:+.1f}]   "
                  f"modèle {100*m[0]:+6.1f}% [{100*m[1][0]:+.1f} ; {100*m[1][1]:+.1f}]")

    print("\n\n### Q5 — espérance de gain d'une correction déterministe de marée")
    print("(ajustée sur le passé du pli test, appliquée hors échantillon)")
    for st in STATIONS:
        print(f"\n-- {st}")
        for with_range, name in ((False, f"phase seule ({PHASE_BINS} bins)"),
                                 (True, f"phase x marnage ({PHASE_BINS}x{RANGE_BINS})")):
            r = deterministic_correction(published[st], residuals[st], with_range)
            print(f"   {name} — {r['n_cells']} cellules, {r['n']} lignes")
            for tag in ("baseline", "modele"):
                v = r[tag]
                print(f"       sur la {tag:<9} : MAE {v['avant_cm']:6.2f} → "
                      f"{v['apres_cm']:6.2f} cm   gain {100*v['gain']:+6.2f}%  "
                      f"IC95 [{100*v['ic'][0]:+.2f}% ; {100*v['ic'][1]:+.2f}%]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
