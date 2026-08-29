"""Météo-France DPObs `/bouees` — observations horaires des 9 bouées ancrées.

Pourquoi archiver : l'endpoint ne sert qu'une fenêtre glissante et il n'existe
aucune API d'archive pour ces bouées. Toute heure non capturée est perdue
définitivement — et le premier entraînement Méditerranée (demande produit 4) ne
peut commencer à compter que du jour où ce collecteur tourne.

Une seule requête par run : `id_bouee` omis renvoie les 9 bouées d'un coup.
Les positions (`lat`/`lon`) viennent de la réponse elle-même, jamais en dur.

Rétention **mesurée** le 2026-08-03, pas lue dans la doc (règle du projet : la
dispo se prouve en comptant sur la requête exacte) : `date_debut` à T-96 h
répond 200 avec la grille horaire complète (97 pas distincts) ; T-120 h et
au-delà sont refusés HTTP 400 « Contrôle de date en erreur ». La doc Confluence
annonce 24 h — elle se trompe, dans le sens favorable. `LOOKBACK_HOURS` reste
sous la limite dure mesurée : c'est ce qui rend un run quotidien raté
rattrapable au lieu d'être un trou permanent.

Les valeurs sont archivées **brutes**, sans filtre de plausibilité (contrairement
à `candhis.fetch_wave_obs`) : ce corpus est la vérité terrain d'un futur
entraînement, le filtrage appartient à qui le consomme.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from scoreboard.sources import SourceError, make_session

_URL = "https://public-api.meteofrance.fr/public/DPObs/v1/bouees"
_TIMEOUT = 120
_SOURCE_ID = "mfbuoy"

# Sous la limite dure de 96 h mesurée, avec de la marge pour l'écart d'horloge
# entre notre `now` et celui de la passerelle (le 400 est calculé côté serveur).
LOOKBACK_HOURS = 90

# Colonnes vagues, les seules qui motivent l'archivage ; le reste du payload
# (vent, pression, températures) est conservé tel quel — il est déjà là, et il
# sert la demande « stations de vent ».
WAVE_COLUMNS = ["haut_vag", "per_moy_vag", "dir_vag"]
KEY_COLUMNS = ["geo_id_wmo", "validity_time"]

FRESHNESS_THRESHOLD = pd.Timedelta(hours=3)
HS_COMPLETENESS_THRESHOLD = 0.80
QUALITY_WINDOW = pd.Timedelta(hours=24)


@dataclass(frozen=True)
class BuoyQuality:
    """Complétude de hauteur significative pour une bouée capable d'en fournir."""

    buoy_id: str
    hs_hours: int
    expected_hours: int
    hs_completeness: float


@dataclass(frozen=True)
class QualityReport:
    """État qualité d'une fenêtre d'observations, indépendant de son rendu."""

    checked_at: pd.Timestamp
    latest_timestamp: pd.Timestamp | None
    freshness: pd.Timedelta | None
    is_fresh: bool
    buoys: tuple[BuoyQuality, ...]

    @property
    def failing_buoy_ids(self) -> tuple[str, ...]:
        return tuple(
            buoy.buoy_id for buoy in self.buoys if buoy.hs_completeness < HS_COMPLETENESS_THRESHOLD
        )

    @property
    def has_collective_hs_failure(self) -> bool:
        return bool(self.buoys) and len(self.failing_buoy_ids) == len(self.buoys)

    @property
    def is_healthy(self) -> bool:
        return self.is_fresh and bool(self.buoys) and not self.failing_buoy_ids


def quality_report(
    obs: pd.DataFrame,
    *,
    now: pd.Timestamp,
    wave_ids: Collection[str],
) -> QualityReport:
    """Mesure fraîcheur et complétude Hs sur les 24 dernières heures.

    ``wave_ids`` vient du dernier catalogue connu. Il exclut une bouée telle que
    BOUEE_SARDAIGNE, tout en gardant dans le rapport une bouée wave absente du
    payload courant (complétude 0 %) au lieu de masquer une panne collective.
    """
    checked_at = pd.Timestamp(now)
    if checked_at.tzinfo is None:
        checked_at = checked_at.tz_localize("UTC")
    else:
        checked_at = checked_at.tz_convert("UTC")

    timestamps = pd.to_datetime(obs["validity_time"], utc=True, format="ISO8601")
    latest = timestamps.max() if not timestamps.empty else None
    freshness = checked_at - latest if latest is not None and not pd.isna(latest) else None
    is_fresh = freshness is not None and pd.Timedelta(0) <= freshness <= FRESHNESS_THRESHOLD

    window_start = checked_at - QUALITY_WINDOW
    expected_hours = int(QUALITY_WINDOW / pd.Timedelta(hours=1))
    in_window = obs.loc[(timestamps > window_start) & (timestamps <= checked_at)].copy()
    in_window["_quality_timestamp"] = timestamps.loc[in_window.index]

    buoys = []
    for buoy_id in sorted(str(value) for value in wave_ids):
        rows = in_window.loc[in_window["geo_id_wmo"].astype(str) == buoy_id]
        hs_hours = rows.loc[rows["haut_vag"].notna(), "_quality_timestamp"].nunique()
        buoys.append(
            BuoyQuality(
                buoy_id=buoy_id,
                hs_hours=int(hs_hours),
                expected_hours=expected_hours,
                hs_completeness=min(1.0, hs_hours / expected_hours),
            )
        )
    return QualityReport(
        checked_at=checked_at,
        latest_timestamp=latest,
        freshness=freshness,
        is_fresh=is_fresh,
        buoys=tuple(buoys),
    )


