# Scoreboard Metocean IA — Implementation Plan

> ## 📦 ARCHIVÉ — 2026-08-04. Ne plus utiliser comme TODO.
>
> **Tasks 1 à 10 : livrées.** Cases cochées ci-dessous. Le pipeline tourne en
> cron quotidien depuis le 2026-08-03.
>
> **Tasks 11 à 14 : caduques, jamais construites.** Le `web/` Next.js prévu ici
> n'existe pas : le scoreboard vit dans le site Ocean Data Consulting
> (`~/Documents/DEV/WEB/ODC_WEBSITE/site/src/sections/scoreboard/`), en Astro +
> JSX, sous le design system ODC. Leurs cases restent décochées à dessein — les
> cocher laisserait croire que `web/` a été livré.
>
> **Écarts entre ce plan et ce qui tourne réellement** — le plan n'a pas été
> réécrit au fil de l'eau, donc une case cochée veut dire « l'objectif de
> l'étape est atteint », pas « à la lettre » :
>
> - **Task 5 (MFWAM/CMEMS)** a bien été construite, puis **retirée du pipeline**
>   lors du retrain multi-modèles (2026-08). La baseline vague est aujourd'hui
>   le meilleur des 5 modèles de l'API Marine d'Open-Meteo, choisi par station à
>   l'entraînement. Voir `docs/data-sources.md` § 4ter.
> - **Task 10 (cron)** : l'heure a bougé plusieurs fois (06:30 → 09:30 → 08:30 →
>   **07:30 UTC**) une fois la contrainte de disponibilité CMEMS disparue.
> - **Task 7 (gate)** : le gate porte sur le **gain hors biais**, pas sur le gain
>   affiché — durcissement postérieur au plan.
> - **Vent** : « volontairement absent des features v1 » en bas de page. Il est
>   depuis une **variable scorée** à part entière (`kind = "wind"`, 3 stations).
> - Le scoring va jusqu'à **48 h** via le mécanisme `pending` / `_rescore_pending`
>   (2026-08-03), absent du plan.
>
> **Source de vérité pour la suite : `docs/demandes-produit.md`.**

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un site public mis à jour chaque jour où une IA prédit Hs (bouées Candhis) et niveau d'eau (marégraphes) et se fait noter contre le modèle officiel et les observations.

**Architecture:** Batch quotidien GitHub Actions (Python/uv) qui fetch obs + prévisions, score la veille, prédit +48 h, commit des JSON statiques ; site Next.js sur Vercel qui lit ces JSON. Aucun serveur.

**Tech Stack:** Python 3.12 + uv, pandas, scikit-learn (HistGradientBoostingRegressor), utide, copernicusmarine ; Next.js (App Router) + Recharts ; GitHub Actions ; Vercel + Vercel Analytics.

## Global Constraints

