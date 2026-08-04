"""Orchestration de `scoreboard archive-obs` : collecte → archive → publication.

Même étage que `daily.py` et `backfill.py`, et pour la même raison : la CLI est
une façade argparse, et un câblage qui ne tourne qu'en production est un câblage
que personne ne teste. Ce module existe pour que `sources/mfbuoy.py` reste ce que
sont tous ses voisins (`candhis`, `marine`, `wind`, `waterlevel`) — un fetcher qui
interroge une API et rend une frame, rien de plus. Un fetcher qui connaîtrait
`publish` coupleraît la lecture d'une API au contrat public du site.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from scoreboard import archive, publish
from scoreboard.sources import mfbuoy


def run(archive_dir: Path, out_dir: Path) -> tuple[pd.DataFrame, list[Path]]:
    """Récupère la fenêtre glissante, la fusionne dans l'archive jour, publie `buoys.json`.

    `buoys.json` est réécrit à chaque run à partir de la fenêtre qui vient
    d'arriver, pas d'une liste tenue à la main : une bouée déplacée, renommée ou
    remise à émettre de la houle se corrige toute seule au run suivant.
    """
    obs = mfbuoy.fetch_buoy_obs()
    written = archive.write_obs_days(archive_dir, obs, key=mfbuoy.KEY_COLUMNS)
    publish.write_buoys(
        out_dir,
        mfbuoy.positions(obs),
        updated=obs["validity_time"].max().replace("+00:00", "Z"),
        # Le premier jour archivé, donc l'âge réel du corpus. Relu du disque à
        # chaque run plutôt que mémorisé : c'est la seule source qui reste vraie
        # si un fichier jour est ajouté a posteriori ou retiré.
        since=min((p.stem for p in archive_dir.glob("*.parquet")), default=None),
    )
    return obs, written