def _format_duration(value: pd.Timedelta | None) -> str:
    if value is None:
        return "inconnue"
    hours = value.total_seconds() / 3600
    return f"{hours:.1f}".rstrip("0").rstrip(".") + "h"


def quality_warnings(report: QualityReport) -> list[str]:
    """Rend les annotations GitHub Actions sans modifier ni lever sur le rapport."""
    prefix = "::warning title=Qualité bouées::"
    warnings = []
    if not report.is_fresh:
        warnings.append(
            f"{prefix}fraîcheur {_format_duration(report.freshness)} "
            f"(seuil {_format_duration(FRESHNESS_THRESHOLD)})"
        )
    if not report.buoys:
        warnings.append(f"{prefix}aucun identifiant de bouée wave connu")
    elif report.has_collective_hs_failure:
        warnings.append(
            f"{prefix}panne collective Hs possible : les {len(report.buoys)} bouées wave "
            f"sont sous le seuil {HS_COMPLETENESS_THRESHOLD:.0%}"
        )
    for buoy in report.buoys:
        if buoy.hs_completeness < HS_COMPLETENESS_THRESHOLD:
            warnings.append(
                f"{prefix}{buoy.buoy_id} Hs {buoy.hs_completeness:.1%} "
                f"({buoy.hs_hours}/{buoy.expected_hours}h, "
                f"seuil {HS_COMPLETENESS_THRESHOLD:.0%})"
            )
    return warnings


def quality_summary(report: QualityReport) -> str:
    """Rend le résumé Markdown destiné à ``GITHUB_STEP_SUMMARY``."""
    latest = (
        report.latest_timestamp.isoformat() if report.latest_timestamp is not None else "aucune"
    )
    lines = [
        "## Qualité des bouées Météo-France",
        "",
        f"Dernière observation : `{latest}` — fraîcheur {_format_duration(report.freshness)} "
        f"({'✅' if report.is_fresh else '⚠️'}).",
        f"Seuil fraîcheur : {_format_duration(FRESHNESS_THRESHOLD)}. "
        f"Seuil Hs : {HS_COMPLETENESS_THRESHOLD:.0%} sur les dernières 24h.",
        "",
        "| Bouée WMO | Hs disponibles | Complétude | État |",
        "|---|---:|---:|:---:|",
    ]
    lines.extend(
        f"| {buoy.buoy_id} | {buoy.hs_hours}/{buoy.expected_hours} | "
        f"{buoy.hs_completeness:.1%} | "
        f"{'✅' if buoy.hs_completeness >= HS_COMPLETENESS_THRESHOLD else '⚠️'} |"
        for buoy in report.buoys
    )
    if not report.buoys:
        lines.append("| — | 0/24 | — | ⚠️ |")
    return "\n".join(lines) + "\n"


def known_wave_ids(
    archived_obs: pd.DataFrame,
    catalog_buoys: Iterable[Mapping[str, object]],
) -> set[str]:
    """Union du catalogue et de toute preuve Hs historique dans l'archive."""
    catalog_ids = {str(buoy["id"]) for buoy in catalog_buoys if buoy.get("wave") is True}
    historical_ids = set(
        archived_obs.loc[archived_obs["haut_vag"].notna(), "geo_id_wmo"].astype(str)
    )
    return catalog_ids | historical_ids


def _utc_bound(value: date | datetime | pd.Timestamp, *, end: bool = False) -> pd.Timestamp:
    """Normalise une borne; une ``date`` de fin couvre toute sa journée UTC."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    if end and isinstance(value, date) and not isinstance(value, (datetime, pd.Timestamp)):
        timestamp += pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    return timestamp


def read_archived_buoy_obs(
    archive_dir: Path,
    source_id: str,
    start: date | datetime | pd.Timestamp,
    end: date | datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Lit Hs brut d'une bouée dans les partitions Parquet, bornes inclusives.

    Une borne ``date`` désigne la journée UTC entière; une borne horodatée est
    comparée exactement. Les valeurs nulles et physiquement suspectes restent
    intactes : leur traitement appartient au consommateur du dataset.
    """
    columns = ["geo_id_wmo", "validity_time", "haut_vag"]
    frames = [
        pd.read_parquet(path, columns=columns)
        for path in sorted(Path(archive_dir).glob("*.parquet"))
    ]
    if not frames:
        return pd.DataFrame(
            {"hs": pd.Series(dtype="float64")},
            index=pd.DatetimeIndex([], tz="UTC", name="time"),
        )

    obs = pd.concat(frames, ignore_index=True)
    timestamps = pd.to_datetime(obs["validity_time"], utc=True, format="ISO8601")
    lower = _utc_bound(start)
    upper = _utc_bound(end, end=True)
    selected = obs.loc[
        (obs["geo_id_wmo"].astype(str) == str(source_id))
        & timestamps.between(lower, upper, inclusive="both"),
        ["haut_vag"],
    ].rename(columns={"haut_vag": "hs"})
    selected.index = pd.DatetimeIndex(timestamps.loc[selected.index], name="time")
    return selected.sort_index()