- Repo : `~/Documents/DEV/OCEANO/metocean-ai-scoreboard` (git déjà initialisé, spec commitée).
- Secrets : jamais commités. Local = `.env` (déjà gitignoré, contient `CANDHIS_API_KEY`). CI = GitHub Actions secrets `CANDHIS_API_KEY`, `COPERNICUSMARINE_SERVICE_USERNAME`, `COPERNICUSMARINE_SERVICE_PASSWORD`.
- API Candhis (validée en live le 2026-07-30) : base `https://candhis.cerema.fr/API/v1/`, header `Authorization: <clé>`, GET uniquement. Fonctions : `getCampListe.php`, `getCampTR.php?camp=NNNNN&dateDeb=YYYY-MM-DD` (retour JSON : `{success, nbLig, entete, results}` ; TR = pas de 30 min, colonnes `["Date","H1/3 (m)","Hmax (m)","TH1/3 (s)","Dir. au pic (°)","Etal. au pic (°)","Temp. mer (°C)"]`), `getCampTD.php` pour l'archive. Quota quotidien existe (HTTP 429) : 1 requête par station par run, jamais de boucle de retry agressive.
- Contrats JSON pipeline→web (répertoire `data/` à la racine du repo) — voir Task 8, source de vérité.
- Framing public : "post-processing ML du modèle physique", jamais "remplace la physique".
- Délégation sous-agents (règle workspace) : chaque tâche indique son tier modèle — **[Sonnet]** implémentation cadrée, **[Opus]** modélisation/debug complexe, **[Haiku]** inventaires. Les tâches 3/4/5 sont parallélisables, ainsi que 11-13 vs 8-10.
- Tous les timestamps en UTC partout (obs Candhis TR sont en UTC, à re-vérifier au spike ; le web affiche UTC avec mention).
- Commits fréquents, messages `feat:/fix:/test:/chore:`, co-author `Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Spike sources de données + config stations [Sonnet] ✅

**Files:**
- Create: `pipeline/config/stations.toml`
- Create: `docs/data-sources.md`

**Interfaces:**
- Produces: `stations.toml`, lu par `config.load_stations()` (Task 2). Schéma par entrée :

```toml
[[station]]
id = "pierres-noires"        # slug URL/JSON
name = "Les Pierres Noires"
kind = "wave"                 # "wave" | "tide"
lat = 48.29
lon = -4.97
source = "candhis"            # "candhis" | "ioc" | "shom"
source_id = "02911"           # camp Candhis, ou code station IOC/SHOM
baseline = "mfwam"            # "mfwam" | "harmonic"
```

- [x] **Step 1 : Sélection des bouées Candhis.** Appeler `getCampListe.php` (clé dans `.env`), filtrer `Actif == "1"`, croiser avec les zones cibles (Bretagne/Iroise, Gascogne, Manche). Pour chaque candidate, appeler `getCampTR.php?camp=<id>&dateDeb=<J-7>` et vérifier ≥ 300 lignes sur 7 jours (données fraîches). Retenir 3-4 bouées (Pierres Noires 02911 déjà validée). Noter les coordonnées (page campagne Candhis ou `getCampInfos.php`).
- [x] **Step 2 : Vérifier la profondeur d'archive TR.** Appeler `getCampTR.php?camp=02911&dateDeb=<J-365>` et noter la date la plus ancienne réellement servie ; si < 12 mois, tester `getCampTD.php` (doc PDF : `https://candhis.cerema.fr/doc/04_Candhis_API_v1_Utilisateur.pdf`) pour l'historique d'entraînement. Documenter le choix TR vs TD dans `docs/data-sources.md`.
- [x] **Step 3 : Niveau d'eau — identifier les codes IOC.** `curl "https://www.ioc-sealevelmonitoring.org/service.php?query=stationlist&format=json"` puis filtrer côté client sur les stations françaises (le paramètre `country` ne filtre pas, vérifié) ; chercher Brest et Saint-Malo (codes probables `bres`, `smal` — à confirmer). Valider `service.php?query=data&code=<code>&timestart=<J-2>&format=json` renvoie des niveaux. En parallèle, envoyer la demande de clé SHOM (data.shom.fr) — upgrade futur, pas bloquant.
- [x] **Step 4 : Vérifier l'accès MFWAM.** `uvx copernicusmarine subset --dataset-id cmems_mod_glo_wav_anfc_0.083deg_PT3H-i --variable VHM0 --start-datetime <J> --end-datetime <J+2> --minimum-longitude -5.1 --maximum-longitude -4.9 --minimum-latitude 48.2 --maximum-latitude 48.4 -o /tmp -f test_mfwam.nc` avec les identifiants CMEMS. Confirmer que le produit couvre +48 h de prévision et noter l'heure de disponibilité du run quotidien (conditionne l'heure du cron, spec dit ~06h UTC).
- [x] **Step 5 : Écrire `pipeline/config/stations.toml`** avec les stations retenues (3-4 wave + 2 tide) au schéma ci-dessus, et `docs/data-sources.md` : pour chaque source — URL exacte, auth, format, fraîcheur, profondeur d'archive, quota, décision prise.
- [x] **Step 6 : Commit** `chore: spike sources — stations.toml + data-sources.md`.

---

### Task 2: Scaffold pipeline + chargeur de config [Sonnet] ✅

**Files:**
- Create: `pipeline/pyproject.toml`, `pipeline/src/scoreboard/__init__.py`, `pipeline/src/scoreboard/config.py`, `pipeline/tests/test_config.py`

**Interfaces:**
- Produces: `Station` (dataclass : `id, name, kind, lat, lon, source, source_id, baseline` — types str/float, `kind ∈ {"wave","tide"}`) et `load_stations(path: Path | None = None) -> list[Station]` (défaut : `pipeline/config/stations.toml`). Consommé par toutes les tâches suivantes.

- [x] **Step 1 : Init projet.** `cd pipeline && uv init --lib --name scoreboard --python 3.12`, puis `uv add pandas requests scikit-learn joblib && uv add --dev pytest`. Structure src layout (`src/scoreboard/`).
- [x] **Step 2 : Test qui échoue** — `pipeline/tests/test_config.py` :

```python
from pathlib import Path
from scoreboard.config import load_stations, Station

def test_load_stations_parses_toml(tmp_path: Path):
    f = tmp_path / "stations.toml"
    f.write_text('''
[[station]]
id = "pierres-noires"
name = "Les Pierres Noires"
kind = "wave"
lat = 48.29
lon = -4.97
source = "candhis"
source_id = "02911"
baseline = "mfwam"
''')
    stations = load_stations(f)
    assert stations == [Station(id="pierres-noires", name="Les Pierres Noires",
                                kind="wave", lat=48.29, lon=-4.97,
                                source="candhis", source_id="02911", baseline="mfwam")]

def test_load_stations_rejects_bad_kind(tmp_path: Path):
    f = tmp_path / "stations.toml"
    f.write_text('[[station]]\nid="x"\nname="x"\nkind="banana"\nlat=0.0\nlon=0.0\nsource="candhis"\nsource_id="0"\nbaseline="mfwam"\n')
    import pytest
    with pytest.raises(ValueError):
        load_stations(f)
```

- [x] **Step 3 : Run** `uv run pytest tests/test_config.py -v` → FAIL (module absent).
- [x] **Step 4 : Implémenter `config.py`** — stdlib `tomllib` + `dataclasses.dataclass(frozen=True)` ; valider `kind` et `source` contre les ensembles autorisés, lever `ValueError` sinon. Pas de pydantic (YAGNI).
- [x] **Step 5 : Run** → PASS. **Commit** `feat: pipeline scaffold + station config loader`.

---

### Task 3: Fetcher Candhis (obs vagues) [Sonnet] ✅

**Files:**
- Create: `pipeline/src/scoreboard/sources/__init__.py`, `pipeline/src/scoreboard/sources/candhis.py`, `pipeline/tests/test_candhis.py`, `pipeline/tests/fixtures/candhis_tr.json`

**Interfaces:**
- Consumes: `Station` (Task 2) ; env `CANDHIS_API_KEY`.
- Produces: `fetch_wave_obs(station: Station, date_start: date, session: requests.Session | None = None) -> pd.DataFrame` — index UTC `DatetimeIndex` nommé `time`, colonnes `hs` (float, m), `tp` (float, s). Lève `SourceError(station_id, msg)` (définie dans `sources/__init__.py`, consommée par Task 8 pour le statut `missing`).

- [x] **Step 1 : Enregistrer la fixture.** `curl -H "Authorization: $CANDHIS_API_KEY" "https://candhis.cerema.fr/API/v1/getCampTR.php?camp=02911&dateDeb=<J-2>" > pipeline/tests/fixtures/candhis_tr.json` (tronquer à ~20 lignes de `results` à la main pour garder la fixture petite).
- [x] **Step 2 : Test qui échoue** — `test_candhis.py` :

```python
import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock
from scoreboard.config import Station
from scoreboard.sources.candhis import fetch_wave_obs
from scoreboard.sources import SourceError

FIX = json.loads((Path(__file__).parent / "fixtures/candhis_tr.json").read_text())
ST = Station(id="pierres-noires", name="PN", kind="wave", lat=48.29, lon=-4.97,
             source="candhis", source_id="02911", baseline="mfwam")

def make_session(payload, status=200):
    s = Mock(); r = Mock()
    r.status_code = status; r.json.return_value = payload
    s.get.return_value = r
    return s

def test_parses_tr_payload():
    df = fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(FIX))
    assert list(df.columns) == ["hs", "tp"]
    assert df.index.tz is not None and str(df.index.tz) == "UTC"
    assert df["hs"].iloc[0] == 1.0          # valeur de la fixture
    assert (df["hs"] < 30).all()             # garde-fou valeurs aberrantes

def test_failure_raises_source_error():
    bad = {"success": False, "message": "Clé d'API non valide", "results": None}
    import pytest
    with pytest.raises(SourceError):
        fetch_wave_obs(ST, date(2026, 7, 28), session=make_session(bad, status=401))
```

- [x] **Step 3 : Run** → FAIL. **Step 4 : Implémenter** — GET avec header `Authorization`, timeout 30 s, parse `entete`/`results` (colonne `H1/3 (m)` → `hs`, `TH1/3 (s)` → `tp`), `pd.to_datetime(..., utc=True)`, drop des valeurs hors bornes physiques (`0 <= hs < 30`), tri + dédoublonnage index. `success != True` ou HTTP ≠ 200 → `SourceError`. Une seule requête, pas de retry (le backfill du lendemain rattrape).
- [x] **Step 5 : Run** → PASS. **Commit** `feat: candhis wave obs fetcher`.

---

### Task 4: Fetcher niveau d'eau (IOC) + prédiction harmonique [Sonnet] ✅

**Files:**
- Create: `pipeline/src/scoreboard/sources/waterlevel.py`, `pipeline/src/scoreboard/harmonic.py`, `pipeline/tests/test_waterlevel.py`, `pipeline/tests/fixtures/ioc_data.json`

**Interfaces:**
- Consumes: `Station` (kind="tide", source="ioc").
- Produces:
  - `fetch_tide_obs(station, date_start, session=None) -> pd.DataFrame` — index UTC `time`, colonne `level` (m). Même contrat d'erreur `SourceError`.
  - `harmonic.fit(obs: pd.Series) -> HarmonicModel` et `HarmonicModel.predict(times: pd.DatetimeIndex) -> pd.Series` — baseline "marée astronomique" (utide). `HarmonicModel.save(path)/load(path)` via pickle joblib.

- [x] **Step 1 :** `uv add utide`. Enregistrer la fixture IOC réelle (code station validé au spike, Task 1) : `service.php?query=data&code=<code>&timestart=...&format=json`, tronquée.
- [x] **Step 2 : Test qui échoue** — parsing fixture → DataFrame `level` UTC, garde-fou `-15 < level < 15` ; test harmonic : fit sur 30 jours de signal synthétique `2.0*sin(2π t/12.42h)`, prédire 24 h de plus, `assert` corrélation > 0.99 avec le signal exact (utide retrouve M2 sur un cas jouet).
- [x] **Step 3 : Run** → FAIL. **Step 4 : Implémenter.** `waterlevel.py` : parse du JSON IOC (champ capteur type `rad`/`prs` — prendre celui présent, documenté au spike), resample 10 min → horaire (moyenne). `harmonic.py` : wrapper mince autour de `utide.solve/reconstruct` (lat de la station requise par utide — la passer en argument de `fit`).
- [x] **Step 5 : Run** → PASS. **Commit** `feat: ioc water level fetcher + harmonic baseline`.

---

### Task 5: Fetcher prévisions MFWAM (baseline vagues) [Sonnet] ✅ puis ⚠️ retirée du pipeline (2026-08)

**Files:**
- Create: `pipeline/src/scoreboard/sources/mfwam.py`, `pipeline/tests/test_mfwam.py`, `pipeline/tests/fixtures/mfwam_point.nc`

**Interfaces:**
- Consumes: `Station` (kind="wave") ; identifiants CMEMS en env.
- Produces: `fetch_wave_forecast(stations: list[Station], run_date: date) -> dict[str, pd.DataFrame]` — par station id, index UTC horaire (interpolé depuis PT3H), colonne `hs_baseline` (VHM0 au point le plus proche). Un seul subset spatial englobant pour toutes les stations (une requête CMEMS par run, pas une par station).

> **Remplacée.** La baseline vague est passée à l'API Marine d'Open-Meteo
> (meilleur des 5 modèles par station, choisi à l'entraînement) lors du retrain
> multi-modèles. `sources/mfwam.py` et CMEMS ne sont plus dans le pipeline —
> justification et chiffres dans `docs/data-sources.md` § 4ter. Les cases sont
> cochées parce que l'étape a bien été livrée en son temps.

- [x] **Step 1 :** `uv add copernicusmarine xarray netcdf4`. Générer la fixture : un subset réel minuscule (1 point, 48 h) sauvé en `.nc` via la commande validée au spike (Task 1 Step 4).
- [x] **Step 2 : Test qui échoue** — la partie extraction/interp est testée sur la fixture : `_extract_point(ds, lat, lon) -> pd.DataFrame` retourne du `hs_baseline` horaire UTC sans NaN interne ; le test du fetch réseau lui-même est skippé sans credentials (`@pytest.mark.skipif(not os.getenv("COPERNICUSMARINE_SERVICE_USERNAME"), ...)`).
- [x] **Step 3 : Run** → FAIL. **Step 4 : Implémenter** — `copernicusmarine.subset` (dataset `cmems_mod_glo_wav_anfc_0.083deg_PT3H-i`, variable `VHM0`, bbox = min/max lat/lon des stations ± 0.2°, `run_date` → `run_date+2j`), ouverture xarray, `sel(..., method="nearest")` par station, `resample("1h").interpolate()`. Échec réseau/auth → `SourceError("mfwam", ...)` pour toutes les stations wave.
- [x] **Step 5 : Run** → PASS. **Commit** `feat: mfwam baseline forecast fetcher`.

---

### Task 6: Features + dataset d'entraînement [Opus] ✅

**Files:**
- Create: `pipeline/src/scoreboard/features.py`, `pipeline/src/scoreboard/dataset.py`, `pipeline/tests/test_features.py`
- Create: `pipeline/scripts/build_dataset.py`

**Interfaces:**
- Consumes: fetchers Tasks 3-5 (pour `build_dataset.py`) ; DataFrames obs/baseline.
- Produces: `build_features(baseline: pd.Series, obs_recent: pd.Series, t0: pd.Timestamp) -> pd.DataFrame` — une ligne par heure de `baseline` postérieure à `t0`, colonnes **exactement** `["baseline", "lead_h", "last_err", "mean_err_24h", "hour_sin", "hour_cos"]` où `last_err = obs_recent.iloc[-1] - baseline_aligné(t_dernière_obs)` et `mean_err_24h` = moyenne de (obs − baseline) sur les 24 h avant `t0` (0.0 si historique insuffisant — jamais NaN). Identique à l'entraînement et à l'inférence — c'est LE point qui garantit l'absence de train/serve skew. Cible d'entraînement : `y = obs(t)` aux mêmes heures.
- `dataset.py` : `assemble(station, obs: pd.DataFrame, baseline: pd.DataFrame, issue_hours: list[int] = [6]) -> tuple[pd.DataFrame, pd.Series]` — simule une émission par jour à 06h UTC sur l'historique, empile les features X et cible y.

- [x] **Step 1 : Test qui échoue** — cas synthétique : baseline constante 1.0, obs constante 1.3 → `last_err == 0.3`, `mean_err_24h == 0.3`, `lead_h` croît de 1 en 1 ; historique obs vide → `last_err == 0.0` et pas de NaN ; vérifier qu'aucune feature n'utilise d'obs postérieure à `t0` (anti-fuite : passer des obs futures piégées à 99.0 et vérifier qu'elles n'influencent rien).
- [x] **Step 2 : Run** → FAIL. **Step 3 : Implémenter** `features.py` puis `dataset.py`. **Step 4 : Run** → PASS.
- [x] **Step 5 : Script `build_dataset.py`** (pas un module, un script) : pour chaque station, fetch l'historique (Candhis TR/TD profond, IOC ; MFWAM : archive analyse du même dataset anfc sur la période disponible — documenté au spike), assemble et écrit `pipeline/data_train/<station>.parquet`. Ajouter `uv add pyarrow`.
- [x] **Step 6 : Exécuter le script** pour toutes les stations, vérifier taille des datasets (viser ≥ 3 mois × 42 leads/jour ≈ 4 000 lignes/station minimum ; sinon élargir la profondeur au TD).
- [x] **Step 7 : Commit** `feat: feature engineering + training datasets` (les parquet ne sont PAS commités — ajouter `pipeline/data_train/` au `.gitignore`).

---

### Task 7: Entraînement + évaluation des modèles [Opus] ✅

**Files:**
- Create: `pipeline/src/scoreboard/model.py`, `pipeline/scripts/train.py`, `pipeline/tests/test_model.py`, `pipeline/models/` (artefacts commités), `docs/model-eval.md`

**Interfaces:**
- Consumes: parquet Task 6, features Task 6.
- Produces: `model.train(X, y) -> sklearn.Pipeline`, `model.save(m, station_id)` / `model.load(station_id)` (joblib, chemin `pipeline/models/<station_id>.joblib`), `model.predict(m, X) -> np.ndarray`. Pour `kind="tide"`, la cible est le **résidu** obs − harmonique et la prédiction publiée est `harmonique + résidu_prédit` (assemblage fait en Task 8).

> **Durci depuis.** Le gate porte sur le **gain hors biais** (`gain_debiased`),
> jamais sur le gain affiché — une station ne peut plus passer sur un simple
> débiaisage. Trois candidats sont comparés sur une fenêtre de validation
> (`hgb`, `ridge`, `hgb-per-lead`), seul le gagnant est ré-entraîné puis évalué
> une fois sur le test. Voir `docs/model-eval.md`.

- [x] **Step 1 : Test qui échoue** — sur données synthétiques où `y = baseline + 0.5*last_err` : entraîner, prédire, `assert MAE(model) < MAE(baseline seule) * 0.5` ; round-trip save/load donne des prédictions identiques.
- [x] **Step 2 : Run** → FAIL. **Step 3 : Implémenter** — `HistGradientBoostingRegressor(max_iter=300, learning_rate=0.06, early_stopping=True)` dans un `sklearn.pipeline.Pipeline` (pas de scaler nécessaire pour les arbres — pipeline quand même pour figer les noms de colonnes via un `ColumnTransformer passthrough` minimal ou un check des colonnes en entrée de `predict`).
- [x] **Step 4 : Run** → PASS.
- [x] **Step 5 : `scripts/train.py`** — split temporel (dernier mois = test, jamais aléatoire), entraîne par station, imprime MAE modèle vs MAE baseline sur le test, sauve les artefacts, écrit `docs/model-eval.md` (tableau par station). **Gate de réalisme : si l'IA ne bat pas la baseline sur le test d'au moins ~5 %, ne pas mettre en ligne cette station — le scoreboard public perdrait sa raison d'être.** Itérer (plus d'historique, feature vent ARPEGE en upgrade) avant de continuer.
- [x] **Step 6 : Exécuter, vérifier le gate, commit** `feat: per-station models + eval report` (artefacts `.joblib` commités — ils font partie du produit).

---

### Task 8: Run quotidien — prédire, scorer, publier [Sonnet] ✅

**Files:**
- Create: `pipeline/src/scoreboard/publish.py`, `pipeline/src/scoreboard/daily.py`, `pipeline/src/scoreboard/cli.py`, `pipeline/tests/test_publish.py`, `pipeline/tests/test_daily.py`

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces — **contrat JSON définitif** (répertoire `data/` à la racine du repo, lu par le web) :

```
data/stations.json          [{"id","name","kind","lat","lon","unit"}]   unit: "m"
data/<id>/latest.json       {"station","issued":"2026-07-30T06:00:00Z",
                             "series":[{"t":"2026-07-30T07:00:00Z","ia":1.42,"baseline":1.55}]}
data/<id>/history.json      {"station","days":[{"date":"2026-07-29","status":"ok"|"missing",
                             "series":[{"t":...,"obs":1.4,"ia":1.5,"baseline":1.6}],
                             "mae_ia":0.11,"mae_baseline":0.19}]}        (90 derniers jours max)
data/scores.json            {"updated":"...","stations":[{"id","n_days",
                             "mae_ia_7d","mae_baseline_7d","mae_ia_30d","mae_baseline_30d",
                             "mae_ia_all","mae_baseline_all"}]}
```

- `cli.py` : `uv run scoreboard daily [--date YYYY-MM-DD] [--dry-run]` (entry point `[project.scripts] scoreboard = "scoreboard.cli:main"`, argparse).

> **Étendu depuis.** Les leads non couverts par les obs au moment du scoring
> partent en `pending` dans `history.json` et sont complétés par les runs
> suivants (`daily._rescore_pending`, 2026-08-03) : `max_lead_h` monte jusqu'à
> 48 h au lieu de s'arrêter à ce qui était observable le jour même.

- [x] **Step 1 : Tests qui échouent** — `test_publish.py` : (a) `score_day(obs, pred_ia, pred_baseline) -> (mae_ia, mae_baseline)` sur valeurs connues ; (b) history.json tronqué à 90 jours ; (c) **idempotence** : publier deux fois le même jour J sur un `tmp_path` → fichiers identiques, pas de jour dupliqué ; (d) station en échec → entrée `{"date": J, "status": "missing"}` et scores agrégés inchangés. `test_daily.py` : orchestration avec fetchers mockés — un `SourceError` sur une station n'empêche pas les autres d'être publiées.
- [x] **Step 2 : Run** → FAIL. **Step 3 : Implémenter.** `daily.py` : (1) pour chaque station, fetch obs de la veille → scorer les prédictions émises la veille (relues depuis `latest.json` du jour précédent, conservé dans `history`) ; (2) fetch baseline du jour (MFWAM une fois pour toutes les wave ; harmonique pour les tide) ; (3) features + inférence ; (4) `publish.py` écrit les 4 familles de JSON (écriture atomique : tmp + rename). `--dry-run` : tout sauf l'écriture dans `data/` (écrit dans un tmp affiché). Try/except par station, jamais global.
- [x] **Step 4 : Run** → PASS. **Step 5 : Run réel local** `uv run scoreboard daily --dry-run` avec les vraies sources, inspecter la sortie. **Commit** `feat: daily run — predict, score, publish JSON contract`.

---

### Task 9: Backfill [Sonnet] ✅

**Files:**
- Modify: `pipeline/src/scoreboard/cli.py`, `pipeline/src/scoreboard/daily.py`
- Create: `pipeline/tests/test_backfill.py`

**Interfaces:**
- Produces: `uv run scoreboard backfill --since YYYY-MM-DD` — rejoue `daily` pour chaque date manquante dans `history.json` jusqu'à hier (scoring compris ; les prédictions des jours passés sont re-générées à partir des données disponibles a posteriori, marquées `"backfilled": true` dans le jour concerné).

- [x] **Step 1 : Test qui échoue** — avec daily mocké : un historique troué (J-3 manquant) + `backfill --since J-5` → seuls les jours manquants sont rejoués, ordre chronologique, pas de doublon.
- [x] **Step 2 : Run** → FAIL. **Step 3 : Implémenter** (une boucle sur les dates + le flag `backfilled`). **Step 4 : Run** → PASS. **Commit** `feat: backfill command`.

---

### Task 10: GitHub Actions cron [Sonnet] ✅

**Files:**
- Create: `.github/workflows/daily.yml`, `README.md`

**Interfaces:**
- Consumes: CLI Task 8/9 ; secrets GitHub `CANDHIS_API_KEY`, `COPERNICUSMARINE_SERVICE_USERNAME`, `COPERNICUSMARINE_SERVICE_PASSWORD`.

> **Heure de cron changée depuis.** 06:30 → 09:30 → 08:30 → **07:30 UTC**, une
> fois la contrainte de disponibilité CMEMS disparue. Les secrets CMEMS ne sont
> plus utilisés ; s'y sont ajoutées les clés Météo-France (DPObs, DPClim).
> Un second workflow `ci.yml` (pytest + ruff, path-scopé sur `pipeline/**`)
> a été ajouté hors plan.

- [x] **Step 1 : Créer le repo GitHub** (public — c'est le produit vitrine) : `gh repo create metocean-ai-scoreboard --public --source . --push`. Poser les 3 secrets via `gh secret set`.
- [x] **Step 2 : Écrire `daily.yml`** — `schedule: cron "30 6 * * *"` (ajusté à l'heure de dispo MFWAM notée au spike) + `workflow_dispatch:`. Jobs : checkout → `astral-sh/setup-uv` → `uv sync` (dossier pipeline) → `uv run scoreboard daily` → si `git status --porcelain data/` non vide : commit `chore(data): daily run YYYY-MM-DD` + push (avec `permissions: contents: write`). `concurrency: group: daily, cancel-in-progress: false`.
- [x] **Step 3 : Déclencher manuellement** (`gh workflow run daily.yml`), vérifier le commit de données. Relancer une 2e fois → aucun nouveau commit (idempotence en conditions réelles).
- [x] **Step 4 : README.md** court : pitch, architecture (schéma texte de la spec), commandes, lien vers la spec. **Commit.**

---

### Task 11: Web — scaffold + Scoreboard (accueil) [Sonnet] ⛔ CADUQUE

> **Jamais construite, et ne le sera pas.** Le répertoire `web/` n'existe pas.
> Le scoreboard est une section du site Ocean Data Consulting
> (`ODC_WEBSITE/site/src/sections/scoreboard/` + `pages/ScoreboardPage.jsx`),
> sous le design system ODC. Cases laissées décochées à dessein.

**Files:**
- Create: `web/` (Next.js App Router, TypeScript, Tailwind), `web/lib/data.ts`, `web/app/page.tsx`, `web/components/ScoreTable.tsx`

**Interfaces:**
- Consumes: les JSON du contrat Task 8, importés **au build** (`import scores from "../../data/scores.json"` — le site est régénéré à chaque push de données par Vercel, donc SSG suffit, zéro fetch runtime).
- Produces: `lib/data.ts` exporte les types TS du contrat (`StationMeta`, `Scores`, `StationHistory`, `StationLatest`) et les loaders typés — consommés par Tasks 12-13.

- [ ] **Step 1 :** `npx create-next-app@latest web --ts --tailwind --app --no-src-dir --import-alias "@/*"`. Purger le boilerplate.
- [ ] **Step 2 : `lib/data.ts`** — types miroir du contrat JSON + `getScores()`, `getStations()`, `getStationHistory(id)`, `getStationLatest(id)` (lecture `fs` dans `../data` au build, `export const dynamic = "force-static"`).
- [ ] **Step 3 : Page d'accueil** — le scoreboard : une ligne par station (nom, type, MAE 30 j IA vs baseline, delta en %, mini-badge "IA gagne/perd"), tri par delta. En-tête : titre + une phrase du framing honnête + date de dernière mise à jour. Design sobre lisible (pas de refonte design à ce stade ; l'esthétique pourra passer par un skill design plus tard).
- [ ] **Step 4 : Vérifier** `npm run build && npm run start` avec les données réelles commitées par Task 10. **Commit** `feat(web): scoreboard home`.

---

### Task 12: Web — page station [Sonnet] ⛔ CADUQUE

> Remplacée par `StationChart.jsx` + `DailyMaeTable.jsx` dans le site ODC.

**Files:**
- Create: `web/app/station/[id]/page.tsx`, `web/components/SeriesChart.tsx`

**Interfaces:**
- Consumes: `getStationHistory`, `getStationLatest`, types Task 11.
- Produces: route `/station/<id>` avec `generateStaticParams()` sur `stations.json`.

- [ ] **Step 1 :** `npm i recharts`. `SeriesChart` : courbes obs (trait plein), IA, baseline (pointillés) sur 7 jours passés + zone "prévision +48 h" (ia & baseline seulement), axe UTC.
- [ ] **Step 2 : Page** — chart + tableau des MAE quotidiennes récentes + jours `missing` affichés grisés ("données manquantes").
- [ ] **Step 3 : Build OK, navigation depuis le scoreboard. Commit** `feat(web): station detail page`.

---

### Task 13: Web — page méthode + analytics [Haiku] ⛔ CADUQUE

> Le contenu de méthode vit dans le site ODC et dans `docs/data-sources.md`.

**Files:**
- Create: `web/app/methode/page.tsx`
- Modify: `web/app/layout.tsx`

**Interfaces:**
- Consumes: rien de nouveau.

- [ ] **Step 1 : Page méthode** (contenu rédigé, pas de lorem) : données utilisées (Candhis/Cerema, IOC, Copernicus Marine — crédits obligatoires des licences), ce que fait l'IA (post-processing ML, features, ré-entraînement), limites assumées, qui je suis + CTA (LinkedIn, email, site).
- [ ] **Step 2 : Analytics** — `npm i @vercel/analytics`, `<Analytics/>` dans `layout.tsx`. Metadata SEO (title, description, OG image simple).
- [ ] **Step 3 : Build OK. Commit** `feat(web): method page + analytics`.

---

### Task 14: Déploiement Vercel + E2E [Sonnet] ⛔ CADUQUE

> Le déploiement est celui du site ODC, pas d'un `web/` propre à ce repo.

**Files:**
- Create: `web/vercel.json` (si nécessaire — root directory `web`)

- [ ] **Step 1 : Connecter le repo à Vercel** (root directory = `web`, framework Next.js). Premier deploy preview, puis production.
- [ ] **Step 2 : E2E réel** — `gh workflow run daily.yml` → vérifier : commit data → build Vercel déclenché → nouvelles valeurs visibles en prod sur les 3 pages.
- [ ] **Step 3 : Vérifier Vercel Analytics** reçoit des events (visite manuelle).
- [ ] **Step 4 : Commit final + tag `v1.0.0`.** Livrer à Matthieu : URL prod, checklist de com' LinkedIn (hors scope du plan).

---

## Self-review (fait à l'écriture)

- Spec coverage : stations/variables (T1), modèle honnête (T6-7 + gate), pipeline quotidien (T8, T10), erreurs/degradation douce (T3-5 `SourceError`, T8 statut missing, T9 backfill), 3 écrans web (T11-13), tracking (T13), secrets (contraintes globales + T10), délégation sous-agents (tiers par tâche), évolutions (rien construit — YAGNI).
- Types cohérents : `Station`, `SourceError`, contrat JSON et loaders TS alignés (Task 8 ↔ 11).
- Vent ARPEGE : volontairement absent des features v1 (upgrade listé en Task 7 si le gate échoue) — décision, pas un oubli.
