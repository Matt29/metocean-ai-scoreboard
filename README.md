# Scoreboard Metocean IA

Un modèle IA **post-traite** la prévision physique officielle (le meilleur des
5 modèles de vagues Open-Meteo Marine pour la houle et le meilleur des 3
modèles de vent — `baseline_model`, choisi par station à l'entraînement — un
modèle harmonique ajusté sur 2 ans glissants pour le niveau d'eau) sur des stations françaises de
référence, et publie chaque jour son écart mesuré face à cette baseline
officielle. Ce n'est pas un modèle qui remplace la physique — c'est une
correction statistique dessus, notée publiquement, jour après jour.

Spec complète : [`docs/superpowers/specs/2026-07-30-scoreboard-metocean-ia-design.md`](docs/superpowers/specs/2026-07-30-scoreboard-metocean-ia-design.md).
Justification de chaque source de données : [`docs/data-sources.md`](docs/data-sources.md).

## Stations

9 stations actives, **8 publiées**, plus un pilote Gascogne inactif — la
neuvième station active est mesurée comme
les autres tous les jours, mais un *gate* qualité (`pipeline/models/gate.json`)
retient toute station où l'IA ne bat pas sa propre baseline sur les données
d'entraînement. Ce n'est pas une faiblesse cachée : c'est l'argument du
produit — ce scoreboard ne publie que ce qu'il peut démontrer.

Sur les stations de niveau d'eau, la baseline harmonique est ajustée sur
**2 ans glissants** (`harmonic.FIT_LOOKBACK_DAYS`), et cette profondeur n'est
pas un réglage : 90 jours ne séparent ni S2/K2 ni K1/P1 (182,6 j requis), et
365 jours tombent *exactement* sur le seuil de Rayleigh de la constituante
annuelle Sa — estimée au bord de sa séparabilité, donc bruitée et mal
extrapolée. Mesuré le 2026-08-04, en causal : passer de 365 à 730 jours réduit
la MAE du résidu de 29 % à Brest (16,8 → 11,9 cm).

Cette profondeur décide du verdict, et pas dans le sens attendu. Améliorer la
baseline physique n'a pas réduit la marge du modèle : ça a supprimé l'avantage
injuste du concurrent. Tant que la baseline dérivait, la « baseline débiaisée »
à laquelle le gate la compare pouvait retirer gratuitement plusieurs
centimètres de biais — un adversaire artificiellement fort, contre lequel le
modèle faisait match nul. Baseline conditionnée, ce biais s'effondre et le
skill réel apparaît. Brest et Saint-Malo passent toutes deux le gate.

Les stations `tide` sont entraînées sur un forçage **stratifié par âge de
run** : une ligne à +48 h est forcée par une prévision ECMWF réellement émise
deux jours plus tôt (Previous Runs API d'Open-Meteo), et le run quotidien sert
le même modèle. Cette contrainte a été posée le 2026-08-04 pour lever un doute
sur les chiffres — le forçage d'entraînement provenait jusque-là de runs
concaténés au plus frais, donc d'une quasi-analyse. Le doute est levé : à ce
changement-là les gains n'ont quasiment pas bougé (brest +53,1 → +53,3 %,
saint-malo +30,0 → +28,0 %) et la pente gain/échéance est restée la même. Le
forçage n'est pas accessoire pour autant — l'annuler coûte 14 points à Brest —
mais son **âge** ne coûte presque rien, ce qui est cohérent avec l'ordre de
grandeur physique : 1,4 hPa d'erreur ECMWF à +48 h, soit ~1,4 cm de baromètre
inverse contre ~6 cm de MAE modèle. Détail et méthode :
[`docs/plan-dev-modele.md`](docs/plan-dev-modele.md).

Chiffres publiés au 2026-08-04, après l'ajout de la phase de marée et de la
tendance de pression en features : **brest +53,9 % hors biais** (11,8 → 5,4 cm
de MAE) et **saint-malo +34,1 %** (15,1 → 9,9 cm). Les deux features apportent
+3,5 et +4,3 points, et pas aux mêmes stations : la tendance de pression fait
presque tout à Brest, la phase de marée presque tout à Saint-Malo.

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
| Bouée Gascogne | houle (Hs) | Météo-France DPObs `/bouees` | Open-Meteo Marine (à sélectionner) | pilote inactif |
| Brest | niveau d'eau | SHOM REFMAR | harmonique (utide, 2 ans) | oui |
| Saint-Malo | niveau d'eau | SHOM REFMAR | harmonique (utide, 2 ans) | oui |
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
        ├─ 1. archive une fois les 9 bouées Météo-France
        │      → Parquet brut + contrôle fraîcheur/complétude Hs
        │      → data/buoys.json + data/buoys/<wmo>/{latest,history}.json
        │
        ▼
  uv run scoreboard daily
        │
        ├─ 2. score les prédictions publiées hier (obs Candhis/SHOM d'aujourd'hui)
        ├─ 3. baseline du jour : meilleur modèle vague Open-Meteo Marine
        │      (`baseline_model`, choisi par station à l'entraînement) ou
        │      constantes harmoniques persistées (`models/<station>-harmonic.joblib`,
        │      ré-ajustées par le run lui-même quand elles dépassent 30 j)
        ├─ 4. prévision atmosphérique Open-Meteo (ARPEGE/ICON/ECMWF selon le kind)
        │      → inférence du modèle IA (par station)
        ├─ 5. publie data/<station>/latest.json + history.json + data/scores.json
        └─ 6. archive le vent + les modèles vague servis → pipeline/data_forecast_archive/YYYY-MM-DD.parquet
        │
        ▼
  git commit + push (uniquement si data/ ou pipeline/data_forecast_archive/ a changé)
```

Le chemin vague est passé de Copernicus Marine (MFWAM/CMEMS) à l'API Marine
d'Open-Meteo lors du retrain multi-modèles (2026-08) — voir
`docs/data-sources.md` § 4ter pour la justification et les chiffres de
couverture/écart mesurés.

`pipeline/data_forecast_archive/` n'est pas un sous-produit accessoire : c'est
le corpus du vent *réellement servi*, jour après jour — voir
`docs/data-sources.md` §4bis pour le skew qu'il corrige. Ce skew est désormais
traité en amont sur les trois kinds (chacun s'entraîne sur des runs passés du
modèle qu'on lui sert, et la marée sur des runs stratifiés par âge), donc
l'archive n'est plus le seul instrument possible — elle reste la seule mesure
véritablement vraie, à des mois d'échéance. Il archive aussi, depuis Task 7,
les colonnes `hs_*` des 5 modèles vague effectivement servis à chaque station —
pas seulement le vent.

## Commandes

```bash
cd pipeline
uv sync

uv run scoreboard daily [--date YYYY-MM-DD] [--dry-run]
uv run scoreboard backfill --since YYYY-MM-DD [--dry-run]
uv run pytest

# Construit uniquement le pilote inactif Gascogne depuis l'archive bouées locale,
# sans refaire une requête `/bouees` ni consommer le quota Candhis des stations actives.
uv run python scripts/build_dataset.py --kind wave --station gascogne-bouee --include-pilots --days 90

# Quand son historique sera suffisant, l'entraîne explicitement sans l'activer.
uv run python scripts/train.py --station gascogne-bouee --include-pilots

# régénère la section « Data » de docs/dev-dashboard.html (couverture par
# station, archive bouées Météo-France, jeux d'entraînement) — lecture seule
# ailleurs, réécrit uniquement le bloc balisé DATA:START/DATA:END
uv run python scripts/data_coverage.py
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