def quality_main(argv: list[str] | None = None) -> int:
    """Émet les alertes qualité de l'archive sans jamais en modifier le contenu."""
    parser = argparse.ArgumentParser(prog="python -m scoreboard.sources.mfbuoy")
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--now", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    columns = ["geo_id_wmo", "validity_time", "haut_vag"]
    frames = [
        pd.read_parquet(path, columns=columns)
        for path in sorted(args.archive_dir.glob("*.parquet"))
    ]
    obs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=columns)
    catalog = json.loads(args.catalog.read_text())
    wave_ids = known_wave_ids(obs, catalog.get("buoys", []))
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp.now(tz="UTC")
    report = quality_report(obs, now=now, wave_ids=wave_ids)
    for warning in quality_warnings(report):
        print(warning)
    summary = quality_summary(report)
    print(summary, end="")
    if args.summary:
        with args.summary.open("a") as summary_file:
            summary_file.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(quality_main())


def fetch_buoy_obs(
    now: pd.Timestamp | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Observations horaires des 9 bouées sur [now - lookback_hours, now].

    `validity_time` est renvoyé en chaîne ISO (comme `archive.write_day`) : c'est
    la moitié de la clé de déduplication, une chaîne ne dérive pas de dtype au
    passage par parquet.
    """
    api_key = os.environ.get("METEOFRANCE_API_KEY")
    if not api_key:
        raise SourceError(
            _SOURCE_ID, "METEOFRANCE_API_KEY absente de l'environnement (.env non chargé ?)"
        )

    now = (now or pd.Timestamp.now(tz="UTC")).floor("h")
    start = now - timedelta(hours=LOOKBACK_HOURS)

    session = session or make_session()
    try:
        resp = session.get(
            _URL,
            params={
                "date_debut": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "date_fin": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "format": "json",
            },
            headers={"apikey": api_key},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SourceError(_SOURCE_ID, f"bouees request failed: {exc}") from exc

    if resp.status_code != 200:
        # La passerelle WSO2 répond en XML sur erreur, pas en JSON — ne pas
        # tenter .json() ici, le message utile est dans le corps brut.
        raise SourceError(_SOURCE_ID, f"bouees HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        rows = resp.json()
    except ValueError as exc:
        raise SourceError(_SOURCE_ID, f"bouees réponse non-JSON: {exc}") from exc

    if not rows:
        raise SourceError(_SOURCE_ID, f"bouees a répondu 200 mais 0 mesure sur [{start}, {now}]")

    obs = pd.DataFrame(rows)
    missing = [c for c in (*KEY_COLUMNS, *WAVE_COLUMNS) if c not in obs.columns]
    if missing:
        raise SourceError(_SOURCE_ID, f"colonnes absentes du payload bouees: {missing}")

    obs["validity_time"] = pd.to_datetime(
        obs["validity_time"], utc=True, format="ISO8601"
    ).dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    obs = obs.sort_values(KEY_COLUMNS)
    return obs.drop_duplicates(KEY_COLUMNS, keep="last").reset_index(drop=True)


def positions(obs: pd.DataFrame) -> list[dict]:
    """Une entrée par bouée vue dans la fenêtre : `{id, name, lat, lon, wave}`.

    Tout vient du payload, y compris les positions (règle du projet : jamais de
    coordonnée en dur). `wave` dit si la bouée a servi au moins une hauteur
    significative sur la fenêtre — c'est ce qui sépare les 8 bouées exploitables
    de BOUEE_SARDAIGNE, qui émet vent et pression mais aucune vague. Un booléen
    recalculé à chaque run plutôt qu'une liste figée : le jour où elle se remet à
    émettre, la carte le dit sans qu'on ait à y toucher.
    """
    g = obs.groupby("geo_id_wmo", sort=True)
    return [
        {
            "id": str(wmo),
            "name": str(rows["name"].iloc[0]),
            "lat": round(float(rows["lat"].iloc[0]), 4),
            "lon": round(float(rows["lon"].iloc[0]), 4),
            "wave": bool(rows["haut_vag"].notna().any()),
        }
        for wmo, rows in g
    ]


def non_null_counts(obs: pd.DataFrame) -> pd.DataFrame:
    """Non-null par bouée et par variable vagues — imprimé à chaque run.

    Un 200 OK ne prouve rien : seul le comptage sur la requête exacte prouve que
    la donnée est là (leçon payée trois fois sur ce projet). En sortie de cron,
    c'est la trace qui rend une panne silencieuse visible.
    """
    counts = obs.groupby("name")[WAVE_COLUMNS].count()
    counts.insert(0, "heures", obs.groupby("name")["validity_time"].nunique())
    return counts
