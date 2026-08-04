# Scoreboard Metocean IA

Un modèle IA **post-traite** la prévision physique officielle (le meilleur des
5 modèles de vagues Open-Meteo Marine pour la houle et le meilleur des 3
modèles de vent — `baseline_model`, choisi par station à l'entraînement — un
modèle harmonique pour le niveau d'eau) sur des stations françaises de
référence, et publie chaque jour son écart mesuré face à cette baseline
officielle. Ce n'est pas un modèle qui remplace la physique — c'est une
correction statistique dessus, notée publiquement, jour après jour.

Spec complète : [`docs/superpowers/specs/2026-07-30-scoreboard-metocean-ia-design.md`](docs/superpowers/specs/2026-07-30-scoreboard-metocean-ia-design.md).
Justification de chaque source de données : [`docs/data-sources.md`](docs/data-sources.md).

## Stations

9 stations retenues, **7 publiées** — les deux restantes sont mesurées comme
les autres tous les jours, mais un *gate* qualité (`pipeline/models/gate.json`)
retient toute station où l'IA ne bat pas sa propre baseline sur les données
d'entraînement. Ce n'est pas une faiblesse cachée : c'est l'argument du
produit — ce scoreboard ne publie que ce qu'il peut démontrer.

`pipeline/models/gate.json` fait foi : le tableau ci-dessous le recopie, il ne
le décide pas. En cas de désaccord, c'est le tableau qui a vieilli.

Pour les stations houle **et vent**, la baseline officielle n'est pas un modèle
fixe : à l'entraînement, `scripts/train.py` retient par station le **meilleur**
des 5 modèles de vagues Open-Meteo Marine (`meteofrance_wave`, `ecmwf_wam025`,
`gwam`, `ewam`, `ncep_gfswave025`) ou des 3 modèles de vent
(`meteofrance_arpege_europe`, `ecmwf_ifs025`, `icon_eu`) et l'écrit dans
l'artefact (`baseline_model`) — c'est ce choix, pas un nom générique "MFWAM",
que le gate et le serve utilisent ensuite.

| station | variable | source obs | baseline officielle | publiée |
|---|---|---|---|---|
| Pierres Noires | houle (Hs) | Candhis | Open-Meteo Marine (`ncep_gfswave025`) | oui |
| Belle-Île | houle (Hs) | Candhis | Open-Meteo Marine (`ewam`) | oui |
| Anglet | houle (Hs) | Candhis | Open-Meteo Marine (`meteofrance_wave`) | oui |
| Cherbourg | houle (Hs) | Candhis | Open-Meteo Marine (`ewam`) | non (sous le gate) |
| Brest | niveau d'eau | SHOM REFMAR | harmonique (utide) | oui (signalée `weak`) |
| Saint-Malo | niveau d'eau | SHOM REFMAR | harmonique (utide) | non (sous le gate) |
| Ouessant (Le Stiff) | vent 10 m | Météo-France DPObs | Open-Meteo (`meteofrance_arpege_europe`) | oui |
| Dieppe | vent 10 m | Météo-France DPObs | Open-Meteo (`meteofrance_arpege_europe`) | oui |
| Cherbourg (Homet) | vent 10 m | Météo-France DPObs | Open-Meteo (`meteofrance_arpege_europe`) | oui |

Les trois stations de vent sont des **anémomètres côtiers à 10 m au-dessus du
sol** : un proxy du vent au large, pas le vent au large. Dieppe est à 8,6 km du
parc éolien de Dieppe-Le Tréport, mais à 40 m d'altitude et bien plus abritée
(4,61 m/s de moyenne, contre 7,75 à Ouessant). Le critère de sélection de
chaque station et sa mesure justificative sont dans
[`docs/data-sources.md`](docs/data-sources.md) § 4quinquies.

## Architecture

Aucun serveur : un batch quotidien GitHub Actions écrit des fichiers JSON
(+ un Parquet d'archive) commités dans ce repo ; un site statique externe
les consomme.

```
GitHub Actions (cron, voir .github/workflows/daily.yml)
        │
        ▼
  uv run scoreboard daily
        │
        ├─ 1. score les prédictions publiées hier (obs Candhis/SHOM d'aujourd'hui)
        ├─ 2. baseline du jour : meilleur modèle vague Open-Meteo Marine
        │      (`baseline_model`, choisi par station à l'entraînement) ou
        │      refit harmonique (utide)
        ├─ 3. prévision vent ARPEGE (Open-Meteo) → inférence du modèle IA (par station)
        ├─ 4. publie data/<station>/latest.json + history.json + data/scores.json
        └─ 5. archive le vent + les modèles vague servis → pipeline/data_forecast_archive/YYYY-MM-DD.parquet
        │
        ▼
  git commit + push (uniquement si data/ ou pipeline/data_forecast_archive/ a changé)
```

Le chemin vague est passé de Copernicus Marine (MFWAM/CMEMS) à l'API Marine
d'Open-Meteo lors du retrain multi-modèles (2026-08) — voir
`docs/data-sources.md` § 4ter pour la justification et les chiffres de
couverture/écart mesurés.

`pipeline/data_forecast_archive/` n'est pas un sous-produit accessoire : c'est
le corpus qui permettra un jour de ré-entraîner sur le vent *réellement servi*
(ARPEGE) plutôt que sur la réanalyse ERA5 utilisée à l'entraînement — voir
`docs/data-sources.md` §4bis pour le skew que ça corrige (résolu pour les
vagues, toujours ouvert pour le vent). Il archive aussi, depuis Task 7, les
colonnes `hs_*` des 5 modèles vague effectivement servis à chaque station —
pas seulement le vent.

## Commandes

```bash
cd pipeline
uv sync

uv run scoreboard daily [--date YYYY-MM-DD] [--dry-run]
uv run scoreboard backfill --since YYYY-MM-DD [--dry-run]
uv run pytest
```

`--dry-run` écrit dans un répertoire temporaire — jamais dans `data/` ni
`pipeline/data_forecast_archive/`.

## Mise en service (à exécuter par un humain, une seule fois)

Aucun agent n'a créé le dépôt ni posé de secret pour ce projet — ces deux
actions manipulent de vraies clés et sont volontairement laissées à
l'utilisateur.

**Important** : un `schedule:` GitHub Actions ne se déclenche que sur la
branche par défaut du dépôt. Le code est actuellement sur `feat/v1` ; la
branche poussée par `gh repo create --source . --push` ci-dessous devient la
branche par défaut d'un dépôt tout neuf, donc c'est réglé automatiquement ici
— mais si `.github/workflows/daily.yml` finit un jour sur une branche non
défaut (fusion vers `main` avec `feat/v1` gardée active, par exemple), le
cron ne se déclenchera plus jamais silencieusement (`workflow_dispatch`
continuera de marcher et masquera le problème). Vérifier la branche par
défaut du dépôt si le run quotidien s'arrête sans erreur visible.

```bash
# 1. Créer le dépôt (public — c'est le produit vitrine) et pousser le code
gh repo create metocean-ai-scoreboard --public --source . --push

# 2. Poser le secret attendu par .github/workflows/daily.yml
gh secret set CANDHIS_API_KEY

# 3. Déclencher un premier run manuel et vérifier le commit de données
gh workflow run daily.yml
# ... attendre la fin du run (gh run list / gh run watch) ...
# puis relancer une 2e fois : aucun nouveau commit ne doit apparaître
# (idempotence — cf. score déjà publié pour la date du jour)
gh workflow run daily.yml
```

## Cron

Le run quotidien est planifié à **07:30 UTC** (`.github/workflows/daily.yml`).

**Historique de cette heure.** Le brief prévoyait 06:00 UTC. Elle a été recalée
à 09:30 après avoir listé directement le bucket S3 source de Copernicus Marine
sur 12 jours : le fichier MFWAM du jour n'était publié qu'entre 08:10 et
08:50 UTC selon les jours, et 09:30 laissait ~40 min de marge sur le pire cas
observé (méthode et données :
`.superpowers/sdd/2026-07-30-scoreboard-metocean-ia/task-10-report.md`). Cette
contrainte a disparu avec le retrait de CMEMS (`docs/data-sources.md` § 4ter),
mais 09:30 est resté en place faute de nouvelle mesure.

**Ce qui a été mesuré le 2026-08-04, à 07:00 UTC.** Un `scoreboard daily
--dry-run` complet passe : les 7 stations publiées ressortent `ok`. Et la
fraîcheur des observations — la contrainte qu'on soupçonnait la plus basse,
puisque le run note la prévision de la veille contre l'obs réelle — n'en est
pas une : Candhis et REFMAR sont à 0,1 h du temps réel, DPObs à 1,1 h, et la
veille est complète (24/24 h) chez les trois.

**Pourquoi 07:30, et pourquoi pas une marge plus large.** Un premier réflexe a
été de garder ~1,5 h de marge sur le point mesuré, au nom du pire cas. C'est
le mauvais arbitrage, parce que les deux modes de panne ne sont pas
symétriques :

- *Trop tôt, source pas encore servie* → `missing`. Visible, et rattrapable :
  `_missing_dates` ne considère jamais un `missing` comme fait, un
  `backfill --since` le rejoue depuis les archives.
- *Run de la veille servi à la place de celui du jour* → aucune trace.
  Open-Meteo sert le dernier run disponible, celui de la veille couvre encore
  tout l'horizon et ressort `ok` à l'identique.

Une marge horaire protège du premier et **pas du second** : elle achète une
assurance contre la panne récupérable et zéro contre la panne silencieuse.
07:30 laisse 30 min sur le point mesuré, ce qui couvre le glissement de
planification d'Actions, et n'achète rien de plus.

**Ce qu'il faut surveiller.** Un `missing` n'est pas gratuit pour autant : le
jour rejoué porte `backfilled: true` et ne compte pas comme jour noté en
direct. Or c'est le stock de jours réels qui manque aujourd'hui, et qui
conditionne les graphiques et l'arbitrage nowcasting. Un cron trop bas se
paierait en jours reconstitués, pas en données perdues. Si le taux de
`missing` monte, remonter l'heure — c'est une ligne.

**Réserve à ne pas perdre.** Le dry-run prouve qu'une prévision *utilisable*
est servie à 07:00, pas que c'est le run du jour ; la panne silencieuse
ci-dessus n'est donc pas exclue à 07:30, ni ne l'était à 09:30. La trancher
demande de comparer, sur plusieurs jours, la prévision servie tôt à celle
servie plus tard pour les mêmes heures cibles —
`pipeline/data_forecast_archive/` est le corpus qui le permettra.

Une station en `"missing"` (503 transitoire, station sous le gate, source
indisponible) est un résultat normal du run — le job ne devient rouge que sur
un vrai échec (exception non rattrapée, `uv sync` cassé, push refusé).

Note d'exploitation : un `schedule:` GitHub Actions peut se déclencher avec
plusieurs minutes de retard, et un run planifié peut être sauté si le dépôt
reste inactif longtemps — `workflow_dispatch:` reste le rattrapage manuel
dans les deux cas.
