"""Section « Data » de docs/dev-dashboard.html — où en est l'accumulation.

Trois corpus distincts, lus depuis les fichiers réels (rien n'est tapé à la
main) :

1. Par station (les 9 de config/stations.toml) : data/<id>/history.json
   (jours publiés, jours en défaut, trous de calendrier), data/scores.json
   (n_days vs n_days_backfilled — ce qui s'accumule en live), et
   data_forecast_archive/*.parquet (jours où la voie prévisions archivées a
   tourné pour cette station).
2. Archive bouées Météo-France (data_obs_archive/*.parquet) : corpus
   national, irremplaçable (fenêtre glissante ~96 h côté API), pas clé par
   station du scoreboard.
3. Jeux d'entraînement (data_train/*.parquet) : une ligne par fichier.

Réécrit strictement le bloc balisé
`<!-- DATA:START ... -->` / `<!-- DATA:END -->` dans docs/dev-dashboard.html.
Erreur explicite si les marqueurs sont absents — jamais d'insertion devinée.

Run : cd pipeline && uv run python scripts/data_coverage.py
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from scoreboard.config import load_stations

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SCORES_PATH = DATA_DIR / "scores.json"
FORECAST_ARCHIVE = ROOT / "pipeline" / "data_forecast_archive"
OBS_ARCHIVE = ROOT / "pipeline" / "data_obs_archive"
TRAIN_DIR = ROOT / "pipeline" / "data_train"
DASHBOARD = ROOT / "docs" / "dev-dashboard.html"

START_MARKER = "<!-- DATA:START — généré par pipeline/scripts/data_coverage.py, ne pas éditer à la main -->"
END_MARKER = "<!-- DATA:END -->"


def missing_days(present: set[date], first: date, last: date) -> list[date]:
    """Jours du calendrier [first, last] absents de `present`."""
    if first > last:
        return []
    total = (last - first).days + 1
    return [first + timedelta(days=i) for i in range(total) if first + timedelta(days=i) not in present]


def _iso(d: date) -> str:
    return d.isoformat()


def station_row(station_id: str, scores_by_id: dict) -> dict:
    row: dict = {"id": station_id}
    history_path = DATA_DIR / station_id / "history.json"
    if not history_path.exists():
        row["published"] = False
        return row
    row["published"] = True
    days = pd.read_json(history_path)["days"].tolist()
    dates = sorted(date.fromisoformat(d["date"]) for d in days)
    row["first"] = _iso(dates[0])
    row["last"] = _iso(dates[-1])
    row["n_present"] = len(dates)
    row["n_not_ok"] = sum(1 for d in days if d["status"] != "ok")
    row["n_missing"] = len(missing_days(set(dates), dates[0], dates[-1]))

    s = scores_by_id.get(station_id)
    row["n_days"] = s["n_days"] if s else None
    row["n_backfilled"] = s["n_days_backfilled"] if s else None
    row["n_live"] = (s["n_days"] - s["n_days_backfilled"]) if s else None

    n_forecast_days = 0
    for f in sorted(FORECAST_ARCHIVE.glob("*.parquet")):
        ids = pd.read_parquet(f, columns=["station_id"])["station_id"]
        if (ids == station_id).any():
            n_forecast_days += 1
    row["n_forecast_archive_days"] = n_forecast_days
    return row


def obs_archive_summary() -> dict:
    files = sorted(OBS_ARCHIVE.glob("*.parquet"))
    if not files:
        return {"n_files": 0}
    file_dates = sorted(date.fromisoformat(f.stem) for f in files)
    total_rows = 0
    wmo_ids: set = set()
    for f in files:
        df = pd.read_parquet(f, columns=["geo_id_wmo"])
        total_rows += len(df)
        wmo_ids.update(df["geo_id_wmo"].unique().tolist())
    return {
        "n_files": len(files),
        "first": _iso(file_dates[0]),
        "last": _iso(file_dates[-1]),
        "n_missing": len(missing_days(set(file_dates), file_dates[0], file_dates[-1])),
        "n_rows": total_rows,
        "n_buoys": len(wmo_ids),
    }


def train_rows() -> list[dict]:
    rows = []
    for f in sorted(TRAIN_DIR.glob("*.parquet")):
        df = pd.read_parquet(f)
        idx = df.index
        rows.append(
            {
                "name": f.name,
                "n_rows": len(df),
                "first": str(idx.min()) if len(idx) else "—",
                "last": str(idx.max()) if len(idx) else "—",
            }
        )
    return rows


def render_html(stations: list[dict], obs: dict, train: list[dict], generated: str, commit: str) -> str:
    lines = [START_MARKER]
    lines.append('<section class="plan">')
    lines.append('  <div class="plan-head">')
    lines.append('    <h3 class="plan-title">Data — accumulation aux bouées et aux stations</h3>')
    lines.append(
        '    <span class="plan-source">pipeline/scripts/data_coverage.py</span>'
    )
    lines.append("  </div>")
    lines.append(
        f'  <p class="plan-note">Généré le {generated} au commit {commit}. '
        "Régénérer : <code>cd pipeline &amp;&amp; uv run python scripts/data_coverage.py</code>. "
        "Les trous sont le signal utile — un cron qui glisse ou saute est documenté dans "
        "<code>.github/workflows/daily.yml</code>.</p>"
    )

    lines.append("  <h4>Par station</h4>")
    lines.append('  <div style="overflow-x:auto"><table class="data-table">')
    lines.append(
        "    <tr><th>station</th><th>publié</th><th>1er jour</th><th>dernier jour</th>"
        "<th>jours présents</th><th>défaut</th><th>trous</th>"
        "<th>n_days</th><th>backfilled</th><th>live</th><th>voie prévisions archivées</th></tr>"
    )
    for s in stations:
        if not s["published"]:
            lines.append(
                f'    <tr><td>{s["id"]}</td><td colspan="10">aucune donnée publiée '
                "— configurée dans stations.toml, sans data/&lt;id&gt;/history.json</td></tr>"
            )
            continue
        lines.append(
            "    <tr>"
            f'<td>{s["id"]}</td><td>oui</td><td>{s["first"]}</td><td>{s["last"]}</td>'
            f'<td>{s["n_present"]}</td><td>{s["n_not_ok"]}</td><td>{s["n_missing"]}</td>'
            f'<td>{s["n_days"]}</td><td>{s["n_backfilled"]}</td><td>{s["n_live"]}</td>'
            f'<td>{s["n_forecast_archive_days"]}</td></tr>'
        )
    lines.append("  </table></div>")

    lines.append("  <h4>Archive bouées Météo-France (nationale, irremplaçable)</h4>")
    if obs["n_files"] == 0:
        lines.append('  <p class="plan-note">Aucun fichier dans pipeline/data_obs_archive/.</p>')
    else:
        lines.append(
            f'  <p class="plan-note">{obs["n_files"]} jour(s) archivé(s), du {obs["first"]} au '
            f'{obs["last"]} ({obs["n_missing"]} jour(s) manquant(s) dans l\'intervalle) · '
            f'{obs["n_rows"]} lignes · {obs["n_buoys"]} bouées distinctes (geo_id_wmo). '
            "Fenêtre glissante ~96 h côté API Météo-France : aucun jour manqué ici n'est "
            "rattrapable après coup.</p>"
        )

    lines.append("  <h4>Jeux d'entraînement (pipeline/data_train/)</h4>")
    lines.append('  <div style="overflow-x:auto"><table class="data-table">')
    lines.append("    <tr><th>fichier</th><th>lignes</th><th>première date</th><th>dernière date</th></tr>")
    for t in train:
        lines.append(
            f'    <tr><td>{t["name"]}</td><td>{t["n_rows"]}</td><td>{t["first"]}</td><td>{t["last"]}</td></tr>'
        )
    lines.append("  </table></div>")

    lines.append("</section>")
    lines.append(END_MARKER)
    return "\n".join(lines)


def write_block(new_block: str) -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1:
        raise SystemExit(
            f"Marqueurs DATA:START/DATA:END absents de {DASHBOARD} — insertion devinée refusée. "
            "Ajoute-les manuellement une première fois, avant section.reste."
        )
    end += len(END_MARKER)
    DASHBOARD.write_text(text[:start] + new_block + text[end:], encoding="utf-8")


def main() -> None:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    generated = date.today().isoformat()

    scores = pd.read_json(SCORES_PATH)
    scores_by_id = {s["id"]: s for s in scores["stations"].tolist()}

    stations = [station_row(s.id, scores_by_id) for s in load_stations()]
    obs = obs_archive_summary()
    train = train_rows()

    block = render_html(stations, obs, train, generated, commit)
    write_block(block)
    print(f"écrit : bloc DATA dans {DASHBOARD.relative_to(ROOT)} ({len(stations)} stations)")


if __name__ == "__main__":
    main()
